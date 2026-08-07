"""Picture Edit Engine V2 — approved EditorialPlan -> real picture timeline.

Covers the full required matrix: hook execution, beat order, bounds, binding
duration, determinism/idempotency, pacing, transitions (executable + pending),
speed ramps, crops, feature-flag routing, no-silent-fallback, bridge and
Product Editor compatibility. Real FFmpeg only in the marked render tests.
"""
import copy
import os
import subprocess
import uuid

import pytest

os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "fake-service-key")
os.environ["WORKER_ENABLED"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from app import jobs, supa  # noqa: E402
from app.main import app  # noqa: E402
from app.pipeline import picture_edit_v2 as pe2  # noqa: E402
from app.pipeline import picture_render_v2  # noqa: E402
from tests.fake_supa import FakeSupabase, install  # noqa: E402
from tests.test_editorial_planner import _segments, _valid_plan  # noqa: E402

NOW = "2026-08-05T00:00:00+00:00"


def _plan_row(plan=None, status="approved", version=1, request=None,
              gate_passed=True, project_id="proj-1"):
    return {"id": f"plan-{version}", "project_id": project_id,
            "version": version, "status": status,
            "request": request or {},
            "plan": plan if plan is not None else _valid_plan(),
            "validation": {"deterministicGate": {
                "passed": gate_passed, "score": 100 if gate_passed else 40,
                "hardFailures": [] if gate_passed else ["hook_grounded"]}}}


def _build(plan=None, segments=None, **row_kw):
    return pe2.build_picture_edit(_plan_row(plan=plan, **row_kw),
                                  segments or _segments(), now=NOW)


def _reject(plan=None, segments=None, needle=None, **row_kw):
    with pytest.raises(pe2.PictureEditRejected) as exc:
        _build(plan=plan, segments=segments, **row_kw)
    flat = " | ".join(exc.value.reasons)
    if needle:
        assert needle in flat, flat
    return flat


# ------------------------------------------------ 1+2: hook + beat order
def test_hook_is_timeline_zero_and_beat_order_preserved():
    out = _build()
    maps = out["segmentMappings"]
    assert maps[0]["timelineIn"] == 0.0
    assert maps[0]["segmentId"] == "seg-1"                   # the planned hook
    plan = _valid_plan()
    assert [m["segmentId"] for m in maps] \
        == [t["segmentId"] for t in plan["timeline"]]        # exact plan order
    assert [m["beat"] for m in maps] == ["hook", "process", "payoff"]
    assert len(maps) == len(plan["timeline"])                # nothing omitted


def test_hook_cannot_be_replaced_by_chronological_footage():
    plan = _valid_plan()
    plan["timeline"] = plan["timeline"][1:] + plan["timeline"][:1]
    for i, seg in enumerate(plan["timeline"]):               # keep contiguity
        seg["timelineIn"], seg["timelineOut"] = i * 4.0, (i + 1) * 4.0
    _reject(plan=plan, needle="timeline[0] is not the planned hook")


# ------------------------------------------------ 3: source bounds + trims
def test_source_bounds_enforced_and_epsilon_trims_logged():
    plan = _valid_plan()
    plan["timeline"][1]["sourceOut"] = 12.0                  # catalog ends at 10
    plan["timeline"][1]["timelineOut"] = 4.0 + 12.0
    _reject(plan=plan, needle="past its real source end")

    eps = _valid_plan()
    eps["timeline"][2]["sourceOut"] = 10.04                  # epsilon overshoot
    eps["timeline"][2]["sourceIn"] = 6.04
    out = _build(plan=eps)
    adj = out["trimAdjustments"]
    assert adj and adj[0]["segmentId"] == "seg-3" \
        and "clamped" in adj[0]["reason"]                    # logged, not silent


def test_catalog_drift_rejected():
    segs = [s for s in _segments() if s.segmentId != "seg-2"]
    _reject(segments=segs, needle="absent from the current source catalog")


# ------------------------------------------------ 4+5: binding duration + speed
def test_requested_duration_is_binding_at_execution_time():
    _reject(request={"durationMin": 45, "durationMax": 60},
            needle="materially shorter than the requested minimum")


def test_speed_adjusted_duration_verified():
    plan = _valid_plan()
    plan["timeline"][1].update(playbackSpeed=2.0, sourceIn=0.0, sourceOut=8.0)
    out = _build(plan=plan)                                  # 8s src @2x = 4s tl
    assert out["actualDurationSeconds"] == 12.0
    bad = _valid_plan()
    bad["timeline"][1]["playbackSpeed"] = 2.0                # 4s src @2x != 4s tl
    _reject(plan=bad, needle="speed-adjusted duration is impossible")


# ------------------------------------------------ 6: honest insufficient footage
def test_insufficient_footage_plan_refused_honestly():
    plan = _valid_plan()
    plan["status"] = "insufficient_footage"
    plan["achievableDurationSeconds"] = 12.0
    plan["missingFootage"] = [{"beat": "closing reaction",
                               "shotType": "close-up",
                               "recommendedDurationSeconds": 5.0,
                               "why": "the payoff needs a reaction shot"}]
    flat = _reject(plan=plan, status="insufficient_footage",
                   needle="insufficient footage")
    assert "closing reaction" in flat                        # exact missing shots


def test_unapproved_or_gateless_plan_refused():
    _reject(status="running", needle="not 'approved'")
    _reject(gate_passed=False, needle="did not pass the deterministic quality gate")
    with pytest.raises(pe2.PictureEditRejected, match="run the Editorial Planner"):
        pe2.build_picture_edit(None, _segments(), now=NOW)


# ------------------------------------------------ 7: duplicates rejected
def test_duplicate_source_ranges_rejected():
    plan = _valid_plan()
    plan["timeline"][2] = dict(plan["timeline"][0],
                               timelineIn=8.0, timelineOut=12.0, beat="payoff")
    _reject(plan=plan, needle="repeats an identical source range")


# ------------------------------------------------ 9+10: determinism
def test_same_inputs_produce_identical_hash_regardless_of_clock():
    a = pe2.build_picture_edit(_plan_row(), _segments(), now=NOW)
    b = pe2.build_picture_edit(_plan_row(), _segments(),
                               now="2030-01-01T00:00:00+00:00")
    assert a["deterministicHash"] == b["deterministicHash"]
    assert a["createdAt"] != b["createdAt"]


def test_plan_version_and_catalog_change_the_hash():
    base = _build()
    v2 = _build(version=2)
    assert v2["deterministicHash"] != base["deterministicHash"]
    drift = _segments()
    drift[0] = drift[0].model_copy(update={"sourceEnd": 9.5})
    other = _build(segments=drift)
    assert other["deterministicHash"] != base["deterministicHash"]
    assert other["sourceCatalogHash"] != base["sourceCatalogHash"]


# ------------------------------------------------ 11+12: pacing + payoff hold
def test_pacing_metrics_calculated_per_beat():
    out = _build()
    metrics = {m["beat"]: m for m in out["pacingMetrics"]}
    assert set(metrics) == {"hook", "process", "payoff"}
    for m in metrics.values():
        assert m["actualDurationSeconds"] == 4.0 and m["deviationSeconds"] == 0.0
        assert m["shotCount"] == 1 and m["shotDensity"] == 0.25
        assert 0 <= m["energyTarget"] <= 1
        assert 0 <= m["actualEnergy"] <= 1                  # measured, not copied
        assert m["energyDeviation"] == round(m["actualEnergy"]
                                             - m["energyTarget"], 3)


def test_actual_energy_measured_from_footage_motion_and_cut_density():
    segs = [s.model_copy(update={"motionIntensity": 0.8}) for s in _segments()]
    out = _build(segments=segs)
    hook = next(m for m in out["pacingMetrics"] if m["beat"] == "hook")
    # duration-weighted motion 0.8, cut density 0.25 shots/s:
    # 0.6*0.8 + 0.4*min(1, 0.25/2) = 0.48 + 0.05 = 0.53
    assert hook["actualEnergy"] == 0.53
    assert hook["energyDeviation"] == round(0.53 - hook["energyTarget"], 3)
    calm = _build()                                          # motionIntensity 0
    calm_hook = next(m for m in calm["pacingMetrics"] if m["beat"] == "hook")
    assert calm_hook["actualEnergy"] == 0.05                 # cuts only
    assert calm_hook["actualEnergy"] != calm_hook["energyTarget"]


def test_matching_energy_yields_zero_deviation():
    """A plan whose target equals the measured delivery scores zero deviation."""
    segs = [s.model_copy(update={"motionIntensity": 0.8}) for s in _segments()]
    plan = _valid_plan()
    for pb in plan["pacing"]:
        pb["energy"] = 0.53           # == 0.6*0.8 + 0.4*min(1, 0.25/2)
    out = _build(plan=plan, segments=segs)
    for m in out["pacingMetrics"]:
        assert m["actualEnergy"] == 0.53
        assert m["energyDeviation"] == 0.0


def test_payoff_hold_materially_violated_is_rejected():
    plan = _valid_plan()
    plan["pacing"][2]["targetDurationSeconds"] = 10.0        # payoff hold of 10s
    _reject(plan=plan, needle="pacing materially violates the approved plan")


# ------------------------------------------------ 13-15: transitions
def test_hard_cuts_and_dissolve_are_executable_instructions():
    plan = _valid_plan()
    plan["timeline"][1].update(sourceIn=1.0, sourceOut=5.0)
    plan["transitions"][0].update(type="dissolve", durationSeconds=0.5)
    out = _build(plan=plan)
    by_type = {t["type"]: t for t in out["transitionInstructions"]}
    assert by_type["dissolve"]["status"] == "executable"
    assert by_type["hard_cut"]["status"] == "executable"
    assert out["unsupportedExecution"] == []


def test_dissolve_without_handles_rejected():
    plan = _valid_plan()
    plan["transitions"][0].update(type="dissolve", durationSeconds=0.5)
    _reject(plan=plan, needle="source handle")               # head handle is 0


def test_unsupported_transition_preserved_not_replaced():
    plan = _valid_plan()
    plan["timeline"][1].update(sourceIn=1.0, sourceOut=5.0)
    plan["transitions"][0].update(type="whip", durationSeconds=0.5)
    out = _build(plan=plan)
    whip = next(t for t in out["transitionInstructions"] if t["type"] == "whip")
    assert whip["status"] == "pending_renderer_support"
    assert "NOT replaced" in whip["note"]
    assert whip in out["unsupportedExecution"]               # clearly surfaced
    assert not any(t["type"] != "whip" and t["boundaryIndex"] == 0
                   for t in out["transitionInstructions"])   # nothing substituted


# ------------------------------------------------ 16+17: speed ramps
def test_valid_speed_ramp_carried_as_structured_instruction():
    plan = _valid_plan()
    plan["speedRamps"] = [{"segmentId": "seg-2", "sourceStart": 0.0,
                           "sourceEnd": 4.0, "entrySpeed": 0.5, "peakSpeed": 1.5,
                           "exitSpeed": 0.5, "easing": "ease-in-out",
                           "narrativePurpose": "hold the action"}]
    # avg speed (0.5+3+0.5)/4 = 1.0 -> effective 4.0s == constant 4.0s
    out = _build(plan=plan)
    inst = out["speedInstructions"][0]
    assert inst["effectiveDurationSeconds"] == 4.0
    assert inst["status"] == "pending_renderer_support"      # honest, not faked
    assert "slow motion" in inst["warning"]                  # fps metadata absent


def test_duration_changing_ramp_rejected():
    plan = _valid_plan()
    plan["speedRamps"] = [{"segmentId": "seg-2", "sourceStart": 0.0,
                           "sourceEnd": 4.0, "entrySpeed": 3.0, "peakSpeed": 3.0,
                           "exitSpeed": 3.0, "easing": "linear",
                           "narrativePurpose": "x"}]
    _reject(plan=plan, needle="would change the segment's duration")


# ------------------------------------------------ 18-20: reframes
def test_static_and_pan_crops_executable_zoom_pending():
    plan = _valid_plan()
    plan["reframes"] = [
        {"segmentId": "seg-1", "outputAspectRatio": "9:16",
         "subjectTarget": "crew",
         "startCrop": {"x": 0.1, "y": 0.0, "width": 0.5, "height": 0.9},
         "endCrop": {"x": 0.1, "y": 0.0, "width": 0.5, "height": 0.9}},
        {"segmentId": "seg-2", "outputAspectRatio": "9:16",
         "subjectTarget": "crew",
         "startCrop": {"x": 0.0, "y": 0.0, "width": 0.5, "height": 0.9},
         "endCrop": {"x": 0.4, "y": 0.05, "width": 0.5, "height": 0.9}},
        {"segmentId": "seg-3", "outputAspectRatio": "9:16",
         "subjectTarget": "crew",
         "startCrop": {"x": 0.0, "y": 0.0, "width": 0.9, "height": 0.9},
         "endCrop": {"x": 0.2, "y": 0.2, "width": 0.45, "height": 0.45}}]
    out = _build(plan=plan)
    modes = {r["segmentId"]: r for r in out["reframeInstructions"]}
    assert modes["seg-1"]["mode"] == "static" \
        and modes["seg-1"]["status"] == "executable"
    assert modes["seg-2"]["mode"] == "pan_interpolated" \
        and modes["seg-2"]["status"] == "executable"
    assert modes["seg-3"]["mode"] == "zoom_interpolated" \
        and modes["seg-3"]["status"] == "pending_renderer_support"
    assert any("upscale risk" in w for w in out["technicalWarnings"])


def test_out_of_frame_crop_rejected():
    plan = _valid_plan()
    plan["reframes"] = [{"segmentId": "seg-1", "outputAspectRatio": "9:16",
                         "subjectTarget": "crew",
                         "startCrop": {"x": 0.9, "y": 0.0, "width": 0.9,
                                       "height": 0.5},
                         "endCrop": {"x": 0.9, "y": 0.0, "width": 0.9,
                                     "height": 0.5}}]
    _reject(plan=plan, needle="out-of-frame geometry is rejected")


# ------------------------------------------------ real-FFmpeg render tests
@pytest.fixture(scope="module")
def two_clips(tmp_path_factory):
    d = tmp_path_factory.mktemp("pe2-src")
    paths = {}
    for name, pattern in (("asset-a", "testsrc"), ("asset-b", "testsrc2")):
        p = str(d / f"{name}.mp4")
        subprocess.run([picture_render_v2.FFMPEG, "-y", "-loglevel", "error",
                        "-f", "lavfi", "-i", f"{pattern}=size=320x180:rate=30",
                        "-f", "lavfi", "-i", "sine=frequency=440",
                        "-t", "3", "-c:v", "libx264", "-preset", "veryfast",
                        "-c:a", "aac", "-shortest", p], check=True, timeout=120)
        paths[name] = p
    return paths


def _render_result(transition_type):
    """A minimal executed PictureEditV2 result over two 3s clips."""
    clips = [{"id": "c0", "assetId": "asset-a", "sourceStart": 0.0,
              "sourceEnd": 2.0, "timelineStart": 0.0, "timelineEnd": 2.0,
              "speed": 1.0, "volume": 1.0},
             {"id": "c1", "assetId": "asset-b", "sourceStart": 1.0,
              "sourceEnd": 3.0, "timelineStart": 2.0, "timelineEnd": 4.0,
              "speed": 1.0, "volume": 1.0}]
    tr = {"fromSegmentId": "s-a", "toSegmentId": "s-b", "type": transition_type,
          "durationSeconds": 0.0 if transition_type == "hard_cut" else 0.5,
          "purpose": "test", "boundaryIndex": 0, "status": "executable"}
    return {"timeline": {"version": 1, "width": 1920, "height": 1080,
                         "fps": 30, "duration": 4.0,
                         "tracks": [{"id": "v", "type": "video",
                                     "clips": clips}]},
            "segmentMappings": [{"segmentId": "s-a"}, {"segmentId": "s-b"}],
            "transitionInstructions": [tr],
            "reframeInstructions": [
                {"segmentId": "s-a", "mode": "static", "status": "executable",
                 "startCrop": {"x": 0.1, "y": 0.1, "width": 0.8, "height": 0.8},
                 "endCrop": {"x": 0.1, "y": 0.1, "width": 0.8, "height": 0.8}},
                {"segmentId": "s-b", "mode": "pan_interpolated",
                 "status": "executable",
                 "startCrop": {"x": 0.0, "y": 0.0, "width": 0.8, "height": 0.8},
                 "endCrop": {"x": 0.2, "y": 0.1, "width": 0.8, "height": 0.8}}]}


def _probe_duration(path):
    from app.renderer import probe
    return probe(path).duration


@pytest.mark.parametrize("transition", ["hard_cut", "dissolve", "dip_to_black"])
def test_render_executes_cuts_transitions_and_crops(two_clips, tmp_path,
                                                    transition):
    out = str(tmp_path / f"out-{transition}.mp4")
    picture_render_v2.render_picture_edit(_render_result(transition),
                                          two_clips, out)
    dur = _probe_duration(out)
    assert abs(dur - 4.0) < 0.35        # timeline duration preserved (soft
    #                                     transitions consume source handles)


def test_preview_dims_preserve_every_approved_aspect():
    dims = picture_render_v2._preview_dims
    assert dims({"width": 1920, "height": 1080}) == (640, 360)   # landscape
    assert dims({"width": 1080, "height": 1920}) == (360, 640)   # portrait
    assert dims({"width": 1080, "height": 1080}) == (640, 640)   # SQUARE
    assert dims({"width": 1080, "height": 1350}) == (512, 640)   # 4:5


def test_fps_string_never_truncates_fractional_rates():
    fps = picture_render_v2._fps_str
    assert fps({"fps": 29.97}) == "29.97"
    assert fps({"fps": 30}) == "30"
    assert fps({"fps": 59.94}) == "59.94"
    assert fps({"fps": 23.976}) == "23.976"


def _probe_dims_fps(out_path):
    import json as _json

    from app.renderer import FFPROBE, probe
    info = probe(out_path)
    raw = subprocess.run([FFPROBE, "-v", "error", "-print_format", "json",
                          "-show_streams", out_path], capture_output=True,
                         check=True, timeout=60).stdout
    vstream = next(s for s in _json.loads(raw)["streams"]
                   if s["codec_type"] == "video")
    num, den = vstream["r_frame_rate"].split("/")
    return (info.width, info.height), int(num) / int(den)


@pytest.mark.parametrize("w,h,fps,want_dims", [
    (1080, 1080, 29.97, (640, 640)),     # SQUARE @ fractional (Codex repro)
    (1920, 1080, 30.0, (640, 360)),      # landscape @ integer
    (1080, 1920, 59.94, (360, 640)),     # portrait @ fractional
])
def test_every_aspect_and_fps_rendered_correctly(two_clips, tmp_path,
                                                 w, h, fps, want_dims):
    """Real ffprobe verification for landscape, portrait AND square outputs at
    integer and fractional frame rates — never coerced to 16:9, never
    truncated to an integer rate."""
    result = _render_result("hard_cut")
    result["timeline"].update(width=w, height=h, fps=fps)
    out = str(tmp_path / f"out-{w}x{h}-{fps}.mp4")
    picture_render_v2.render_picture_edit(result, two_clips, out)
    dims, real_fps = _probe_dims_fps(out)
    assert dims == want_dims
    assert abs(real_fps - fps) < 0.01


# ------------------------------------------------ handler / flag / idempotency
def _project_env(fake, with_plan=True, plan=None, request=None):
    uid, token = fake.add_user("pe2@example.com")
    project = fake.add_project(uid, "PE2 Test", status="ready")
    aid = str(uuid.uuid4())          # Product Editor requires real UUID assets
    key = f"users/{uid}/projects/{project['id']}/raw/clip.mp4"
    fake.insert("media_assets", {"id": aid, "project_id": project["id"],
                                 "user_id": uid, "filename": "clip.mp4",
                                 "storage_path": key,
                                 "duration_seconds": 10.0})
    fake.storage[f"raw-footage/{key}"] = b"VIDEO"
    for s in _segments(asset_id=aid):
        fake.insert("segments", {"project_id": project["id"], "user_id": uid,
                                 "data": s.model_dump()})
    if with_plan:
        the_plan = copy.deepcopy(plan or _valid_plan())
        for entry in the_plan["timeline"]:
            entry["assetId"] = aid
        fake.insert("editorial_plans", {
            "project_id": project["id"], "user_id": uid, "version": 1,
            "status": "approved", "quality_score": 100, "attempts": 1,
            "request": request or {}, "plan": the_plan,
            "validation": {"deterministicGate": {"passed": True, "score": 100,
                                                 "hardFailures": []}}})
    return uid, token, project


def _stub_render(monkeypatch):
    def fake_render(result, sources, out_path, timeout=900, cancel_check=None):
        with open(out_path, "wb") as f:
            f.write(b"PREVIEW")
        return result["timeline"]["duration"]
    monkeypatch.setattr(picture_render_v2, "render_picture_edit", fake_render)


def test_flag_on_builds_timeline_and_feeds_the_bridge(monkeypatch):
    fake = FakeSupabase()
    install(monkeypatch, fake)
    monkeypatch.setenv("PICTURE_EDIT_ENGINE_V2_ENABLED", "1")
    _stub_render(monkeypatch)
    uid, token, project = _project_env(fake)
    jobs.enqueue_job(project["id"], uid, "autoedit", {})
    jobs._run_job(jobs._claim_next())
    row = fake.select("pipeline_jobs", f"project_id=eq.{project['id']}")[0]
    assert row["status"] == "completed", row.get("error_message")
    art = row["artifacts"]
    assert art["engine"] == "picture_edit_v2" and art["reused"] is False
    tl = fake.select("timelines", f"id=eq.{art['timelineId']}")[0]
    assert tl["lineage"] == "autonomous_revised" and tl["is_immutable"] is True
    clips = tl["timeline_json"]["tracks"][0]["clips"]
    assert clips[0]["timelineStart"] == 0.0 and len(clips) == 3
    # bridge compatibility: a bridged candidate exists over the V2 timeline
    cands = fake.select("candidate_runs", f"project_id=eq.{project['id']}")
    assert len(cands) == 1 and cands[0]["generation_kind"] == "bridged"
    assert art["bridgedCandidateRunId"] == cands[0]["id"]
    assert fake.select("projects", f"id=eq.{project['id']}")[0]["status"] \
        == "draft_ready"


def test_flag_on_repeated_run_reuses_persisted_result(monkeypatch):
    fake = FakeSupabase()
    install(monkeypatch, fake)
    monkeypatch.setenv("PICTURE_EDIT_ENGINE_V2_ENABLED", "1")
    _stub_render(monkeypatch)
    uid, token, project = _project_env(fake)
    for _ in range(2):
        jobs.enqueue_job(project["id"], uid, "autoedit", {})
        jobs._run_job(jobs._claim_next())
    runs = [r for r in fake.select("pipeline_jobs",
                                   f"project_id=eq.{project['id']}")
            if r["kind"] == "autoedit"]
    assert [r["status"] for r in runs] == ["completed", "completed"]
    assert runs[0]["artifacts"]["reused"] is False
    assert runs[1]["artifacts"]["reused"] is True
    assert runs[1]["artifacts"]["timelineId"] == runs[0]["artifacts"]["timelineId"]
    # the reused result still hands the Product Editor its candidate
    assert runs[1]["artifacts"]["bridgedCandidateRunId"] \
        == runs[0]["artifacts"]["bridgedCandidateRunId"]
    # no duplicate timelines or edit runs
    assert len(fake.select("timelines", f"project_id=eq.{project['id']}")) == 1
    assert len(fake.select("edit_runs", f"project_id=eq.{project['id']}")) == 1


def test_retry_after_bridge_failure_repairs_the_missing_candidate(monkeypatch):
    """Codex repro: attempt 1 persists the timeline but the bridge fails ->
    job failed, no candidate. The retry must NOT report success through
    timeline reuse while the Product Editor still has nothing to open — it
    must repair the bridge."""
    from app import autoedit_bridge
    fake = FakeSupabase()
    install(monkeypatch, fake)
    monkeypatch.setenv("PICTURE_EDIT_ENGINE_V2_ENABLED", "1")
    _stub_render(monkeypatch)
    uid, token, project = _project_env(fake)

    real_bridge = autoedit_bridge.bridge_from_autoedit
    def broken_bridge(*a, **k):
        raise RuntimeError("storage exploded mid-bridge")
    monkeypatch.setattr(autoedit_bridge, "bridge_from_autoedit", broken_bridge)
    jobs.enqueue_job(project["id"], uid, "autoedit", {})
    jobs._run_job(jobs._claim_next())
    first = [r for r in fake.select("pipeline_jobs",
                                    f"project_id=eq.{project['id']}")
             if r["kind"] == "autoedit"][0]
    assert first["status"] == "failed"
    assert len(fake.select("timelines", f"project_id=eq.{project['id']}")) == 1
    assert fake.select("candidate_runs", f"project_id=eq.{project['id']}") == []

    monkeypatch.setattr(autoedit_bridge, "bridge_from_autoedit", real_bridge)
    jobs.enqueue_job(project["id"], uid, "autoedit", {})
    jobs._run_job(jobs._claim_next())
    retry = sorted((r for r in fake.select("pipeline_jobs",
                                           f"project_id=eq.{project['id']}")
                    if r["kind"] == "autoedit"),
                   key=lambda r: r["created_at"])[-1]
    assert retry["status"] == "completed", retry.get("error_message")
    art = retry["artifacts"]
    assert art["reused"] is True and art.get("bridgeRepaired") is True
    cands = fake.select("candidate_runs", f"project_id=eq.{project['id']}")
    assert len(cands) == 1 and cands[0]["generation_kind"] == "bridged"
    assert art["bridgedCandidateRunId"] == cands[0]["id"]
    # still exactly one timeline + edit run (repair, not duplication)
    assert len(fake.select("timelines", f"project_id=eq.{project['id']}")) == 1
    assert len(fake.select("edit_runs", f"project_id=eq.{project['id']}")) == 1
    assert fake.select("projects", f"id=eq.{project['id']}")[0]["status"] \
        == "draft_ready"


def test_flag_on_without_approved_plan_fails_loudly_no_fallback(monkeypatch):
    fake = FakeSupabase()
    install(monkeypatch, fake)
    monkeypatch.setenv("PICTURE_EDIT_ENGINE_V2_ENABLED", "1")
    called = {"legacy": False}

    def legacy_sentinel(*a, **k):
        called["legacy"] = True
        raise AssertionError("legacy autoedit must never run as a fallback")
    import app.pipeline.autoedit as legacy
    monkeypatch.setattr(legacy, "autoedit", legacy_sentinel)
    uid, token, project = _project_env(fake, with_plan=False)
    jobs.enqueue_job(project["id"], uid, "autoedit", {})
    jobs._run_job(jobs._claim_next())
    row = fake.select("pipeline_jobs", f"project_id=eq.{project['id']}")[0]
    assert row["status"] == "failed"
    assert "run the Editorial Planner first" in row["error_message"]
    assert called["legacy"] is False                     # 23: no silent fallback
    assert fake.select("timelines", f"project_id=eq.{project['id']}") == []


def test_flag_off_routes_to_legacy_path_untouched(monkeypatch):
    fake = FakeSupabase()
    install(monkeypatch, fake)
    monkeypatch.delenv("PICTURE_EDIT_ENGINE_V2_ENABLED", raising=False)

    class LegacyReached(Exception):
        pass

    def legacy_sentinel(*a, **k):
        raise LegacyReached()
    import app.pipeline.autoedit as legacy
    monkeypatch.setattr(legacy, "autoedit", legacy_sentinel)
    monkeypatch.setattr(jobs, "handle_autoedit_v2",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("v2 must not run with the flag off")))
    uid, token, project = _project_env(fake, with_plan=False)
    job = {"id": str(uuid.uuid4()), "project_id": project["id"],
           "user_id": uid, "kind": "autoedit", "params": {},
           "status": "processing", "attempt_count": 1, "max_attempts": 3}
    fake.insert("pipeline_jobs", dict(job))
    with pytest.raises(LegacyReached):
        jobs.handle_autoedit(job, fake.select("projects",
                                              f"id=eq.{project['id']}")[0],
                             "/tmp", jobs.JobContext(job))
    # 21+26: legacy path entered, V2 untouched, no editorial plan required


