"""Phase 2 editorial intelligence — substrate, dialogue integrity, hook
engine, loop ledger, caption coherence, tension shape, interest gate.

Contract under test (docs/PHASE2_EDITORIAL_INTELLIGENCE.md):
  * with every flag OFF, planner behavior is unchanged — canonically
    identical prompts/schema and deterministic serialized output, except
    loops: [] and dialogueAdjustments: [] (proven against an origin/main
    golden)
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
            _seg_with_speech("seg-3", "don't ever ignore stains like this "
                                      "on your ceiling")]
    plan = _plan()                                          # hooks seg-1
    out = p2.hook_violations(plan, segs)
    assert out and "seg-2" in out[0] and "seg-3" in out[0]


def test_single_candidate_shortlist_is_advisory_not_forced():
    """A12: one candidate is not a ranked choice — shown, never enforced."""
    segs = [_seg_with_speech("seg-1", "quiet establishing shot"),
            _seg_with_speech("seg-2", "why is the floor wet again?",
                             storyUses=["hook"]),
            _seg_with_speech("seg-3", "steady b-roll of the yard")]
    assert len(p2.rank_hook_candidates(segs)) == 1
    assert p2.hook_violations(_plan(), segs) == []          # hooks seg-1


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
    """The flat-0.5 curve, judged with its feeding flag ON (A1: rules whose
    subsystem is disabled are excluded, so TENSION must be on to judge it)."""
    monkeypatch.setenv(p2.TENSION_FLAG, "1")
    monkeypatch.setenv(p2.COHERENCE_FLAG, "1")
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

    # Below-floor via a GATE-ONLY rule (utilization has no hard counterpart,
    # so the plan passes every truth+hard check and only the interest floor
    # rejects it): 63-segment catalog, 3 segments used -> score 50 < 60.
    calls.clear()

    def generate_same(parts, schema):
        calls.append(1)
        return copy.deepcopy(_valid_plan())

    with pytest.raises(ep.PlanRejected) as exc:
        ep.plan_editorial(_padded_catalog(60), {}, False, generate_same,
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


# ============================ BATCH A (audit fixes) ============================
import json as _json  # noqa: E402
import pathlib as _pathlib  # noqa: E402

_GOLDEN = _pathlib.Path(__file__).parent / "goldens" / \
    "plan_editorial_flags_off.json"


# ---- A15/A16: identity against the PRE-Phase-2 baseline (origin/main)
def test_flags_off_deterministic_serialized_identity_vs_pre_phase2():
    """The golden was generated by running origin/main's planner (a git
    worktree of fb1f03b) on the same fixture — a REAL cross-version
    comparison, not a source assertion.

    PRECISE CLAIM (per Codex wording review): this proves DETERMINISTIC
    SERIALIZED identity — prompt parts compared as strings, the schema
    compared as a parsed object (canonically serialized identity), and the
    result compared via json.dumps(sort_keys=True) — NOT raw transport-byte
    identity (no wire bytes exist in a stubbed run). The result is identical
    except two documented additive plan keys (`loops`,
    `dialogueAdjustments`), asserted to be exactly [] with all flags off."""
    golden = _json.loads(_GOLDEN.read_text(encoding="utf-8"))
    captured = {}

    def generate(parts, schema):
        captured["parts"] = [p["text"] for p in parts]
        captured["schema"] = schema
        return copy.deepcopy(_valid_plan())

    result = ep.plan_editorial(_segments(), {}, False, generate)

    def _normalize_vocab(parts):
        """Batch B7 (merged after the golden was cut) intentionally adds
        digit tokens to the ALLOWED FACTUAL VOCABULARY — numbers are now
        groundable content. Strip standalone digits from the vocab part on
        BOTH sides and assert the delta is EXACTLY that, so any other prompt
        drift still fails this test."""
        out = []
        for p_ in parts:
            if p_.startswith("ALLOWED FACTUAL VOCABULARY"):
                head, _, words = p_.partition(":\n")
                kept = " ".join(w for w in words.split()
                                if not any(c.isdigit() for c in w))
                out.append(head + ":\n" + kept)
            else:
                out.append(p_)
        return out

    assert _normalize_vocab(captured["parts"]) \
        == _normalize_vocab(golden["parts"])             # prompt: identical
    vocab_now = next(p_ for p_ in captured["parts"]
                     if p_.startswith("ALLOWED FACTUAL VOCABULARY"))
    vocab_then = next(p_ for p_ in golden["parts"]
                      if p_.startswith("ALLOWED FACTUAL VOCABULARY"))
    delta = set(vocab_now.split()) - set(vocab_then.split())
    assert delta == {"1", "2", "3"}                      # the documented B7 delta
    assert captured["schema"] == golden["schema"]        # wire: identical
    plan = dict(result["plan"])
    assert plan.pop("loops") == []                       # documented delta 1
    assert plan.pop("dialogueAdjustments") == []         # documented delta 2
    assert plan.pop("brollInsertions") == []             # documented delta 3 (Phase 3)
    got = {**result, "plan": plan}
    assert _json.dumps(got, sort_keys=True, default=str) \
        == _json.dumps(golden["result"], sort_keys=True, default=str)


# ---- A1: flag-dependency interlock / normalized interest scoring
def test_interest_alone_no_longer_bricks_the_journey(monkeypatch):
    """THE audit blocker: INTEREST on, HOOK+DIALOGUE off, spoken footage.
    Loops/dialogue rules are now excluded (their subsystems are off) and the
    score normalizes over what remains — a truth-passing plan passes."""
    monkeypatch.setenv(p2.INTEREST_FLAG, "1")
    spoken = [_speech_segment("seg-1"), _speech_segment("seg-2", "asset-1"),
              _speech_segment("seg-3", "asset-1")]
    ig = p2.interest_gate(_plan(), spoken)
    by_name = {r["rule"]: r for r in ig["rules"]}
    assert not by_name["loops_closed"]["applicable"]
    assert not by_name["dialogue_clean"]["applicable"]
    assert ig["passed"] and ig["score"] == 100
    # and through the full planner: one attempt, gate attached, no rejection
    result = ep.plan_editorial(_segments(), {}, False,
                               lambda p, s: copy.deepcopy(_valid_plan()))
    assert result["interestGate"]["passed"]


def test_interest_rules_apply_exactly_with_their_feeders(monkeypatch):
    monkeypatch.setenv(p2.INTEREST_FLAG, "1")
    monkeypatch.setenv(p2.DIALOGUE_FLAG, "1")
    seg = _speech_segment()
    raw = _valid_plan()
    raw["timeline"][0]["sourceOut"] = 2.6           # severs "works"
    raw["timeline"][0]["timelineOut"] = 2.6
    ig = p2.interest_gate(_plan(raw), [seg])
    by_name = {r["rule"]: r for r in ig["rules"]}
    assert by_name["dialogue_clean"]["applicable"]
    assert not by_name["dialogue_clean"]["passed"]
    assert not by_name["loops_closed"]["applicable"]     # HOOK still off
    total = sum(r["weight"] for r in ig["rules"] if r["applicable"])
    assert ig["applicableWeight"] == total


def test_every_enabled_combination_can_reach_100(monkeypatch):
    """No structurally unearnable points under ANY flag combination: a plan
    that satisfies every applicable rule normalizes to exactly 100."""
    import itertools
    flags = (p2.HOOK_FLAG, p2.DIALOGUE_FLAG, p2.COHERENCE_FLAG,
             p2.TENSION_FLAG)
    raw = _valid_plan()
    raw["loops"] = _loops(openedAtSeconds=0.5, closedAtSeconds=10.0)
    plan = _plan(raw)
    for combo in itertools.product((False, True), repeat=len(flags)):
        for f, on in zip(flags, combo, strict=True):
            monkeypatch.setenv(f, "1") if on \
                else monkeypatch.delenv(f, raising=False)
        ig = p2.interest_gate(plan, _segments())
        assert ig["score"] == 100, (combo, ig["rules"])


# ---- A2: snap respects reserved transition handles
def _dissolve_fixture():
    hseg = Segment(segmentId="h1", assetId="a", sourceStart=0.0, sourceEnd=5.0,
                   speechSpans=[SpeechSpan(start=4.0, end=4.9,
                                           text="almost done")],
                   wordTimings=[Word(word="almost", start=4.0, end=4.4),
                                Word(word="done", start=4.4, end=4.9)])
    nseg = Segment(segmentId="h2", assetId="a", sourceStart=5.0,
                   sourceEnd=12.0)
    raw = {"timeline": [
        {"segmentId": "h1", "assetId": "a", "sourceIn": 0.0,
         "sourceOut": 4.5, "timelineIn": 0.0, "timelineOut": 4.5,
         "beat": "hook"},
        {"segmentId": "h2", "assetId": "a", "sourceIn": 6.0,
         "sourceOut": 10.0, "timelineIn": 4.5, "timelineOut": 8.5,
         "beat": "payoff"}],
        "transitions": [{"fromSegmentId": "h1", "toSegmentId": "h2",
                         "type": "dissolve", "durationSeconds": 0.4,
                         "purpose": "soften"}]}
    return hseg, nseg, raw


def test_snap_never_consumes_a_reserved_transition_handle():
    hseg, nseg, raw = _dissolve_fixture()
    p2.snap_timeline_speech(raw, [hseg, nseg])
    out = raw["timeline"][0]["sourceOut"]
    assert out <= hseg.sourceEnd - 0.4 + p2.WORD_EPS    # handle preserved
    assert p2.word_severed_at(hseg, out) is None        # AND speech-safe
    assert raw["dialogueAdjustments"][0]["field"] == "sourceOut"


def test_unrepairable_cut_stays_put_and_reports(monkeypatch):
    """If the reserved handle leaves no safe boundary, the cut is NOT
    mutated into invalid geometry — it is reported as a violation."""
    monkeypatch.setenv(p2.DIALOGUE_FLAG, "1")
    hseg, nseg, raw = _dissolve_fixture()
    # widen the dissolve so the reserve window excludes every boundary
    raw["transitions"][0]["durationSeconds"] = 2.0
    before = raw["timeline"][0]["sourceOut"]
    p2.snap_timeline_speech(raw, [hseg, nseg])
    assert raw["timeline"][0]["sourceOut"] == before     # untouched
    raw2 = _valid_plan()
    raw2["timeline"][0]["sourceOut"] = 2.6
    raw2["timeline"][0]["timelineOut"] = 2.6
    assert any("severs the word" in v
               for v in p2.dialogue_violations(_plan(raw2),
                                               [_speech_segment()]))


# ---- A3: malformed entry skips, ledger survives (regression: old code
# aborted with a bare return AFTER mutating earlier entries)
def test_malformed_entry_keeps_earlier_snaps_in_the_ledger():
    seg = _speech_segment()
    raw = {"timeline": [
        {"segmentId": "seg-1", "assetId": "a", "sourceIn": 0.0,
         "sourceOut": 2.6, "timelineIn": 0.0, "timelineOut": 2.6,
         "beat": "hook"},
        {"segmentId": "seg-1", "assetId": "a", "sourceIn": "oops",
         "sourceOut": 5.0, "timelineIn": 2.6, "timelineOut": 5.0,
         "beat": "next"}]}
    p2.snap_timeline_speech(raw, [seg])
    assert raw["timeline"][0]["sourceOut"] == 3.0        # snapped
    assert raw["timeline"][1]["sourceIn"] == "oops"      # left for validator
    adj = raw.get("dialogueAdjustments")
    assert adj and adj[0]["index"] == 0                  # ledger SURVIVES


# ---- A4: playbackSpeed — normalize and validator agree at every speed
@pytest.mark.parametrize("speed", [0.5, 1.0, 2.0])
def test_playback_speed_normalize_and_validate_agree(speed):
    raw = _valid_plan()
    raw["timeline"][1]["playbackSpeed"] = speed
    ep._normalize_timeline_arithmetic(raw, _segments())
    e = raw["timeline"][1]
    assert abs((e["timelineOut"] - e["timelineIn"])
               - (e["sourceOut"] - e["sourceIn"]) / speed) < 0.01
    plan = _plan(raw)
    timing = [v for v in ep.validate_plan(plan, _segments(), {}, False)
              if "duration does not match" in v or "real length" in v
              or "not contiguous" in v]
    assert timing == [], timing


# ---- A5: threshold env garbage degrades to the default, never crashes
@pytest.mark.parametrize("bad", ["60.0", "", "high"])
def test_interest_threshold_garbage_defaults_to_60(monkeypatch, bad):
    monkeypatch.setenv("PHASE2_INTEREST_THRESHOLD", bad)
    ig = p2.interest_gate(_plan(), _segments())
    assert ig["threshold"] == (60 if bad != "60.0" else 60)


# ---- A6: model-supplied dialogueAdjustments are discarded; hook out-point
# is speech-safe after the 5s cap
def test_model_written_dialogue_adjustments_are_discarded():
    raw = _valid_plan()
    raw["dialogueAdjustments"] = [{"index": 9, "field": "sourceIn",
                                   "from": 0, "to": 1,
                                   "reason": "FABRICATED"}]
    ep._normalize_timeline_arithmetic(raw, _segments())
    assert "dialogueAdjustments" not in raw              # system-owned


def test_hook_out_point_is_speech_safe_after_cap(monkeypatch):
    monkeypatch.setenv(p2.DIALOGUE_FLAG, "1")
    seg = Segment(segmentId="seg-1", assetId="asset-1", sourceStart=0.0,
                  sourceEnd=10.0, transcript="the whole crew kept working",
                  speechSpans=[SpeechSpan(start=0.2, end=6.0,
                                          text="the whole crew kept working")],
                  wordTimings=[Word(word="the", start=0.2, end=0.5),
                               Word(word="whole", start=0.5, end=1.0),
                               Word(word="crew", start=1.0, end=1.6),
                               Word(word="kept", start=1.6, end=2.3),
                               Word(word="working", start=2.3, end=6.0)])
    raw = _valid_plan()
    raw["timeline"][0]["sourceOut"] = 6.0
    raw["timeline"][0]["timelineOut"] = 6.0
    raw["hook"]["durationSeconds"] = 5.0                 # cap lands in a word
    ep._normalize_timeline_arithmetic(raw, [seg] + _segments()[1:])
    h = raw["hook"]
    assert p2.word_severed_at(seg, h["sourceOut"]) is None
    assert h["durationSeconds"] <= 5.0
    assert abs((h["sourceOut"] - h["sourceIn"]) - h["durationSeconds"]) < 0.01


# ---- A8: single source of truth for the minimum clip
def test_min_clip_constant_is_the_planners():
    assert p2._MIN_CLIP_S == ep._CLAMP_MIN_DURATION_S


# ---- A9: one shared hook-opens-loop predicate, prompt states timeline time
def test_loop_predicate_shared_between_validator_and_gate(monkeypatch):
    monkeypatch.setenv(p2.HOOK_FLAG, "1")
    monkeypatch.setenv(p2.INTEREST_FLAG, "1")
    raw = _valid_plan()
    raw["loops"] = _loops(openedBySegmentId="seg-2")      # identity mismatch
    plan = _plan(raw)
    assert not p2.primary_loop_opened_by_hook(plan)
    assert any("opened by the hook" in v for v in p2.loop_violations(plan))
    ig = p2.interest_gate(plan, _segments())
    assert not {r["rule"]: r for r in ig["rules"]}["hook_opens_loop"]["passed"]


def test_hook_prompt_states_timeline_seconds_and_exact_fraction(monkeypatch):
    monkeypatch.setenv(p2.HOOK_FLAG, "1")
    segs = [_seg_with_speech("s1", "why is the floor wet again?",
                             storyUses=["hook"]),
            _seg_with_speech("s2", "don't ever ignore stains on your "
                                   "ceiling")]
    text = " ".join(p["text"] for p in p2.prompt_parts(segs))
    assert "TIMELINE seconds" in text
    pct = round((1 - p2.PRIMARY_LOOP_CLOSE_FRACTION) * 100)
    assert f"final {pct}%" in text


# ---- A10: payoff is whole-token
def test_payoff_matching_is_whole_token():
    assert not p2._is_payoff_beat("rafter_install")
    assert not p2._is_payoff_beat("aftermath")
    assert p2._is_payoff_beat("before_and_after")        # real 'after' token
    assert p2._is_payoff_beat("payoff")


# ---- A11: utilization judged only on catalogs >= 20 segments
def test_small_catalog_is_never_underutilized():
    ig = p2.interest_gate(_plan(), _segments())          # 3 segments
    r = {x["rule"]: x for x in ig["rules"]}["catalog_utilization"]
    assert r["passed"] and "not judged" in r["detail"]


def _padded_catalog(n=60):
    return _segments() + [
        Segment(segmentId=f"pad-{i}", assetId="asset-1",
                sourceStart=float(i), sourceEnd=float(i) + 1.0)
        for i in range(n)]


def test_large_catalog_utilization_floor_applies():
    big = _padded_catalog(60)                            # 63 segs, 3 used
    ig = p2.interest_gate(_plan(), big)
    r = {x["rule"]: x for x in ig["rules"]}["catalog_utilization"]
    assert not r["passed"]


# ---- A13: attaching an unverified transcript quote no longer bypasses
def test_unverified_transcript_quote_is_still_narration():
    raw = _valid_plan()
    raw["captions"] = [_caption(evidence=[
        {"sourceType": "segment_metadata", "segmentId": "seg-1",
         "quoteOrValue": "crew works on step 1"},
        {"sourceType": "transcript", "segmentId": "seg-1",
         "quoteOrValue": "words never spoken"}])]        # seg-1: no transcript
    out = p2.coherence_violations(_plan(raw), _segments())
    assert any("narrates the visible picture" in v for v in out)


# ---- A14: joined-span rules — a join is only ever a REAL contiguous phrase
def test_quote_across_sentence_boundary_resolves():
    seg = _speech_segment()                  # spans: "the crew works", "job done"
    raw = _valid_plan()
    raw["timeline"][0]["sourceOut"] = 7.0    # the cut contains BOTH spans
    raw["timeline"][0]["timelineOut"] = 7.0
    raw["captions"] = [_caption(text="works job",
                                evidence=[{"sourceType": "transcript",
                                           "segmentId": "seg-1",
                                           "quoteOrValue": "works job"}],
                                timelineStart=6.0, timelineEnd=7.0)]
    out = p2.coherence_violations(_plan(raw), [seg])
    assert any("spoken at" in v for v in out)            # resolved + mistimed
    # correctly timed -> clean (the genuine contiguous joined phrase passes;
    # the timing anchor is the FIRST span's start — a documented
    # approximation for phrases that begin mid-span)
    raw["captions"] = [_caption(text="works job",
                                evidence=[{"sourceType": "transcript",
                                           "segmentId": "seg-1",
                                           "quoteOrValue": "works job"}],
                                timelineStart=1.0, timelineEnd=2.5)]
    assert p2.coherence_violations(_plan(raw), [seg]) == []


def _spans_segment(spans, seg_id="seg-1", transcript=None):
    return Segment(segmentId=seg_id, assetId="asset-1", sourceStart=0.0,
                   sourceEnd=12.0,
                   transcript=transcript or " ".join(t for _, _, t in spans),
                   speechSpans=[SpeechSpan(start=a, end=b, text=t)
                                for a, b, t in spans])


def test_codex_alpha_beta_distant_spans_rejected():
    """THE Codex reproduction: 'alpha' at 1-2s and 'beta' at 9-10s must NOT
    fabricate evidence for a phrase never spoken contiguously."""
    seg = _spans_segment([(1.0, 2.0, "alpha"), (9.0, 10.0, "beta")])
    raw = _valid_plan()
    raw["timeline"][0]["sourceOut"] = 4.0    # beta outside the cut too
    raw["captions"] = [_caption(text="alpha beta",
                                evidence=[{"sourceType": "transcript",
                                           "segmentId": "seg-1",
                                           "quoteOrValue": "alpha beta"}],
                                timelineStart=1.0, timelineEnd=2.0)]
    out = p2.coherence_violations(_plan(raw), [seg])
    assert any("not a contiguous spoken phrase" in v for v in out)


def test_joined_phrase_tail_outside_cut_rejected():
    seg = _spans_segment([(1.0, 2.0, "alpha"), (2.4, 3.4, "beta")])
    raw = _valid_plan()
    raw["timeline"][0]["sourceOut"] = 2.2    # gap ok, but beta's words cut off
    raw["captions"] = [_caption(text="alpha beta",
                                evidence=[{"sourceType": "transcript",
                                           "segmentId": "seg-1",
                                           "quoteOrValue": "alpha beta"}],
                                timelineStart=1.0, timelineEnd=2.0)]
    out = p2.coherence_violations(_plan(raw), [seg])
    assert any("not a contiguous spoken phrase" in v for v in out)


def test_nearby_same_segment_spans_join():
    seg = _spans_segment([(1.0, 2.0, "alpha"), (2.5, 3.5, "beta")])
    raw = _valid_plan()                       # cut 0-4 covers both
    raw["captions"] = [_caption(text="alpha beta",
                                evidence=[{"sourceType": "transcript",
                                           "segmentId": "seg-1",
                                           "quoteOrValue": "alpha beta"}],
                                timelineStart=1.0, timelineEnd=2.0)]
    assert p2.coherence_violations(_plan(raw), [seg]) == []


def test_gap_just_over_the_limit_rejects():
    gap = p2.MAX_JOINED_SPEECH_GAP_SECONDS + 0.05
    seg = _spans_segment([(1.0, 2.0, "alpha"), (2.0 + gap, 3.0 + gap, "beta")])
    raw = _valid_plan()
    raw["timeline"][0]["sourceOut"] = 4.0
    raw["captions"] = [_caption(text="alpha beta",
                                evidence=[{"sourceType": "transcript",
                                           "segmentId": "seg-1",
                                           "quoteOrValue": "alpha beta"}],
                                timelineStart=1.0, timelineEnd=2.0)]
    out = p2.coherence_violations(_plan(raw), [seg])
    assert any("not a contiguous spoken phrase" in v for v in out)


def test_cross_segment_and_reversed_order_never_join():
    # different segments: quote spanning seg-1's and seg-2's speech
    s1 = _spans_segment([(1.0, 2.0, "alpha")], "seg-1", transcript="alpha beta")
    raw = _valid_plan()
    raw["captions"] = [_caption(text="alpha beta",
                                evidence=[{"sourceType": "transcript",
                                           "segmentId": "seg-1",
                                           "quoteOrValue": "alpha beta"}],
                                timelineStart=1.0, timelineEnd=2.0)]
    out = p2.coherence_violations(_plan(raw), [s1])
    assert any("not a contiguous spoken phrase" in v for v in out)
    # reversed order: the spoken order is "beta alpha" — "alpha beta" is not
    s2 = _spans_segment([(1.0, 2.0, "beta"), (2.4, 3.4, "alpha")], "seg-1",
                        transcript="beta alpha ... alpha beta")
    out = p2.coherence_violations(_plan(raw), [s2])
    assert any("not a contiguous spoken phrase" in v for v in out)


# ---- A16: each flag alone completes the full planner run
@pytest.mark.parametrize("flag", ALL_FLAGS)
def test_each_flag_alone_completes(monkeypatch, flag):
    monkeypatch.setenv(flag, "1")
    raw = copy.deepcopy(_valid_plan())
    raw["loops"] = _loops(openedAtSeconds=0.5, closedAtSeconds=10.0)
    result = ep.plan_editorial(_segments(), {}, False,
                               lambda p, s: copy.deepcopy(raw))
    assert result["status"] == "approved"


# ---- Codex blocker A11: usability-aware utilization
def _black(i):
    return Segment(segmentId=f"blk-{i}", assetId="asset-1",
                   sourceStart=float(i), sourceEnd=float(i) + 1.0,
                   problems=["mostly_black"])


def test_97_black_segments_do_not_penalize_a_valid_edit(monkeypatch):
    monkeypatch.setenv(p2.INTEREST_FLAG, "1")
    catalog = _segments() + [_black(i) for i in range(97)]   # 3 usable
    ig = p2.interest_gate(_plan(), catalog)
    r = {x["rule"]: x for x in ig["rules"]}["catalog_utilization"]
    assert r["passed"] and "3 usable" in r["detail"]
    assert ig["score"] == 100                                # THE repro fixed


@pytest.mark.parametrize("usable,judged", [(19, False), (20, True),
                                           (21, True)])
def test_utilization_floor_boundary(usable, judged):
    catalog = _segments() + [
        Segment(segmentId=f"pad-{i}", assetId="asset-1",
                sourceStart=float(i), sourceEnd=float(i) + 1.0)
        for i in range(usable - 3)] + [_black(i) for i in range(5)]
    ig = p2.interest_gate(_plan(), catalog)
    r = {x["rule"]: x for x in ig["rules"]}["catalog_utilization"]
    if judged:
        assert f"of {usable} usable" in r["detail"]
        assert r["passed"] == (3 >= usable // 10)
    else:
        assert "not judged" in r["detail"] and r["passed"]


def test_unusable_states_excluded_from_denominator_and_shortlist():
    zero = Segment(segmentId="z", assetId="asset-1", sourceStart=2.0,
                   sourceEnd=2.0)                            # no duration
    noasset = Segment(segmentId="na", assetId="", sourceStart=0.0,
                      sourceEnd=3.0)
    for s in (_black(0), zero, noasset):
        assert not p2.is_usable_for_editorial_selection(s)
    assert p2.is_usable_for_editorial_selection(
        Segment(segmentId="soft", assetId="a", sourceStart=0.0,
                sourceEnd=3.0, problems=["soft_focus"]))     # warning only
    hookish = _seg_with_speech("blk-h", "why is the floor wet again?",
                               storyUses=["hook"], problems=["mostly_black"])
    assert p2.rank_hook_candidates([hookish]) == []          # never shortlisted


def test_selected_unusable_segment_is_a_hard_violation(monkeypatch):
    monkeypatch.setenv(p2.TENSION_FLAG, "1")                 # any flag on
    catalog = [_segments()[0].model_copy(update={"problems": ["mostly_black"]}),
               *_segments()[1:]]
    out = p2.phase2_violations(_plan(), catalog)
    assert any("unusable segment seg-1" in v and "mostly_black" in v
               for v in out)


# ---- Codex schema-v3 coexistence: deterministic latest-version loading
def _seg_row(rid, key, ver, extra=None):
    data = {"segmentId": key, "assetId": "asset-1", "sourceStart": 0.0,
            "sourceEnd": 5.0, "schemaVersion": ver, **(extra or {})}
    return {"id": rid, "project_id": "p1", "asset_id": "asset-1",
            "segment_key": key, "schema_version": ver, "data": data}


def test_loader_selects_latest_schema_version_deterministically(monkeypatch):
    from app import jobs
    from tests.fake_supa import FakeSupabase, install
    fake = FakeSupabase()
    install(monkeypatch, fake)
    rows = [_seg_row("r1", "seg-a", 2, {"action": "old"}),
            _seg_row("r2", "seg-a", 3, {"action": "new"}),
            _seg_row("r3", "seg-b", 2, {"action": "only-v2"}),
            _seg_row("r4", "seg-c", 3),
            _seg_row("r6", "seg-d", 3, {"action": "dup-hi-id"}),
            _seg_row("r5", "seg-d", 3, {"action": "dup-lo-id"})]
    for order in (rows, list(reversed(rows))):     # row order cannot matter
        fake.tables["segments"] = []
        for r in order:
            fake.insert("segments", dict(r, data=dict(r["data"])))
        segs = jobs._load_segments("p1")
        by_id = {s.segmentId: s for s in segs}
        assert len(segs) == 4                      # one row per key
        assert by_id["seg-a"].action == "new"      # v3 beats v2, unmerged
        assert by_id["seg-b"].action == "only-v2"  # v2-only preserved
        assert by_id["seg-c"].schemaVersion == 3
        assert by_id["seg-d"].action == "dup-lo-id"  # tie -> lowest id


def test_loader_v2_only_project_unchanged(monkeypatch):
    from app import jobs
    from tests.fake_supa import FakeSupabase, install
    fake = FakeSupabase()
    install(monkeypatch, fake)
    fake.insert("segments", _seg_row("r1", "seg-a", 2))
    fake.insert("segments", _seg_row("r2", "seg-b", 2))
    segs = jobs._load_segments("p1")
    assert [s.segmentId for s in segs] == ["seg-a", "seg-b"]
    assert all(s.schemaVersion == 2 for s in segs)


# ---- committed mixed-speed coverage (one timeline, three speeds)
def test_mixed_speed_timeline_normalize_and_validate_agree():
    raw = _valid_plan()
    for e, sp in zip(raw["timeline"], (1.0, 2.0, 0.5), strict=True):
        e["playbackSpeed"] = sp
    ep._normalize_timeline_arithmetic(raw, _segments())
    plan = _plan(raw)
    timing = [v for v in ep.validate_plan(plan, _segments(), {}, False)
              if "duration does not match" in v or "real length" in v
              or "not contiguous" in v]
    assert timing == [], timing
    assert abs(plan.plannedDurationSeconds - (4.0 + 2.0 + 8.0)) < 0.05


def test_pov_shaped_flat_plan_fails_honestly_under_tension(monkeypatch):
    monkeypatch.setenv(p2.TENSION_FLAG, "1")
    flat = copy.deepcopy(_valid_plan())
    for pb in flat["pacing"]:
        pb["energy"] = 0.5
    with pytest.raises(ep.PlanRejected) as exc:
        ep.plan_editorial(_segments(), {}, False,
                          lambda p, s: copy.deepcopy(flat), max_attempts=2)
    last = exc.value.violations_history[-1]
    assert any("tension:" in v and "flat" in v for v in last)
