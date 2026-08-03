"""S3 multipart raw-footage upload flow: auth, ownership, validation, cleanup,
provenance, and the guarantee that no customer video body flows through Railway."""
import inspect
import os

import pytest

os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "fake-service-key")
os.environ["WORKER_ENABLED"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from app import mediaprobe, raw_uploads, s3store  # noqa: E402
from app.main import app  # noqa: E402
from tests.fake_s3 import FakeS3  # noqa: E402
from tests.fake_supa import FakeSupabase, install  # noqa: E402

TWO_GB = 2 * 1024 * 1024 * 1024


@pytest.fixture
def env(monkeypatch):
    fake = FakeSupabase()
    install(monkeypatch, fake)
    s3 = FakeS3()
    monkeypatch.setattr(s3store, "_CLIENT", s3)
    monkeypatch.setenv("AWS_S3_BUCKET", "test-bucket")
    monkeypatch.setattr(mediaprobe, "probe_video",
                        lambda *a, **k: {"valid": True, "duration": 8.0,
                                         "width": 1080, "height": 1920})
    return fake, s3


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _owner(fake):
    uid, token = fake.add_user("owner@stromation.app")
    project = fake.add_project(uid, "S3 Upload")
    return uid, token, project


# ---------------- pure validation / key logic ----------------
def test_size_boundary_logic_without_huge_fixtures():
    raw_uploads.validate_size(TWO_GB - 1)          # just below — ok
    raw_uploads.validate_size(TWO_GB)              # exactly 2 GB — ok
    with pytest.raises(raw_uploads.UploadValidationError) as e:
        raw_uploads.validate_size(TWO_GB + 1)      # over — rejected
    assert e.value.code == "too_large"


def test_safe_filename_blocks_path_traversal():
    assert raw_uploads.safe_filename("../../etc/passwd") == "passwd"   # path stripped
    key = raw_uploads.object_key("u", "p", "a", "../../evil.mp4")
    assert key == "users/u/projects/p/raw-footage/a/evil.mp4"
    assert ".." not in key
    assert raw_uploads.object_key("u", "p", "a", "clip.mp4") == \
        "users/u/projects/p/raw-footage/a/clip.mp4"
    assert not raw_uploads.key_belongs_to("users/OTHER/projects/p/raw-footage/a/x.mp4", "u", "p")


# ---------------- initiate ----------------
def test_initiate_allowed(env, client):
    fake, _ = env
    _, token, project = _owner(fake)
    r = client.post(f"/projects/{project['id']}/raw-uploads/initiate",
                    headers=_auth(token),
                    json={"filename": "clip.mp4", "contentType": "video/mp4", "size": 40_000_000})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["objectKey"].startswith(f"users/{project['user_id']}/projects/{project['id']}/raw-footage/")
    assert body["objectKey"].endswith("/clip.mp4")
    assert body["partSize"] >= 5 * 1024 * 1024
    assert fake.tables["raw_upload_sessions"][0]["status"] == "initiated"


def test_initiate_foreign_project_rejected(env, client):
    fake, _ = env
    _, _, project = _owner(fake)
    _, intruder = fake.add_user("intruder@stromation.app")
    r = client.post(f"/projects/{project['id']}/raw-uploads/initiate",
                    headers=_auth(intruder),
                    json={"filename": "clip.mp4", "contentType": "video/mp4", "size": 10})
    assert r.status_code == 403


def test_initiate_oversized_rejected(env, client):
    fake, _ = env
    _, token, project = _owner(fake)
    r = client.post(f"/projects/{project['id']}/raw-uploads/initiate",
                    headers=_auth(token),
                    json={"filename": "clip.mp4", "contentType": "video/mp4", "size": TWO_GB + 1})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "too_large"


def test_initiate_bad_extension_and_mime_rejected(env, client):
    fake, _ = env
    _, token, project = _owner(fake)
    bad_ext = client.post(f"/projects/{project['id']}/raw-uploads/initiate",
                          headers=_auth(token),
                          json={"filename": "clip.avi", "contentType": "video/mp4", "size": 10})
    assert bad_ext.status_code == 422 and bad_ext.json()["detail"]["code"] == "unsupported_extension"
    bad_mime = client.post(f"/projects/{project['id']}/raw-uploads/initiate",
                           headers=_auth(token),
                           json={"filename": "clip.mp4", "contentType": "application/zip", "size": 10})
    assert bad_mime.status_code == 422 and bad_mime.json()["detail"]["code"] == "unsupported_content_type"


# ---------------- happy path: sign, complete, finalize, provenance ----------------
def _run_upload(fake, s3, client, token, project, data=b"x" * (6 * 1024 * 1024),
                content_type="video/mp4", filename="clip.mp4"):
    init = client.post(f"/projects/{project['id']}/raw-uploads/initiate",
                       headers=_auth(token),
                       json={"filename": filename, "contentType": content_type,
                             "size": len(data)}).json()
    sid = init["sessionId"]
    # sign part(s)
    sign = client.post(f"/projects/{project['id']}/raw-uploads/{sid}/sign-parts",
                       headers=_auth(token), json={"partNumbers": [1]})
    assert sign.status_code == 200
    assert sign.json()["parts"][0]["url"].startswith("https://s3.fake/")   # goes to S3, not Railway
    # simulate the browser PUTting the bytes straight to S3
    session = fake.tables["raw_upload_sessions"][-1]
    s3.put_part(session["upload_id"], 1, data)
    complete = client.post(f"/projects/{project['id']}/raw-uploads/{sid}/complete",
                           headers=_auth(token),
                           json={"parts": [{"partNumber": 1, "etag": "etag-1"}]})
    assert complete.status_code == 200, complete.text
    return sid, session


def test_full_upload_creates_validated_provenance(env, client):
    fake, s3 = env
    _, token, project = _owner(fake)
    sid, session = _run_upload(fake, s3, client, token, project)
    fin = client.post(f"/projects/{project['id']}/raw-uploads/{sid}/finalize",
                      headers=_auth(token))
    assert fin.status_code == 200, fin.text
    asset = fin.json()
    assert asset["storage_provider"] == "s3"
    assert asset["storage_bucket"] == "test-bucket"
    assert asset["storage_key"] == session["object_key"]
    assert asset["etag"] and asset["size_bytes"] == 6 * 1024 * 1024
    assert asset["duration_seconds"] == 8.0
    assert asset["validation_status"] == "validated"
    # project flipped to ready; session finalized
    assert fake.tables["projects"][0]["status"] == "ready"
    assert fake.tables["raw_upload_sessions"][-1]["status"] == "finalized"


def test_part_signing_range_enforced(env, client):
    fake, s3 = env
    _, token, project = _owner(fake)
    init = client.post(f"/projects/{project['id']}/raw-uploads/initiate",
                       headers=_auth(token),
                       json={"filename": "clip.mp4", "contentType": "video/mp4",
                             "size": 6 * 1024 * 1024}).json()
    r = client.post(f"/projects/{project['id']}/raw-uploads/{init['sessionId']}/sign-parts",
                    headers=_auth(token), json={"partNumbers": [9999]})
    assert r.status_code == 422


# ---------------- failure / cleanup paths ----------------
def test_completion_with_missing_parts_rejected_and_cleaned(env, client):
    fake, s3 = env
    _, token, project = _owner(fake)
    init = client.post(f"/projects/{project['id']}/raw-uploads/initiate",
                       headers=_auth(token),
                       json={"filename": "clip.mp4", "contentType": "video/mp4",
                             "size": 6 * 1024 * 1024}).json()
    # never uploaded part 1 -> S3 complete fails
    r = client.post(f"/projects/{project['id']}/raw-uploads/{init['sessionId']}/complete",
                    headers=_auth(token), json={"parts": [{"partNumber": 1, "etag": "nope"}]})
    assert r.status_code == 409
    assert fake.tables["raw_upload_sessions"][-1]["status"] == "failed"


def test_size_mismatch_rejected_and_object_deleted(env, client):
    fake, s3 = env
    _, token, project = _owner(fake)
    sid, session = _run_upload(fake, s3, client, token, project, data=b"x" * (6 * 1024 * 1024))
    # tamper: report a different size at HEAD
    s3.objects[session["object_key"]]["force_size"] = 7 * 1024 * 1024
    fin = client.post(f"/projects/{project['id']}/raw-uploads/{sid}/finalize", headers=_auth(token))
    assert fin.status_code == 409
    assert session["object_key"] not in s3.objects           # deleted on failure
    assert not fake.tables["media_assets"]                    # no asset created


def test_object_over_2gb_rejected_at_finalize(env, client):
    fake, s3 = env
    _, token, project = _owner(fake)
    # declared exactly 2GB (passes initiate) but object HEADs larger
    init = client.post(f"/projects/{project['id']}/raw-uploads/initiate",
                       headers=_auth(token),
                       json={"filename": "clip.mp4", "contentType": "video/mp4",
                             "size": TWO_GB}).json()
    sid = init["sessionId"]
    session = fake.tables["raw_upload_sessions"][-1]
    fake.patch("raw_upload_sessions", f"id=eq.{sid}", {"status": "completed"})
    s3.objects[session["object_key"]] = {"body": b"", "content_type": "video/mp4",
                                         "etag": "e", "size": 0, "force_size": TWO_GB + 1}
    fin = client.post(f"/projects/{project['id']}/raw-uploads/{sid}/finalize", headers=_auth(token))
    assert fin.status_code == 413
    assert not fake.tables["media_assets"]


def test_exactly_2gb_accepted_at_finalize(env, client):
    fake, s3 = env
    _, token, project = _owner(fake)
    init = client.post(f"/projects/{project['id']}/raw-uploads/initiate",
                       headers=_auth(token),
                       json={"filename": "clip.mp4", "contentType": "video/mp4",
                             "size": TWO_GB}).json()
    sid = init["sessionId"]
    session = fake.tables["raw_upload_sessions"][-1]
    fake.patch("raw_upload_sessions", f"id=eq.{sid}", {"status": "completed"})
    s3.objects[session["object_key"]] = {"body": b"", "content_type": "video/mp4",
                                         "etag": "e2", "size": 0, "force_size": TWO_GB}
    fin = client.post(f"/projects/{project['id']}/raw-uploads/{sid}/finalize", headers=_auth(token))
    assert fin.status_code == 200, fin.text
    assert fin.json()["size_bytes"] == TWO_GB


def test_mime_mismatch_at_finalize_rejected(env, client):
    fake, s3 = env
    _, token, project = _owner(fake)
    sid, session = _run_upload(fake, s3, client, token, project)
    s3.objects[session["object_key"]]["content_type"] = "application/octet-stream"
    fin = client.post(f"/projects/{project['id']}/raw-uploads/{sid}/finalize", headers=_auth(token))
    assert fin.status_code == 415
    assert session["object_key"] not in s3.objects


def test_invalid_video_rejected_and_deleted(env, client, monkeypatch):
    fake, s3 = env
    _, token, project = _owner(fake)
    sid, session = _run_upload(fake, s3, client, token, project)
    monkeypatch.setattr(mediaprobe, "probe_video",
                        lambda *a, **k: {"valid": False, "duration": None,
                                         "width": None, "height": None})
    fin = client.post(f"/projects/{project['id']}/raw-uploads/{sid}/finalize", headers=_auth(token))
    assert fin.status_code == 422
    assert session["object_key"] not in s3.objects
    assert not fake.tables["media_assets"]


def test_abort_cleans_up(env, client):
    fake, s3 = env
    _, token, project = _owner(fake)
    init = client.post(f"/projects/{project['id']}/raw-uploads/initiate",
                       headers=_auth(token),
                       json={"filename": "clip.mp4", "contentType": "video/mp4",
                             "size": 6 * 1024 * 1024}).json()
    sid = init["sessionId"]
    session = fake.tables["raw_upload_sessions"][-1]
    r = client.post(f"/projects/{project['id']}/raw-uploads/{sid}/abort", headers=_auth(token))
    assert r.status_code == 200
    assert fake.tables["raw_upload_sessions"][-1]["status"] == "aborted"
    assert session["upload_id"] not in s3.multipart


def test_foreign_user_cannot_touch_session(env, client):
    fake, s3 = env
    _, token, project = _owner(fake)
    init = client.post(f"/projects/{project['id']}/raw-uploads/initiate",
                       headers=_auth(token),
                       json={"filename": "clip.mp4", "contentType": "video/mp4",
                             "size": 6 * 1024 * 1024}).json()
    _, intruder = fake.add_user("intruder@stromation.app")
    r = client.post(f"/projects/{project['id']}/raw-uploads/{init['sessionId']}/sign-parts",
                    headers=_auth(intruder), json={"partNumbers": [1]})
    assert r.status_code in (403, 404)   # ownership fails before session lookup


# ---------------- worker S3 download + signed export ----------------
def test_worker_downloads_owned_source_from_s3(env, tmp_path):
    fake, s3 = env
    from app import jobs
    uid, _, project = _owner(fake)
    key = f"users/{uid}/projects/{project['id']}/raw-footage/asset-1/clip.mp4"
    s3.objects[key] = {"body": b"VIDEOBYTES", "content_type": "video/mp4",
                       "etag": "e", "size": 10}
    fake.insert("media_assets", {"id": "asset-1", "project_id": project["id"],
                                 "user_id": uid, "filename": "clip.mp4",
                                 "storage_path": key, "storage_provider": "s3",
                                 "storage_key": key})
    sources, assets = jobs._download_sources(project, str(tmp_path))
    assert len(assets) == 1
    with open(sources["asset-1"], "rb") as fh:
        assert fh.read() == b"VIDEOBYTES"


def test_signed_export_download_uses_s3_when_configured(env, client):
    from uuid import uuid4
    fake, s3 = env
    uid, token, project = _owner(fake)
    job_id = str(uuid4())
    path = f"users/{uid}/projects/{project['id']}/renders/{job_id}.mp4"
    fake.insert("pipeline_jobs", {"id": job_id, "project_id": project["id"],
                                  "user_id": uid, "kind": "final_render",
                                  "status": "completed",
                                  "artifacts": {"output": path, "export_provider": "s3"}})
    r = client.post(f"/projects/{project['id']}/editor/renders/{job_id}/sign", headers=_auth(token))
    assert r.status_code == 200, r.text
    assert r.json()["url"].startswith("https://s3.fake/")


def test_s3_connectivity_probe(env, client):
    r = client.get("/readyz/s3")
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is True and body["reachable"] is True
    assert "error" not in body


# ---------------- no customer video body through Railway ----------------
def test_no_raw_upload_route_accepts_a_file_body():
    from fastapi import Request, UploadFile
    raw_routes = [r for r in app.routes
                  if getattr(r, "path", "").startswith("/projects/{project_id}/raw-uploads")]
    assert raw_routes, "raw-upload routes must exist"
    for route in raw_routes:
        for param in inspect.signature(route.endpoint).parameters.values():
            assert param.annotation not in (UploadFile, Request), \
                f"{route.path} must not receive a file/body — video goes to S3, not Railway"
