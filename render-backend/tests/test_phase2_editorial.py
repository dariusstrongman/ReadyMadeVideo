"""Phase 2 editorial intelligence — substrate, dialogue integrity, hook
engine, loop ledger, caption coherence, tension shape, interest gate.

Contract under test (docs/PHASE2_EDITORIAL_INTELLIGENCE.md):
  * with every flag OFF, planner behavior is byte-identical to Phase 1
  * checks can only ADD violations — the truth gate is never weakened
  * every check is deterministic (no model call, no clock, no randomness)
"""
import copy
import os

import pytest

os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "fake-service-key")
os.environ["WORKER_ENABLED"] = "0"

from app.pipeline import catalog as catalog_mod  # noqa: E402
from app.pipeline import editorial_phase2 as p2  # noqa: E402
from app.pipeline import editorial_planner as ep  # noqa: E402
from app.pipeline.schemas import (  # noqa: E402
    AudioArtifact, MechanicalArtifact, MotionArtifact, MotionScene,
    SceneMechanical, SceneRange, ScenesArtifact, Segment, SemanticArtifact,
    SemanticSegment, Sentence, SpeechSpan, TranscriptArtifact, Word,
)
from tests.test_editorial_planner import _segments, _valid_plan  # noqa: E402

ALL_FLAGS = (p2.DIALOGUE_FLAG, p2.HOOK_FLAG, p2.COHERENCE_FLAG,
             p2.TENSION_FLAG, p2.INTEREST_FLAG)


@pytest.fixture(autouse=True)
def _flags_off(monkeypatch):
    for f in ALL_FLAGS:
        monkeypatch.delenv(f, raising=False)
    monkeypatch.delenv("PHASE2_INTEREST_THRESHOLD", raising=False)


def _plan(raw=None):
    return ep.EditorialPlan(**(raw or _valid_plan()))


def _speech_segment(seg_id="seg-1", asset="asset-1"):
    """10s segment: sentence 1 at 1.0-3.0, sentence 2 at 4.0-6.0."""
    words = [Word(word="the", start=1.0, end=1.4),
             Word(word="crew", start=1.4, end=2.0),
             Word(word="works", start=2.0, end=3.0),
             Word(word="job", start=4.0, end=4.6),
             Word(word="done", start=4.6, end=6.0)]
    spans = [SpeechSpan(start=1.0, end=3.0, text="the crew works"),
             SpeechSpan(start=4.0, end=6.0, text="job done")]
    return Segment(segmentId=seg_id, assetId=asset, sourceStart=0.0,
                   sourceEnd=10.0, transcript="the crew works job done",
                   speechSpans=spans, wordTimings=words,
                   speechFreeRanges=[SpeechSpan(start=0.0, end=1.0),
                                     SpeechSpan(start=3.0, end=4.0),
                                     SpeechSpan(start=6.0, end=10.0)])


# ---------------------------------------------------------------- substrate
def test_catalog_derives_narrative_substrate():
    scenes = ScenesArtifact(detector="content", threshold=27.0,
                            scenes=[SceneRange(index=0, start=0.0, end=10.0)])
    mech = MechanicalArtifact(sampled_fps=4, scenes=[SceneMechanical(
        index=0, start=0.0, end=10.0, blur_laplacian_var=100, focus_score=0.8,
        exposure_mean=120, exposure_clipped_low=0, exposure_clipped_high=0,
        exposure_score=0.8, black_frame_fraction=0, frozen_frame_fraction=0,
        motion_energy_mean=5, motion_energy_peak=9, shake_score=0.9,
        phash="0" * 16)])
    transcript = TranscriptArtifact(
        provider="test",
        words=[Word(word="hello", start=1.0, end=1.5),
               Word(word="world", start=1.5, end=2.0),
               Word(word="offscreen", start=11.0, end=12.0)],
        sentences=[Sentence(text="hello world", start=1.0, end=2.0),
                   Sentence(text="offscreen", start=11.0, end=12.0)])
    semantic = SemanticArtifact(provider="test", model="test", segments=[
        SemanticSegment(scene_index=0, action="crew inspects",
                        shot_type="Close-up", composition="rule of thirds",
                        continuity="same blue shirt",
                        natural_sound_value=0.7,
                        search_description="crew inspecting")])
    motion = MotionArtifact(method="opencv-framediff", sampled_fps=12,
                            scenes=[MotionScene(index=0, start=0.0, end=10.0,
                                                motion_intensity=0.5,
                                                peak_moments=[2.5, 11.5],
                                                stationary_ranges=[[7.0, 9.0]])])
    segs = catalog_mod.build_catalog("asset-1", scenes, mech,
                                     audio=AudioArtifact(has_audio=True),
                                     transcript=transcript, semantic=semantic,
                                     motion=motion)
    s = segs[0]
    assert [sp.text for sp in s.speechSpans] == ["hello world"]
    assert [w.word for w in s.wordTimings] == ["hello", "world"]
    # gaps: before speech (0-1) and after it (2-10)
    assert [(g.start, g.end) for g in s.speechFreeRanges] == [(0.0, 1.0),
                                                             (2.0, 10.0)]
    assert s.motionPeaks == [2.5]              # 11.5 is outside the segment
    assert s.stationaryRanges == [[7.0, 9.0]]
    assert s.composition == "rule of thirds"
    assert s.continuity == "same blue shirt"
    assert s.naturalSoundValue == 0.7
    assert s.shotSize == "close"


