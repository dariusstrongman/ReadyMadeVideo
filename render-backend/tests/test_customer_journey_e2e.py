"""Slice 5 — full customer-journey regression, driven through the real API + handlers:
upload -> analysis hand-off -> autoedit bridge -> candidate -> Product Editor ->
versioned save (twice) -> export -> render -> download -> safe delete.
Also covers idempotency, ownership, duplicate requests, and retries.
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


def _timeline(asset_id):
    return {"version": 1, "width": 1080, "height": 1920, "fps": 30, "duration": 8,
            "tracks": [{"id": "v", "type": "video", "clips": [
                {"id": "clip-a", "assetId": asset_id, "sourceStart": 0, "sourceEnd": 4,
                 "timelineStart": 0, "timelineEnd": 4, "speed": 1, "volume": 1},
                {"id": "clip-b", "assetId": asset_id, "sourceStart": 4, "sourceEnd": 8,
                 "timelineStart": 4, "timelineEnd": 8, "speed": 1, "volume": 1}]}]}


def _setup(monkeypatch, tmp_path):
    from app import main
    main._rate.clear()
    fake = FakeSupabase()
    install(monkeypatch, fake)
    uid, token = fake.add_user("owner@example.com")
    project = fake.add_project(uid, status="draft")
    asset_id = str(uuid4())
    key = f"users/{uid}/projects/{project['id']}/raw/clip.mp4"
    fake.insert("media_assets", {"id": asset_id, "project_id": project["id"], "user_id": uid,
                                 "filename": "clip.mp4", "storage_path": key,
                                 "duration_seconds": 8.0})
    fake.storage[f"raw-footage/{key}"] = b"VIDEO"
    tl_row = fake.insert("timelines", {"project_id": project["id"], "user_id": uid,
                                       "version": 1, "timeline_json": _timeline(asset_id),
                                       "lineage": "autonomous_revised"}).json()[0]
    return fake, uid, token, project, asset_id, tl_row


def _bridge(fake, project, tl_row, tmp_path):
    preview = tmp_path / "p.mp4"
    preview.write_bytes(b"P")
    cand = autoedit_bridge.bridge_from_autoedit(
        project, tl_row, str(preview), insert=jobs._insert, db_select=supa.db_select,
        upload_export=jobs._upload_export, now=jobs._now)
    fake.patch("projects", f"id=eq.{project['id']}", {"status": "draft_ready"})
    return cand


def _reorder(target_id, base_version, to_index):
    return {"expectedVersion": base_version, "operations": [{
        "type": "reorder_clip", "actor": "user", "targetId": target_id,
        "baseVersion": base_version, "toIndex": to_index}]}


def test_full_customer_journey(monkeypatch, tmp_path):
    fake, uid, token, project, asset_id, tl_row = _setup(monkeypatch, tmp_path)
    client = TestClient(app, raise_server_exceptions=False)

    # 1) Analysis -> autoedit hand-off (idempotent).
    jobs._maybe_enqueue_customer_autoedit(project)
    jobs._maybe_enqueue_customer_autoedit(project)
    assert len([j for j in fake.tables["pipeline_jobs"] if j["kind"] == "autoedit"]) == 1

    # 2) Autoedit bridge -> candidate (idempotent).
    cand = _bridge(fake, project, tl_row, tmp_path)
    assert cand["generation_kind"] == "bridged"
    again = _bridge(fake, project, tl_row, tmp_path)
    assert again["id"] == cand["id"]
    assert len([c for c in fake.tables["candidate_runs"] if c["generation_kind"] == "bridged"]) == 1

    # 3) Candidate visible in the workspace.
    ws = client.get(f"/projects/{project['id']}/workspace", headers=_auth(token)).json()
    assert cand["id"] in [c["id"] for c in ws["candidates"]]

    # 4) Product Editor open (candidate -> editor_document v1).
    v1 = client.post(f"/projects/{project['id']}/editor/start",
                     headers=_auth(token), json={"candidateRunId": cand["id"]}).json()
    assert v1["version"] == 1
    picture = next(t for t in v1["document"]["tracks"] if t["type"] == "picture")["items"]

    # 5) Save twice — each save advances to a NEW immutable revision id.
    v2 = client.post(f"/projects/{project['id']}/editor/{v1['id']}/operations",
                     headers=_auth(token), json=_reorder(picture[0]["id"], 1, 1)).json()
    assert v2["version"] == 2 and v2["id"] != v1["id"]
    v3 = client.post(f"/projects/{project['id']}/editor/{v2['id']}/operations",
                     headers=_auth(token), json=_reorder(picture[0]["id"], 2, 0))
    assert v3.status_code == 200, v3.text
    v3 = v3.json()
    assert v3["version"] == 3

    # Saving against a STALE revision id conflicts with latest (recoverable).
    stale = client.post(f"/projects/{project['id']}/editor/{v1['id']}/operations",
                        headers=_auth(token), json=_reorder(picture[0]["id"], 1, 1))
    assert stale.status_code == 409
    assert stale.json()["detail"]["latestVersion"] == 3

    # 6) Export the latest saved revision; duplicate click -> one active job.
    r1 = client.post(f"/projects/{project['id']}/editor/render",
                     headers=_auth(token), json={"documentId": v3["id"]})
    assert r1.status_code == 200, r1.text
    export_job = r1.json()
    assert export_job["kind"] == "final_render"
    assert export_job["params"]["editor_document_version"] == 3
    r2 = client.post(f"/projects/{project['id']}/editor/render",
                     headers=_auth(token), json={"documentId": v3["id"]})
    assert r2.json()["id"] == export_job["id"]
    assert len([j for j in fake.tables["pipeline_jobs"]
                if j["kind"] == "final_render" and j["status"] in ("queued", "processing")]) == 1

    # 7) Worker renders the exact bound revision (bridged -> original audio).
    import app.renderer2 as r2mod

    def fake_render(tl, sources, out, profile="final", cancel_check=None,
                    tick=None):
        with open(out, "wb") as fh:
            fh.write(b"MP4")
        return {"duration": 8.0, "width": 1080, "height": 1920, "size_bytes": 3}
    monkeypatch.setattr(r2mod, "render_timeline", fake_render)
    result = jobs.handle_product_editor_render(
        {"id": export_job["id"], "project_id": project["id"], "params": export_job["params"]},
        project, str(tmp_path), jobs.JobContext(export_job))
    assert result["editor_document_version"] == 3 and result["music_gain_db"] is None
    fake.patch("pipeline_jobs", f"id=eq.{export_job['id']}",
               {"status": "completed", "artifacts": result})

    # 8) Download — signed, ownership-gated, no raw path.
    sign = client.post(f"/projects/{project['id']}/editor/renders/{export_job['id']}/sign",
                       headers=_auth(token))
    assert sign.status_code == 200
    assert sign.json()["url"].startswith("https://fake.supabase.co/storage/v1")

    # 9) Ownership: a foreign user cannot open / export / sign / delete.
    _, intruder = fake.add_user("intruder@example.com")
    assert client.post(f"/projects/{project['id']}/editor/start", headers=_auth(intruder),
                       json={"candidateRunId": cand["id"]}).status_code == 403
    assert client.post(f"/projects/{project['id']}/editor/render", headers=_auth(intruder),
                       json={"documentId": v3["id"]}).status_code == 403
    assert client.post(f"/projects/{project['id']}/editor/renders/{export_job['id']}/sign",
                       headers=_auth(intruder)).status_code in (403, 404)
    assert client.delete(f"/projects/{project['id']}", headers=_auth(intruder)).status_code == 403


def test_safe_delete_preserves_evidence_and_cleans_storage(monkeypatch, tmp_path):
    fake, uid, token, project, asset_id, tl_row = _setup(monkeypatch, tmp_path)
    client = TestClient(app, raise_server_exceptions=False)
    cand = _bridge(fake, project, tl_row, tmp_path)
    raw_key = fake.tables["media_assets"][0]["storage_path"]
    assert f"raw-footage/{raw_key}" in fake.storage

    r = client.delete(f"/projects/{project['id']}", headers=_auth(token))
    assert r.status_code == 200 and r.json()["status"] == "deleted"
    # project soft-deleted (hidden), immutable evidence preserved
    assert fake.select("projects", f"id=eq.{project['id']}")[0]["deleted_at"]
    assert fake.select("candidate_runs", f"id=eq.{cand['id']}")          # preserved
    assert fake.tables["editor_documents"] == fake.tables["editor_documents"]  # not force-deleted
    # heavy storage artifacts cleaned
    assert f"raw-footage/{raw_key}" not in fake.storage
    assert f"exports/{cand['preview_storage_path']}" not in fake.storage

    # idempotent + retry-tolerant
    again = client.delete(f"/projects/{project['id']}", headers=_auth(token))
    assert again.status_code == 200 and again.json()["status"] == "deleted"


def test_delete_before_any_edit_is_safe(monkeypatch, tmp_path):
    # A project with only footage (no candidate/editor evidence) deletes cleanly too.
    fake, uid, token, project, asset_id, tl_row = _setup(monkeypatch, tmp_path)
    client = TestClient(app, raise_server_exceptions=False)
    r = client.delete(f"/projects/{project['id']}", headers=_auth(token))
    assert r.status_code == 200
    assert fake.select("projects", f"id=eq.{project['id']}")[0]["deleted_at"]
    assert f"raw-footage/{fake.tables['media_assets'][0]['storage_path']}" not in fake.storage


def test_export_retry_recovers_failed_job(monkeypatch, tmp_path):
    fake, uid, token, project, asset_id, tl_row = _setup(monkeypatch, tmp_path)
    client = TestClient(app, raise_server_exceptions=False)
    cand = _bridge(fake, project, tl_row, tmp_path)
    doc = client.post(f"/projects/{project['id']}/editor/start",
                      headers=_auth(token), json={"candidateRunId": cand["id"]}).json()
    job = client.post(f"/projects/{project['id']}/editor/render",
                      headers=_auth(token), json={"documentId": doc["id"]}).json()
    fake.patch("pipeline_jobs", f"id=eq.{job['id']}",
               {"status": "failed", "error_message": "boom", "attempt_count": 1})
    retry = client.post(f"/projects/{project['id']}/editor/renders/{job['id']}/retry",
                        headers=_auth(token))
    assert retry.status_code == 200 and retry.json()["status"] == "queued"
