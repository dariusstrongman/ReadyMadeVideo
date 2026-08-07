"""Phase 3 creative intelligence — Hook V2, intelligent b-roll (plan ->
engine -> renderers -> editor), motion-graphics vocabulary, caption
refinement, audio-aware breath padding, visual rhythm, retention critic.

Contract: all flags default OFF = planner behavior unchanged (the Phase 2
golden still passes untouched apart from the documented additive keys);
every subsystem is deterministic; system-authored fields are never
model-writable.
"""
import copy
import os

import pytest

os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "fake-service-key")
os.environ["WORKER_ENABLED"] = "0"

from app.pipeline import creative_phase3 as p3  # noqa: E402
from app.pipeline import editorial_planner as ep  # noqa: E402
from app.pipeline import picture_edit_v2 as pe2  # noqa: E402
from app.pipeline.schemas import Segment, SpeechSpan, Word  # noqa: E402
from tests.test_editorial_planner import _segments, _valid_plan  # noqa: E402


@pytest.fixture(autouse=True)
def _flags_off(monkeypatch):
    for f in p3.ALL_PHASE3_FLAGS:
        monkeypatch.delenv(f, raising=False)
    monkeypatch.delenv("PHASE3_RETENTION_FLOOR", raising=False)
    for f in ("PHASE2_DIALOGUE", "PHASE2_HOOK", "PHASE2_COHERENCE",
              "PHASE2_TENSION", "PHASE2_INTEREST_GATE"):
        monkeypatch.delenv(f, raising=False)


def _plan(raw=None):
    return ep.EditorialPlan(**(raw or _valid_plan()))


def _talker(seg_id="seg-1", text="we inspect the ceiling for water damage",
            **kw):
    return Segment(segmentId=seg_id, assetId="asset-1", sourceStart=0.0,
                   sourceEnd=20.0, transcript=text,
                   speechSpans=[SpeechSpan(start=0.5, end=9.5, text=text)],
                   wordTimings=[Word(word=w, start=0.5 + i, end=1.3 + i)
                                for i, w in enumerate(text.split()[:8])],
                   **kw)


def _broll_seg(seg_id="br-1", search="ceiling water damage close up",
               **kw):
    return Segment(segmentId=seg_id, assetId="asset-2", sourceStart=0.0,
                   sourceEnd=8.0, storyUses=["broll"],
                   action=search, searchText=search, **kw)


def _long_plan(host_seconds=10.0):
    """Two-entry plan: a short hook + one long talking host clip."""
    raw = _valid_plan()
    raw["timeline"] = [
        dict(raw["timeline"][0], segmentId="seg-3", sourceIn=0.0,
             sourceOut=2.0, timelineIn=0.0, timelineOut=2.0, beat="hook"),
        dict(raw["timeline"][1], segmentId="seg-1", sourceIn=0.0,
             sourceOut=host_seconds, timelineIn=2.0,
             timelineOut=2.0 + host_seconds, beat="payoff")]
    raw["hook"]["segmentId"] = "seg-3"
    raw["hook"]["sourceOut"] = 2.0
    raw["hook"]["durationSeconds"] = 2.0
    raw["pacing"] = [{"beat": "hook", "targetDurationSeconds": 2.0,
                      "energy": 0.9},
                     {"beat": "payoff", "targetDurationSeconds": host_seconds,
                      "energy": 0.6}]
    raw["beats"] = [{"key": "hook", "purpose": "p"},
                    {"key": "payoff", "purpose": "p"}]
    raw["plannedDurationSeconds"] = 2.0 + host_seconds
    raw["audio"]["naturalSoundSegmentIds"] = []
    raw["audioTreatments"] = []
    raw["colorStabilization"] = []
    raw["transitions"] = []                # the inherited ones no longer join
    raw["storySentence"] = {               # evidence matching the real segs
        "text": "This is a story about the crew at work, leading to the "
                "finished job.",
        "claimType": "fact",
        "evidence": [{"sourceType": "transcript", "segmentId": "seg-3",
                      "quoteOrValue": "we finished the job"}]}
    return raw


# ================================================================ Hook V2
def test_banned_stock_openings_are_rejected(monkeypatch):
    monkeypatch.setenv(p3.HOOK_V2_FLAG, "1")
    seg = _talker("seg-1", "hey guys welcome back to the channel")
    raw = _valid_plan()
    plan = _plan(raw)
    out = p3.hook_v2_violations(plan, [seg] + _segments()[1:])
    assert any("stock introduction" in v for v in out)


def test_strong_opening_passes():
    seg = _talker("seg-1", "this ceiling should not be wet")
    assert p3.hook_v2_violations(_plan(), [seg] + _segments()[1:]) == []


def test_chronological_opening_needs_merit():
    # seg-1 is the earliest recorded moment AND not a ranked candidate,
    # while real candidates exist -> violation
    segs = [_talker("seg-1", "steady establishing footage of the house"),
            _talker("seg-2", "why is the floor wet again?",
                    storyUses=["hook"]),
            _talker("seg-3", "don't ever ignore stains on your ceiling",
                    storyUses=["hook"])]
    out = p3.hook_v2_violations(_plan(), segs)      # plan hooks seg-1
    assert any("chronologically first" in v for v in out)


# ======================================================== B-roll planning
def test_broll_proposed_with_lexical_match_and_confidence():
    raw = _long_plan()
    segs = [_talker("seg-1"), _broll_seg("br-1")] + _segments()[1:]
    p3.propose_broll(raw, segs)
    ins = raw.get("brollInsertions")
    assert ins and ins[0]["brollSegmentId"] == "br-1"
    assert ins[0]["motivation"] == "illustrate_claim"
    assert ins[0]["confidence"] >= p3.BROLL_MIN_CONFIDENCE
    assert set(ins[0]["matchedTokens"]) & {"ceiling", "water", "damage"}
    assert ins[0]["audioUnder"] is True
    assert ins[0]["targetIndex"] == 1               # never the hook