def test_shot_size_normalizes_the_eight_production_spellings():
    for raw, want in (("close", "close"), ("close-up", "close"),
                      ("Close-up", "close"), ("medium", "medium"),
                      ("medium shot", "medium"), ("Medium shot", "medium"),
                      ("wide shot", "wide"), ("Wide shot", "wide"),
                      ("", ""), ("dutch angle", "")):
        assert catalog_mod._shot_size(raw) == want, raw


def test_v2_catalog_rows_load_unchanged():
    row = {"segmentId": "s", "assetId": "a", "sourceStart": 0.0,
           "sourceEnd": 5.0}
    seg = Segment(**row)                       # no v3 fields anywhere
    assert seg.speechSpans == [] and seg.wordTimings == []
    assert seg.shotSize == "" and seg.motionPeaks == []


# --------------------------------------------------------- dialogue integrity
def test_word_severed_detection_respects_boundaries():
    seg = _speech_segment()
    assert p2.word_severed_at(seg, 1.7).word == "crew"
    assert p2.word_severed_at(seg, 1.4) is None     # exact boundary is safe
    assert p2.word_severed_at(seg, 3.5) is None     # inter-sentence gap
    assert p2.word_severed_at(seg, 0.5) is None     # before any speech


def test_snap_moves_a_severing_cut_to_the_nearest_boundary():
    seg = _speech_segment()
    raw = {"timeline": [{"segmentId": "seg-1", "assetId": "asset-1",
                         "sourceIn": 0.0, "sourceOut": 2.6,
                         "timelineIn": 0.0, "timelineOut": 2.6,
                         "beat": "hook"}]}
    p2.snap_timeline_speech(raw, [seg])
    # 2.6 severs "works" (2.0-3.0); sentence end 3.0 wins the preference
    assert raw["timeline"][0]["sourceOut"] == 3.0
    adj = raw["dialogueAdjustments"][0]
    assert adj["field"] == "sourceOut" and "works" in adj["reason"]


def test_snap_never_leaves_the_segment_or_guts_the_clip():
    seg = _speech_segment()
    raw = {"timeline": [{"segmentId": "seg-1", "assetId": "asset-1",
                         "sourceIn": 4.2, "sourceOut": 5.0,
                         "timelineIn": 0.0, "timelineOut": 0.8,
                         "beat": "hook"}]}
    p2.snap_timeline_speech(raw, [seg])
    e = raw["timeline"][0]
    assert e["sourceOut"] - e["sourceIn"] >= 0.5   # still a usable clip
    assert e["sourceIn"] >= seg.sourceStart
    assert e["sourceOut"] <= seg.sourceEnd


def test_unsnappable_cut_becomes_a_violation_with_a_hint(monkeypatch):
    monkeypatch.setenv(p2.DIALOGUE_FLAG, "1")
    seg = _speech_segment()
    raw = _valid_plan()
    raw["timeline"][0]["sourceOut"] = 4.3          # severs "job" (4.0-4.6)
    raw["timeline"][0]["timelineOut"] = 4.3
    out = p2.dialogue_violations(_plan(raw), [seg])
    assert any("severs the word 'job'" in v and "safe boundary" in v
               for v in out)


