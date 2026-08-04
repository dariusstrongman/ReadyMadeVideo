"""Final repair pass — P1 blockers: delete retry/cleanup-state, soft-deleted projects
rejected from every customer API, and the disabled legacy /render surface.
"""
import os
from uuid import uuid4

import pytest

os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "fake-service-key")
os.environ["WORKER_ENABLED"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from app import autoedit_bridge, jobs, supa  # noqa: E402
from app.main import app  # noqa: E402
from tests.fake_supa import FakeSupabase, install  # noqa: E402


def _auth(t):
    return {"Authorization": f"Bearer {t}"}


def _setup(monkeypatch, tmp_path, bridge=True):
    from app import main
    main._rate.clear()
    fake = FakeSupabase()
    install(monkeypatch, fake)
    uid, token = fake.add_user("owner@example.com")
    project = fake.add_project(uid, status="draft_ready")
    asset_id = str(uuid4())
    key = f"users/{uid}/projects/{project['id']}/raw/clip.mp4"
    fake.insert("media_assets", {"id": asset_id, "project_id": project["id"], "user_id": uid,
                                 "filename": "clip.mp4", "storage_path": key,
                                 "duration_seconds": 8.0})
    fake.storage[f"raw-footage/{key}"] = b"VIDEO"
    cand = None
    if bridge:
        tl = {"version": 1, "width": 1080, "height": 1920, "fps": 30, "duration": 8,
              "tracks": [{"id": "v", "type": "video", "clips": [
                  {"id": "c", "assetId": asset_id, "sourceStart": 0, "sourceEnd": 8,
                   "timelineStart": 0, "timelineEnd": 8, "speed": 1, "volume": 1}]}]}
        tl_row = fake.insert("timelines", {"project_id": project["id"], "user_id": uid,
                                           "version": 1, "timeline_json": tl,
                                           "lineage": "autonomous_revised"}).json()[0]
        preview = tmp_path / "p.mp4"
        preview.write_bytes(b"P")
        cand = autoedit_bridge.bridge_from_autoedit(
            project, tl_row, str(preview), insert=jobs._insert, db_select=supa.db_select,
            upload_export=jobs._upload_export, now=jobs._now, remove=supa.storage_remove)
    return fake, uid, token, project, asset_id, cand


# ---------------- P1-5: delete cleanup retry + not swallowed ----------------
def test_delete_records_and_retries_failed_cleanup(monkeypatch, tmp_path):
    fake, uid, token, project, _, _ = _setup(monkeypatch, tmp_path)
    client = TestClient(app, raise_server_exceptions=False)
    calls = {"n": 0}

    def flaky(bucket, prefix):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("storage unavailable")   # first cleanup fails
        return 0
    monkeypatch.setattr(supa, "storage_remove_prefix", flaky)

    first = client.delete(f"/projects/{project['id']}", headers=_auth(token))
    assert first.status_code == 503                     # failure surfaced, not swallowed
    row = fake.select("projects", f"id=eq.{project['id']}")[0]
    assert row["deleted_at"] and row["deleted_cleanup_done"] is False   # retryable state

    second = client.delete(f"/projects/{project['id']}", headers=_auth(token))
    assert second.status_code == 200                    # repeated DELETE retries cleanup
    assert fake.select("projects", f"id=eq.{project['id']}")[0]["deleted_cleanup_done"] is True

    third = client.delete(f"/projects/{project['id']}", headers=_auth(token))
    assert third.status_code == 200 and third.json()["cleanup"] == "complete"  # idempotent


def test_delete_cleans_all_project_storage_prefixes(monkeypatch, tmp_path):
    fake, uid, token, project, _, cand = _setup(monkeypatch, tmp_path)
    # seed derived artifacts under both project prefixes (proxies, drafts, etc.)
    for k in (f"raw-footage/users/{uid}/projects/{project['id']}/raw/proxy.mp4",
              f"raw-footage/users/{uid}/projects/{project['id']}/licensed-music/a.wav",
              f"exports/users/{uid}/projects/{project['id']}/drafts/j/preview.mp4",
              f"exports/{cand['preview_storage_path']}"):
        fake.storage[k] = b"x"
    client = TestClient(app, raise_server_exceptions=False)
    r = client.delete(f"/projects/{project['id']}", headers=_auth(token))
    assert r.status_code == 200
    leftover = [k for k in fake.storage if f"projects/{project['id']}/" in k]
    assert leftover == []                               # every prefix cleaned


# ---------------- P1-6: soft-deleted projects rejected from customer APIs ----------------
def test_soft_deleted_project_rejected_everywhere(monkeypatch, tmp_path):
    fake, uid, token, project, _, cand = _setup(monkeypatch, tmp_path)
    client = TestClient(app, raise_server_exceptions=False)
    assert client.delete(f"/projects/{project['id']}", headers=_auth(token)).status_code == 200
    pid = project["id"]
    assert client.get(f"/projects/{pid}/workspace", headers=_auth(token)).status_code == 404
    assert client.post(f"/projects/{pid}/editor/start", headers=_auth(token),
                       json={"candidateRunId": cand["id"]}).status_code == 404
    assert client.post(f"/projects/{pid}/candidates/{cand['id']}/preview-url",
                       headers=_auth(token), json={}).status_code == 404
    assert client.post(f"/projects/{pid}/editor/render", headers=_auth(token),
                       json={"documentId": str(uuid4())}).status_code == 404


def test_get_job_rejects_soft_deleted_parent_for_owner(monkeypatch, tmp_path):
    fake, uid, token, project, _, cand = _setup(monkeypatch, tmp_path)
    client = TestClient(app, raise_server_exceptions=False)
    job = fake.insert("pipeline_jobs", {"project_id": project["id"], "user_id": uid,
                                        "kind": "autoedit"}).json()[0]
    assert client.get(f"/jobs/{job['id']}", headers=_auth(token)).status_code == 200
    assert client.delete(f"/projects/{project['id']}", headers=_auth(token)).status_code == 200
    r = client.get(f"/jobs/{job['id']}", headers=_auth(token))
    assert r.status_code == 404          # parent soft-deleted -> job hidden from the owner
    # An operator still sees it (support/forensics access preserved).
    _, op_token = fake.add_user("op@example.com", operator=True)
    assert client.get(f"/jobs/{job['id']}", headers=_auth(op_token)).status_code == 200


# ---------------- P1-7: legacy /render disabled ----------------
def test_legacy_render_disabled(monkeypatch, tmp_path):
    fake, uid, token, project, _, _ = _setup(monkeypatch, tmp_path, bridge=False)
    client = TestClient(app, raise_server_exceptions=False)
    r = client.post("/render", headers=_auth(token), json={"job_id": str(uuid4())})
    assert r.status_code == 410


# ---------------- new blocker 1: version-bound export idempotency ----------------
def _reorder(target, base):
    return {"expectedVersion": base, "operations": [{
        "type": "reorder_clip", "actor": "user", "targetId": target,
        "baseVersion": base, "toIndex": 0}]}


def test_export_never_reuses_a_different_revisions_render(monkeypatch, tmp_path):
    fake, uid, token, project, asset_id, cand = _setup(monkeypatch, tmp_path)
    client = TestClient(app, raise_server_exceptions=False)
    v1 = client.post(f"/projects/{project['id']}/editor/start",
                     headers=_auth(token), json={"candidateRunId": cand["id"]}).json()
    picture = next(t for t in v1["document"]["tracks"] if t["type"] == "picture")["items"]

    r1 = client.post(f"/projects/{project['id']}/editor/render",
                     headers=_auth(token), json={"documentId": v1["id"]})
    assert r1.status_code == 200
    job_v1 = r1.json()

    # duplicate export of the SAME revision -> same job (idempotent)
    dup = client.post(f"/projects/{project['id']}/editor/render",
                      headers=_auth(token), json={"documentId": v1["id"]})
    assert dup.json()["id"] == job_v1["id"]

    # save v2, then export v2 while v1's render is still active -> rejected, NOT the v1 job
    v2 = client.post(f"/projects/{project['id']}/editor/{v1['id']}/operations",
                     headers=_auth(token), json=_reorder(picture[0]["id"], 1)).json()
    r2 = client.post(f"/projects/{project['id']}/editor/render",
                     headers=_auth(token), json={"documentId": v2["id"]})
    assert r2.status_code == 409                       # never silently returns the v1 job


def test_export_race_never_returns_a_different_revisions_job(monkeypatch, tmp_path):
    """Faithful simulation of the concurrent race: a different-revision request won the
    insert AFTER our pre-check passed, so enqueue_job hands us back ITS job. The endpoint
    must revalidate and reject — never return a render bound to the wrong revision."""
    fake, uid, token, project, asset_id, cand = _setup(monkeypatch, tmp_path)
    client = TestClient(app, raise_server_exceptions=False)
    v1 = client.post(f"/projects/{project['id']}/editor/start",
                     headers=_auth(token), json={"candidateRunId": cand["id"]}).json()
    picture = next(t for t in v1["document"]["tracks"] if t["type"] == "picture")["items"]
    v2 = client.post(f"/projects/{project['id']}/editor/{v1['id']}/operations",
                     headers=_auth(token), json=_reorder(picture[0]["id"], 1)).json()

    # enqueue_job returns the winner's active v1 job (what the real 409-dedup does when a
    # concurrent v1 request landed inside our race window). No v1 job is actually active,
    # so the pre-check passes and control reaches the post-enqueue revalidation guard.
    winner = {"id": "winner-v1-job", "project_id": project["id"], "kind": "final_render",
              "status": "queued",
              "params": {"editor_document_id": v1["id"], "editor_document_version": 1}}
    monkeypatch.setattr(jobs, "enqueue_job", lambda *a, **k: winner)

    r = client.post(f"/projects/{project['id']}/editor/render",
                    headers=_auth(token), json={"documentId": v2["id"]})
    assert r.status_code == 409
    assert "winner-v1-job" not in r.text          # never leaks the wrong-revision job


def test_true_concurrent_exports_yield_one_job_and_no_cross_revision(monkeypatch, tmp_path):
    """Real threads: several exports fired in parallel for two different revisions. The
    (project, kind) partial-unique index admits ONE active final_render; every other
    request must get 409/429 and no 200 may return a job for a revision it didn't ask
    for. Serializing only the insert append makes the fake's unique index atomic — the
    request handlers still run concurrently."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    fake, uid, token, project, asset_id, cand = _setup(monkeypatch, tmp_path)
    client = TestClient(app, raise_server_exceptions=False)
    v1 = client.post(f"/projects/{project['id']}/editor/start",
                     headers=_auth(token), json={"candidateRunId": cand["id"]}).json()
    picture = next(t for t in v1["document"]["tracks"] if t["type"] == "picture")["items"]
    v2 = client.post(f"/projects/{project['id']}/editor/{v1['id']}/operations",
                     headers=_auth(token), json=_reorder(picture[0]["id"], 1)).json()

    lock, orig_insert = threading.Lock(), fake.insert
    def locked_insert(table, body):          # make check-then-append atomic across threads
        with lock:
            return orig_insert(table, body)
    monkeypatch.setattr(fake, "insert", locked_insert)

    def fire(doc_id):
        c = TestClient(app, raise_server_exceptions=False)
        r = c.post(f"/projects/{project['id']}/editor/render",
                   headers=_auth(token), json={"documentId": doc_id})
        return doc_id, r.status_code, r.json()

    requests = [v1["id"], v2["id"]] * 3       # 6 concurrent, two revisions interleaved
    with ThreadPoolExecutor(max_workers=6) as ex:
        results = list(ex.map(fire, requests))

    for doc_id, status, _ in results:
        assert status in (200, 409, 429)      # never 500 / never silent wrong job
    created = [r for r in fake.select("pipeline_jobs", f"project_id=eq.{project['id']}")
              if r["kind"] == "final_render"]
    assert len(created) <= 1                  # at most one active render for the project
    ok = [(doc_id, body) for doc_id, status, body in results if status == 200]
    for doc_id, body in ok:                   # every success maps to ITS OWN revision
        want = 1 if doc_id == v1["id"] else 2
        assert (body.get("params") or {}).get("editor_document_version") == want
    assert len({body["id"] for _, body in ok}) <= 1   # all successes share one job


# ---------------- new blocker 3: deleted project cannot restart analysis ----------------
def test_deleted_project_cannot_request_analysis(monkeypatch, tmp_path):
    fake, uid, token, project, _, _ = _setup(monkeypatch, tmp_path, bridge=False)
    client = TestClient(app, raise_server_exceptions=False)
    assert client.delete(f"/projects/{project['id']}", headers=_auth(token)).status_code == 200
    r = client.post(f"/projects/{project['id']}/request-analysis", headers=_auth(token))
    assert r.status_code == 404


# ---------------- new blocker 5: bridge ancestry bound to its source timeline ----------------
def test_bridge_does_not_reuse_another_timelines_partial_ancestry(monkeypatch, tmp_path):
    fake, uid, token, project, asset_id, _cand = _setup(monkeypatch, tmp_path, bridge=False)
    # partial ancestry left by a prior failed bridge of timeline A (no candidate)
    fake.insert("preproduction_runs", {
        "id": "pre-A", "project_id": project["id"], "user_id": uid, "version": 1,
        "status": "ready", "request": {"origin": "basic_autoedit", "timeline_id": "A"},
        "creative_treatment": {}, "capture_quality_report": {},
        "composition_by_segment": {}, "story_variants": []})
    # now bridge timeline B — must NOT reuse timeline A's preproduction
    tl = {"version": 1, "width": 1080, "height": 1920, "fps": 30, "duration": 8,
          "tracks": [{"id": "v", "type": "video", "clips": [
              {"id": "c", "assetId": asset_id, "sourceStart": 0, "sourceEnd": 8,
               "timelineStart": 0, "timelineEnd": 8, "speed": 1, "volume": 1}]}]}
    tl_row = fake.insert("timelines", {"id": "B", "project_id": project["id"], "user_id": uid,
                                       "version": 2, "timeline_json": tl,
                                       "lineage": "autonomous_revised"}).json()[0]
    preview = tmp_path / "p.mp4"
    preview.write_bytes(b"P")
    cand = autoedit_bridge.bridge_from_autoedit(
        project, tl_row, str(preview), insert=jobs._insert, db_select=supa.db_select,
        upload_export=jobs._upload_export, now=jobs._now, remove=supa.storage_remove)
    assert cand["preproduction_run_id"] != "pre-A"     # did not cross timelines
    pre = fake.select("preproduction_runs", f"id=eq.{cand['preproduction_run_id']}")[0]
    assert pre["request"]["timeline_id"] == "B"


# ---------------- new blocker 4: storage cleanup paginates past 1000 objects ----------------
class _Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_storage_remove_prefix_paginates(monkeypatch):
    import httpx

    from app import supa
    seen = {"list": 0, "deleted": []}

    def fake_post(url, headers=None, json=None, timeout=None):
        if "/object/list/" in url:
            seen["list"] += 1
            offset = json["offset"]
            if offset == 0:
                return _Resp(200, [{"name": f"f{i}.mp4", "id": str(i)} for i in range(1000)])
            if offset == 1000:
                return _Resp(200, [{"name": f"f{i}.mp4", "id": str(i)} for i in range(1000, 1500)])
            return _Resp(200, [])
        return _Resp(200, {})

    def fake_request(method, url, headers=None, json=None, timeout=None):
        seen["deleted"].extend(json["prefixes"])
        return _Resp(200, {})

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "request", fake_request)
    removed = supa.storage_remove_prefix("raw-footage", "users/u/projects/p/")
    assert removed == 1500                    # every object across pages
    assert seen["list"] >= 2                  # paginated beyond the first 1000
    assert len(seen["deleted"]) == 1500


# ---------------- Issue 5: bridge concurrency + ancestry ----------------
def _timeline_row(fake, project, asset_id, uid, *, tid=None, version=1):
    tl = {"version": 1, "width": 1080, "height": 1920, "fps": 30, "duration": 8,
          "tracks": [{"id": "v", "type": "video", "clips": [
              {"id": "c", "assetId": asset_id, "sourceStart": 0, "sourceEnd": 8,
               "timelineStart": 0, "timelineEnd": 8, "speed": 1, "volume": 1}]}]}
    body = {"project_id": project["id"], "user_id": uid, "version": version,
            "timeline_json": tl, "lineage": "autonomous_revised"}
    if tid:
        body["id"] = tid
    return fake.insert("timelines", body).json()[0]


def _bridge(project, tl_row, preview, **over):
    kw = dict(insert=jobs._insert, db_select=supa.db_select, upload_export=jobs._upload_export,
              now=jobs._now, remove=supa.storage_remove, update=supa.db_update)
    kw.update(over)
    return autoedit_bridge.bridge_from_autoedit(project, tl_row, str(preview), **kw)


def test_bridge_version_allocation_retries_on_collision(monkeypatch, tmp_path):
    """max(version)+1 is race-prone; a unique-violation must trigger a bounded retry, not
    a crash or a lost candidate."""
    fake, uid, token, project, asset_id, _ = _setup(monkeypatch, tmp_path, bridge=False)
    tl_row = _timeline_row(fake, project, asset_id, uid, tid="T1")
    preview = tmp_path / "p.mp4"
    preview.write_bytes(b"P")
    fake.conflict_once_tables.add("preproduction_runs")   # first version insert 409s once
    cand = _bridge(project, tl_row, preview)
    assert cand and cand["generation_kind"] == "bridged"  # retry produced the candidate
    assert len(fake.select("preproduction_runs", f"project_id=eq.{project['id']}")) == 1


def test_concurrent_same_timeline_bridge_is_one_candidate_no_dup_ancestry(monkeypatch, tmp_path):
    """True concurrent bridges of the SAME project+timeline: exactly one candidate, one
    preproduction, one picture (bound to it), and no cross-timeline linkage."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    fake, uid, token, project, asset_id, _ = _setup(monkeypatch, tmp_path, bridge=False)
    tl_row = _timeline_row(fake, project, asset_id, uid, tid="TL-CONC")
    preview = tmp_path / "p.mp4"
    preview.write_bytes(b"P")

    lock, orig_insert = threading.Lock(), fake.insert
    def locked_insert(table, body):          # atomic check-then-append (real unique index)
        with lock:
            return orig_insert(table, body)
    monkeypatch.setattr(fake, "insert", locked_insert)   # _route -> self.insert picks this up

    results = list(ThreadPoolExecutor(max_workers=4).map(
        lambda _i: _bridge(project, tl_row, preview), range(4)))

    assert all(r is not None for r in results)
    assert len({r["id"] for r in results}) == 1                    # one shared candidate
    cands = fake.select("candidate_runs", f"project_id=eq.{project['id']}")
    pres = fake.select("preproduction_runs", f"project_id=eq.{project['id']}")
    pics = fake.select("picture_edit_runs", f"project_id=eq.{project['id']}")
    assert len(cands) == 1 and len(pres) == 1 and len(pics) == 1   # no duplicate ancestry
    assert pics[0]["preproduction_run_id"] == pres[0]["id"]        # picture bound to preproduction
    assert cands[0]["preproduction_run_id"] == pres[0]["id"]
    assert cands[0]["picture_edit_run_id"] == pics[0]["id"]
    assert pics[0]["request"]["timeline_id"] == "TL-CONC"          # no cross-timeline link


# ---------------- Issue 6: orphan preview cleanup persist + retry ----------------
def test_orphan_preview_cleanup_is_persisted_then_drained(monkeypatch, tmp_path):
    fake, uid, token, project, asset_id, _ = _setup(monkeypatch, tmp_path, bridge=False)
    tl_row = _timeline_row(fake, project, asset_id, uid, tid="T-ORPHAN")
    preview = tmp_path / "p.mp4"
    preview.write_bytes(b"P")

    def dead_remove(bucket, path):
        raise RuntimeError("storage unavailable")

    # (1) candidate insert fails AND cleanup fails -> failure surfaced, orphan persisted.
    fake.fail_tables.add("candidate_runs")
    with pytest.raises(RuntimeError):
        _bridge(project, tl_row, preview, remove=dead_remove)
    fake.fail_tables.discard("candidate_runs")
    pending = [r for r in fake.select("pending_storage_cleanup",
                                      f"project_id=eq.{project['id']}") if not r.get("cleaned_at")]
    assert len(pending) == 1                               # retryable state recorded, not swallowed
    orphan = pending[0]
    assert f"{orphan['bucket']}/{orphan['object_path']}" in fake.storage   # object still there

    # (2) storage recovers -> the drain retries and clears the persisted record.
    cleaned = autoedit_bridge._drain_pending_cleanup(
        db_select=supa.db_select, remove=supa.storage_remove, update=supa.db_update,
        now=jobs._now, project_id=project["id"])
    assert cleaned == 1
    row = fake.select("pending_storage_cleanup", f"id=eq.{orphan['id']}")[0]
    assert row["cleaned_at"]                               # successful later cleanup
    assert f"{orphan['bucket']}/{orphan['object_path']}" not in fake.storage


def _preproduction(fake, project, uid, version):
    return fake.insert("preproduction_runs", {
        "project_id": project["id"], "user_id": uid, "version": version, "status": "ready",
        "request": {}, "creative_treatment": {}, "capture_quality_report": {},
        "composition_by_segment": {}, "story_variants": []}).json()[0]


def test_bridged_candidate_with_mismatched_ancestry_is_rejected(monkeypatch, tmp_path):
    """DB-level exact ancestry (mirrored in the fake): a bridged candidate pairing
    preproduction B with a picture run tied to preproduction A is rejected even though all
    rows share project + user."""
    fake, uid, token, project, asset_id, cand = _setup(monkeypatch, tmp_path)
    pre_a = _preproduction(fake, project, uid, 10)
    pre_b = _preproduction(fake, project, uid, 11)
    pic_a = fake.insert("picture_edit_runs", {
        "project_id": project["id"], "user_id": uid, "preproduction_run_id": pre_a["id"],
        "version": 10, "status": "ready", "request": {}, "visual_rhythm_plans": [],
        "candidates": [], "selected_candidate_id": "x"}).json()[0]
    resp = jobs._insert("candidate_runs", {
        "batch_id": str(uuid4()), "project_id": project["id"], "user_id": uid,
        "preproduction_run_id": pre_b["id"], "picture_edit_run_id": pic_a["id"],
        "candidate_key": "bridged", "candidate_index": 9, "generation_kind": "bridged",
        "source_picture_candidate_id": "x", "variant_config": {},
        "manifest": {"fabricatedFootage": False}, "render_qc": {},
        "preview_storage_bucket": "exports",
        "preview_storage_path": f"users/{uid}/projects/{project['id']}/autoedit/z.mp4",
        "created_by": uid})
    assert resp.status_code == 400
    assert "descend" in resp.json()["message"]


def test_orphan_cleanup_reopens_resolved_record_on_repeat_failure(monkeypatch, tmp_path):
    fake, uid, token, project, asset_id, _ = _setup(monkeypatch, tmp_path, bridge=False)
    bucket = "exports"
    path = f"users/{uid}/projects/{project['id']}/autoedit/orphan.mp4"

    def dead(_b, _p):
        raise RuntimeError("storage down")

    def persist():
        autoedit_bridge._cleanup_or_persist(
            remove=dead, insert=jobs._insert, update=supa.db_update, now=jobs._now,
            project=project, bucket=bucket, path=path, reason="cleanup failed")

    def drain():
        return autoedit_bridge._drain_pending_cleanup(
            db_select=supa.db_select, remove=supa.storage_remove, update=supa.db_update,
            now=jobs._now, project_id=project["id"])

    # 1) first failure creates a pending record
    fake.storage[f"{bucket}/{path}"] = b"orphan"
    persist()
    rows = fake.select("pending_storage_cleanup", f"project_id=eq.{project['id']}")
    assert len(rows) == 1 and rows[0]["cleaned_at"] is None
    rid = rows[0]["id"]

    # 2) successful drain marks cleaned_at
    assert drain() == 1
    assert fake.select("pending_storage_cleanup", f"id=eq.{rid}")[0]["cleaned_at"]

    # 3) same path fails again later -> the SAME row is reopened (no duplicate, eligible)
    fake.storage[f"{bucket}/{path}"] = b"orphan-again"
    persist()
    rows = fake.select("pending_storage_cleanup", f"project_id=eq.{project['id']}")
    assert len(rows) == 1                                  # reopened, not duplicated
    assert rows[0]["id"] == rid and rows[0]["cleaned_at"] is None

    # 4) later drain processes the reopened row -> back to resolved
    assert drain() == 1
    assert fake.select("pending_storage_cleanup", f"id=eq.{rid}")[0]["cleaned_at"]
    assert f"{bucket}/{path}" not in fake.storage


def test_concurrent_orphan_reopen_is_safe(monkeypatch, tmp_path):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    fake, uid, token, project, asset_id, _ = _setup(monkeypatch, tmp_path, bridge=False)
    bucket = "exports"
    path = f"users/{uid}/projects/{project['id']}/autoedit/c.mp4"
    # a previously-resolved row for this object
    fake.insert("pending_storage_cleanup", {
        "project_id": project["id"], "user_id": uid, "bucket": bucket, "object_path": path,
        "attempts": 1, "cleaned_at": jobs._now()})

    lock, orig_insert = threading.Lock(), fake.insert
    def locked_insert(table, body):
        with lock:
            return orig_insert(table, body)
    monkeypatch.setattr(fake, "insert", locked_insert)

    def dead(_b, _p):
        raise RuntimeError("storage down")

    def reopen(_i):
        autoedit_bridge._cleanup_or_persist(
            remove=dead, insert=jobs._insert, update=supa.db_update, now=jobs._now,
            project=project, bucket=bucket, path=path, reason="concurrent reopen")

    list(ThreadPoolExecutor(max_workers=4).map(reopen, range(4)))
    rows = fake.select("pending_storage_cleanup", f"project_id=eq.{project['id']}")
    assert len(rows) == 1                 # UNIQUE(bucket, object_path): no duplicate rows
    assert rows[0]["cleaned_at"] is None  # reopened -> eligible for the drain worker again


def test_idempotency_race_returns_winner_without_touching_shared_preview(monkeypatch, tmp_path):
    """On the idempotency-race (409) path the winner is returned. Because the preview key
    is deterministic per project, our upload IS the winner's referenced object, so it must
    be left intact — no deletion, no failure of the successful edit, even if remove would
    have failed."""
    fake, uid, token, project, asset_id, cand = _setup(monkeypatch, tmp_path)  # bridged cand exists
    winner_preview = f"exports/{cand['preview_storage_path']}"
    fake.storage.setdefault(winner_preview, b"WINNER")
    # Hide the existing candidate ONLY at the early idempotency check so control reaches the
    # candidate insert, which then 409s against the real (batch_id, candidate_key) unique row.
    calls = {"n": 0}
    real_select = supa.db_select
    def select_hiding_existing(table, filters, sel="*"):
        if (table == "candidate_runs" and "generation_kind=eq.bridged" in filters
                and calls["n"] == 0):
            calls["n"] += 1
            return []
        return real_select(table, filters, sel)

    def dead_remove(bucket, path):
        raise RuntimeError("remove must never be called on the shared winner preview")
    tl_row = fake.select("timelines", f"project_id=eq.{project['id']}")[0]
    preview = tmp_path / "p.mp4"
    preview.write_bytes(b"P")
    got = _bridge(project, tl_row, preview, db_select=select_hiding_existing, remove=dead_remove)
    assert got and got["id"] == cand["id"]               # successful edit: winner returned
    assert winner_preview in fake.storage                # winner's preview left intact
    assert fake.select("pending_storage_cleanup", f"project_id=eq.{project['id']}") == []
