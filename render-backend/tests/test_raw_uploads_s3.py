"""S3 multipart raw-footage upload flow: auth, ownership, validation, cleanup,
provenance, and the guarantee that no customer video body flows through Railway."""
import inspect
import json
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
    etag = s3.put_part(session["upload_id"], 1, data)   # real md5 ETag
    complete = client.post(f"/projects/{project['id']}/raw-uploads/{sid}/complete",
                           headers=_auth(token),
                           json={"parts": [{"partNumber": 1, "etag": etag}]})
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
                                 "storage_bucket": "test-bucket", "storage_key": key})
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


# ---------------- worker ownership validation (review blocker #1) ----------------
def _s3_asset(uid, pid, key, bucket="test-bucket"):
    return {"id": "asset-x", "user_id": uid, "project_id": pid, "filename": "clip.mp4",
            "storage_provider": "s3", "storage_bucket": bucket,
            "storage_key": key, "storage_path": key}


def test_worker_rejects_forged_cross_user_key(env, tmp_path):
    from app import media_store
    fake, s3 = env
    attacker, _, project = _owner(fake)  # project owned by attacker
    victim_key = "users/VICTIM/projects/VICTIM/raw-footage/a/private.mp4"
    s3.objects[victim_key] = {"body": b"secret", "content_type": "video/mp4",
                              "etag": "e", "size": 6}
    forged = _s3_asset(attacker, project["id"], victim_key)
    with pytest.raises(media_store.MediaOwnershipError):
        media_store.download_media_asset(forged, project, str(tmp_path / "x.mp4"))


def test_worker_rejects_wrong_bucket(env, tmp_path):
    from app import media_store
    fake, s3 = env
    uid, _, project = _owner(fake)
    key = f"users/{uid}/projects/{project['id']}/raw-footage/a/clip.mp4"
    asset = _s3_asset(uid, project["id"], key, bucket="someone-elses-bucket")
    with pytest.raises(media_store.MediaOwnershipError):
        media_store.download_media_asset(asset, project, str(tmp_path / "x.mp4"))


def test_worker_rejects_cross_project_asset(env, tmp_path):
    from app import media_store
    fake, s3 = env
    uid, _, project = _owner(fake)
    asset = _s3_asset(uid, "OTHER-PROJECT",
                      "users/x/projects/OTHER-PROJECT/raw-footage/a/clip.mp4")
    with pytest.raises(media_store.MediaOwnershipError):
        media_store.download_media_asset(asset, project, str(tmp_path / "x.mp4"))


def test_worker_downloads_legacy_supabase_asset(env, tmp_path):
    from app import media_store
    fake, s3 = env
    uid, _, project = _owner(fake)
    path = f"users/{uid}/projects/{project['id']}/raw/asset/clip.mp4"
    fake.storage[f"raw-footage/{path}"] = b"LEGACYBYTES"
    asset = {"id": "a", "user_id": uid, "project_id": project["id"],
             "filename": "clip.mp4", "storage_provider": "supabase", "storage_path": path}
    dest = str(tmp_path / "out.mp4")
    media_store.download_media_asset(asset, project, dest)
    with open(dest, "rb") as fh:
        assert fh.read() == b"LEGACYBYTES"


# ---------------- idempotency + races (review blocker #4) ----------------
def test_complete_is_idempotent(env, client):
    fake, s3 = env
    _, token, project = _owner(fake)
    sid, _ = _run_upload(fake, s3, client, token, project)  # already completes once
    again = client.post(f"/projects/{project['id']}/raw-uploads/{sid}/complete",
                        headers=_auth(token), json={"parts": [{"partNumber": 1, "etag": "x"}]})
    assert again.status_code == 200 and again.json()["status"] == "completed"


def test_finalize_is_idempotent(env, client):
    fake, s3 = env
    _, token, project = _owner(fake)
    sid, _ = _run_upload(fake, s3, client, token, project)
    first = client.post(f"/projects/{project['id']}/raw-uploads/{sid}/finalize", headers=_auth(token))
    second = client.post(f"/projects/{project['id']}/raw-uploads/{sid}/finalize", headers=_auth(token))
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert len(fake.tables["media_assets"]) == 1   # not duplicated