def test_validate_plan_flags_dialogue_only_when_flag_on(monkeypatch):
    seg = _speech_segment()
    segs = [seg] + _segments()[1:]
    raw = _valid_plan()
    raw["timeline"][0]["sourceOut"] = 2.6          # severs "works"
    raw["timeline"][0]["timelineOut"] = 2.6
    for e, shift in zip(raw["timeline"][1:], (2.6, 6.6), strict=True):
        e["timelineIn"], e["timelineOut"] = shift, shift + 4.0
    raw["plannedDurationSeconds"] = 10.6
    raw["hook"]["sourceOut"], raw["hook"]["durationSeconds"] = 2.0, 2.0
    plan = _plan(raw)
    assert not [v for v in ep.validate_plan(plan, segs, {}, False)
                if v.startswith("dialogue:")]
    monkeypatch.setenv(p2.DIALOGUE_FLAG, "1")
    assert [v for v in ep.validate_plan(plan, segs, {}, False)
            if v.startswith("dialogue:")]


# ---------------------------------------------------------------- hook engine
def _seg_with_speech(seg_id, text, **kw):
    return Segment(segmentId=seg_id, assetId="asset-1", sourceStart=0.0,
                   sourceEnd=10.0, transcript=text,
                   speechSpans=[SpeechSpan(start=0.5, end=3.0, text=text)],
                   **kw)


def test_archetype_classification():
    cases = [
        ("everyone thinks mold is harmless, but this house proves them wrong",
         "contradiction"),
        ("why does this ceiling keep leaking after every repair?", "question"),
        ("don't ever ignore water stains on your ceiling", "warning"),
        ("this repair cost 4000 dollars because they waited", "stakes"),
        ("that was the moment everything went sideways", "in_media_res"),
    ]
    for text, want in cases:
        got, _ = p2.classify_hook_archetype(_seg_with_speech("s", text))
        assert got == want, (text, got)


def test_discourse_openers_veto_in_media_res():
    got, reason = p2.classify_hook_archetype(
        _seg_with_speech("s", "so today we're checking a house"))
    assert got is None and "vetoes" in reason


def test_rank_is_deterministic_and_archetype_led():
    segs = [
        _seg_with_speech("seg-a", "nothing notable here"),
        _seg_with_speech("seg-b", "why does this keep happening here?",
                         storyUses=["hook"], focusScore=0.8, audioScore=0.8),
        _seg_with_speech("seg-c", "steady b-roll of the yard"),
    ]
    first = p2.rank_hook_candidates(segs)
    assert first == p2.rank_hook_candidates(segs)          # deterministic
    assert first[0]["segmentId"] == "seg-b"
    assert first[0]["archetype"] == "question"


def test_hook_violation_names_the_candidates(monkeypatch):
    monkeypatch.setenv(p2.HOOK_FLAG, "1")
    segs = [_seg_with_speech("seg-1", "quiet establishing shot"),
            _seg_with_speech("seg-2", "why is the floor wet again?",
                             storyUses=["hook"]),
            _seg_with_speech("seg-3", "we finished the job")]
    plan = _plan()                                          # hooks seg-1
    out = p2.hook_violations(plan, segs)
    assert out and "seg-2" in out[0]


def test_empty_shortlist_never_blocks():
    assert p2.hook_violations(_plan(), _segments()) == []


# ---------------------------------------------------------------- loop ledger
def _loops(**over):
    base = {"question": "why is the floor wet?", "openedAtSeconds": 0.5,
            "openedBySegmentId": "seg-1", "closedAtSeconds": 10.0,
            "closedBySegmentId": "seg-3"}
    base.update(over)
    return [base]


def test_loops_required_when_hook_flag_on():
    plan = _plan()
    assert any("declare 1-3 curiosity loops" in v
               for v in p2.loop_violations(plan))