def _picture_timeline_id(fake, candidate):
    """The source timeline a bridged candidate ancestrally descends from."""
    pic = fake.select("picture_edit_runs",
                      f"id=eq.{candidate['picture_edit_run_id']}")[0]
    return pic["request"]["timeline_id"]


def test_new_engine_version_gets_its_own_timeline_bound_candidate(monkeypatch):
    """Codex repro: a 2.1.0 candidate must NEVER be reused for a new engine
    version's timeline. Each timeline gets its own immutable candidate; the
    old pair stays untouched; repeats stay idempotent per timeline."""
    fake = FakeSupabase()
    install(monkeypatch, fake)
    monkeypatch.setenv("PICTURE_EDIT_ENGINE_V2_ENABLED", "1")
    _stub_render(monkeypatch)
    uid, token, project = _project_env(fake)

    jobs.enqueue_job(project["id"], uid, "autoedit", {})
    jobs._run_job(jobs._claim_next())
    first = [r for r in fake.select("pipeline_jobs",
                                    f"project_id=eq.{project['id']}")
             if r["kind"] == "autoedit"][0]["artifacts"]
    cand_a = fake.select("candidate_runs", f"id=eq.{first['bridgedCandidateRunId']}")[0]
    assert _picture_timeline_id(fake, cand_a) == first["timelineId"]

    monkeypatch.setattr(pe2, "ENGINE_VERSION", "2.2.0-test")
    jobs.enqueue_job(project["id"], uid, "autoedit", {})
    jobs._run_job(jobs._claim_next())
    runs = sorted((r for r in fake.select("pipeline_jobs",
                                          f"project_id=eq.{project['id']}")
                   if r["kind"] == "autoedit"), key=lambda r: r["created_at"])
    second = runs[-1]["artifacts"]
    assert runs[-1]["status"] == "completed"
    assert second["timelineId"] != first["timelineId"]        # new timeline B
    assert second["bridgedCandidateRunId"] != first["bridgedCandidateRunId"]
    cand_b = fake.select("candidate_runs",
                         f"id=eq.{second['bridgedCandidateRunId']}")[0]
    # candidate B descends from timeline B; candidate A still from timeline A
    assert _picture_timeline_id(fake, cand_b) == second["timelineId"]
    cand_a_after = fake.select("candidate_runs", f"id=eq.{cand_a['id']}")[0]
    assert cand_a_after == cand_a                             # A unchanged
    assert _picture_timeline_id(fake, cand_a_after) == first["timelineId"]
    cands = fake.select("candidate_runs", f"project_id=eq.{project['id']}")
    assert len(cands) == 2                                    # coexist safely

    # idempotency per timeline: repeat under 2.2.0-test returns candidate B
    jobs.enqueue_job(project["id"], uid, "autoedit", {})
    jobs._run_job(jobs._claim_next())
    third = sorted((r for r in fake.select("pipeline_jobs",
                                           f"project_id=eq.{project['id']}")
                    if r["kind"] == "autoedit"),
                   key=lambda r: r["created_at"])[-1]["artifacts"]
    assert third["reused"] is True
    assert third["bridgedCandidateRunId"] == cand_b["id"]     # never A
    assert len(fake.select("candidate_runs",
                           f"project_id=eq.{project['id']}")) == 2