def test_abort_refused_during_finalizing(env, client):
    fake, s3 = env
    _, token, project = _owner(fake)
    sid, _ = _run_upload(fake, s3, client, token, project)
    fake.patch("raw_upload_sessions", f"id=eq.{sid}", {"status": "finalizing"})
    r = client.post(f"/projects/{project['id']}/raw-uploads/{sid}/abort", headers=_auth(token))
    assert r.status_code == 409


def test_replay_cannot_downgrade_finalized_to_failed(env, client):
    fake, s3 = env
    _, token, project = _owner(fake)
    sid, session = _run_upload(fake, s3, client, token, project)
    client.post(f"/projects/{project['id']}/raw-uploads/{sid}/finalize", headers=_auth(token))
    # a late complete replay must not fail the finalized session
    client.post(f"/projects/{project['id']}/raw-uploads/{sid}/complete",
                headers=_auth(token), json={"parts": [{"partNumber": 1, "etag": "x"}]})
    assert fake.tables["raw_upload_sessions"][-1]["status"] == "finalized"


# ---------------- manifest validation (review important) ----------------
def _init(fake, s3, client, token, project, size=6 * 1024 * 1024):
    return client.post(f"/projects/{project['id']}/raw-uploads/initiate",
                       headers=_auth(token),
                       json={"filename": "clip.mp4", "contentType": "video/mp4",
                             "size": size}).json()


def test_manifest_duplicate_parts_rejected(env, client):
    fake, s3 = env
    _, token, project = _owner(fake)
    init = _init(fake, s3, client, token, project, size=20 * 1024 * 1024)  # -> 2 parts
    r = client.post(f"/projects/{project['id']}/raw-uploads/{init['sessionId']}/complete",
                    headers=_auth(token),
                    json={"parts": [{"partNumber": 1, "etag": "a"}, {"partNumber": 1, "etag": "b"}]})
    assert r.status_code == 422


def test_manifest_bad_etag_rejected(env, client):
    fake, s3 = env
    _, token, project = _owner(fake)
    init = _init(fake, s3, client, token, project)
    r = client.post(f"/projects/{project['id']}/raw-uploads/{init['sessionId']}/complete",
                    headers=_auth(token),
                    json={"parts": [{"partNumber": 1, "etag": "has spaces/and*bad"}]})
    assert r.status_code == 422


def test_completion_wrong_etag_rejected(env, client):
    fake, s3 = env
    _, token, project = _owner(fake)
    init = _init(fake, s3, client, token, project)
    session = fake.tables["raw_upload_sessions"][-1]
    s3.put_part(session["upload_id"], 1, b"x" * (6 * 1024 * 1024))  # real etag stored
    r = client.post(f"/projects/{project['id']}/raw-uploads/{init['sessionId']}/complete",
                    headers=_auth(token),
                    json={"parts": [{"partNumber": 1, "etag": "deadbeef"}]})  # valid format, wrong value
    assert r.status_code == 409
    assert fake.tables["raw_upload_sessions"][-1]["status"] == "failed"


# ---------------- expiry + orphan cleanup (review important) ----------------
def test_expired_session_rejected_on_sign(env, client):
    fake, s3 = env
    _, token, project = _owner(fake)
    init = _init(fake, s3, client, token, project)
    fake.patch("raw_upload_sessions", f"id=eq.{init['sessionId']}",
               {"expires_at": "2000-01-01T00:00:00+00:00"})
    r = client.post(f"/projects/{project['id']}/raw-uploads/{init['sessionId']}/sign-parts",
                    headers=_auth(token), json={"partNumbers": [1]})
    assert r.status_code == 409
    assert fake.tables["raw_upload_sessions"][-1]["status"] == "failed"