def test_broll_never_covers_hook_or_emotional_closeups():
    raw = _long_plan()
    crying = _talker("seg-1", "ceiling water damage everywhere",
                     emotion="crying", shotType="close")
    crying = crying.model_copy(update={"shotSize": "close"})
    segs = [crying, _broll_seg("br-1")] + _segments()[1:]
    p3.propose_broll(raw, segs)
    assert "brollInsertions" not in raw             # face is the content


def test_broll_edge_guard_and_duration_bounds():
    raw = _long_plan(host_seconds=10.0)
    segs = [_talker("seg-1"), _broll_seg("br-1")] + _segments()[1:]
    p3.propose_broll(raw, segs)
    b = raw["brollInsertions"][0]
    assert b["offsetSeconds"] >= p3.BROLL_EDGE_GUARD_SECONDS
    assert b["offsetSeconds"] + b["durationSeconds"] \
        <= 10.0 - p3.BROLL_EDGE_GUARD_SECONDS + 1e-6
    assert p3.BROLL_MIN_SECONDS <= b["durationSeconds"] \
        <= p3.BROLL_MAX_SECONDS


def test_broll_is_deterministic_and_model_cannot_write_it():
    raw1, raw2 = _long_plan(), _long_plan()
    segs = [_talker("seg-1"), _broll_seg("br-1")] + _segments()[1:]
    p3.propose_broll(raw1, segs)
    p3.propose_broll(raw2, segs)
    assert raw1["brollInsertions"] == raw2["brollInsertions"]
    fabricated = _long_plan()
    fabricated["brollInsertions"] = [{"id": "FAKE"}]
    ep._normalize_timeline_arithmetic(fabricated, segs)   # flags off
    assert "brollInsertions" not in fabricated            # system-owned


def test_provider_seam_accepts_external_sources():
    class StockProvider:
        origin = "stock"

        def candidates(self):
            return [{"segmentId": "stock-1", "assetId": "stock-asset",
                     "sourceStart": 0.0, "sourceEnd": 6.0,
                     "searchTokens": {"ceiling", "water", "damage"},
                     "origin": "stock"}]
    raw = _long_plan()
    p3.propose_broll(raw, [_talker("seg-1")] + _segments()[1:],
                     providers=[StockProvider()])
    assert raw["brollInsertions"][0]["origin"] == "stock"


def test_broll_validation_rejects_drift(monkeypatch):
    monkeypatch.setenv(p3.BROLL_FLAG, "1")
    raw = _long_plan()
    segs = [_talker("seg-1"), _broll_seg("br-1")] + _segments()[1:]
    p3.propose_broll(raw, segs)
    plan = _plan(raw)
    assert p3.broll_violations(plan, segs) == []
    # hand-authored abuse: cover the hook
    bad = copy.deepcopy(raw)
    bad["brollInsertions"][0]["targetIndex"] = 0
    assert any("covers the hook" in v
               for v in p3.broll_violations(_plan(bad), segs))
    # invented b-roll segment
    bad2 = copy.deepcopy(raw)
    bad2["brollInsertions"][0]["brollSegmentId"] = "ghost"
    assert any("invented or unusable" in v
               for v in p3.broll_violations(_plan(bad2), segs))


# ============================================== B-roll execution (engine)
def _plan_row(raw):
    return {"id": "plan-1", "project_id": "p1", "version": 1,
            "status": "approved", "plan": raw, "request": {},
            "validation": {"deterministicGate": {"passed": True,
                                                 "score": 100,
                                                 "hardFailures": []}}}


def test_engine_splits_host_and_carries_audio_under():
    raw = _long_plan()
    segs = [_talker("seg-1"), _broll_seg("br-1")] + _segments()[1:]
    p3.propose_broll(raw, segs)
    out = pe2.build_picture_edit(_plan_row(raw), segs, now="t")
    clips = out["timeline"]["tracks"][0]["clips"]
    assert len(clips) == 4                        # hook + A1 + B + A2
    b = next(c for c in clips if c.get("audioFrom"))
    ins = raw["brollInsertions"][0]
    assert b["assetId"] == "asset-2" and b["volume"] == 0.0
    assert b["audioFrom"]["assetId"] == "asset-1"     # host speech continues
    # donor audio window matches the covered host picture exactly
    host_a1 = clips[1]
    assert abs(b["audioFrom"]["sourceStart"] - host_a1["sourceEnd"]) < 1e-6
    assert abs((b["audioFrom"]["sourceEnd"] - b["audioFrom"]["sourceStart"])
               - ins["durationSeconds"]) < 1e-6
    # total duration unchanged; timeline contiguous
    assert abs(clips[-1]["timelineEnd"] - raw["plannedDurationSeconds"]) < 1e-6
    for a, bb in zip(clips, clips[1:], strict=False):
        assert abs(a["timelineEnd"] - bb["timelineStart"]) < 1e-6
    assert out["brollApplied"][0]["brollSegmentId"] == "br-1"
    assert out["engineVersion"] == pe2.ENGINE_VERSION == "2.3.0"


def test_engine_without_broll_is_unchanged():
    raw = _long_plan()
    out = pe2.build_picture_edit(_plan_row(raw),
                                 [_talker("seg-1")] + _segments()[1:],
                                 now="t")
    assert out["brollApplied"] == []
    assert len(out["timeline"]["tracks"][0]["clips"]) == 2


