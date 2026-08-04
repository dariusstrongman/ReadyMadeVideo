"""Final repair pass — P1 blockers: delete retry/cleanup-state, soft-deleted projects
rejected from every customer API, and the disabled legacy /render surface.
"""
import os
from uuid import uuid4

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