def test_editor_opens_the_new_timelines_candidate(monkeypatch):
    from app import main
    main._rate.clear()
    fake = FakeSupabase()
    install(monkeypatch, fake)
    monkeypatch.setenv("PICTURE_EDIT_ENGINE_V2_ENABLED", "1")
    _stub_render(monkeypatch)
    uid, token, project = _project_env(fake)
    jobs.enqueue_job(project["id"], uid, "autoedit", {})
    jobs._run_job(jobs._claim_next())
    monkeypatch.setattr(pe2, "ENGINE_VERSION", "2.2.0-test")
    jobs.enqueue_job(project["id"], uid, "autoedit", {})
    jobs._run_job(jobs._claim_next())
    art = sorted((r for r in fake.select("pipeline_jobs",
                                         f"project_id=eq.{project['id']}")
                  if r["kind"] == "autoedit"),
                 key=lambda r: r["created_at"])[-1]["artifacts"]
    cand_b = fake.select("candidate_runs",
                         f"id=eq.{art['bridgedCandidateRunId']}")[0]
    assert _picture_timeline_id(fake, cand_b) == art["timelineId"]
    client = TestClient(app, raise_server_exceptions=False)
    r = client.post(f"/projects/{project['id']}/editor/start",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"candidateRunId": cand_b["id"]})
    assert r.status_code == 200, r.text
    picture = next(t for t in r.json()["document"]["tracks"]
                   if t["type"] == "picture")
    assert len(picture["items"]) == 3        # the NEW timeline's picture items


