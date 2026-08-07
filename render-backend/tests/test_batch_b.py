"""Batch B — main-branch customer/operations correctness (docs/AUDIT_TRIAGE.md).

B2 deterministic segment loading · B3 truthful S3 deletion · B4 render
cancellation is CANCELLED not failed · B5 cancel_requested dedupe · B6
reuse-path status · B7 digit grounding · B8 heartbeats · B9 real token
accounting · B10 budget guard. (B1's regression tests arrived with the
cherry-picked preview fix; B11 is documentation.)
"""
import copy
import os
import time
from uuid import uuid4

import pytest

os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "fake-service-key")
os.environ["WORKER_ENABLED"] = "0"

from app import jobs, s3store, segment_store  # noqa: E402
from app.main import _cleanup_project_storage  # noqa: E402
from app.pipeline import ai_budget, editorial_planner as ep  # noqa: E402
from app.renderer2 import RenderCancelled, _run_interruptible  # noqa: E402
from tests.fake_s3 import FakeS3  # noqa: E402
from tests.fake_supa import FakeSupabase, install  # noqa: E402
from tests.test_editorial_planner import _segments, _valid_plan  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("AI_BUDGET_PER_JOB_USD", "AI_BUDGET_PER_PROJECT_USD",
              "PHASE2_DIALOGUE", "PHASE2_HOOK", "PHASE2_COHERENCE",
              "PHASE2_TENSION", "PHASE2_INTEREST_GATE"):
        monkeypatch.delenv(k, raising=False)


# ================================ B2: segment store
def _row(rid, key, ver, action=""):
    return {"id": rid, "project_id": "p1", "asset_id": "a1",
            "segment_key": key, "schema_version": ver,
            "data": {"segmentId": key, "assetId": "a1", "sourceStart": 0.0,
                     "sourceEnd": 5.0, "schemaVersion": ver,
                     "action": action}}


def test_latest_rows_is_order_independent_and_version_deterministic():
    rows = [_row("r1", "k1", 2, "old"), _row("r2", "k1", 3, "new"),
            _row("r4", "k2", 3, "hi-id"), _row("r3", "k2", 3, "lo-id")]
    for perm in (rows, list(reversed(rows)), rows[::2] + rows[1::2]):
        out = segment_store.latest_rows(perm)
        by_key = {r["segment_key"]: r for r in out}
        assert by_key["k1"]["data"]["action"] == "new"      # v3 beats v2
        assert by_key["k2"]["data"]["action"] == "lo-id"    # tie -> lowest id
        assert [r["segment_key"] for r in out] == ["k1", "k2"]


def test_every_consumer_path_uses_the_store(monkeypatch):
    """jobs delegates; main.py routes through segment_store.load_rows — no
    raw full-catalog db_select('segments', project...) reads remain."""
    import inspect
    src = inspect.getsource(jobs._load_segments)
    assert "segment_store" in src
    import app.main as main_mod
    main_src = inspect.getsource(main_mod)
    assert 'db_select("segments", f"project_id=eq.{project_id}")' \
        not in main_src


# ================================ B3: truthful S3 deletion
def _s3_env(monkeypatch):
    s3 = FakeS3()
    monkeypatch.setattr(s3store, "_CLIENT", s3)
    monkeypatch.setenv("AWS_S3_BUCKET", "test-bucket")
    return s3


def test_cleanup_deletes_enumerated_s3_objects(monkeypatch):
    s3 = _s3_env(monkeypatch)
    fake = FakeSupabase()
    install(monkeypatch, fake)
    prefix = "users/u1/projects/p1/"
    for k in (f"{prefix}raw-footage/a/clip.mp4", f"{prefix}renders/r1.mp4"):
        s3.objects[k] = {"body": b"x", "content_type": "video/mp4",
                         "etag": "e", "size": 1}
    s3.objects["users/u2/projects/px/keep.mp4"] = {
        "body": b"x", "content_type": "video/mp4", "etag": "e", "size": 1}
    failures = _cleanup_project_storage("u1", "p1")
    assert failures == []
    assert list(s3.objects) == ["users/u2/projects/px/keep.mp4"]  # scoped


