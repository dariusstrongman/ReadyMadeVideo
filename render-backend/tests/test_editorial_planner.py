"""Editorial Planner v1 — evidence-backed claims, honest shortfall, structured
creative policies, execution geometry, deterministic gate, handler + endpoints.
The model call is always a stub: no network, no fabrication.
"""
import copy
import os
from uuid import uuid4

import pytest

os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "fake-service-key")
os.environ["WORKER_ENABLED"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from app import jobs, supa  # noqa: E402
from app.main import app  # noqa: E402
from app.pipeline import editorial_planner as ep  # noqa: E402
from app.pipeline.schemas import Segment  # noqa: E402
from tests.fake_supa import FakeSupabase, install  # noqa: E402


def _auth(t):
    return {"Authorization": f"Bearer {t}"}


def _segments(asset_id="asset-1"):
    shots = {1: "wide", 2: "medium", 3: "close"}
    return [Segment(segmentId=f"seg-{i}", assetId=asset_id,
                    sourceStart=0.0, sourceEnd=10.0,
                    action=f"crew works on step {i}", shotType=shots[i],
                    location="Dallas backyard" if i == 1 else "",
                    transcript="we finished the job" if i == 3 else None)
            for i in (1, 2, 3)]


def _editorial(text):
    return {"text": text, "claimType": "editorial_label", "evidence": []}


def _option():
    scores = {k: 80 for k in ("hookStrength", "storyClarity", "payoffStrength",
                              "footageSupport", "emotionalInterest",
                              "visualVariety", "platformFit", "durationFit",
                              "originality")}
    return {"premise": _editorial("the work process"),
            "viewerPromise": "see the finished work", "hook": "reveal first",
            "structure": "results_first",
            "payoff": _editorial("the final reveal"),
            "idealDurationSeconds": 12, "strengths": ["s"], "weaknesses": ["w"],
            "footageCoverage": "full", "retentionRisks": ["r"],
            "scores": scores}


def _self_assessment(**over):
    base = {"hook": 12, "storyClarity": 12, "flowContinuity": 8, "pacing": 8,
            "clipSelection": 8, "payoff": 8, "visualVariety": 4,
            "creativeTreatment": 8, "soundDesign": 4, "platformFit": 4,
            "durationCompliance": 4, "hardFailures": []}
    base.update(over)
    return base


_MAX_SELF = dict(hook=15, storyClarity=15, flowContinuity=10, pacing=10,
                 clipSelection=10, payoff=10, visualVariety=5,
                 creativeTreatment=10, soundDesign=5, platformFit=5,
                 durationCompliance=5)


def _valid_plan(music_available=False):
    """A grounded plan over _segments(): three contiguous cuts, 12 s total."""
    tl = []
    for i, seg in enumerate(("seg-1", "seg-2", "seg-3")):
        tl.append({"segmentId": seg, "assetId": "asset-1",
                   "sourceIn": 0.0, "sourceOut": 4.0,
                   "timelineIn": i * 4.0, "timelineOut": (i + 1) * 4.0,
                   "beat": ["hook", "process", "payoff"][i],
                   "reason": "advances the story", "addsNew": "new info",
                   "expectedViewerEffect": "curiosity"})
    return {
        "schemaVersion": 1,
        # the story sentence describes REAL footage, so it is a fact with
        # structured evidence (catalog words never pass as evidence-free labels)
        "storySentence": {"text": "This is a story about the crew at work, "
                                  "leading to the finished job.",
                          "claimType": "fact",
                          "evidence": [{"sourceType": "segment_metadata",
                                        "segmentId": "seg-1",
                                        "quoteOrValue": "crew works on step 1"},
                                       {"sourceType": "transcript",
                                        "segmentId": "seg-3",
                                        "quoteOrValue": "we finished the job"}]},
        "footageSummary": "three usable segments", "intendedAudience": "local",
        "viewerPromise": _editorial("watch until the end"),
        "options": [_option(), _option(), _option()], "chosenOption": 0,
        "hook": {"segmentId": "seg-1", "sourceIn": 0.0, "sourceOut": 2.0,
                 "firstFrame": "reveal", "audioCue": "natural sound",
                 "durationSeconds": 2.0, "transitionOut": "cut",
                 "curiosityCreated": "how did it get here",
                 "promiseToFulfill": "show the process"},
        "beats": [{"key": "hook", "purpose": "stop the scroll"},
                  {"key": "process", "purpose": "advance"},
                  {"key": "payoff", "purpose": "deliver"}],
        "timeline": tl,
        "pacing": [{"beat": "hook", "targetDurationSeconds": 4, "energy": 0.9},
                   {"beat": "process", "targetDurationSeconds": 4, "energy": 0.6},
                   {"beat": "payoff", "targetDurationSeconds": 4, "energy": 0.8}],
        "pacingRationale": "fast hook, steady middle, held payoff",
        "transitions": [{"fromSegmentId": "seg-1", "toSegmentId": "seg-2",
                         "type": "hard_cut", "durationSeconds": 0,
                         "purpose": "action match"},
                        {"fromSegmentId": "seg-2", "toSegmentId": "seg-3",
                         "type": "hard_cut", "durationSeconds": 0,
                         "purpose": "into the payoff"}],
        "transitionsRationale": "hard cuts on action",
        "speedRamps": [], "reframes": [], "captions": [], "graphics": [],
        "audio": {"musicAvailable": music_available,
                  "musicPlan": "one supplied cue under the montage"
                  if music_available else None,
                  "naturalSoundSegmentIds": ["seg-3"]},
        "audioTreatments": [{"segmentId": "seg-3", "gainDb": 0,
                             "preserveNaturalSound": True}],
        "colorStabilization": [{"segmentId": "seg-1"}],
        "plannedDurationSeconds": 12.0, "achievableDurationSeconds": None,
        "technicalWarnings": [], "retentionReview": [
            {"position": "mid", "risk": "repetition",
             "mitigation": "vary shot size"}],
        "modelSelfAssessment": _self_assessment(),
        "missingFootage": [],
        "render": {"width": 1080, "height": 1920, "fps": 30, "aspect": "9:16"},
        "status": "approved",
    }


def _shortfall_plan(achievable=12.0):
    plan = _valid_plan()
    plan["status"] = "insufficient_footage"
    plan["achievableDurationSeconds"] = achievable
    plan["missingFootage"] = [{"beat": "closing reaction", "shotType": "close-up",
                               "recommendedDurationSeconds": 5.0,
                               "why": "the payoff needs a human reaction shot "
                                      "to land emotionally"}]
    return plan


def _gen(*plans):
    seq = list(plans)
    calls = {"n": 0, "parts": []}

    def generate(parts, schema):
        calls["parts"].append(parts)
        plan = seq[min(calls["n"], len(seq) - 1)]
        calls["n"] += 1
        return copy.deepcopy(plan)
    generate.calls = calls
    return generate


def _reject(plan, constraints=None, music=False, needle=None):
    with pytest.raises(ep.PlanRejected) as exc:
        ep.plan_editorial(_segments(), constraints or {}, music, _gen(plan),
                          max_attempts=1)
    flat = " | ".join(exc.value.violations_history[0])
    if needle:
        assert needle in flat, flat
    return flat


def _accept(plan, constraints=None, music=False):
    return ep.plan_editorial(_segments(), constraints or {}, music, _gen(plan))


# ------------------------------------------------------------------ happy path
def test_valid_plan_approved_by_deterministic_gate():
    out = _accept(_valid_plan())
    assert out["attempts"] == 1 and out["status"] == "approved"
    assert out["qualityScore"] == 100
    assert out["deterministicGate"]["passed"] is True
    assert sum(r["weight"] for r in out["deterministicGate"]["rules"]) == 100


def test_schema_version_is_enforced():
    plan = _valid_plan()
    plan["schemaVersion"] = 2
    _reject(plan, needle="schema:")


# --------------------------------- blocker 1: evidence-backed factual claims
CODEX_FABRICATIONS = [
    "Marcus loved the result in paris",
    "customer loved the transformation",
    "the customer cried with joy and booked ten more jobs",
    "Marcus says this changed his life",
]


@pytest.mark.parametrize("text", CODEX_FABRICATIONS)
def test_codex_fabrications_rejected_as_editorial(text):
    plan = _valid_plan()
    plan["storySentence"] = _editorial(text)
    _reject(plan, needle="implies unsupported factual content")


@pytest.mark.parametrize("text", CODEX_FABRICATIONS)
def test_codex_fabrications_rejected_as_fact_with_real_quote(text):
    """Even with a VALID evidence quote attached, unsupported content words in
    the claim text itself are rejected (evidence must cover the claim)."""
    plan = _valid_plan()
    plan["storySentence"] = {"text": text, "claimType": "fact",
                             "evidence": [{"sourceType": "transcript",
                                           "segmentId": "seg-3",
                                           "quoteOrValue": "we finished the job"}]}
    _reject(plan, needle="unsupported factual content")


@pytest.mark.parametrize("field,text,needle", [
    ("location", "filmed in paris", "implies unsupported"),
    ("name", "built by Marcus himself", "implies unsupported"),
    ("reaction", "the customer was thrilled", "implies unsupported"),
    ("result", "sales doubled after this", "implies unsupported"),
    ("quantity", "took only three hours", "implies unsupported"),
])
def test_unsupported_claim_categories_rejected(field, text, needle):
    plan = _valid_plan()
    plan["viewerPromise"] = _editorial(text)
    _reject(plan, needle=needle)


def test_fact_with_no_evidence_rejected():
    plan = _valid_plan()
    plan["storySentence"] = {"text": "we finished the job",
                             "claimType": "fact", "evidence": []}
    _reject(plan, needle="factual claim with no evidence")


def test_ungrounded_caption_is_dropped_not_rendered(monkeypatch):
    """2026-08-06: a caption whose quote is not verbatim in its cited source
    is UNGROUNDED. It is removed from the plan rather than failing the whole
    plan — captions are optional decoration, and the fabricated text never
    reaches the output either way. Fabrication in load-bearing fields
    (storySentence, premises, payoffs, invented segment ids) still rejects."""
    plan = _valid_plan()
    plan["captions"] = [{"claimType": "fact", "text": "we finished the job",
                         "evidence": [{"sourceType": "transcript",
                                       "segmentId": "seg-1",   # no transcript
                                       "quoteOrValue": "we finished the job"}],
                         "timelineStart": 8.0, "timelineEnd": 11.0}]
    result = ep.plan_editorial(_segments(), {}, music_available=False,
                               generate=_gen(plan))
    assert result["plan"]["captions"] == []      # dropped, never rendered


def test_supported_claims_from_transcript_and_user_input_accepted():
    plan = _valid_plan()
    plan["captions"] = [{"claimType": "fact", "text": "we finished the job",
                         "evidence": [{"sourceType": "transcript",
                                       "segmentId": "seg-3",
                                       "quoteOrValue": "we finished the job"}],
                         "timelineStart": 8.0, "timelineEnd": 11.0}]
    assert _accept(plan)["status"] == "approved"

    constraints = {"brief": "deck rebuild for marcus in dallas"}
    user_backed = _valid_plan()
    user_backed["captions"] = [{"claimType": "fact",
                                "text": "deck rebuild for marcus",
                                "evidence": [{"sourceType": "user_input",
                                              "quoteOrValue":
                                              "deck rebuild for marcus"}],
                                "timelineStart": 1.0, "timelineEnd": 3.0}]
    out = ep.plan_editorial(_segments(), constraints, False, _gen(user_backed))
    assert out["status"] == "approved"


def test_editorial_labels_safe_and_unsafe():
    safe = _valid_plan()
    safe["captions"] = [{"claimType": "cta", "text": "follow for more",
                         "evidence": [], "timelineStart": 10.0,
                         "timelineEnd": 12.0}]
    assert _accept(safe)["status"] == "approved"

    unsafe = _valid_plan()
    unsafe["captions"] = [{"claimType": "editorial_label",
                           "text": "Rated 5 stars by every customer",
                           "evidence": [], "timelineStart": 1.0,
                           "timelineEnd": 3.0}]
    _reject(unsafe, needle="implies unsupported factual content")


def test_supported_catalog_claims_pass_as_facts():
    plan = _valid_plan()   # "Dallas" is real catalog metadata (seg-1 location)
    plan["storySentence"] = {
        "text": "This is a story about the crew at work in Dallas, leading "
                "to the finished job.",
        "claimType": "fact",
        "evidence": [{"sourceType": "segment_metadata", "segmentId": "seg-1",
                      "quoteOrValue": "dallas backyard"},
                     {"sourceType": "transcript", "segmentId": "seg-3",
                      "quoteOrValue": "we finished the job"}]}
    assert _accept(plan)["status"] == "approved"


def test_catalog_words_cannot_pass_as_evidence_free_labels():
    """Codex repro: 'we finished the job' is REAL transcript text, but an
    evidence-free editorial label may not state it — footage facts must be
    claimType=fact with evidence."""
    plan = _valid_plan()
    plan["captions"] = [{"claimType": "editorial_label",
                         "text": "we finished the job", "evidence": [],
                         "timelineStart": 8.0, "timelineEnd": 11.0}]
    _reject(plan, needle="must be a factual claim with evidence")


# ----------------------- gap 1: closed neutral-label boundary (no finite lexicon)
@pytest.mark.parametrize("text", [
    "audience cheered",          # reaction outside any lexicon
    "crew celebrated",           # group + reaction
    "viewers were stunned",      # group + emotion
    "customer approved",         # subject + outcome
])
def test_unlisted_reactions_fail_closed_without_evidence(text):
    plan = _valid_plan()
    plan["captions"] = [{"claimType": "editorial_label", "text": text,
                         "evidence": [], "timelineStart": 1.0,
                         "timelineEnd": 3.0}]
    _reject(plan, needle="must be a factual claim with evidence")


@pytest.mark.parametrize("text", [
    "The Final Push", "Step 2", "Part 1", "Watch Until the End",
    "See the Result", "Behind the Scenes", "Key Takeaway", "Quick Tip",
    "Bonus",
])
def test_neutral_structural_labels_accepted(text):
    plan = _valid_plan()
    plan["captions"] = [{"claimType": "editorial_label", "text": text,
                         "evidence": [], "timelineStart": 1.0,
                         "timelineEnd": 3.0}]
    assert _accept(plan)["status"] == "approved"


def test_supported_reaction_from_transcript_accepted_as_fact():
    segs = _segments()
    segs[2] = segs[2].model_copy(
        update={"transcript": "we finished the job and the audience cheered"})
    plan = _valid_plan()
    plan["captions"] = [{"claimType": "fact", "text": "the audience cheered",
                         "evidence": [{"sourceType": "transcript",
                                       "segmentId": "seg-3",
                                       "quoteOrValue": "the audience cheered"}],
                         "timelineStart": 8.0, "timelineEnd": 11.0}]
    out = ep.plan_editorial(segs, {}, False, _gen(plan))
    assert out["status"] == "approved"


def test_supported_reaction_from_user_input_accepted_as_fact():
    constraints = {"brief": "the crew celebrated at the end of this job"}
    plan = _valid_plan()
    plan["captions"] = [{"claimType": "fact", "text": "the crew celebrated",
                         "evidence": [{"sourceType": "user_input",
                                       "quoteOrValue": "the crew celebrated"}],
                         "timelineStart": 8.0, "timelineEnd": 11.0}]
    out = ep.plan_editorial(_segments(), constraints, False, _gen(plan))
    assert out["status"] == "approved"


# ------------------------------ gap 2: tone/style never silently ignored
def test_quiet_somber_rejects_high_energy_plan():
    # default plan paces hook at 0.9 energy — too hot for a low-energy tone
    flat = _reject(_valid_plan(), constraints={"tone": "quiet and somber"})
    assert "restrained pacing" in flat


def test_quiet_somber_rejects_aggressive_transition():
    plan = _valid_plan()
    plan["timeline"][1].update(sourceIn=1.0, sourceOut=5.0)
    plan["transitions"][0].update(type="whip", durationSeconds=0.5)
    for pb in plan["pacing"]:
        pb["energy"] = 0.5
    _reject(plan, constraints={"tone": "quiet and somber"},
            needle="forbids aggressive transitions")


def test_quiet_somber_accepts_restrained_plan():
    plan = _valid_plan()
    for pb in plan["pacing"]:
        pb["energy"] = 0.5
    out = ep.plan_editorial(_segments(), {"tone": "quiet and somber"}, False,
                            _gen(plan))
    assert out["status"] == "approved"
    assert out["qualityScore"] == 100        # fully resolved + fully honored


def test_conflicting_tone_directives_rejected():
    _reject(_valid_plan(), constraints={"tone": "playful and somber"},
            needle="conflicting tone directives")


def test_unknown_tone_phrase_is_a_hard_failure():
    flat = _reject(_valid_plan(), constraints={"tone": "glorpy zebra energy"})
    assert "could not be converted into enforceable policy" in flat
    assert "deterministic gate: creative_policy_honored failed" in flat


def test_advisory_unknown_tone_needs_warning_and_reduces_score():
    constraints = {"tone": "glorpy", "toneAdvisoryOnly": True}
    _reject(_valid_plan(), constraints=constraints,
            needle="must surface an explicit warning")
    warned = _valid_plan()
    warned["technicalWarnings"] = [
        _editorial("tone directive glorpy could not be enforced")]
    out = ep.plan_editorial(_segments(), constraints, False, _gen(warned))
    assert out["status"] == "approved"
    assert out["qualityScore"] == 97          # tone_fully_resolved (3) withheld
    rule = next(r for r in out["deterministicGate"]["rules"]
                if r["rule"] == "tone_fully_resolved")
    assert rule["passed"] is False and rule["hard"] is False


def test_no_tone_supplied_is_not_a_failure():
    assert _accept(_valid_plan())["qualityScore"] == 100


def test_high_energy_tone_rejects_listless_plan():
    """Codex repro: tone='aggressive' with every pacing beat at 0.1 must not
    silently pass — high-energy requests are enforced symmetrically."""
    plan = _valid_plan()
    for pb in plan["pacing"]:
        pb["energy"] = 0.1
    flat = _reject(plan, constraints={"tone": "aggressive"})
    assert "high-energy tone requires" in flat
    assert "deterministic gate: creative_policy_honored failed" in flat


def test_high_energy_tone_accepts_energetic_plan():
    # default pacing peaks at 0.9 and averages ~0.77 — delivers the request
    out = ep.plan_editorial(_segments(), {"tone": "aggressive"}, False,
                            _gen(_valid_plan()))
    assert out["status"] == "approved" and out["qualityScore"] == 100


# ------------------------------ gap 3: closed transition-type enum
@pytest.mark.parametrize("bad_type", ["teleport", "unknown_transition"])
def test_unknown_transition_types_fail_schema(bad_type):
    plan = _valid_plan()
    plan["transitions"][0]["type"] = bad_type
    _reject(plan, needle="schema:")


def test_whip_allowed_only_when_policy_permits():
    plan = _valid_plan()
    plan["timeline"][1].update(sourceIn=1.0, sourceOut=5.0)
    plan["transitions"][0].update(type="whip", durationSeconds=0.5)
    assert _accept(plan)["status"] == "approved"     # default policy: expressive
    _reject(copy.deepcopy(plan),
            constraints={"forbiddenTransitionTypes": ["whip"]},
            needle="policy: transition type 'whip' is forbidden")


# ------------------------------ gap 4: one transition per boundary
@pytest.mark.parametrize("second_type", ["dissolve", "whip"])
def test_multiple_transitions_on_one_boundary_rejected(second_type):
    plan = _valid_plan()
    plan["timeline"][1].update(sourceIn=1.0, sourceOut=5.0)
    plan["transitions"][0].update(type="dissolve", durationSeconds=0.5)
    plan["transitions"].append({"fromSegmentId": "seg-1", "toSegmentId": "seg-2",
                                "type": second_type, "durationSeconds": 0.5,
                                "purpose": "duplicate"})
    _reject(plan, needle="target the same boundary")


def test_one_transition_per_boundary_accepted():
    # the valid plan carries exactly one transition on each of its two
    # boundaries — different boundaries are fine
    out = _accept(_valid_plan())
    assert out["status"] == "approved"


# ------------------------------- blocker 2: honest insufficient-footage report
def test_invented_achievable_duration_999_rejected():
    plan = _shortfall_plan(achievable=999.0)
    flat = _reject(plan, constraints={"durationMin": 45, "durationMax": 60})
    assert "does not equal the computed timeline duration" in flat
    assert "not below the requested minimum" in flat


def test_honest_shortfall_with_computed_duration_accepted():
    out = ep.plan_editorial(_segments(), {"durationMin": 45, "durationMax": 60},
                            False, _gen(_shortfall_plan(achievable=12.0)))
    assert out["status"] == "insufficient_footage"
    assert out["qualityScore"] >= 80


def test_within_range_timeline_cannot_claim_insufficient():
    plan = _shortfall_plan(achievable=12.0)
    _reject(plan, constraints={"durationMin": 10, "durationMax": 20},
            needle="already satisfies the requested range")


def test_out_of_range_approved_plan_rejected():
    _reject(_valid_plan(), constraints={"durationMin": 45, "durationMax": 60},
            needle="REQUESTED duration range")


def test_vague_missing_footage_rejected():
    plan = _shortfall_plan(achievable=12.0)
    plan["missingFootage"] = [{"beat": "end", "shotType": "any",
                               "recommendedDurationSeconds": 5.0,
                               "why": "need more"}]        # vague: under 10 chars
    _reject(plan, constraints={"durationMin": 45, "durationMax": 60},
            needle="schema:")


def test_shortfall_rejected_while_unused_grounded_footage_remains():
    # catalog holds 30 s; requesting 20-60 s: a 12 s "shortfall" is dishonest
    plan = _shortfall_plan(achievable=12.0)
    _reject(plan, constraints={"durationMin": 20, "durationMax": 60},
            needle="unused material")


# --------------------------------- blocker 3: structured creative policies
def test_policy_parser_reads_the_original_request():
    p = ep.parse_creative_policies({"style": "hard cuts only, no speed ramps",
                                    "tone": "no music"})
    assert p["transitionPolicy"] == "hard_cuts_only"
    assert p["speedRampPolicy"] == "forbidden"
    assert p["musicPolicy"] == "none"
    explicit = ep.parse_creative_policies({"style": "whatever",
                                           "transitionPolicy": "none"})
    assert explicit["transitionPolicy"] == "none"


def test_hard_cuts_only_rejects_dissolve():
    plan = _valid_plan()
    plan["transitions"][1]["type"] = "dissolve"
    plan["transitions"][1]["durationSeconds"] = 0.5
    _reject(plan, constraints={"style": "hard cuts only"},
            needle="policy: the request allows hard cuts only")


def test_no_transitions_rejects_any_transition_object():
    _reject(_valid_plan(), constraints={"style": "no transitions"},
            needle="policy: the request forbids transitions")


def test_forbidden_speed_ramps_rejected():
    plan = _valid_plan()
    plan["speedRamps"] = [{"segmentId": "seg-2", "sourceStart": 0.0,
                           "sourceEnd": 4.0, "entrySpeed": 1, "peakSpeed": 3,
                           "exitSpeed": 1, "easing": "ease",
                           "narrativePurpose": "compress the walk"}]
    _reject(plan, constraints={"style": "no speed ramps"},
            needle="policy: the request forbids speed ramps")


def test_graphics_none_policy_rejected():
    plan = _valid_plan()
    plan["graphics"] = [{"graphicType": "label", "claimType": "editorial_label",
                         "text": "step one", "evidence": [],
                         "timelineStart": 4.0, "timelineEnd": 6.0,
                         "durationSeconds": 2.0}]
    _reject(plan, constraints={"style": "no graphics"},
            needle="policy: the request forbids graphics")


def test_expressive_style_accepts_valid_dissolve():
    plan = _valid_plan()
    # give the incoming cut a source handle so the dissolve is executable
    plan["timeline"][1].update(sourceIn=1.0, sourceOut=5.0)
    plan["transitions"][0].update(type="dissolve", durationSeconds=0.5)
    assert _accept(plan)["status"] == "approved"


def test_full_caption_policy_requires_captions_when_supported():
    _reject(_valid_plan(), constraints={"style": "full captions"},
            needle="policy: the request requires captions")


def test_impossible_music_requirement_needs_honest_warning():
    _reject(_valid_plan(), constraints={"tone": "music required"},
            needle="policy: the request requires music")
    warned = _valid_plan()
    warned["technicalWarnings"] = [
        _editorial("music was requested but no licensed music is available")]
    out = ep.plan_editorial(_segments(), {"tone": "music required"}, False,
                            _gen(warned))
    assert out["status"] == "approved"


# --------------------------------- blocker 4: execution geometry validation
def test_inverted_ramp_range_rejected():
    plan = _valid_plan()
    plan["speedRamps"] = [{"segmentId": "seg-2", "sourceStart": 3.0,
                           "sourceEnd": 2.0, "entrySpeed": 1, "peakSpeed": 2,
                           "exitSpeed": 1, "easing": "ease",
                           "narrativePurpose": "x"}]
    _reject(plan, needle="execution: speedRamps[0] has an inverted")


def test_ramp_outside_planned_cut_rejected():
    plan = _valid_plan()
    plan["speedRamps"] = [{"segmentId": "seg-2", "sourceStart": 0.0,
                           "sourceEnd": 8.0, "entrySpeed": 1, "peakSpeed": 2,
                           "exitSpeed": 1, "easing": "ease",
                           "narrativePurpose": "x"}]
    _reject(plan, needle="execution: speedRamps[0] range is outside")


@pytest.mark.parametrize("speed", [0, -1, 9])
def test_out_of_limit_ramp_speeds_rejected(speed):
    plan = _valid_plan()
    plan["speedRamps"] = [{"segmentId": "seg-2", "sourceStart": 0.0,
                           "sourceEnd": 4.0, "entrySpeed": speed,
                           "peakSpeed": 2, "exitSpeed": 1, "easing": "ease",
                           "narrativePurpose": "x"}]
    _reject(plan, needle="schema:")


def test_overlapping_ramps_on_one_segment_rejected():
    plan = _valid_plan()
    plan["speedRamps"] = [
        {"segmentId": "seg-2", "sourceStart": 0.0, "sourceEnd": 3.0,
         "entrySpeed": 1, "peakSpeed": 3, "exitSpeed": 1, "easing": "ease",
         "narrativePurpose": "a"},
        {"segmentId": "seg-2", "sourceStart": 2.0, "sourceEnd": 4.0,
         "entrySpeed": 1, "peakSpeed": 2, "exitSpeed": 1, "easing": "ease",
         "narrativePurpose": "b"}]
    _reject(plan, needle="overlaps another speed ramp")


@pytest.mark.parametrize("crop,needle", [
    ({"x": 0.9, "y": 0.0, "width": 0.9, "height": 0.5}, "right edge"),
    ({"x": 0.0, "y": 0.9, "width": 0.5, "height": 0.9}, "bottom edge"),
])
def test_out_of_frame_crops_rejected(crop, needle):
    plan = _valid_plan()
    ok = {"x": 0.0, "y": 0.0, "width": crop["width"], "height": crop["height"]}
    plan["reframes"] = [{"segmentId": "seg-1", "outputAspectRatio": "9:16",
                         "subjectTarget": "crew", "startCrop": crop,
                         "endCrop": ok}]
    _reject(plan, needle=needle)


@pytest.mark.parametrize("dim", ["width", "height"])
def test_zero_crop_dimensions_rejected(dim):
    crop = {"x": 0.0, "y": 0.0, "width": 0.5, "height": 0.5, dim: 0}
    plan = _valid_plan()
    plan["reframes"] = [{"segmentId": "seg-1", "outputAspectRatio": "9:16",
                         "subjectTarget": "crew", "startCrop": crop,
                         "endCrop": {"x": 0, "y": 0, "width": 0.5,
                                     "height": 0.5}}]
    _reject(plan, needle="schema:")


def test_distorting_crop_aspect_mismatch_rejected():
    plan = _valid_plan()
    plan["reframes"] = [{"segmentId": "seg-1", "outputAspectRatio": "9:16",
                         "subjectTarget": "crew",
                         "startCrop": {"x": 0, "y": 0, "width": 1, "height": 1},
                         "endCrop": {"x": 0, "y": 0, "width": 0.5,
                                     "height": 1}}]
    _reject(plan, needle="aspect mismatch would distort")


def test_transition_longer_than_source_handles_rejected():
    plan = _valid_plan()
    plan["timeline"][1].update(sourceIn=1.0, sourceOut=5.0)   # head handle 1.0 s
    plan["transitions"][0].update(type="dissolve", durationSeconds=1.5)
    _reject(plan, needle="exceeds the available source handles")


def test_valid_ramp_crop_and_transition_accepted_together():
    plan = _valid_plan()
    plan["timeline"][1].update(sourceIn=1.0, sourceOut=5.0)
    plan["transitions"][0].update(type="dissolve", durationSeconds=0.5)
    plan["speedRamps"] = [{"segmentId": "seg-2", "sourceStart": 1.5,
                           "sourceEnd": 4.5, "entrySpeed": 1, "peakSpeed": 3,
                           "exitSpeed": 1, "easing": "ease-in-out",
                           "narrativePurpose": "compress the middle"}]
    plan["reframes"] = [{"segmentId": "seg-1", "outputAspectRatio": "9:16",
                         "subjectTarget": "crew",
                         "startCrop": {"x": 0.1, "y": 0.0, "width": 0.5,
                                       "height": 0.9},
                         "endCrop": {"x": 0.3, "y": 0.05, "width": 0.5,
                                     "height": 0.9}}]
    assert _accept(plan)["status"] == "approved"


# ------------------------- blocker 5: hard failures the model cannot override
@pytest.mark.parametrize("mutate,constraints,rule", [
    (lambda p: p.update(storySentence=_editorial("customer loved it")),
     {}, "claims_grounded"),
    (lambda p: p.update(status="insufficient_footage",
                        achievableDurationSeconds=999.0,
                        missingFootage=[{"beat": "closing reaction",
                                         "shotType": "close-up",
                                         "recommendedDurationSeconds": 5.0,
                                         "why": "the payoff needs a reaction"}]),
     {"durationMin": 45, "durationMax": 60}, "duration_compliant"),
    (lambda p: None, {"style": "no transitions"}, "creative_policy_honored"),
    (lambda p: p.update(speedRamps=[{"segmentId": "seg-2", "sourceStart": 3.0,
                                     "sourceEnd": 2.0, "entrySpeed": 1,
                                     "peakSpeed": 2, "exitSpeed": 1,
                                     "easing": "e", "narrativePurpose": "x"}]),
     {}, "execution_geometry"),
    # unresolved tone directive (not advisory) — hard policy failure
    (lambda p: None, {"tone": "glorpy zebra energy"}, "creative_policy_honored"),
    # duplicate transition boundary — hard execution failure
    (lambda p: p["transitions"].append(
        {"fromSegmentId": "seg-1", "toSegmentId": "seg-2", "type": "hard_cut",
         "durationSeconds": 0, "purpose": "duplicate"}),
     {}, "execution_geometry"),
])
def test_perfect_self_assessment_cannot_pass_hard_failures(mutate, constraints,
                                                           rule):
    plan = _valid_plan()
    plan["modelSelfAssessment"] = _self_assessment(**_MAX_SELF)   # claims 100
    mutate(plan)
    flat = _reject(plan, constraints=constraints)
    assert f"deterministic gate: {rule} failed" in flat


def test_revision_loop_receives_rule_level_feedback():
    bad = _valid_plan()
    bad["storySentence"] = _editorial("customer loved it")
    gen = _gen(bad, _valid_plan())
    out = ep.plan_editorial(_segments(), {}, False, gen)
    assert out["attempts"] == 2
    feedback = gen.calls["parts"][1][-1]["text"]
    assert "implies unsupported factual content" in feedback
    assert "claims_grounded" in feedback


def test_rejected_after_attempt_budget():
    bad = _valid_plan()
    bad["timeline"][0]["segmentId"] = "seg-NOPE"
    with pytest.raises(ep.PlanRejected) as exc:
        ep.plan_editorial(_segments(), {}, False, _gen(bad), max_attempts=3)
    assert len(exc.value.violations_history) == 3


# ------------------------------------------------- retained grounding checks
def test_hook_is_rebound_to_the_opening_cut():
    """2026-08-06: the hook IS the opening cut, so a hook whose source range
    disagrees with timeline[0] is an internal bookkeeping mismatch, not a
    creative choice — the code binds it to the cut that will actually render.
    Nothing about the rendered video changes; only the metadata is made true."""
    plan = _valid_plan()
    plan["hook"].update(sourceIn=8.0, sourceOut=13.0, durationSeconds=5.0)
    result = ep.plan_editorial(_segments(), {}, music_available=False,
                               generate=_gen(plan))
    hook, first = result["plan"]["hook"], result["plan"]["timeline"][0]
    assert hook["segmentId"] == first["segmentId"]
    assert hook["sourceIn"] == first["sourceIn"]
    assert hook["sourceOut"] <= first["sourceOut"]


def test_invented_audio_segment_ids_rejected():
    for field in ("naturalSoundSegmentIds", "jCutSegmentIds", "lCutSegmentIds"):
        plan = _valid_plan()
        plan["audio"][field] = ["seg-INVENTED"]
        _reject(plan, needle=f"audio.{field} references invented segment")


def test_musicless_plan_music_state_is_normalized_not_rejected():
    """musicAvailable is the PROJECT'S authoritative state (2026-08-05): the
    system writes the truth in rather than rejecting the plan for mis-echoing
    it, and erases any phantom musicPlan when no music exists. Real licensing
    fabrication (case 3) still rejects."""
    wrong_flag = _valid_plan()
    wrong_flag["audio"]["musicAvailable"] = True
    wrong_flag["audio"]["musicPlan"] = "an upbeat cue"
    result = ep.plan_editorial(_segments(), {}, music_available=False,
                               generate=_gen(wrong_flag))
    assert result["plan"]["audio"]["musicAvailable"] is False
    assert result["plan"]["audio"]["musicPlan"] is None

    licensed = _valid_plan(music_available=True)
    licensed["audio"]["musicPlan"] = "track licensed under ID 12345"
    _reject(licensed, music=True, needle="licensing metadata")


def test_model_cannot_redefine_platform_or_aspect():
    plan = _valid_plan()
    plan["render"] = {"width": 1920, "height": 1080, "fps": 30, "aspect": "16:9"}
    _reject(plan, constraints={"platform": "vertical"},
            needle="violates the requested platform/aspect")


def test_must_include_and_exclude_are_binding():
    _reject(_valid_plan(), constraints={"mustInclude": ["drone shot"]},
            needle="required moment 'drone shot' is not represented")
    _reject(_valid_plan(), constraints={"mustExclude": ["crew"]},
            needle="excluded moment 'crew' appears")


# ---------------------------------------------------------------- job handler
def _setup_project(fake, status="draft_ready"):
    uid, token = fake.add_user("planner@example.com")
    project = fake.add_project(uid, "Plan Test", status=status)
    for s in _segments():
        fake.insert("segments", {"project_id": project["id"], "user_id": uid,
                                 "asset_id": "asset-1",
                                 "segment_key": s.segmentId,
                                 "source_start": s.sourceStart,
                                 "source_end": s.sourceEnd,
                                 "data": s.model_dump()})
    return uid, token, project


def test_invalid_job_kind_is_rejected_by_the_store(monkeypatch):
    fake = FakeSupabase()
    install(monkeypatch, fake)
    uid, _, project = _setup_project(fake)
    with pytest.raises(RuntimeError, match="kind_check"):
        jobs.enqueue_job(project["id"], uid, "made_up_kind", {})
    assert jobs.enqueue_job(project["id"], uid, "editorial_plan", {})["kind"] \
        == "editorial_plan"


def test_handler_persists_deterministic_result(monkeypatch):
    fake = FakeSupabase()
    install(monkeypatch, fake)
    uid, token, project = _setup_project(fake)
    monkeypatch.setattr(ep, "gemini_generate", _gen(_valid_plan()))
    jobs.enqueue_job(project["id"], uid, "editorial_plan", {"brief": "b"})
    jobs._run_job(jobs._claim_next())
    row = fake.select("pipeline_jobs", f"project_id=eq.{project['id']}")[0]
    assert row["status"] == "completed"
    assert row["artifacts"]["qualityScore"] == 100
    plans = fake.select("editorial_plans", f"project_id=eq.{project['id']}")
    assert len(plans) == 1 and plans[0]["quality_score"] == 100
    assert plans[0]["validation"]["deterministicGate"]["passed"] is True


@pytest.mark.parametrize("prior", ["ready", "draft_ready"])
def test_status_preserved_on_success_failure_and_cancellation(monkeypatch, prior):
    fake = FakeSupabase()
    install(monkeypatch, fake)
    uid, token, project = _setup_project(fake, status=prior)
    monkeypatch.setattr(ep, "gemini_generate", _gen(_valid_plan()))
    jobs.enqueue_job(project["id"], uid, "editorial_plan", {})
    jobs._run_job(jobs._claim_next())
    assert fake.select("projects", f"id=eq.{project['id']}")[0]["status"] == prior

    def explode(parts, schema):
        raise RuntimeError("model unavailable")
    monkeypatch.setattr(ep, "gemini_generate", explode)
    jobs.enqueue_job(project["id"], uid, "editorial_plan", {})
    jobs._run_job(jobs._claim_next())
    assert fake.select("projects", f"id=eq.{project['id']}")[0]["status"] == prior

    plans_before = len(fake.select("editorial_plans",
                                   f"project_id=eq.{project['id']}"))
    monkeypatch.setattr(ep, "gemini_generate", _gen(_valid_plan()))
    job = jobs.enqueue_job(project["id"], uid, "editorial_plan", {})
    claimed = jobs._claim_next()
    fake.patch("pipeline_jobs", f"id=eq.{claimed['id']}",
               {"status": "cancel_requested"})
    claimed["status"] = "processing"
    jobs._run_job(claimed)
    assert fake.select("pipeline_jobs", f"id=eq.{job['id']}")[0]["status"] \
        == "cancelled"
    assert len(fake.select("editorial_plans",
                           f"project_id=eq.{project['id']}")) == plans_before
    assert fake.select("projects", f"id=eq.{project['id']}")[0]["status"] == prior


def test_binding_constraints_reach_the_validator_not_just_the_prompt(monkeypatch):
    fake = FakeSupabase()
    install(monkeypatch, fake)
    uid, token, project = _setup_project(fake)
    monkeypatch.setattr(ep, "gemini_generate", _gen(_valid_plan()))
    jobs.enqueue_job(project["id"], uid, "editorial_plan",
                     {"durationMin": 45, "durationMax": 60})
    jobs._run_job(jobs._claim_next())
    row = fake.select("pipeline_jobs", f"project_id=eq.{project['id']}")[0]
    assert row["status"] == "failed"
    assert "REQUESTED duration range" in row["error_message"]


def test_handler_grounds_music_availability(monkeypatch):
    fake = FakeSupabase()
    install(monkeypatch, fake)
    uid, token, project = _setup_project(fake)
    fake.insert("licensed_music_assets", {
        "project_id": project["id"], "user_id": uid,
        "music_sound_run_id": str(uuid4()), "version": 1})
    monkeypatch.setattr(ep, "gemini_generate",
                        _gen(_valid_plan(music_available=True)))
    jobs.enqueue_job(project["id"], uid, "editorial_plan", {})
    jobs._run_job(jobs._claim_next())
    plans = fake.select("editorial_plans", f"project_id=eq.{project['id']}")
    assert plans[0]["plan"]["audio"]["musicAvailable"] is True


# ------------------------------------------------------------------ endpoints
def test_endpoint_requires_analysis_then_enqueues(monkeypatch):
    from app import main
    main._rate.clear()
    fake = FakeSupabase()
    install(monkeypatch, fake)
    uid, token = fake.add_user("api@example.com")
    project = fake.add_project(uid, "API Test", status="draft_ready")
    client = TestClient(app, raise_server_exceptions=False)
    assert client.post(f"/projects/{project['id']}/editorial-plan",
                       headers=_auth(token), json={}).status_code == 409
    fake.insert("segments", {"project_id": project["id"], "user_id": uid,
                             "data": _segments()[0].model_dump()})
    r = client.post(f"/projects/{project['id']}/editorial-plan",
                    headers=_auth(token),
                    json={"brief": "make it pop", "durationMin": 30,
                          "durationMax": 60, "platform": "vertical",
                          "aspectRatio": "9:16", "style": "hard cuts only"})
    assert r.status_code == 200
    job = r.json()
    assert job["kind"] == "editorial_plan"
    assert job["params"]["style"] == "hard cuts only"
    again = client.post(f"/projects/{project['id']}/editorial-plan",
                        headers=_auth(token), json={})
    assert again.json()["id"] == job["id"]
    assert client.post(f"/projects/{project['id']}/editorial-plan",
                       headers=_auth(token),
                       json={"durationMin": 90, "durationMax": 30}
                       ).status_code == 422


def test_endpoint_rejects_deleted_and_foreign(monkeypatch):
    from app import main
    main._rate.clear()
    fake = FakeSupabase()
    install(monkeypatch, fake)
    uid, token, project = _setup_project(fake)
    _, intruder = fake.add_user("intruder@example.com")
    client = TestClient(app, raise_server_exceptions=False)
    assert client.post(f"/projects/{project['id']}/editorial-plan",
                       headers=_auth(intruder), json={}).status_code == 403
    assert client.delete(f"/projects/{project['id']}",
                         headers=_auth(token)).status_code == 200
    assert client.post(f"/projects/{project['id']}/editorial-plan",
                       headers=_auth(token), json={}).status_code == 404
    assert client.get(f"/projects/{project['id']}/editorial-plan",
                      headers=_auth(token)).status_code == 404


def test_get_returns_latest_plan(monkeypatch):
    from app import main
    main._rate.clear()
    fake = FakeSupabase()
    install(monkeypatch, fake)
    uid, token, project = _setup_project(fake)
    client = TestClient(app, raise_server_exceptions=False)
    monkeypatch.setattr(ep, "gemini_generate", _gen(_valid_plan()))
    jobs.enqueue_job(project["id"], uid, "editorial_plan", {})
    jobs._run_job(jobs._claim_next())
    r = client.get(f"/projects/{project['id']}/editorial-plan", headers=_auth(token))
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "approved" and body["quality_score"] == 100
    assert "ffmpeg" not in str(body["plan"]).lower()