def test_concurrent_bridges_of_new_timeline_never_return_old_candidate(monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from app import autoedit_bridge, supa as supa_mod
    fake = FakeSupabase()
    install(monkeypatch, fake)
    monkeypatch.setenv("PICTURE_EDIT_ENGINE_V2_ENABLED", "1")
    _stub_render(monkeypatch)
    uid, token, project = _project_env(fake)
    jobs.enqueue_job(project["id"], uid, "autoedit", {})     # candidate A
    jobs._run_job(jobs._claim_next())
    cand_a = fake.select("candidate_runs", f"project_id=eq.{project['id']}")[0]
    tl_a = fake.select("timelines", f"project_id=eq.{project['id']}")[0]
    tl_b = fake.insert("timelines", {
        "project_id": project["id"], "user_id": uid, "version": 99,
        "timeline_json": tl_a["timeline_json"],
        "lineage": "autonomous_revised", "is_immutable": True}).json()[0]

    lock, orig_insert = threading.Lock(), fake.insert
    def locked_insert(table, body):
        with lock:
            return orig_insert(table, body)
    monkeypatch.setattr(fake, "insert", locked_insert)

    def bridge(_i, tmp_path="unused"):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            f.write(b"P")
            preview = f.name
        return autoedit_bridge.bridge_from_autoedit(
            fake.select("projects", f"id=eq.{project['id']}")[0], tl_b, preview,
            insert=jobs._insert, db_select=supa_mod.db_select,
            upload_export=jobs._upload_export, now=jobs._now,
            remove=supa_mod.storage_remove, update=supa_mod.db_update)

    results = list(ThreadPoolExecutor(max_workers=4).map(bridge, range(4)))
    ids = {r["id"] for r in results}
    assert len(ids) == 1 and cand_a["id"] not in ids          # one B, never A
    cands = fake.select("candidate_runs", f"project_id=eq.{project['id']}")
    assert len(cands) == 2                                    # A + B, no dupes


def test_retry_repair_resolves_the_correct_timeline_bound_candidate(monkeypatch):
    """Bridge fails after the NEW engine version's timeline persisted, while
    the OLD candidate A still exists. The retry must repair with a candidate
    for timeline B — not report the old candidate A."""
    from app import autoedit_bridge
    fake = FakeSupabase()
    install(monkeypatch, fake)
    monkeypatch.setenv("PICTURE_EDIT_ENGINE_V2_ENABLED", "1")
    _stub_render(monkeypatch)
    uid, token, project = _project_env(fake)
    jobs.enqueue_job(project["id"], uid, "autoedit", {})     # candidate A
    jobs._run_job(jobs._claim_next())
    cand_a = fake.select("candidate_runs", f"project_id=eq.{project['id']}")[0]

    monkeypatch.setattr(pe2, "ENGINE_VERSION", "2.2.0-test")
    real_bridge = autoedit_bridge.bridge_from_autoedit
    def broken_bridge(*a, **k):
        raise RuntimeError("bridge died")
    monkeypatch.setattr(autoedit_bridge, "bridge_from_autoedit", broken_bridge)
    jobs.enqueue_job(project["id"], uid, "autoedit", {})
    jobs._run_job(jobs._claim_next())                        # timeline B, no cand B

    monkeypatch.setattr(autoedit_bridge, "bridge_from_autoedit", real_bridge)
    jobs.enqueue_job(project["id"], uid, "autoedit", {})
    jobs._run_job(jobs._claim_next())
    retry = sorted((r for r in fake.select("pipeline_jobs",
                                           f"project_id=eq.{project['id']}")
                    if r["kind"] == "autoedit"),
                   key=lambda r: r["created_at"])[-1]
    art = retry["artifacts"]
    assert retry["status"] == "completed"
    assert art.get("bridgeRepaired") is True                 # A did NOT satisfy reuse
    assert art["bridgedCandidateRunId"] != cand_a["id"]
    cand_b = fake.select("candidate_runs",
                         f"id=eq.{art['bridgedCandidateRunId']}")[0]
    assert _picture_timeline_id(fake, cand_b) == art["timelineId"]


def test_product_editor_opens_the_v2_candidate(monkeypatch):
    from app import main
    main._rate.clear()
    fake = FakeSupabase()
    install(monkeypatch, fake)
    monkeypatch.setenv("PICTURE_EDIT_ENGINE_V2_ENABLED", "1")
    _stub_render(monkeypatch)
    uid, token, project = _project_env(fake)
    jobs.enqueue_job(project["id"], uid, "autoedit", {})
    jobs._run_job(jobs._claim_next())
    cand = fake.select("candidate_runs", f"project_id=eq.{project['id']}")[0]
    client = TestClient(app, raise_server_exceptions=False)
    r = client.post(f"/projects/{project['id']}/editor/start",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"candidateRunId": cand["id"]})
    assert r.status_code == 200, r.text
    doc = r.json()["document"]
    picture = next(t for t in doc["tracks"] if t["type"] == "picture")
    assert len(picture["items"]) == 3                    # 25: editor-compatible