def test_initiate_db_failure_aborts_multipart(env, client):
    fake, s3 = env
    _, token, project = _owner(fake)
    fake.fail_tables.add("raw_upload_sessions")
    r = client.post(f"/projects/{project['id']}/raw-uploads/initiate",
                    headers=_auth(token),
                    json={"filename": "clip.mp4", "contentType": "video/mp4", "size": 6 * 1024 * 1024})
    assert r.status_code == 503
    assert not s3.multipart          # newly created multipart was aborted (no orphan)


def test_zero_byte_rejected_at_initiate(env, client):
    fake, s3 = env
    _, token, project = _owner(fake)
    r = client.post(f"/projects/{project['id']}/raw-uploads/initiate",
                    headers=_auth(token),
                    json={"filename": "clip.mp4", "contentType": "video/mp4", "size": 0})
    assert r.status_code == 422   # pydantic ge=1


# ---------------- real ffprobe media validation (skips without ffmpeg) ----------------
import shutil  # noqa: E402
import subprocess  # noqa: E402

_HAS_FF = shutil.which("ffmpeg") and shutil.which("ffprobe")


def _ff(args):
    return subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args],
                          capture_output=True, timeout=60).returncode == 0


@pytest.mark.skipif(not _HAS_FF, reason="ffmpeg/ffprobe not installed")
def test_real_media_validation(tmp_path):
    """Real ffmpeg-generated fixtures: a real video passes, audio-only fails."""
    from app import mediaprobe
    valid = str(tmp_path / "valid.mp4")
    audio = str(tmp_path / "audio.mp4")
    assert _ff(["-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=10",
                "-pix_fmt", "yuv420p", valid])
    assert _ff(["-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:a", "aac", audio])
    assert mediaprobe.probe_video(valid)["valid"] is True
    assert mediaprobe.probe_video(audio)["valid"] is False       # audio-only rejected


def test_probe_rejects_attached_pic_audio_and_zero_duration(monkeypatch):
    """Deterministic (no ffmpeg needed): the probe's strictness — attached cover
    artwork, audio-only, zero/invalid duration, and insane dimensions are rejected;
    a real non-attached video stream with positive duration is accepted."""
    from types import SimpleNamespace

    from app import mediaprobe

    def fake_run(payload, rc=0):
        def _run(*a, **k):
            return SimpleNamespace(returncode=rc, stdout=json.dumps(payload), stderr="")
        return _run

    # attached cover artwork masquerading as video
    monkeypatch.setattr(subprocess, "run", fake_run({
        "streams": [{"codec_type": "video", "width": 640, "height": 480,
                     "disposition": {"attached_pic": 1}}],
        "format": {"duration": "5.0"}}))
    assert mediaprobe.probe_video("x")["valid"] is False

    # audio-only (no video streams)
    monkeypatch.setattr(subprocess, "run", fake_run({"streams": [], "format": {"duration": "5.0"}}))
    assert mediaprobe.probe_video("x")["valid"] is False

    # zero duration
    monkeypatch.setattr(subprocess, "run", fake_run({
        "streams": [{"codec_type": "video", "width": 640, "height": 480,
                     "disposition": {"attached_pic": 0}}],
        "format": {"duration": "0"}}))
    assert mediaprobe.probe_video("x")["valid"] is False

    # insane dimensions
    monkeypatch.setattr(subprocess, "run", fake_run({
        "streams": [{"codec_type": "video", "width": 999999, "height": 480,
                     "disposition": {"attached_pic": 0}}],
        "format": {"duration": "5.0"}}))
    assert mediaprobe.probe_video("x")["valid"] is False

    # a real video stream
    monkeypatch.setattr(subprocess, "run", fake_run({
        "streams": [{"codec_type": "video", "width": 1080, "height": 1920,
                     "disposition": {"attached_pic": 0}}],
        "format": {"duration": "8.0"}}))
    result = mediaprobe.probe_video("x")
    assert result["valid"] is True and result["duration"] == 8.0