def test_engine_rejects_broll_catalog_drift():
    raw = _long_plan()
    segs = [_talker("seg-1"), _broll_seg("br-1")] + _segments()[1:]
    p3.propose_broll(raw, segs)
    drifted = [s for s in segs if s.segmentId != "br-1"]  # b-roll vanished
    with pytest.raises(pe2.PictureEditRejected) as exc:
        pe2.build_picture_edit(_plan_row(raw), drifted, now="t")
    assert any("absent from the current source catalog" in r
               for r in exc.value.reasons)


# ==================================================== Editor audioFrom sync
def test_editor_trim_and_split_keep_donor_audio_in_sync():
    from app.product_editor import _sync_audio_from
    clip = {"id": "b", "assetId": "asset-2", "sourceStart": 1.0,
            "sourceEnd": 3.0, "speed": 1.0,
            "audioFrom": {"assetId": "asset-1", "sourceStart": 10.0,
                          "sourceEnd": 12.0, "speed": 1.0}}
    _sync_audio_from(clip, 1.0, 1.5, 2.5)          # trim 0.5 off each end
    assert clip["audioFrom"]["sourceStart"] == 10.5
    assert clip["audioFrom"]["sourceEnd"] == 11.5
    fast = {"id": "b2", "assetId": "asset-2", "sourceStart": 0.0,
            "sourceEnd": 2.0, "speed": 1.0,
            "audioFrom": {"assetId": "asset-1", "sourceStart": 0.0,
                          "sourceEnd": 4.0, "speed": 2.0}}
    _sync_audio_from(fast, 0.0, 1.0, 2.0)          # donor advances at 2x
    assert fast["audioFrom"]["sourceStart"] == 2.0
    assert fast["audioFrom"]["sourceEnd"] == 4.0


# ============================================================== Graphics V2
def _graphic(**over):
    base = {"graphicType": "stat_card", "claimType": "fact",
            "text": "42 jobs", "evidence": [
                {"sourceType": "transcript", "segmentId": "seg-3",
                 "quoteOrValue": "we finished 42 jobs"}],
            "timelineStart": 4.0, "timelineEnd": 6.0, "durationSeconds": 2.0}
    base.update(over)
    return base


def test_graphic_type_vocabulary_is_closed(monkeypatch):
    raw = _valid_plan()
    raw["graphics"] = [_graphic(graphicType="sparkle_explosion")]
    out = p3.graphics_violations(_plan(raw), _segments())
    assert any("not in the renderer-ready vocabulary" in v for v in out)


def test_stat_card_needs_a_spoken_number():
    raw = _valid_plan()
    raw["graphics"] = [_graphic(text="lots of jobs", evidence=[
        {"sourceType": "transcript", "segmentId": "seg-3",
         "quoteOrValue": "we finished the job"}])]
    out = p3.graphics_violations(_plan(raw), _segments())
    assert any("needs data" in v for v in out)
    assert p3.graphics_violations(_plan({**_valid_plan(),
                                         "graphics": [_graphic()]}),
                                  _segments()) == []


def test_graphics_density_ceiling():
    raw = _valid_plan()
    raw["graphics"] = [_graphic(timelineStart=2.0, timelineEnd=3.0),
                       _graphic(timelineStart=5.0, timelineEnd=6.0)]
    out = p3.graphics_violations(_plan(raw), _segments())
    assert any("minimum spacing" in v.lower() for v in out)


def test_graphics_opportunities_detected_from_catalog():
    segs = [_talker("seg-1", "this repair cost 4000 dollars versus 500 "
                             "for prevention")]
    kinds = {s["graphicType"] for s in p3.propose_graphics(segs)}
    assert "stat_card" in kinds and "comparison" in kinds


# ============================================================== Captions V2
def test_long_caption_splits_at_clause_with_time_split():
    raw = {"captions": [{
        "claimType": "editorial_label",
        "text": "The Setup Begins Here, and the final reveal comes after",
        "timelineStart": 0.0, "timelineEnd": 4.0}]}
    p3.refine_captions(raw, [])
    caps = raw["captions"]
    assert len(caps) == 2
    assert caps[0]["timelineEnd"] == caps[1]["timelineStart"]
    assert caps[0]["timelineEnd"] < 4.0
    assert all(len(c["text"]) <= p3.CAPTION_MAX_LINE_CHARS for c in caps)


def test_emphasis_word_priority_number_then_negation_then_substantive():
    assert p3._emphasis_word("we finished 42 jobs") == "42"
    assert p3._emphasis_word("never ignore the ceiling") == "never"
    assert p3._emphasis_word("the crew keeps working") == "working"
    assert p3._emphasis_word("we do it") is None


def test_short_caption_extended_to_readability_floor():
    raw = {"captions": [
        {"claimType": "cta", "text": "Watch", "timelineStart": 1.0,
         "timelineEnd": 1.3},
        {"claimType": "cta", "text": "The End", "timelineStart": 3.0,
         "timelineEnd": 4.0}]}
    p3.refine_captions(raw, [])
    assert raw["captions"][0]["timelineEnd"] \
        >= 1.0 + p3.CAPTION_MIN_SECONDS - 1e-6


def test_caption_wall_and_floor_violations(monkeypatch):
    raw = _valid_plan()
    raw["captions"] = [
        {"claimType": "editorial_label", "text": "w" * 90,
         "timelineStart": 0.0, "timelineEnd": 0.4, "evidence": []}]
    out = p3.caption_v2_violations(_plan(raw))
    assert any("subtitle wall" in v for v in out)
    assert any("readability floor" in v for v in out)