def test_unclosed_loop_is_a_broken_promise():
    raw = _valid_plan()
    raw["loops"] = _loops(closedAtSeconds=99.0)     # beyond the 12s video
    out = p2.loop_violations(_plan(raw))
    assert any("never closes" in v for v in out)


def test_primary_loop_opens_with_hook_and_closes_late():
    raw = _valid_plan()
    raw["loops"] = _loops(openedAtSeconds=5.0)      # hook is 2.0s long
    assert any("opened by the hook" in v for v in p2.loop_violations(_plan(raw)))
    raw["loops"] = _loops(closedAtSeconds=3.0)      # 25% in — too early
    assert any("closes too early" in v for v in p2.loop_violations(_plan(raw)))
    raw["loops"] = _loops()                         # 0.5 -> 10.0 of 12s
    assert p2.loop_violations(_plan(raw)) == []


def test_loop_referencing_unused_segment_rejected():
    raw = _valid_plan()
    raw["loops"] = _loops(closedBySegmentId="seg-99")
    assert any("does not use" in v for v in p2.loop_violations(_plan(raw)))


# ---------------------------------------------------------- caption coherence
def _caption(**over):
    base = {"claimType": "fact", "text": "the crew works",
            "evidence": [{"sourceType": "transcript", "segmentId": "seg-1",
                          "quoteOrValue": "the crew works"}],
            "timelineStart": 1.0, "timelineEnd": 3.0}
    base.update(over)
    return base


def test_metadata_only_caption_narrates_the_visible():
    raw = _valid_plan()
    raw["captions"] = [_caption(evidence=[{"sourceType": "segment_metadata",
                                           "segmentId": "seg-1",
                                           "quoteOrValue": "crew works on step 1"}])]
    out = p2.coherence_violations(_plan(raw), _segments())
    assert any("narrates the visible picture" in v for v in out)


def test_reading_speed_cap():
    raw = _valid_plan()
    raw["captions"] = [_caption(text="t" * 60, timelineStart=1.0,
                                timelineEnd=2.0)]        # 60 cps
    out = p2.coherence_violations(_plan(raw), _segments())
    assert any("chars/sec" in v for v in out)


def test_caption_bound_to_its_words(monkeypatch):
    seg = _speech_segment()                     # "the crew works" at 1.0s
    raw = _valid_plan()
    raw["captions"] = [_caption(timelineStart=6.0, timelineEnd=7.0)]
    out = p2.coherence_violations(_plan(raw), [seg])
    assert any("spoken at" in v for v in out)
    raw["captions"] = [_caption(timelineStart=1.0, timelineEnd=2.5)]
    assert p2.coherence_violations(_plan(raw), [seg]) == []


# ------------------------------------------------------------- tension shape
def test_flat_energy_curve_rejected():
    raw = _valid_plan()
    for pb in raw["pacing"]:
        pb["energy"] = 0.5                       # the real POV failure mode
    out = p2.tension_violations(_plan(raw))
    assert any("flat" in v for v in out)
    assert any("payoff beat" in v for v in out)


def test_shaped_curve_passes():
    assert p2.tension_violations(_plan()) == []


def test_late_longueur_rejected():
    """The real POV failure: an 11.7s shot parked at t=68 of 85 (80% in),
    not the payoff. Geometry note: a shot starting past 75% needs prior
    clips summing to >= 3x its own length."""
    raw = _valid_plan()
    durs = [3.5, 3.5, 3.5, 3.5, 4.0]             # longest LAST, starts at 78%
    tl, t = [], 0.0
    for i, d in enumerate(durs):
        e = copy.deepcopy(raw["timeline"][min(i, 2)])
        e["sourceIn"], e["sourceOut"] = 0.0 + i * 0.01, 0.0 + i * 0.01 + d
        e["timelineIn"], e["timelineOut"] = t, t + d
        e["beat"] = "process"                    # long AND not the payoff
        t += d
        tl.append(e)
    raw["timeline"] = tl
    raw["plannedDurationSeconds"] = t
    assert any("final quarter" in v for v in p2.tension_violations(_plan(raw)))
    tl[-1]["beat"] = "payoff"                    # the payoff MAY hold long
    assert not any("final quarter" in v
                   for v in p2.tension_violations(_plan(raw)))


