"""FastAPI integration tests for the operator API (actual HTTP routes)."""
import os

import pytest

os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "fake-service-key")
os.environ["WORKER_ENABLED"] = "0"          # no background worker in API tests

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from tests.fake_supa import FakeSupabase, install  # noqa: E402


@pytest.fixture()
def env(monkeypatch):
    fake = FakeSupabase()
    install(monkeypatch, fake)
    # rate limiter reset between tests
    from app import main as m
    m._rate.clear()
    owner_id, owner_tok = fake.add_user("owner@example.com")
    other_id, other_tok = fake.add_user("other@example.com")
    op_id, op_tok = fake.add_user("operator@example.com", operator=True)
    project = fake.add_project(owner_id, "API Test", status="ready")
    client = TestClient(app, raise_server_exceptions=False)
    return SimpleEnv(fake=fake, client=client, project=project,
                     owner=(owner_id, owner_tok), other=(other_id, other_tok),
                     operator=(op_id, op_tok))


class SimpleEnv:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def h(self, token):
        return {"Authorization": f"Bearer {token}"}


# ---------- authn/z ----------
def test_unauthenticated_401(env):
    r = env.client.post(f"/projects/{env.project['id']}/analyze", json={})
    assert r.status_code == 401


def test_normal_user_403(env):
    r = env.client.post(f"/projects/{env.project['id']}/analyze",
                        json={"params": {}}, headers=env.h(env.owner[1]))
    assert r.status_code == 403


@pytest.mark.parametrize("path,kind", [
    ("analyze", "analysis"), ("generate-draft", "autoedit"),
    ("revise", "revision"), ("render-final", "final_render")])
def test_operator_can_enqueue_each_kind(env, path, kind):
    r = env.client.post(f"/projects/{env.project['id']}/{path}",
                        json={"params": {}}, headers=env.h(env.operator[1]))
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == kind
    # audited BEFORE the action
    audits = env.fake.tables["operator_audit"]
    assert any(a["action"] == f"enqueue_{kind}" for a in audits)


def test_unknown_project_404(env):
    r = env.client.post("/projects/00000000-0000-0000-0000-000000000000/analyze",
                        json={"params": {}}, headers=env.h(env.operator[1]))
    assert r.status_code == 404


# ---------- jobs ----------
def _mk_job(env, status="failed", attempts=1, kind="analysis"):
    from app import jobs
    j = jobs.enqueue_job(env.project["id"], env.project["user_id"], kind)
    env.fake.patch("pipeline_jobs", f"id=eq.{j['id']}",
                   {"status": status, "attempt_count": attempts})
    return env.fake.select("pipeline_jobs", f"id=eq.{j['id']}")[0]


def test_owner_reads_own_job_but_not_others(env):
    job = _mk_job(env, status="queued")
    r = env.client.get(f"/jobs/{job['id']}", headers=env.h(env.owner[1]))
    assert r.status_code == 200
    r = env.client.get(f"/jobs/{job['id']}", headers=env.h(env.other[1]))
    assert r.status_code == 403


def test_operator_retry_failed_job(env):
    job = _mk_job(env, status="failed", attempts=1)
    r = env.client.post(f"/jobs/{job['id']}/retry", headers=env.h(env.operator[1]))
    assert r.status_code == 200 and r.json()["status"] == "queued"


def test_retry_completed_rejected(env):
    job = _mk_job(env, status="completed")
    r = env.client.post(f"/jobs/{job['id']}/retry", headers=env.h(env.operator[1]))
    assert r.status_code == 409


def test_retry_after_max_attempts_rejected(env):
    job = _mk_job(env, status="failed", attempts=3)
    r = env.client.post(f"/jobs/{job['id']}/retry", headers=env.h(env.operator[1]))
    assert r.status_code == 409


def test_cancel_queued_job(env):
    job = _mk_job(env, status="queued")
    r = env.client.post(f"/jobs/{job['id']}/cancel", headers=env.h(env.operator[1]))
    assert r.status_code == 200 and r.json()["status"] == "cancelled"


def test_cancel_processing_job_two_phase(env):
    job = _mk_job(env, status="processing")
    r = env.client.post(f"/jobs/{job['id']}/cancel", headers=env.h(env.operator[1]))
    assert r.status_code == 200 and r.json()["status"] == "cancel_requested"
    row = env.fake.select("pipeline_jobs", f"id=eq.{job['id']}")[0]
    assert row["cancel_requested_by"] == env.operator[0]


