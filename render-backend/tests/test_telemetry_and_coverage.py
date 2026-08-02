"""Telemetry trust-layer tests + footage-coverage validator tests."""
import os

import pytest

os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "fake-service-key")

from app.pipeline import telemetry  # noqa: E402
from app.pipeline.coverage import validate_coverage  # noqa: E402
from app.pipeline.schemas import Segment  # noqa: E402
from tests.fake_supa import FakeSupabase, install  # noqa: E402


@pytest.fixture()
def fake(monkeypatch):
    f = FakeSupabase()
    install(monkeypatch, f)
    # drain any pending queue from other tests
    telemetry.reconcile_pending()
    with telemetry._pending_lock:
        telemetry._pending.clear()
    return f


# ---------- telemetry ----------
def test_record_success_includes_pricing_version(fake):
    assert telemetry.record("stage_x", "p1", "j1", 1.5,
                            units={"gemini_requests": 2}) is True
    row = fake.tables["stage_metrics"][0]
    assert row["units"]["pricing_version"] == telemetry.pricing_version()
    assert row["estimated_cost_usd"] >= 0


def test_failed_write_is_queued_not_silent(fake):
    fake.fail_tables.add("stage_metrics")
    ok = telemetry.record("stage_y", "p1", "j1", 1.0)
    assert ok is False
    assert telemetry.pending_count() == 1


def test_reconciliation_recovers_failed_writes(fake):
    fake.fail_tables.add("stage_metrics")
    telemetry.record("stage_z", "p1", "j1", 1.0)
    assert telemetry.pending_count() == 1
    fake.fail_tables.discard("stage_metrics")          # store comes back
    status = telemetry.reconcile_pending()
    assert status["recovered"] == 1 and status["still_pending"] == 0
    assert any(m["stage"] == "stage_z" for m in fake.tables["stage_metrics"])


def test_reconciliation_keeps_still_failing_rows(fake):
    fake.fail_tables.add("stage_metrics")
    telemetry.record("stage_q", "p1", "j1", 1.0)
    status = telemetry.reconcile_pending()             # still failing
    assert status["still_pending"] == 1
    assert telemetry.pending_count() == 1


def test_job_telemetry_status_shows_incompleteness(fake, monkeypatch):
    from app import jobs
    j = {"id": "job-1", "project_id": "p-1"}
    ctx = jobs.JobContext(j)
    fake.fail_tables.add("stage_metrics")
    ctx.rec("s1", 1.0)
    ts = ctx.telemetry_status()
    # write failed AND reconciliation failed -> incompleteness is VISIBLE
    assert ts["expected_stages"] == 1
    assert ts["complete"] is False
    assert ts["note"] == "all costs are ESTIMATES"


def test_cost_estimate_math():
    p = telemetry.pricing()
    cost = telemetry.estimate_cost({"whisper_minutes": 10,
                                    "gemini_requests": 2})
    expected = 10 * p["whisper_per_audio_minute"] + 2 * p["gemini_flash_per_request"]
    assert abs(cost - round(expected, 5)) < 1e-9


# ---------- coverage validator ----------
def seg(sid, shot="medium", motion=0.5, uses=None, audio=0.0, transcript=None,
        move="static", problems=None):
    return Segment(segmentId=sid, assetId="a", sourceStart=0, sourceEnd=5,
                   shotType=shot, motionIntensity=motion,
                   storyUses=uses or [], audioScore=audio,
                   transcript=transcript, cameraMovement=move,
                   focusScore=0.8, exposureScore=0.8, stabilityScore=0.8,
                   problems=problems or [], searchText=sid)


def test_coverage_reports_missing_categories():
    rep = validate_coverage([seg("s1", shot="wide", motion=0.6)])
    assert "Completion moment" in rep.missingRequired
    assert "Reflection / payoff footage" in rep.missingRequired
    assert rep.segmentCount == 1


def test_coverage_detects_present_categories():
    segs = [
        seg("est", shot="wide", motion=0.1, uses=["location"]),
        seg("peak", motion=0.9, uses=["peak"]),
        seg("done", uses=["completion"]),
        seg("audio", audio=0.8),
        seg("track", move="handheld follow", motion=0.6),
    ]
    rep = validate_coverage(segs)
    by = {i.category: i for i in rep.items}
    assert by["establishing"].present
    assert by["peak"].present
    assert by["completion"].present
    assert by["natural_audio"].present
    assert by["tracking"].present


def test_coverage_excludes_unusable_segments():
    rep = validate_coverage([seg("bad", uses=["completion"],
                                 problems=["mostly_black"])])
    assert rep.usableSegmentCount == 0
    assert "Completion moment" in rep.missingRequired


def test_coverage_warns_on_wide_monotony():
    segs = [seg(f"w{i}", shot="wide", motion=0.5) for i in range(8)]
    rep = validate_coverage(segs)
    assert any("wide" in w for w in rep.warnings)


def test_coverage_never_invents_footage():
    rep = validate_coverage([])
    assert all(not i.present for i in rep.items)
    assert rep.segmentCount == 0