# ============================================================ Audio-aware
def test_breath_pad_extends_sentence_end_cuts():
    seg = _talker("seg-1", "we inspect the ceiling")
    seg = seg.model_copy(update={
        "speechSpans": [SpeechSpan(start=0.5, end=5.0,
                                   text="we inspect the ceiling")]})
    raw = {"timeline": [{"segmentId": "seg-1", "assetId": "asset-1",
                         "sourceIn": 0.0, "sourceOut": 5.0,
                         "timelineIn": 0.0, "timelineOut": 5.0,
                         "beat": "hook"}]}
    p3.breath_pad_cuts(raw, [seg])
    assert raw["timeline"][0]["sourceOut"] == 5.0 + p3.BREATH_PAD_SECONDS
    assert raw["dialogueAdjustments"][0]["reason"] \
        == "breath room after sentence end"


def test_breath_pad_respects_reserved_transition_handles():
    seg = _talker("seg-1").model_copy(update={
        "sourceEnd": 5.1,
        "speechSpans": [SpeechSpan(start=0.5, end=5.0, text="x")]})
    raw = {"timeline": [
        {"segmentId": "seg-1", "assetId": "asset-1", "sourceIn": 0.0,
         "sourceOut": 5.0, "timelineIn": 0.0, "timelineOut": 5.0,
         "beat": "hook"},
        {"segmentId": "seg-2", "assetId": "asset-1", "sourceIn": 6.0,
         "sourceOut": 8.0, "timelineIn": 5.0, "timelineOut": 7.0,
         "beat": "next"}],
        "transitions": [{"fromSegmentId": "seg-1", "toSegmentId": "seg-2",
                         "type": "dissolve", "durationSeconds": 0.1,
                         "purpose": "soften"}]}
    p3.breath_pad_cuts(raw, [seg])
    # only 0.1s of unreserved room exists (5.1 end - 0.1 reserve = 5.0):
    # padding would eat the dissolve's handle, so the cut stays put
    assert raw["timeline"][0]["sourceOut"] == 5.0


def test_breath_pad_runs_before_arithmetic_rebuild(monkeypatch):
    monkeypatch.setenv(p3.AUDIO_FLAG, "1")
    seg = _talker("seg-1").model_copy(update={
        "speechSpans": [SpeechSpan(start=0.5, end=4.0, text="x")]})
    raw = _valid_plan()
    raw["timeline"][0]["segmentId"] = "seg-1"
    raw["hook"]["segmentId"] = "seg-1"
    ep._normalize_timeline_arithmetic(raw, [seg] + _segments()[1:])
    e = raw["timeline"][0]
    assert e["sourceOut"] == 4.0 + p3.BREATH_PAD_SECONDS
    # arithmetic was rebuilt FROM the padded trim — no stale bookkeeping
    assert abs((e["timelineOut"] - e["timelineIn"])
               - (e["sourceOut"] - e["sourceIn"])) < 1e-6
    plan = _plan(raw)
    timing = [v for v in ep.validate_plan(plan, [seg] + _segments()[1:], {},
                                          False)
              if "not contiguous" in v or "does not match" in v]
    assert timing == []


# ================================================================= Rhythm
def test_rhythm_flags_monotony_and_metronome(monkeypatch):
    segs = [s.model_copy(update={"shotSize": "medium"}) for s in _segments()]
    segs.append(_segments()[0].model_copy(update={"segmentId": "seg-4",
                                                  "shotSize": "wide"}))
    raw = _valid_plan()
    raw["timeline"] = raw["timeline"] + [
        dict(raw["timeline"][0], segmentId="seg-1", sourceIn=4.0,
             sourceOut=8.0, timelineIn=12.0, timelineOut=16.0,
             beat="payoff")]
    out = p3.rhythm_violations(_plan(raw), segs)
    assert any("consecutive shots" in v for v in out)      # 4x medium
    assert any("metronomic" in v for v in out)             # all 4.0s


def test_rhythm_report_suggests_punch_ins_on_long_talkers():
    raw = _long_plan(host_seconds=9.0)
    rep = p3.rhythm_report(_plan(raw), [_talker("seg-1")] + _segments()[1:])
    assert rep["punchInSuggestions"]
    assert rep["punchInSuggestions"][0]["timelineIndex"] == 1


# ========================================================= Retention critic
def test_retention_report_axes_and_actionable_advice():
    rep = p3.retention_report(_plan(), _segments())
    assert {a["axis"] for a in rep["axes"]} == set(p3.CRITIC_AXES)
    assert all(0 <= a["score"] <= 100 for a in rep["axes"])
    assert all(a["advice"] for a in rep["axes"])
    assert isinstance(rep["wouldKeepWatching"], bool)
    assert rep["rhythm"]["shotCount"] == 3


def test_retention_critic_is_advisory_without_a_floor(monkeypatch):
    monkeypatch.setenv(p3.CRITIC_FLAG, "1")
    result = ep.plan_editorial(_segments(), {}, False,
                               lambda p, s: copy.deepcopy(_valid_plan()))
    assert "retentionReport" in result
    assert result["status"] == "approved"          # no floor -> never blocks


def test_retention_floor_turns_weak_axes_into_revise_feedback(monkeypatch):
    monkeypatch.setenv(p3.CRITIC_FLAG, "1")
    monkeypatch.setenv("PHASE3_RETENTION_FLOOR", "101")   # unreachable
    with pytest.raises(ep.PlanRejected) as exc:
        ep.plan_editorial(_segments(), {}, False,
                          lambda p, s: copy.deepcopy(_valid_plan()),
                          max_attempts=2)
    last = exc.value.violations_history[-1]
    assert any(v.startswith("retention:") for v in last)
    assert any("below the floor" in v for v in last)