def test_cancel_completed_rejected(env):
    job = _mk_job(env, status="completed")
    r = env.client.post(f"/jobs/{job['id']}/cancel", headers=env.h(env.operator[1]))
    assert r.status_code == 409


def test_cancel_already_cancelled_rejected(env):
    job = _mk_job(env, status="cancelled")
    r = env.client.post(f"/jobs/{job['id']}/cancel", headers=env.h(env.operator[1]))
    assert r.status_code == 409


# ---------- cross-project protections ----------
def test_operator_cannot_sign_foreign_path(env):
    other_project = env.fake.add_project(env.other[0], "Other")
    foreign = (f"users/{env.other[0]}/projects/{other_project['id']}"
               f"/raw/x/clip.mp4")
    r = env.client.post(f"/projects/{env.project['id']}/sign",
                        json={"bucket": "raw-footage", "path": foreign},
                        headers=env.h(env.operator[1]))
    assert r.status_code == 403


def test_timeline_ops_reject_foreign_timeline(env):
    other_project = env.fake.add_project(env.other[0], "Other")
    tl = env.fake.insert("timelines", {
        "project_id": other_project["id"], "user_id": env.other[0],
        "version": 1, "timeline_json": {"version": 1, "tracks": []}}).json()[0]
    r = env.client.post(f"/projects/{env.project['id']}/timeline-ops",
                        json={"base_timeline_id": tl["id"], "operations": []},
                        headers=env.h(env.operator[1]))
    assert r.status_code == 404


def test_segment_flag_requires_existing_segment(env):
    r = env.client.post("/segments/00000000-0000-0000-0000-000000000000/flag",
                        json={"unusable": True}, headers=env.h(env.operator[1]))
    assert r.status_code == 404


# ---------- audiovisual preproduction ----------
def _add_preproduction_segments(env):
    base = {
        "schemaVersion": 2, "assetId": "asset-a", "sourceStart": 0,
        "sourceEnd": 5, "subjects": ["athlete"], "shotType": "medium",
        "cameraAngle": "eye level", "cameraMovement": "static",
        "location": "gym", "transcript": None, "emotion": "focused",
        "focusScore": 0.8, "exposureScore": 0.8, "stabilityScore": 0.8,
        "audioScore": 0.8, "semanticRelevance": 0.8,
        "duplicateGroupId": None, "problems": [],
    }
    for index, (uses, motion, action, shot, movement) in enumerate([
        (["hook", "peak"], 0.95, "sprint start", "close", "static"),
        (["location", "broll"], 0.1, "gym establishing", "wide", "static"),
        (["early_effort"], 0.5, "running", "medium", "gimbal follow"),
        (["build"], 0.7, "sled push", "medium", "tracking"),
        (["completion", "reflection"], 0.2, "recovery", "medium", "static"),
    ]):
        sid = f"seg-{index}"
        data = {**base, "segmentId": sid, "storyUses": uses,
                "motionIntensity": motion, "action": action, "shotType": shot,
                "cameraMovement": movement, "searchText": action}
        env.fake.insert("segments", {
            "project_id": env.project["id"], "user_id": env.project["user_id"],
            "asset_id": "asset-a", "segment_key": sid, "data": data,
        })


def test_operator_creates_audited_preproduction_contract(env):
    _add_preproduction_segments(env)
    r = env.client.post(
        f"/projects/{env.project['id']}/preproduction",
        json={"purpose": "authentic training recap", "targetDurationSeconds": 24,
              "targetPlatform": "vertical"},
        headers=env.h(env.operator[1]),
    )
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["version"] == 1
    assert payload["creativeTreatment"]["orientation"] == "9:16"
    assert len(payload["storyVariants"]["variants"]) == 5
    saved = env.fake.tables["preproduction_runs"][0]
    assert saved["project_id"] == env.project["id"]
    assert any(a["action"] == "create_preproduction"
               for a in env.fake.tables["operator_audit"])
    second = env.client.post(
        f"/projects/{env.project['id']}/preproduction",
        json={"purpose": "alternate training recap", "targetDurationSeconds": 20},
        headers=env.h(env.operator[1]),
    )
    assert second.status_code == 200 and second.json()["version"] == 2
    assert [row["version"] for row in env.fake.tables["preproduction_runs"]] == [1, 2]