# ------------------------------------------------------------- interest gate
def test_interest_gate_is_a_floor_with_auditable_rules():
    plan = _plan()
    ig = p2.interest_gate(plan, _segments())
    assert sum(r["weight"] for r in ig["rules"]) == 100
    assert ig["threshold"] == 60
    by_name = {r["rule"]: r["passed"] for r in ig["rules"]}
    assert by_name["energy_variance"] and by_name["payoff_peak"]
    assert not by_name["loops_closed"]           # no loops declared
    assert ig["passed"]                          # 75 >= 60


def test_interest_gate_fails_the_real_pov_shape(monkeypatch):
    raw = _valid_plan()
    for pb in raw["pacing"]:
        pb["energy"] = 0.5
    ig = p2.interest_gate(_plan(raw), _segments())
    assert ig["score"] < 60 and not ig["passed"]
    monkeypatch.setenv("PHASE2_INTEREST_THRESHOLD", "40")
    ig = p2.interest_gate(_plan(raw), _segments())
    assert ig["threshold"] == 40 and ig["passed"]


def test_plan_editorial_attaches_gate_and_revises_below_floor(monkeypatch):
    monkeypatch.setenv(p2.INTEREST_FLAG, "1")
    calls = []

    def generate(parts, schema):
        calls.append(1)
        return copy.deepcopy(_valid_plan())

    result = ep.plan_editorial(_segments(), {}, False, generate)
    assert result["interestGate"]["passed"] and len(calls) == 1

    flat = copy.deepcopy(_valid_plan())
    for pb in flat["pacing"]:
        pb["energy"] = 0.5
    calls.clear()

    def generate_flat(parts, schema):
        calls.append(1)
        return copy.deepcopy(flat)

    with pytest.raises(ep.PlanRejected) as exc:
        ep.plan_editorial(_segments(), {}, False, generate_flat,
                          max_attempts=2)
    assert len(calls) == 2                       # interest failures REVISE
    assert any("interest gate score" in v
               for v in exc.value.violations_history[-1])


# ------------------------------------------------------- flags-off invariants
def test_all_flags_off_changes_nothing():
    plan = _plan()
    segs = _segments()
    assert p2.phase2_violations(plan, segs) == []
    assert p2.prompt_parts(segs) == []
    assert p2.catalog_extras(segs[0]) == {}
    assert not [v for v in ep.validate_plan(plan, segs, {}, False)
                if v.startswith(("dialogue:", "loops", "coherence:",
                                 "tension:", "hook:", "interest"))]

    def generate(parts, schema):
        assert "loops" not in schema.get("properties", {})
        assert not any("HOOK CANDIDATES" in p.get("text", "") for p in parts)
        return copy.deepcopy(_valid_plan())

    result = ep.plan_editorial(segs, {}, False, generate)
    assert "interestGate" not in result


def test_loops_schema_rides_the_core_call_only_when_flag_on(monkeypatch):
    assert "loops" not in ep._response_schema().get("properties", {})
    monkeypatch.setenv(p2.HOOK_FLAG, "1")
    schema = ep._response_schema()
    assert schema["properties"]["loops"]["type"] == "ARRAY"
    assert "loops" not in schema["required"]     # optional on the wire


def test_prompt_parts_follow_their_flags(monkeypatch):
    segs = [_seg_with_speech("seg-h", "why is the floor wet again?",
                             storyUses=["hook"])]
    monkeypatch.setenv(p2.HOOK_FLAG, "1")
    monkeypatch.setenv(p2.DIALOGUE_FLAG, "1")
    texts = " ".join(p["text"] for p in p2.prompt_parts(segs))
    assert "HOOK CANDIDATES" in texts and "DIALOGUE INTEGRITY" in texts
    assert "CAPTIONS" not in texts               # coherence flag is off


def test_catalog_extras_ship_speech_only_under_dialogue_flag(monkeypatch):
    seg = _speech_segment()
    assert p2.catalog_extras(seg) == {}
    monkeypatch.setenv(p2.DIALOGUE_FLAG, "1")
    extra = p2.catalog_extras(seg)
    assert extra["speech"] == [[1.0, 3.0], [4.0, 6.0]]
    assert extra["speechFree"][0] == [0.0, 1.0]