def test_engine_output_contract_fields():
    out = _build()
    for field in ("schemaVersion", "engineVersion", "projectId",
                  "editorialPlanId", "editorialPlanVersion", "sourceCatalogHash",
                  "timeline", "segmentMappings", "speedInstructions",
                  "transitionInstructions", "reframeInstructions",
                  "actualDuration", "actualDurationSeconds",
                  "requestedDuration", "requestedDurationMin",
                  "requestedDurationMax", "pacingMetrics", "continuityFindings",
                  "technicalWarnings", "unsupportedExecution",
                  "trimAdjustments", "deterministicHash", "createdAt"):
        assert field in out, field
    # pin the constant, and pin the VALUE so a bump is always a conscious act
    # (2.3.0: splice-remapped transition boundaries + per-clip segmentId)
    assert out["schemaVersion"] == 1
    assert out["engineVersion"] == pe2.ENGINE_VERSION == "2.3.0"
    # canonical fields + documented compatibility aliases carry equal values
    assert out["actualDuration"] == out["actualDurationSeconds"] == 12.0
    assert out["requestedDuration"] == {"min": None, "max": None}
    bounded = _build(request={"durationMin": 10, "durationMax": 20})
    assert bounded["requestedDuration"] == {"min": 10, "max": 20}
    assert bounded["requestedDurationMin"] == 10
    assert bounded["requestedDurationMax"] == 20


def test_engine_version_is_bound_into_the_deterministic_hash(monkeypatch):
    """Payload changes must ship with an ENGINE_VERSION bump: the version is
    part of the hash identity, so a bumped engine never collides with (or
    silently reuses) results persisted by an older engine."""
    base = _build()
    monkeypatch.setattr(pe2, "ENGINE_VERSION", "9.9.9-test")
    bumped = _build()
    assert bumped["engineVersion"] == "9.9.9-test"
    assert bumped["deterministicHash"] != base["deterministicHash"]


def test_continuity_findings_flag_repetition_and_backjumps():
    plan = _valid_plan()
    plan["timeline"].append({"segmentId": "seg-1", "assetId": "asset-1",
                             "sourceIn": 5.0, "sourceOut": 7.0,
                             "timelineIn": 12.0, "timelineOut": 14.0,
                             "beat": "payoff", "reason": "callback",
                             "addsNew": "callback", "playbackSpeed": 1.0,
                             "expectedViewerEffect": "recall"})
    plan["plannedDurationSeconds"] = 14.0
    plan["pacing"][2]["targetDurationSeconds"] = 6.0
    out = _build(plan=plan)
    findings = " | ".join(out["continuityFindings"])
    assert "used 2 times" in findings