def test_preproduction_requires_operator_and_catalog(env):
    path = f"/projects/{env.project['id']}/preproduction"
    denied = env.client.post(path, json={}, headers=env.h(env.owner[1]))
    assert denied.status_code == 403
    missing = env.client.post(path, json={}, headers=env.h(env.operator[1]))
    assert missing.status_code == 409


def test_preproduction_rejects_out_of_scope_duration(env):
    _add_preproduction_segments(env)
    r = env.client.post(
        f"/projects/{env.project['id']}/preproduction",
        json={"targetDurationSeconds": 90}, headers=env.h(env.operator[1]),
    )
    assert r.status_code == 422


# ---------- timeline op validation ----------
def _project_timeline(env):
    tl = {"version": 1, "width": 1920, "height": 1080, "fps": 30, "duration": 6,
          "tracks": [{"id": "video-1", "type": "video", "clips": [
              {"id": "c1", "assetId": "a1", "sourceStart": 0, "sourceEnd": 3,
               "timelineStart": 0, "timelineEnd": 3, "volume": 1, "speed": 1},
              {"id": "c2", "assetId": "a1", "sourceStart": 4, "sourceEnd": 7,
               "timelineStart": 3, "timelineEnd": 6, "volume": 1, "speed": 1}]}]}
    return env.fake.insert("timelines", {
        "project_id": env.project["id"], "user_id": env.project["user_id"],
        "version": 1, "timeline_json": tl}).json()[0]


def test_invalid_operation_422(env):
    tl = _project_timeline(env)
    r = env.client.post(f"/projects/{env.project['id']}/timeline-ops",
                        json={"base_timeline_id": tl["id"],
                              "operations": [{"op": "run_ffmpeg", "cmd": "evil"}]},
                        headers=env.h(env.operator[1]))
    assert r.status_code == 422


def test_protected_range_unchanged(env):
    tl = _project_timeline(env)
    r = env.client.post(f"/projects/{env.project['id']}/timeline-ops",
                        json={"base_timeline_id": tl["id"],
                              "operations": [{"op": "delete_clip",
                                              "clipId": "c2"}],
                              "protected_ranges": [[3, 6]]},
                        headers=env.h(env.operator[1]))
    assert r.status_code == 422
    assert "protected" in r.json()["detail"]
    # base timeline untouched (a new version was never created)
    versions = env.fake.select("timelines",
                               f"project_id=eq.{env.project['id']}")
    assert len(versions) == 1


def test_valid_timeline_op_creates_new_version(env):
    tl = _project_timeline(env)
    r = env.client.post(f"/projects/{env.project['id']}/timeline-ops",
                        json={"base_timeline_id": tl["id"],
                              "operations": [{"op": "trim_clip", "clipId": "c1",
                                              "sourceEnd": 2.0}]},
                        headers=env.h(env.operator[1]))
    assert r.status_code == 200
    assert r.json()["version"] == 2


# ---------- audit reliability ----------
def test_action_aborts_when_audit_store_fails(env):
    env.fake.fail_tables.add("operator_audit")
    r = env.client.post(f"/projects/{env.project['id']}/analyze",
                        json={"params": {}}, headers=env.h(env.operator[1]))
    assert r.status_code == 503
    assert "audit" in r.json()["detail"]
    # the action did NOT happen
    assert len(env.fake.tables["pipeline_jobs"]) == 0


def test_rate_limit_on_enqueue(env, monkeypatch):
    from app import main as m
    monkeypatch.setattr(m, "RATE_LIMIT_PER_MIN", 2)
    for i in range(2):
        env.fake.tables["pipeline_jobs"].clear()
        r = env.client.post(f"/projects/{env.project['id']}/analyze",
                            json={"params": {}}, headers=env.h(env.operator[1]))
        assert r.status_code == 200
    r = env.client.post(f"/projects/{env.project['id']}/analyze",
                        json={"params": {}}, headers=env.h(env.operator[1]))
    assert r.status_code == 429