def test_cleanup_reports_s3_failure_instead_of_claiming_complete(monkeypatch):
    s3 = _s3_env(monkeypatch)
    fake = FakeSupabase()
    install(monkeypatch, fake)
    s3.objects["users/u1/projects/p1/raw-footage/a/clip.mp4"] = {
        "body": b"x", "content_type": "video/mp4", "etag": "e", "size": 1}

    def boom(Bucket, Key):
        raise RuntimeError("s3 down")
    monkeypatch.setattr(s3, "delete_object", boom)
    failures = _cleanup_project_storage("u1", "p1")
    assert any(f.startswith("s3") for f in failures)


def test_s3_list_keys_paginates(monkeypatch):
    s3 = _s3_env(monkeypatch)
    for i in range(2500):
        s3.objects[f"users/u1/projects/p1/x{i:04d}"] = {
            "body": b"", "content_type": "", "etag": "e", "size": 0}
    assert len(s3store.list_keys("users/u1/projects/p1/")) == 2500


# ================================ B4: cancellation is CANCELLED, not failed
def test_interruptible_runner_cancels_promptly_and_deletes_partial(tmp_path):
    from app.renderer import FFMPEG
    out = tmp_path / "out.mp4"
    cmd = [FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi",
           "-i", "testsrc=duration=30:size=64x64:rate=10", str(out)]
    t0 = time.time()
    with pytest.raises(RenderCancelled):
        _run_interruptible(cmd, timeout=60, cancel_check=lambda: True,
                           out_path=str(out))
    assert time.time() - t0 < 10          # terminated, not run to completion
    assert not out.exists()               # partial output deleted


def test_render_cancelled_maps_to_job_status_cancelled(monkeypatch):
    fake = FakeSupabase()
    install(monkeypatch, fake)
    uid, _ = fake.add_user("c@example.com")
    project = fake.add_project(uid, status="ready")
    job = jobs.enqueue_job(project["id"], uid, "autoedit", {})
    jobs.update_job(job["id"], {"status": "processing"})

    def cancelled_handler(j, p, tmp, ctx):
        raise RenderCancelled("ffmpeg terminated by cancellation request")
    monkeypatch.setitem(jobs.HANDLERS, "autoedit", cancelled_handler)
    jobs._run_job({**job, "status": "processing"})
    row = fake.tables["pipeline_jobs"][0]
    assert row["status"] == "cancelled"           # B4: never "failed"
    assert "cancel" in row["error_message"]


# ================================ B5: cancel_requested dedupe + idempotency
def test_enqueue_returns_cancel_requested_duplicate(monkeypatch):
    fake = FakeSupabase()
    install(monkeypatch, fake)
    uid, _ = fake.add_user("d@example.com")
    project = fake.add_project(uid, status="ready")
    first = jobs.enqueue_job(project["id"], uid, "autoedit", {})
    fake.patch("pipeline_jobs", f"id=eq.{first['id']}",
               {"status": "cancel_requested"})
    again = jobs.enqueue_job(project["id"], uid, "autoedit", {})
    assert again["id"] == first["id"]             # returned, not duplicated
    assert len(fake.tables["pipeline_jobs"]) == 1


def test_repeated_cancel_requests_are_idempotent(monkeypatch):
    fake = FakeSupabase()
    install(monkeypatch, fake)
    uid, _ = fake.add_user("e@example.com")
    project = fake.add_project(uid, status="ready")
    job = jobs.enqueue_job(project["id"], uid, "autoedit", {})
    fake.patch("pipeline_jobs", f"id=eq.{job['id']}", {"status": "processing"})
    r1 = jobs.request_cancel({**job, "status": "processing"}, uid)
    r2 = jobs.request_cancel({**job, "status": "cancel_requested"}, uid)
    assert r1["status"] == r2["status"] == "cancel_requested"
    assert fake.tables["pipeline_jobs"][0]["status"] == "cancel_requested"


# ================================ B7: digit grounding
def test_short_numbers_are_content_tokens():
    for text, expect in (("we won 42 jobs", "42"), ("it cost $5", "5"),
                         ("10% failed", "10"), ("8 minutes flat", "8")):
        assert expect in ep._content_tokens(text), text


