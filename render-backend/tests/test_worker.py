"""Direct tests for the persistent job worker (jobs.py) — real state
transitions against the in-memory Supabase-semantics fake."""
import os

import pytest

os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "fake-service-key")

from app import jobs  # noqa: E402
from tests.fake_supa import FakeSupabase, install  # noqa: E402


@pytest.fixture()
def fake(monkeypatch):
    f = FakeSupabase()
    install(monkeypatch, f)
    return f


@pytest.fixture()
def project(fake):
    uid, _ = fake.add_user("owner@example.com")
    return fake.add_project(uid, "Worker Test", status="ready")


def job_row(fake, job_id):
    return fake.select("pipeline_jobs", f"id=eq.{job_id}")[0]


# ---------- enqueue ----------
@pytest.mark.parametrize("kind", ["analysis", "autoedit", "revision",
                                  "final_render"])
def test_enqueue_each_kind(fake, project, kind):
    job = jobs.enqueue_job(project["id"], project["user_id"], kind)
    assert job["status"] == "queued" and job["kind"] == kind


def test_enqueue_duplicate_active_rejected_returns_existing(fake, project):
    j1 = jobs.enqueue_job(project["id"], project["user_id"], "analysis")
    j2 = jobs.enqueue_job(project["id"], project["user_id"], "analysis")
    assert j1["id"] == j2["id"], "duplicate active job must be idempotent"
    assert len(fake.tables["pipeline_jobs"]) == 1


def test_enqueue_concurrency_cap(fake, project, monkeypatch):
    monkeypatch.setattr(jobs, "MAX_ACTIVE_JOBS_PER_USER", 2)
    p2 = fake.add_project(project["user_id"], "P2")
    p3 = fake.add_project(project["user_id"], "P3")
    jobs.enqueue_job(project["id"], project["user_id"], "analysis")
    jobs.enqueue_job(p2["id"], project["user_id"], "analysis")
    with pytest.raises(jobs.ConcurrencyLimit):
        jobs.enqueue_job(p3["id"], project["user_id"], "analysis")


# ---------- claiming ----------
def test_claim_oldest_queued(fake, project):
    j1 = jobs.enqueue_job(project["id"], project["user_id"], "analysis")
    p2 = fake.add_project(project["user_id"], "P2")
    jobs.enqueue_job(p2["id"], project["user_id"], "autoedit")
    claimed = jobs._claim_next()
    assert claimed["id"] == j1["id"]
    assert claimed["status"] == "processing"
    assert claimed["attempt_count"] == 1          # incremented on claim


def test_two_workers_cannot_claim_same_job(fake, project):
    jobs.enqueue_job(project["id"], project["user_id"], "analysis")
    first = jobs._claim_next()
    second = jobs._claim_next()                   # nothing queued anymore
    assert first is not None and second is None


def test_heartbeat_updates(fake, project):
    j = jobs.enqueue_job(project["id"], project["user_id"], "analysis")
    jobs.update_job(j["id"], {"progress": 50})
    row = job_row(fake, j["id"])
    assert row["progress"] == 50 and row["heartbeat_at"]


# ---------- stale recovery ----------
def _make_stale(fake, job_id):
    fake.patch("pipeline_jobs", f"id=eq.{job_id}",
               {"status": "processing",
                "heartbeat_at": "2020-01-01T00:00:00+00:00"})


def test_stale_requeued_when_attempts_remain(fake, project):
    j = jobs.enqueue_job(project["id"], project["user_id"], "analysis")
    fake.patch("pipeline_jobs", f"id=eq.{j['id']}", {"attempt_count": 1})
    _make_stale(fake, j["id"])
    assert jobs.recover_stale() == 1
    assert job_row(fake, j["id"])["status"] == "queued"


def test_stale_failed_after_max_attempts(fake, project):
    j = jobs.enqueue_job(project["id"], project["user_id"], "analysis")
    fake.patch("pipeline_jobs", f"id=eq.{j['id']}", {"attempt_count": 3})
    _make_stale(fake, j["id"])
    jobs.recover_stale()
    row = job_row(fake, j["id"])
    assert row["status"] == "failed"
    assert "max attempts" in row["error_message"]


# ---------- cancellation ----------
def test_cancel_queued_job_immediate(fake, project):
    j = jobs.enqueue_job(project["id"], project["user_id"], "analysis")
    out = jobs.request_cancel(j, requested_by=project["user_id"])
    assert out["status"] == "cancelled"
    row = job_row(fake, j["id"])
    assert row["status"] == "cancelled"
    assert row["cancel_requested_by"] == project["user_id"]
    assert row["cancel_requested_at"]


def test_cancel_processing_sets_cancel_requested(fake, project):
    j = jobs.enqueue_job(project["id"], project["user_id"], "analysis")
    claimed = jobs._claim_next()
    out = jobs.request_cancel(claimed, requested_by="op-1")
    assert out["status"] == "cancel_requested"
    assert job_row(fake, j["id"])["status"] == "cancel_requested"


def test_cancel_completed_job_rejected(fake, project):
    j = jobs.enqueue_job(project["id"], project["user_id"], "analysis")
    fake.patch("pipeline_jobs", f"id=eq.{j['id']}", {"status": "completed"})
    with pytest.raises(ValueError):
        jobs.request_cancel(job_row(fake, j["id"]), requested_by="op-1")