def test_body_size_limit(env):
    r = env.client.post(f"/projects/{env.project['id']}/analyze",
                        content=b"x" * 10,
                        headers={**env.h(env.operator[1]),
                                 "Content-Length": str(50 * 1024 * 1024),
                                 "Content-Type": "application/json"})
    assert r.status_code == 413


def test_unhandled_error_is_sanitized(env, monkeypatch):
    from app import main as m

    def boom(*a, **k):
        raise RuntimeError(r"C:\secret\path\creds.txt exploded")
    monkeypatch.setattr(m.supa, "db_select", boom)
    r = env.client.get("/jobs/any-id", headers=env.h(env.owner[1]))
    assert r.status_code == 500
    assert r.json() == {"detail": "internal error"}
    assert "secret" not in r.text


# ---------- additional route coverage ----------
def test_readyz_ok(env):
    r = env.client.get("/readyz")
    assert r.status_code == 200 and r.json()["ready"] is True


def test_sign_success_for_project_object(env):
    path = (f"users/{env.project['user_id']}/projects/{env.project['id']}"
            f"/raw/x/clip.mp4")
    env.fake.storage[f"raw-footage/{path}"] = b"data"
    r = env.client.post(f"/projects/{env.project['id']}/sign",
                        json={"bucket": "raw-footage", "path": path},
                        headers=env.h(env.operator[1]))
    assert r.status_code == 200 and "url" in r.json()


def test_sign_unknown_bucket_422(env):
    r = env.client.post(f"/projects/{env.project['id']}/sign",
                        json={"bucket": "weird", "path": "x"},
                        headers=env.h(env.operator[1]))
    assert r.status_code == 422


def test_flag_segment_success_roundtrip(env):
    seg = env.fake.insert("segments", {
        "segment_key": "s1", "asset_id": "a1", "project_id": env.project["id"],
        "user_id": env.project["user_id"], "source_start": 0, "source_end": 2,
        "data": {"problems": []}, "search_text": "x"}).json()[0]
    r = env.client.post(f"/segments/{seg['id']}/flag",
                        json={"unusable": True, "reason": "blurry"},
                        headers=env.h(env.operator[1]))
    assert r.status_code == 200
    assert "operator_unusable" in r.json()["problems"]
    r = env.client.post(f"/segments/{seg['id']}/flag",
                        json={"unusable": False},
                        headers=env.h(env.operator[1]))
    assert "operator_unusable" not in r.json()["problems"]


def test_evaluation_recording_and_missing_row(env):
    r = env.client.post(f"/projects/{env.project['id']}/evaluation",
                        json={"fields": {"first_draft_rating": 7}},
                        headers=env.h(env.operator[1]))
    assert r.status_code == 404          # no evaluation row yet
    env.fake.insert("draft_evaluations", {
        "project_id": env.project["id"], "user_id": env.project["user_id"]})
    r = env.client.post(f"/projects/{env.project['id']}/evaluation",
                        json={"fields": {"first_draft_rating": 7,
                                         "human_correction_minutes": 12,
                                         "bogus_field": 1}},
                        headers=env.h(env.operator[1]))
    assert r.status_code == 200
    assert set(r.json()["updated"]) == {"first_draft_rating",
                                        "human_correction_minutes"}


def test_evaluation_rejects_all_unknown_fields(env):
    r = env.client.post(f"/projects/{env.project['id']}/evaluation",
                        json={"fields": {"hack": 1}},
                        headers=env.h(env.operator[1]))
    assert r.status_code == 422


def test_coverage_endpoint(env):
    env.fake.insert("segments", {
        "segment_key": "s1", "asset_id": "a1", "project_id": env.project["id"],
        "user_id": env.project["user_id"], "source_start": 0, "source_end": 5,
        "data": {"schemaVersion": 1, "segmentId": "s1", "assetId": "a1",
                 "sourceStart": 0, "sourceEnd": 5, "storyUses": ["peak"],
                 "motionIntensity": 0.9, "shotType": "wide"},
        "search_text": "x"})
    r = env.client.get(f"/projects/{env.project['id']}/coverage",
                       headers=env.h(env.operator[1]))
    assert r.status_code == 200
    assert r.json()["segmentCount"] == 1


def test_job_get_404(env):
    r = env.client.get("/jobs/00000000-0000-0000-0000-000000000000",
                       headers=env.h(env.operator[1]))
    assert r.status_code == 404