def test_unsupported_number_in_fact_is_rejected():
    segs = _segments()
    raw = _valid_plan()
    raw["storySentence"] = {
        "text": "the crew finished 42 jobs", "claimType": "fact",
        "evidence": [{"sourceType": "transcript", "segmentId": "seg-3",
                      "quoteOrValue": "we finished the job"}]}
    plan = ep.EditorialPlan(**raw)
    v = ep.validate_plan(plan, segs, {}, False)
    assert any("42" in x and "unsupported factual" in x for x in v)


def test_supported_number_passes_and_step_labels_survive():
    segs = _segments()
    segs[2] = segs[2].model_copy(update={"transcript":
                                         "we finished 42 jobs today"})
    raw = _valid_plan()
    raw["storySentence"] = {
        "text": "the crew finished 42 jobs", "claimType": "fact",
        "evidence": [{"sourceType": "transcript", "segmentId": "seg-3",
                      "quoteOrValue": "we finished 42 jobs"}]}
    plan = ep.EditorialPlan(**raw)
    v = [x for x in ep.validate_plan(plan, segs, {}, False)
         if "storySentence" in x]
    assert v == [], v
    assert ep._non_neutral_tokens("Step 2") == []       # structural label OK


# ================================ B8: heartbeats
def test_beat_is_throttled_and_forceable(monkeypatch):
    fake = FakeSupabase()
    install(monkeypatch, fake)
    uid, _ = fake.add_user("h@example.com")
    project = fake.add_project(uid, status="ready")
    job = jobs.enqueue_job(project["id"], uid, "analysis", {})
    ctx = jobs.JobContext(job)
    calls = []
    monkeypatch.setattr(jobs, "update_job",
                        lambda jid, body: calls.append(jid))
    ctx.beat()                    # throttled: just constructed
    assert calls == []
    ctx.beat(force=True)
    assert len(calls) == 1
    ctx._last_beat -= jobs.JobContext.HEARTBEAT_EVERY_S + 1
    assert ctx.cancelled() in (True, False)   # cancel poll piggybacks a beat
    assert len(calls) == 2


def _stub_gemini(monkeypatch, tokens=(50_000, 8_000), fail=False):
    beats = []

    def fake_generate(parts, schema, heartbeat=None, usage=None):
        if heartbeat:
            heartbeat()
            beats.append(1)
        if usage is not None:
            usage.append({"provider": "gemini", "model": "gemini-2.5-pro",
                          "inputTokens": tokens[0],
                          "outputTokens": tokens[1], "cachedTokens": 0,
                          "requests": 1, "ladderRung": 0})
        if fail:
            raise ep.PlanRejected([["synthetic"]])
        return copy.deepcopy(_valid_plan())
    monkeypatch.setattr(ep, "gemini_generate", fake_generate)
    return beats


def _plan_job_env(monkeypatch):
    fake = FakeSupabase()
    install(monkeypatch, fake)
    uid, _ = fake.add_user("p@example.com")
    project = fake.add_project(uid, status="ready")
    for i, s in enumerate(_segments()):
        fake.insert("segments", {"id": f"s{i}", "project_id": project["id"],
                                 "asset_id": s.assetId,
                                 "segment_key": s.segmentId,
                                 "schema_version": s.schemaVersion,
                                 "data": s.model_dump()})
    job = jobs.enqueue_job(project["id"], uid, "editorial_plan", {})
    return fake, project, job


# ================================ B9: real usage accounting
def test_planner_job_records_provider_reported_tokens(monkeypatch, tmp_path):
    beats = _stub_gemini(monkeypatch)
    fake, project, job = _plan_job_env(monkeypatch)
    ctx = jobs.JobContext(job)
    artifacts = jobs.handle_editorial_plan(job, project, str(tmp_path), ctx)
    ai = artifacts["aiUsage"]
    assert ai["inputTokens"] == 50_000 and ai["outputTokens"] == 8_000
    assert ai["actualCostUsd"] is None            # billing actual unknowable
    # 50k in @ $1.25/M + 8k out @ $10/M = 0.0625 + 0.08
    assert abs(ai["estimatedCostUsd"] - 0.1425) < 0.0005
    assert beats                                   # heartbeat ran per call
    metrics = [m for m in fake.tables.get("stage_metrics", [])
               if m["stage"] == "editorial_plan_ai"]
    assert metrics and metrics[0]["units"]["gemini_pro_input_tokens"] == 50_000
    assert abs(metrics[0]["estimated_cost_usd"] - 0.1425) < 0.0005