def test_worker_honors_cancel_between_stages(fake, project, monkeypatch):
    """Handler hits a checkpoint after cancel_requested -> job cancelled,
    project status restored, nothing marked completed."""
    j = jobs.enqueue_job(project["id"], project["user_id"], "analysis")
    claimed = jobs._claim_next()

    def handler(job, proj, tmp, ctx):
        # simulate operator cancelling mid-run, then the next checkpoint
        jobs.request_cancel(job_row(fake, job["id"]), requested_by="op-9")
        ctx.checkpoint("between_assets")
        raise AssertionError("checkpoint must have raised")

    monkeypatch.setitem(jobs.HANDLERS, "analysis", handler)
    jobs._run_job(claimed)
    row = job_row(fake, j["id"])
    assert row["status"] == "cancelled"
    assert "checkpoint" in row["error_message"]
    proj = fake.select("projects", f"id=eq.{project['id']}")[0]
    assert proj["status"] == "ready"              # non-completed state


# ---------- run outcomes ----------
def test_success_preserves_artifacts_and_telemetry_status(fake, project,
                                                          monkeypatch):
    j = jobs.enqueue_job(project["id"], project["user_id"], "autoedit")
    claimed = jobs._claim_next()

    def handler(job, proj, tmp, ctx):
        ctx.rec("stage_one", 1.0, units={"gemini_requests": 1})
        return {"result": "ok"}

    monkeypatch.setitem(jobs.HANDLERS, "autoedit", handler)
    jobs._run_job(claimed)
    row = job_row(fake, j["id"])
    assert row["status"] == "completed"
    assert row["artifacts"]["result"] == "ok"
    ts = row["artifacts"]["telemetry_status"]
    assert ts["expected_stages"] == 1 and ts["recorded_stages"] == 1
    assert ts["complete"] is True
    assert ts["note"] == "all costs are ESTIMATES"
    assert len(fake.tables["stage_metrics"]) == 1


def test_failure_recorded_without_crashing_worker(fake, project, monkeypatch):
    j = jobs.enqueue_job(project["id"], project["user_id"], "analysis")
    claimed = jobs._claim_next()

    def handler(job, proj, tmp, ctx):
        raise RuntimeError("provider exploded")

    monkeypatch.setitem(jobs.HANDLERS, "analysis", handler)
    jobs._run_job(claimed)                        # must NOT raise
    row = job_row(fake, j["id"])
    assert row["status"] == "failed"
    assert "provider exploded" in row["error_message"]
    # failed-job compute cost recorded
    assert any(m["stage"] == "failed_job" for m in fake.tables["stage_metrics"])
    # project moved to the correct failure state
    proj = fake.select("projects", f"id=eq.{project['id']}")[0]
    assert proj["status"] == "analysis_failed"


def test_failed_job_never_marked_completed(fake, project, monkeypatch):
    j = jobs.enqueue_job(project["id"], project["user_id"], "final_render")
    claimed = jobs._claim_next()
    monkeypatch.setitem(jobs.HANDLERS, "final_render",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    jobs._run_job(claimed)
    row = job_row(fake, j["id"])
    assert row["status"] == "failed" and row["status"] != "completed"
    proj = fake.select("projects", f"id=eq.{project['id']}")[0]
    assert proj["status"] == "render_failed"


def test_temp_files_cleaned_after_success_and_failure(fake, project,
                                                      monkeypatch, tmp_path):
    captured = {}

    def ok_handler(job, proj, tmp, ctx):
        captured["ok"] = tmp
        open(os.path.join(tmp, "junk.bin"), "wb").write(b"x" * 100)
        return {}

    def bad_handler(job, proj, tmp, ctx):
        captured["bad"] = tmp
        open(os.path.join(tmp, "junk.bin"), "wb").write(b"x" * 100)
        raise RuntimeError("boom")

    monkeypatch.setitem(jobs.HANDLERS, "autoedit", ok_handler)
    monkeypatch.setitem(jobs.HANDLERS, "analysis", bad_handler)
    j1 = jobs.enqueue_job(project["id"], project["user_id"], "autoedit")
    jobs._run_job(jobs._claim_next())
    j2 = jobs.enqueue_job(project["id"], project["user_id"], "analysis")
    jobs._run_job(jobs._claim_next())
    assert not os.path.exists(captured["ok"]), "temp dir leaked after success"
    assert not os.path.exists(captured["bad"]), "temp dir leaked after failure"


# ---------- retry semantics (endpoint-level rules live in main; core here) ----
def test_retry_failed_job_requeues(fake, project, monkeypatch):
    j = jobs.enqueue_job(project["id"], project["user_id"], "analysis")
    claimed = jobs._claim_next()
    monkeypatch.setitem(jobs.HANDLERS, "analysis",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    jobs._run_job(claimed)
    assert job_row(fake, j["id"])["status"] == "failed"
    jobs.update_job(j["id"], {"status": "queued", "error_message": None})
    reclaimed = jobs._claim_next()
    assert reclaimed["id"] == j["id"] and reclaimed["attempt_count"] == 2