# =========================================================== flags-off inertia
def test_all_flags_off_contributes_nothing():
    assert p3.phase3_violations(_plan(), _segments()) == []
    assert p3.prompt_parts(_segments()) == []
    raw = _long_plan()
    segs = [_talker("seg-1"), _broll_seg("br-1")] + _segments()[1:]
    ep._normalize_timeline_arithmetic(raw, segs)   # all flags off
    assert "brollInsertions" not in raw
    assert "emphasisWord" not in (raw.get("captions") or [{}])[0] \
        if raw.get("captions") else True


def test_audio_under_broll_renders_in_the_FINAL_renderer(tmp_path):
    """Parity by construction: the b-roll clip's audioFrom is honored by
    renderer2 — the FINAL export path — with real ffmpeg. The b-roll shows
    its own picture while the donor's audio continues underneath."""
    import subprocess

    from app.renderer import FFMPEG, probe
    from app.renderer2 import render_timeline
    host = str(tmp_path / "host.mp4")
    broll = str(tmp_path / "broll.mp4")
    subprocess.run([FFMPEG, "-y", "-loglevel", "error",
                    "-f", "lavfi", "-i", "testsrc=duration=6:size=128x72:rate=12",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
                    "-c:v", "libx264", "-c:a", "aac", "-shortest", host],
                   check=True, timeout=120)
    subprocess.run([FFMPEG, "-y", "-loglevel", "error",
                    "-f", "lavfi", "-i", "smptebars=duration=4:size=128x72:rate=12",
                    "-c:v", "libx264", broll], check=True, timeout=120)
    timeline = {"version": 1, "width": 128, "height": 72, "fps": 12,
                "duration": 5.0, "tracks": [{"id": "v", "type": "video",
                                             "clips": [
        {"id": "a1", "assetId": "H", "sourceStart": 0.0, "sourceEnd": 1.5,
         "timelineStart": 0.0, "timelineEnd": 1.5, "speed": 1, "volume": 1},
        {"id": "b", "assetId": "B", "sourceStart": 0.0, "sourceEnd": 2.0,
         "timelineStart": 1.5, "timelineEnd": 3.5, "speed": 1, "volume": 0,
         "audioFrom": {"assetId": "H", "sourceStart": 1.5, "sourceEnd": 3.5,
                       "speed": 1.0}},
        {"id": "a2", "assetId": "H", "sourceStart": 3.5, "sourceEnd": 5.0,
         "timelineStart": 3.5, "timelineEnd": 5.0, "speed": 1, "volume": 1},
    ]}]}
    out = str(tmp_path / "out.mp4")
    render_timeline(timeline, {"H": host, "B": broll}, out, profile="preview")
    info = probe(out)
    assert abs(info.duration - 5.0) < 0.6         # duration preserved
    assert info.has_audio
    # the donor's tone must actually be AUDIBLE inside the b-roll window —
    # whole-file has_audio passed even when the donor branch produced
    # silence (the surrounding host clips carry audio either way)
    import re as _re
    det = subprocess.run(
        [FFMPEG, "-hide_banner", "-ss", "1.8", "-to", "3.2", "-i", out,
         "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, timeout=120)
    text = det.stderr.decode(errors="replace")
    m = _re.search(r"mean_volume: (-?[\d.]+) dB", text)
    assert m, text[-400:]
    assert float(m.group(1)) > -60.0              # speech, not silence


def _three_entry_plan_with_dissolve(host_seconds=10.0):
    """Hook + long talking host + short closing shot, dissolve host->close."""
    raw = _long_plan(host_seconds)
    raw["timeline"].append(
        dict(raw["timeline"][1], segmentId="seg-2", sourceIn=1.0,
             sourceOut=3.0, timelineIn=2.0 + host_seconds,
             timelineOut=4.0 + host_seconds, beat="payoff"))
    raw["pacing"][1]["targetDurationSeconds"] = host_seconds + 2.0
    raw["plannedDurationSeconds"] = 4.0 + host_seconds
    raw["transitions"] = [{"fromSegmentId": "seg-1", "toSegmentId": "seg-2",
                           "type": "dissolve", "durationSeconds": 0.3,
                           "purpose": "soften the move to the closing shot"}]
    return raw


def test_transition_boundary_remaps_after_broll_splice():
    """Audit blocker: after a b-roll splice the dissolve keyed on the PLAN
    boundary index landed at the host->b-roll join instead of host->next."""
    raw = _three_entry_plan_with_dissolve()
    segs = [_talker("seg-1"), _broll_seg("br-1")] + _segments()[1:]
    p3.propose_broll(raw, segs)
    assert raw["brollInsertions"]                     # the splice will happen
    out = pe2.build_picture_edit(_plan_row(raw), segs, now="t")
    clips = out["timeline"]["tracks"][0]["clips"]
    assert [c["segmentId"] for c in clips] == \
        ["seg-3", "seg-1", "br-1", "seg-1", "seg-2"]
    inst = out["transitionInstructions"][0]
    assert inst["status"] == "executable"
    assert inst["planBoundaryIndex"] == 1             # plan-entry join
    assert inst["boundaryIndex"] == 3                 # final clip boundary
    # the renderer xfades exactly where the host hands off to the closer
    assert clips[inst["boundaryIndex"]]["segmentId"] == "seg-1"
    assert clips[inst["boundaryIndex"] + 1]["segmentId"] == "seg-2"


def test_transition_boundary_unchanged_without_splice():
    raw = _three_entry_plan_with_dissolve()
    out = pe2.build_picture_edit(_plan_row(raw),
                                 [_talker("seg-1")] + _segments()[1:],
                                 now="t")
    inst = out["transitionInstructions"][0]
    assert inst["boundaryIndex"] == inst["planBoundaryIndex"] == 1


def test_final_renderer_registers_orphan_donor_input(monkeypatch):
    """An editor delete can leave a b-roll clip whose audioFrom donor asset
    is referenced by NO picture clip — the donor must still be an ffmpeg
    input, or the audio branch silently falls back and the speech is lost."""
    from types import SimpleNamespace

    from app import renderer2 as r2
    monkeypatch.setattr(r2, "probe", lambda p: SimpleNamespace(
        duration=100.0, has_audio=True, width=128, height=72))
    timeline = {"version": 1, "width": 128, "height": 72, "fps": 12,
                "tracks": [{"id": "v", "type": "video", "clips": [
                    {"id": "b", "assetId": "B", "sourceStart": 0.0,
                     "sourceEnd": 2.0, "timelineStart": 0.0,
                     "timelineEnd": 2.0, "speed": 1, "volume": 0,
                     "audioFrom": {"assetId": "H", "sourceStart": 5.0,
                                   "sourceEnd": 7.0, "speed": 1.0}}]}]}
    compiled = r2.compile_timeline(
        timeline, {"B": "broll.mp4", "H": "host.mp4"}, "out.mp4")
    inputs = [compiled.cmd[i + 1] for i, a in enumerate(compiled.cmd)
              if a == "-i"]
    assert "host.mp4" in inputs                       # donor registered
    fc = compiled.cmd[compiled.cmd.index("-filter_complex") + 1]
    d_idx = inputs.index("host.mp4")
    assert f"[{d_idx}:a]atrim=start=5.000:end=7.000" in fc
    assert "anullsrc" not in fc                       # speech, not silence


def test_preview_renderer_orphan_donor_and_spliced_crop(monkeypatch,
                                                        tmp_path):
    from types import SimpleNamespace

    import app.pipeline.picture_render_v2 as prv
    from app import renderer2 as r2
    monkeypatch.setattr(prv, "probe", lambda p: SimpleNamespace(
        duration=100.0, has_audio=True))
    captured = {}

    def fake_run(cmd, timeout, cancel_check=None, out_path=None, tick=None):
        captured["cmd"] = cmd
        with open(out_path, "wb") as fh:
            fh.write(b"x")
        return 0, b""

    monkeypatch.setattr(r2, "_run_interruptible", fake_run)
    result = {
        "timeline": {"width": 1920, "height": 1080, "fps": 30,
                     "tracks": [{"id": "v", "type": "video", "clips": [
                         {"id": "pe2b-001-br-1", "segmentId": "br-1",
                          "assetId": "B", "sourceStart": 0.0,
                          "sourceEnd": 2.0, "timelineStart": 0.0,
                          "timelineEnd": 2.0, "speed": 1.0, "volume": 0.0,
                          "audioFrom": {"assetId": "H", "sourceStart": 5.0,
                                        "sourceEnd": 7.0, "speed": 1.0}}]}]},
        "transitionInstructions": [],
        # a crop authored for the b-roll segment; the stale POSITIONAL
        # mapping points elsewhere — the clip's own segmentId must win
        "reframeInstructions": [
            {"segmentId": "br-1", "status": "executable", "mode": "static",
             "startCrop": {"x": 0.1, "y": 0.1, "width": 0.5, "height": 0.5},
             "endCrop": {"x": 0.1, "y": 0.1, "width": 0.5, "height": 0.5}}],
        "segmentMappings": [{"segmentId": "seg-OTHER"}]}
    out = str(tmp_path / "out.mp4")
    prv.render_picture_edit(result, {"B": "broll.mp4", "H": "host.mp4"}, out)
    cmd = captured["cmd"]
    inputs = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-i"]
    assert "host.mp4" in inputs                       # orphan donor registered
    fc = cmd[cmd.index("-filter_complex") + 1]
    d_idx = inputs.index("host.mp4")
    assert f"[{d_idx}:a]atrim=start=5.000:end=7.000" in fc
    assert "anullsrc" not in fc
    assert "crop=w=iw*0.5000" in fc                   # crop followed the clip


def test_model_supplied_emphasis_word_is_stripped():
    raw = _valid_plan()
    raw["captions"] = [{"claimType": "editorial_label", "text": "The Setup",
                        "timelineStart": 0.0, "timelineEnd": 2.0,
                        "emphasisWord": "Sneaky", "evidence": []}]
    ep._normalize_timeline_arithmetic(raw, _segments())   # flags off
    assert all("emphasisWord" not in c for c in raw.get("captions") or [])


def test_broll_protects_emotional_segments_with_unknown_shot_size():
    # shotSize "" (analysis could not tell) on an emotional segment is
    # treated as protected — when framing is unknown, covering is the risk
    raw = _long_plan()
    crying = _talker("seg-1", "ceiling water damage everywhere",
                     emotion="crying")
    assert crying.shotSize == ""
    assert p3._face_protected(crying)
    segs = [crying, _broll_seg("br-1")] + _segments()[1:]
    p3.propose_broll(raw, segs)
    assert "brollInsertions" not in raw
    wide = crying.model_copy(update={"shotSize": "wide"})
    assert not p3._face_protected(wide)               # visible context: fine


# ==================================================== audit-round-2 fixes
def test_banned_openings_catch_transcribed_variants():
    bad = ["Today, we're going to look at the ceiling",
           "So, today we're building a deck",
           "Hey, guys, look at this",
           "What is up guys",
           "my name's Darius",
           "Um, today we're checking the attic",
           "Before we get started",
           "today were going to show you",
           "Good morning everyone",
           "Let me introduce myself"]
    for t in bad:
        assert p3.BANNED_OPENINGS.match(t), t


def test_banned_openings_spare_real_story_lines():
    good = ["welcome to the hardest job we ever took",
            "my name is on the lawsuit and I never signed it",
            "what's up with this ceiling",
            "so today the floor gave out",
            "Today the ceiling collapsed",
            "this ceiling should not be wet"]
    for t in good:
        assert not p3.BANNED_OPENINGS.match(t), t


def test_stock_line_outside_the_hook_cut_is_not_flagged():
    # the greeting is spoken at 0.5-3.0s but the hook CUT is 10-14s — the
    # viewer never hears it, so it must not be ruled a stock opening
    seg = _talker("seg-1", "hey guys welcome back to the channel")
    raw = _valid_plan()
    raw["hook"].update({"sourceIn": 10.0, "sourceOut": 14.0,
                        "durationSeconds": 4.0})
    out = p3.hook_v2_violations(_plan(raw), [seg] + _segments()[1:])
    assert not any("stock introduction" in v for v in out)


def test_chronology_rule_stands_down_across_assets():
    # across assets there is no creation-time data; lexicographic assetId
    # order must not masquerade as chronology
    segs = [_talker("seg-1", "steady establishing footage of the house")
            .model_copy(update={"assetId": "asset-a"}),
            _talker("seg-2", "why is the floor wet again?",
                    storyUses=["hook"]),
            _talker("seg-3", "don't ignore ceiling stains",
                    storyUses=["hook"])]
    out = p3.hook_v2_violations(_plan(), segs)
    assert not any("chronologically first" in v for v in out)


def test_broll_rounding_never_trips_its_own_edge_guard():
    # host durations landing at sub-ms precision (NTSC-rate footage) made
    # the proposer emit values its own validator rejected
    raw = _long_plan(host_seconds=4.0006)
    segs = [_talker("seg-1"), _broll_seg("br-1")] + _segments()[1:]
    p3.propose_broll(raw, segs)
    assert raw.get("brollInsertions")
    assert p3.broll_violations(_plan(raw), segs) == []


def test_single_generic_token_does_not_clear_the_confidence_floor():
    raw = _long_plan()
    lonely = _broll_seg("br-1", search="ceiling")     # one shared token
    p3.propose_broll(raw, [_talker("seg-1"), lonely] + _segments()[1:])
    ins = raw.get("brollInsertions") or []
    assert not any(i["motivation"] == "illustrate_claim" for i in ins)


def test_number_captions_are_not_split_or_torn():
    raw = {"captions": [{
        "claimType": "editorial_label",
        "text": "the ceiling repair job cost 1,000 dollars in total for us",
        "timelineStart": 0.0, "timelineEnd": 4.0}]}
    p3.refine_captions(raw, [])
    caps = raw["captions"]
    assert all("1,000" in c["text"] for c in caps if "1" in c["text"])
    assert caps[0]["emphasisWord"] == "1,000"
    assert p3._emphasis_word("we saved 1,200 dollars") == "1,200"


def test_caption_extension_never_passes_the_end_of_the_video():
    raw = {"plannedDurationSeconds": 12.0,
           "captions": [{"claimType": "cta", "text": "Watch",
                         "timelineStart": 11.7, "timelineEnd": 12.0}]}
    p3.refine_captions(raw, [])
    assert raw["captions"][0]["timelineEnd"] == 12.0   # clamped, not broken


def test_out_of_order_captions_are_never_inverted():
    raw = {"captions": [
        {"claimType": "cta", "text": "Second", "timelineStart": 5.0,
         "timelineEnd": 5.2},
        {"claimType": "cta", "text": "First", "timelineStart": 1.0,
         "timelineEnd": 3.0}]}
    p3.refine_captions(raw, [])
    for c in raw["captions"]:
        assert c["timelineEnd"] > c["timelineStart"]
    second = next(c for c in raw["captions"] if c["text"] == "Second")
    assert second["timelineEnd"] == 5.83


def test_split_never_manufactures_subfloor_captions():
    raw = {"captions": [{
        "claimType": "editorial_label",
        "text": "the ceiling came down hard, and everyone ran",
        "timelineStart": 0.0, "timelineEnd": 1.0}]}
    p3.refine_captions(raw, [])
    assert len(raw["captions"]) == 1        # 1.0s cannot host two readable


def test_breath_pad_never_duplicates_adjacent_same_segment_footage():
    seg = _talker("seg-1").model_copy(update={
        "speechSpans": [SpeechSpan(start=0.5, end=5.0, text="x")]})
    raw = {"timeline": [
        {"segmentId": "seg-1", "assetId": "asset-1", "sourceIn": 0.0,
         "sourceOut": 5.0, "timelineIn": 0.0, "timelineOut": 5.0,
         "beat": "hook"},
        {"segmentId": "seg-1", "assetId": "asset-1", "sourceIn": 5.0,
         "sourceOut": 9.0, "timelineIn": 5.0, "timelineOut": 9.0,
         "beat": "next"}]}
    p3.breath_pad_cuts(raw, [seg])
    # padding entry 0 would replay 0.2s the next entry already shows
    assert raw["timeline"][0]["sourceOut"] == 5.0


def test_metronome_rule_allows_varied_framing_montage():
    sizes = ["wide", "medium", "close", "wide", "medium", "close"]
    raw = _valid_plan()
    base = raw["timeline"][0]
    raw["timeline"] = [dict(base, segmentId=f"seg-{i + 1}", sourceIn=0.0,
                            sourceOut=2.0, timelineIn=2.0 * i,
                            timelineOut=2.0 * (i + 1)) for i in range(6)]
    segs = [_talker(f"seg-{i + 1}").model_copy(update={"shotSize": sizes[i]})
            for i in range(6)]
    out = p3.rhythm_violations(_plan(raw), segs)
    assert not any("metronomic" in v for v in out)   # beat-synced montage


def test_three_shot_edits_are_not_judged_for_rhythm():
    segs = [s.model_copy(update={"shotSize": "medium"}) for s in _segments()]
    assert p3.rhythm_violations(_plan(), segs) == []   # n=3: too small


def test_phase3_craft_violations_do_not_fail_the_truth_gate():
    # RC-4: advisory retention prose contains the word "hook"; substring
    # matching in the gate failed the HARD hook_grounded rule over it
    violations = [
        "retention: curiosity scored 10 - open a loop the hook promises "
        "and pay it off late",
        "retention: overall 46 is below the floor 90",
        "hook_v2: the hook's opening line opens with a stock introduction"]
    gate = ep.deterministic_gate(_plan(), _segments(), {}, violations)
    hook_rule = next(r for r in gate["rules"] if r["rule"] == "hook_grounded")
    assert hook_rule["passed"]
    assert "hook_grounded" not in gate["hardFailures"]


def test_operator_timeline_ops_keep_donor_audio_in_sync():
    from app import timeline_ops as tops
    tl = {"version": 1, "width": 128, "height": 72, "fps": 12,
          "duration": 8.0,
          "tracks": [{"id": "video-1", "type": "video", "clips": [
              {"id": "a1", "assetId": "H", "sourceStart": 0.0,
               "sourceEnd": 3.5, "timelineStart": 0.0, "timelineEnd": 3.5,
               "speed": 1, "volume": 1},
              {"id": "b", "assetId": "B", "sourceStart": 0.0,
               "sourceEnd": 2.0, "timelineStart": 3.5, "timelineEnd": 5.5,
               "speed": 1, "volume": 0,
               "audioFrom": {"assetId": "H", "sourceStart": 3.5,
                             "sourceEnd": 5.5, "speed": 1.0}},
              {"id": "a2", "assetId": "H", "sourceStart": 5.5,
               "sourceEnd": 8.0, "timelineStart": 5.5, "timelineEnd": 8.0,
               "speed": 1, "volume": 1}]}]}
    ops = tops.parse_operations([
        {"op": "trim_clip", "clipId": "b", "sourceStart": 0.5,
         "sourceEnd": 1.5},
        {"op": "change_speed", "clipId": "b", "speed": 2.0}])
    res = tops.apply_operations(tl, ops, "user")
    assert res.rejected == []
    b = next(c for t in res.timeline["tracks"] for c in t["clips"]
             if c["id"] == "b")
    # trim moved the window; speed change re-locked its length to out_dur
    assert b["audioFrom"]["sourceStart"] == 4.0
    assert b["audioFrom"]["sourceEnd"] == 4.5


def test_forged_donor_asset_rejected_at_document_validation():
    from pydantic import ValidationError

    from app.product_editor import EditorDocument
    from tests.test_product_editor import make_document
    doc = make_document()
    pic = next(t for t in doc["tracks"] if t["type"] == "picture")
    pic["items"][0]["audioFrom"] = {
        "assetId": "00000000-0000-0000-0000-0000deadbeef",
        "sourceStart": 0.0, "sourceEnd": 1.0, "speed": 1.0}
    with pytest.raises(ValidationError, match="donor"):
        EditorDocument(**doc)
    pic["items"][0]["audioFrom"]["assetId"] = str(doc["sourceAssetIds"][0])
    EditorDocument(**doc)                                # legitimate donor


def test_preview_renderer_xfade_after_concat_renders(tmp_path):
    """P0: concat outputs tb 1/1000000 while fps left 1/fps — a dissolve at
    any boundary past the first failed ffmpeg outright. settb normalizes."""
    import subprocess

    from app.pipeline.picture_render_v2 import render_picture_edit
    from app.renderer import FFMPEG, probe
    host = str(tmp_path / "host.mp4")
    subprocess.run([FFMPEG, "-y", "-loglevel", "error",
                    "-f", "lavfi", "-i",
                    "testsrc=duration=8:size=128x72:rate=12",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=8",
                    "-c:v", "libx264", "-c:a", "aac", "-shortest", host],
                   check=True, timeout=120)
    clips = [{"id": f"c{i}", "assetId": "H", "sourceStart": 2.0 * i,
              "sourceEnd": 2.0 * (i + 1), "timelineStart": 2.0 * i,
              "timelineEnd": 2.0 * (i + 1), "speed": 1.0, "volume": 1.0}
             for i in range(3)]
    result = {"timeline": {"width": 128, "height": 72, "fps": 12,
                           "tracks": [{"id": "v", "type": "video",
                                       "clips": clips}]},
              "transitionInstructions": [
                  {"boundaryIndex": 1, "status": "executable",
                   "type": "dissolve", "durationSeconds": 0.3,
                   "fromSegmentId": "s2", "toSegmentId": "s3",
                   "purpose": "soften"}],
              "reframeInstructions": [], "segmentMappings": []}
    out = str(tmp_path / "out.mp4")
    render_picture_edit(result, {"H": host}, out)
    assert abs(probe(out).duration - 6.0) < 0.6


def test_full_planner_run_with_all_phase3_flags(monkeypatch):
    for f in p3.ALL_PHASE3_FLAGS:
        monkeypatch.setenv(f, "1")
    raw = _long_plan()
    segs = [_talker("seg-1"), _broll_seg("br-1")] + _segments()[1:]
    result = ep.plan_editorial(segs, {}, False,
                               lambda p, s: copy.deepcopy(raw))
    assert result["status"] == "approved"
    assert result["plan"]["brollInsertions"]          # planned + validated
    assert "retentionReport" in result