def test_failed_planning_still_records_spend(monkeypatch, tmp_path):
    _stub_gemini(monkeypatch, fail=True)
    fake, project, job = _plan_job_env(monkeypatch)
    with pytest.raises(ep.PlanRejected):
        jobs.handle_editorial_plan(job, project, str(tmp_path), jobs.JobContext(job))
    metrics = [m for m in fake.tables.get("stage_metrics", [])
               if m["stage"] == "editorial_plan_ai"]
    assert metrics                                 # the expensive failure is visible


# ================================ B10: budget guard
def test_budget_limit_parsing_never_yields_accidental_unlimited(monkeypatch):
    monkeypatch.setenv("AI_BUDGET_PER_JOB_USD", "garbage")
    b = ai_budget.AiBudget()
    assert b.job_limit == ai_budget.DEFAULT_JOB_LIMIT_USD
    monkeypatch.setenv("AI_BUDGET_PER_JOB_USD", "-3")
    assert ai_budget.AiBudget().job_limit == ai_budget.DEFAULT_JOB_LIMIT_USD
    monkeypatch.setenv("AI_BUDGET_PER_JOB_USD", "off")     # explicit only
    assert ai_budget.AiBudget().job_limit == float("inf")


def test_budget_stops_before_the_call_with_clear_reason(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_BUDGET_PER_JOB_USD", "0.001")
    _stub_gemini(monkeypatch)
    fake, project, job = _plan_job_env(monkeypatch)
    with pytest.raises(ai_budget.BudgetExceeded) as exc:
        jobs.handle_editorial_plan(job, project, str(tmp_path), jobs.JobContext(job))
    assert "budget_exceeded" in str(exc.value)
    assert "per-job" in str(exc.value)
    # stopped BEFORE spending: no usage rows were recorded
    assert not [m for m in fake.tables.get("stage_metrics", [])
                if m["stage"] == "editorial_plan_ai"]


def test_project_budget_counts_prior_jobs(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_BUDGET_PER_PROJECT_USD", "0.20")
    _stub_gemini(monkeypatch)
    fake, project, job = _plan_job_env(monkeypatch)
    fake.insert("stage_metrics", {"id": str(uuid4()),
                                  "project_id": project["id"],
                                  "stage": "editorial_plan_ai", "units": {},
                                  "estimated_cost_usd": 0.19})
    with pytest.raises(ai_budget.BudgetExceeded) as exc:
        jobs.handle_editorial_plan(job, project, str(tmp_path), jobs.JobContext(job))
    assert "per-project" in str(exc.value)


def test_budget_allows_normal_run_under_default_limits(monkeypatch, tmp_path):
    _stub_gemini(monkeypatch)
    fake, project, job = _plan_job_env(monkeypatch)
    artifacts = jobs.handle_editorial_plan(job, project, str(tmp_path),
                                           jobs.JobContext(job))
    assert artifacts["status"] == "approved"


# ================================ B6: reuse leaves truthful status
def test_v2_reuse_restores_draft_ready_after_a_failure_state(monkeypatch):
    """A project stuck at render_failed whose re-run REUSES a valid draft
    must say draft_ready — reuse was the only success path that left the
    project displaying a stale failure over a working candidate."""
    from tests.test_picture_edit_v2 import _project_env, _stub_render
    fake = FakeSupabase()
    install(monkeypatch, fake)
    monkeypatch.setenv("PICTURE_EDIT_ENGINE_V2_ENABLED", "1")
    _stub_render(monkeypatch)
    uid, token, project = _project_env(fake)
    jobs.enqueue_job(project["id"], uid, "autoedit", {})
    jobs._run_job(jobs._claim_next())
    fake.patch("projects", f"id=eq.{project['id']}",
               {"status": "render_failed"})       # unrelated later failure
    jobs.enqueue_job(project["id"], uid, "autoedit", {})
    jobs._run_job(jobs._claim_next())
    runs = [r for r in fake.select("pipeline_jobs",
                                   f"project_id=eq.{project['id']}")
            if r["kind"] == "autoedit"]
    assert runs[1]["artifacts"]["reused"] is True
    assert fake.select("projects", f"id=eq.{project['id']}")[0]["status"] \
        == "draft_ready"
