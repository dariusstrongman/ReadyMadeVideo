"""Slice 3 — backend contracts the rewired customer frontend depends on:
candidate preview signing (ownership-protected), workspace candidate listing,
candidate -> /editor/start -> editor_document, rehydrate, and version-conflict shape.
Uses the Slice-2 bridge to produce a real bridged candidate + preview.
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


def _setup(monkeypatch, tmp_path, with_candidate=True):
    from app import main
    main._rate.clear()
    fake = FakeSupabase()
    install(monkeypatch, fake)
    uid, token = fake.add_user("owner@example.com")
    project = fake.add_project(uid, status="draft_ready")
    asset_id = str(uuid4())
    key = f"users/{uid}/projects/{project['id']}/raw/clip.mp4"
    fake.insert("media_assets", {"id": asset_id, "project_id": project["id"],
                                 "user_id": uid, "filename": "clip.mp4",
                                 "storage_path": key, "duration_seconds": 8.0})
    fake.storage[f"raw-footage/{key}"] = b"VIDEO"
    cand = None
    if with_candidate:
        tl = {"version": 1, "width": 1080, "height": 1920, "fps": 30, "duration": 8,
              "tracks": [{"id": "v", "type": "video", "clips": [
                  {"id": "clip-a", "assetId": asset_id, "sourceStart": 0, "sourceEnd": 8,
                   "timelineStart": 0, "timelineEnd": 8, "speed": 1, "volume": 1}]}]}
        tl_row = fake.insert("timelines", {"project_id": project["id"], "user_id": uid,
                                           "version": 1, "timeline_json": tl,
                                           "lineage": "autonomous_revised"}).json()[0]
        preview = tmp_path / "preview.mp4"
        preview.write_bytes(b"PREVIEW")
        cand = autoedit_bridge.bridge_from_autoedit(
            project, tl_row, str(preview), insert=jobs._insert,
            db_select=supa.db_select, upload_export=jobs._upload_export, now=jobs._now)
    return fake, uid, token, project, asset_id, cand


# ---------------- candidate preview signing (ownership-protected) ----------------
def test_preview_url_owner_ok(monkeypatch, tmp_path):
    fake, _, token, project, _, cand = _setup(monkeypatch, tmp_path)
    client = TestClient(app, raise_server_exceptions=False)
    r = client.post(f"/projects/{project['id']}/candidates/{cand['id']}/preview-url",
                    headers=_auth(token), json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["url"].startswith("https://fake.supabase.co/storage/v1")
    assert body["expiresIn"] == 3600
    assert "service" not in body["url"].lower()   # no service-role material leaked


def test_preview_url_foreign_rejected(monkeypatch, tmp_path):
    fake, _, _, project, _, cand = _setup(monkeypatch, tmp_path)
    _, intruder = fake.add_user("intruder@example.com")
    client = TestClient(app, raise_server_exceptions=False)
    r = client.post(f"/projects/{project['id']}/candidates/{cand['id']}/preview-url",
                    headers=_auth(intruder), json={})
    assert r.status_code == 403   # project ownership fails first


def test_preview_url_unavailable_when_missing(monkeypatch, tmp_path):
    fake, _, token, project, _, cand = _setup(monkeypatch, tmp_path)
    fake.patch("candidate_runs", f"id=eq.{cand['id']}", {"preview_storage_path": ""})
    client = TestClient(app, raise_server_exceptions=False)
    r = client.post(f"/projects/{project['id']}/candidates/{cand['id']}/preview-url",
                    headers=_auth(token), json={})
    assert r.status_code == 409


# ---------------- workspace candidate listing ----------------
def test_workspace_lists_bridged_candidate(monkeypatch, tmp_path):
    fake, _, token, project, _, cand = _setup(monkeypatch, tmp_path)
    client = TestClient(app, raise_server_exceptions=False)
    r = client.get(f"/projects/{project['id']}/workspace", headers=_auth(token))
    assert r.status_code == 200
    ids = [c["id"] for c in r.json()["candidates"]]
    assert cand["id"] in ids
    kinds = {c["generation_kind"] for c in r.json()["candidates"]}
    assert kinds == {"bridged"}   # same contract shape as initial/revised


def test_workspace_empty_when_no_candidates(monkeypatch, tmp_path):
    fake, _, token, project, _, _ = _setup(monkeypatch, tmp_path, with_candidate=False)
    client = TestClient(app, raise_server_exceptions=False)
    r = client.get(f"/projects/{project['id']}/workspace", headers=_auth(token))
    assert r.status_code == 200 and r.json()["candidates"] == []


# ---------------- candidate -> editor document -> rehydrate ----------------
def test_editor_start_then_get_and_conflict(monkeypatch, tmp_path):
    fake, _, token, project, _, cand = _setup(monkeypatch, tmp_path)
    client = TestClient(app, raise_server_exceptions=False)

    start = client.post(f"/projects/{project['id']}/editor/start",
                        headers=_auth(token), json={"candidateRunId": cand["id"]})
    assert start.status_code == 200, start.text
    doc = start.json()
    assert doc["candidate_run_id"] == cand["id"]      # opens the correct candidate
    assert doc["version"] == 1

    # rehydrate (refresh)
    got = client.get(f"/projects/{project['id']}/editor/{doc['id']}", headers=_auth(token))
    assert got.status_code == 200
    assert got.json()["version"] == 1 and got.json()["document"]["tracks"]

    # start is idempotent — same candidate returns the same document
    again = client.post(f"/projects/{project['id']}/editor/start",
                        headers=_auth(token), json={"candidateRunId": cand["id"]})
    assert again.json()["id"] == doc["id"]

    # version-conflict shape from the operations endpoint
    picture = next(t for t in doc["document"]["tracks"] if t["type"] == "picture")["items"]
    conflict = client.post(
        f"/projects/{project['id']}/editor/{doc['id']}/operations", headers=_auth(token),
        json={"expectedVersion": 99, "operations": [{
            "type": "reorder_clip", "actor": "user", "targetId": picture[0]["id"],
            "baseVersion": 99, "toIndex": 0}]})
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["latestVersion"] == 1
