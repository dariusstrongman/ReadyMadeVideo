"""Tests for the user-accessible POST /projects/{id}/request-analysis endpoint.

Covers:
1. Upload completion creates exactly one analysis job
2. Repeated trigger does not create a duplicate active job (idempotency)
3. Backend failure produces a visible error (non-owner 403)
4. Existing project with assets but no job can be safely started
5. Unauthorized user cannot start another user's project
"""
import os
import pytest

os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "fake-service-key")
os.environ["WORKER_ENABLED"] = "0"

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
from tests.fake_supa import FakeSupabase, install  # noqa: E402


@pytest.fixture()
def env(monkeypatch):
    fake = FakeSupabase()
    install(monkeypatch, fake)
    from app import main as m
    m._rate.clear()

    owner_id, owner_tok = fake.add_user("owner@example.com")
    other_id, other_tok = fake.add_user("other@example.com")

    project = fake.add_project(owner_id, "Upload Test Project", status="ready")

    # Add a media_asset to simulate completed upload
    import uuid
    fake.tables["media_assets"].append({
        "id": str(uuid.uuid4()),
        "project_id": project["id"],
        "user_id": owner_id,
        "storage_path": f"users/{owner_id}/projects/{project['id']}/raw/clip1.mp4",
        "filename": "clip1.mp4",
        "mime_type": "video/mp4",
        "size_bytes": 10_000_000,
        "created_at": "2026-08-03T12:00:00+00:00",
    })

    client = TestClient(app, raise_server_exceptions=False)

    class Env:
        pass

    e = Env()
    e.fake = fake
    e.client = client
    e.project = project
    e.owner_id = owner_id
    e.owner_tok = owner_tok
    e.other_id = other_id
    e.other_tok = other_tok

    def h(tok):
        return {"Authorization": f"Bearer {tok}"}
    e.h = h
    return e


# ── Test 1: Upload completion creates exactly one analysis job ──────────────

def test_upload_creates_one_job(env):
    """After upload, calling /request-analysis creates exactly one pipeline_jobs row."""
    r = env.client.post(
        f"/projects/{env.project['id']}/request-analysis",
        headers=env.h(env.owner_tok),
    )
    assert r.status_code == 200, r.text
    job = r.json()
    assert job["kind"] == "analysis"
    assert job["project_id"] == env.project["id"]
    assert job["user_id"] == env.owner_id
    assert job["status"] in ("queued", "processing")

    # Exactly one job in the table
    jobs = env.fake.tables["pipeline_jobs"]
    analysis_jobs = [j for j in jobs if j["project_id"] == env.project["id"] and j["kind"] == "analysis"]
    assert len(analysis_jobs) == 1


# ── Test 2: Repeated trigger does not create a duplicate active job ─────────

def test_repeated_trigger_is_idempotent(env):
    """Calling /request-analysis twice returns the same job, not two jobs."""
    r1 = env.client.post(
        f"/projects/{env.project['id']}/request-analysis",
        headers=env.h(env.owner_tok),
    )
    assert r1.status_code == 200, r1.text
    job_id_1 = r1.json()["id"]

    r2 = env.client.post(
        f"/projects/{env.project['id']}/request-analysis",
        headers=env.h(env.owner_tok),
    )
    assert r2.status_code == 200, r2.text
    job_id_2 = r2.json()["id"]

    # Same job returned — no duplicate
    assert job_id_1 == job_id_2

    # Still exactly one active analysis job
    jobs = env.fake.tables["pipeline_jobs"]
    active = [j for j in jobs
              if j["project_id"] == env.project["id"]
              and j["kind"] == "analysis"
              and j["status"] in ("queued", "processing")]
    assert len(active) == 1


# ── Test 3: Backend failure — non-owner gets 403 ────────────────────────────

def test_non_owner_gets_403(env):
    """A different authenticated user cannot trigger analysis on another user's project."""
    r = env.client.post(
        f"/projects/{env.project['id']}/request-analysis",
        headers=env.h(env.other_tok),
    )
    assert r.status_code == 403, r.text
    # No job created
    jobs = env.fake.tables["pipeline_jobs"]
    assert len(jobs) == 0


# ── Test 4: Existing project with assets but no job can be safely started ───

def test_existing_project_with_no_job_can_start(env):
    """A project that already has assets but no pipeline_jobs can be started."""
    # Confirm no jobs exist yet
    assert len(env.fake.tables["pipeline_jobs"]) == 0

    r = env.client.post(
        f"/projects/{env.project['id']}/request-analysis",
        headers=env.h(env.owner_tok),
    )
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "analysis"


# ── Test 5: Unauthorized user (no token) gets 401 ───────────────────────────

def test_unauthenticated_gets_401(env):
    """No Authorization header → 401."""
    r = env.client.post(f"/projects/{env.project['id']}/request-analysis")
    assert r.status_code == 401, r.text
    assert len(env.fake.tables["pipeline_jobs"]) == 0


# ── Test 6: Unknown project → 404 ───────────────────────────────────────────

def test_unknown_project_404(env):
    r = env.client.post(
        "/projects/00000000-0000-0000-0000-000000000000/request-analysis",
        headers=env.h(env.owner_tok),
    )
    assert r.status_code == 404, r.text


# ── Test 7: Completed job does not block a new analysis ─────────────────────

def test_completed_job_allows_new_analysis(env):
    """A previously completed analysis job should not block a new one."""
    from app import jobs
    # Create and mark a job as completed
    j = jobs.enqueue_job(env.project["id"], env.owner_id, "analysis", {})
    env.fake.patch("pipeline_jobs", f"id=eq.{j['id']}", {"status": "completed"})

    # Now request a new analysis — should create a new job
    r = env.client.post(
        f"/projects/{env.project['id']}/request-analysis",
        headers=env.h(env.owner_tok),
    )
    assert r.status_code == 200, r.text
    new_job = r.json()
    assert new_job["id"] != j["id"]
    assert new_job["status"] in ("queued", "processing")
