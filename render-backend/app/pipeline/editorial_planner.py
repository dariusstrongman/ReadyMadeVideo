"""Editorial Planner v1 — a separate, structured planning stage.

Sits BETWEEN analysis (the canonical segment catalog) and timeline generation.
Consumes only real, already-extracted metadata (segments, transcripts, quality
scores, binding constraints) — never raw video — and emits a schema-validated,
EXECUTION-READY EditorialPlan JSON that downstream picture-edit, graphics,
audio, color and render systems can consume.

Trust model (enforced, never assumed):
  * the USER'S REQUEST is authoritative — duration range, platform, aspect,
    tone, style, required/excluded moments and music availability are passed
    into the validator separately; the model cannot redefine or weaken them
  * every planned reference (timeline cuts, hook, captions, graphics,
    transitions, ramps, reframes, audio treatments) must point at REAL catalog
    segments and stay inside their real ranges
  * factual text (captions/graphics/story claims) must carry evidence from the
    transcripts, catalog metadata or the user's own words — invented names,
    numbers, locations, results and reactions are rejected
  * music/licensing metadata is never fabricated: with no licensed music the
    plan carries NO music description at all
  * the plan is DATA — it never contains FFmpeg or filter commands
  * approval is decided by a DETERMINISTIC quality gate computed from
    verifiable properties; the model's self-score is advisory metadata only

The production autoedit pipeline is NOT modified. The model call is injected
(``generate``) so the module is fully unit-testable without network access.
"""
from __future__ import annotations

import json
import os
import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from .schemas import Segment

SCHEMA_VERSION = 1
MAX_ATTEMPTS = 3
APPROVAL_THRESHOLD = 80
TIME_EPSILON = 0.05
BANNED_COMMAND_TOKENS = ("ffmpeg", "filter_complex", "-vf ", "libx264")
MUSIC_FABRICATION_TOKENS = ("bpm", "beat grid", "beatgrid", "phrase map",
                            "licens", "tempo", "track:", "song:")
MAX_SEGMENT_REUSE = 3

_ASPECTS = {"9:16": ("vertical", "tiktok", "reels", "shorts", "portrait"),
            "16:9": ("horizontal", "landscape", "youtube"),
            "1:1": ("square",)}
_SENTENCE_BREAK = re.compile(r"[.!?:]\s+$")
_WORD = re.compile(r"[A-Za-z0-9''-]+")


# ---------------------------------------------------------------- output schema
class StoryScores(BaseModel):
    hookStrength: int = Field(ge=0, le=100)
    storyClarity: int = Field(ge=0, le=100)
    payoffStrength: int = Field(ge=0, le=100)
    footageSupport: int = Field(ge=0, le=100)
    emotionalInterest: int = Field(ge=0, le=100)
    visualVariety: int = Field(ge=0, le=100)
    platformFit: int = Field(ge=0, le=100)
    durationFit: int = Field(ge=0, le=100)
    originality: int = Field(ge=0, le=100)


class StoryOption(BaseModel):
    premise: str
    viewerPromise: str
    hook: str
    structure: str
    payoff: str
    idealDurationSeconds: float = Field(gt=0)
    strengths: list[str]
    weaknesses: list[str]
    footageCoverage: str
    retentionRisks: list[str]
    scores: StoryScores


class HookDesign(BaseModel):
    segmentId: str
    sourceIn: float = Field(ge=0)
    sourceOut: float = Field(gt=0)
    firstFrame: str
    text: str | None = None
    audioCue: str
    durationSeconds: float = Field(gt=0, le=5)
    transitionOut: str
    curiosityCreated: str
    promiseToFulfill: str


class Caption(BaseModel):
    type: Literal["factual", "editorial_label", "cta"]
    text: str = Field(min_length=1, max_length=90)
    sourceSegmentId: str | None = None     # required for factual
    timelineStart: float = Field(ge=0)
    timelineEnd: float = Field(gt=0)
    styleTemplate: str = "default"
    safeArea: str = "platform-safe"
    evidence: str = Field(min_length=1)    # transcript span / user text / "editorial"


class GraphicItem(BaseModel):
    graphicType: str                        # label | step_marker | title | cta | ...
    text: str = Field(min_length=1, max_length=90)
    sourceSegmentId: str | None = None
    timelineStart: float = Field(ge=0)
    timelineEnd: float = Field(gt=0)
    anchor: str = "bottom-center"
    animation: str = "fade"
    durationSeconds: float = Field(gt=0)
    evidence: str = Field(min_length=1)


class TransitionPlan(BaseModel):
    fromSegmentId: str
    toSegmentId: str
    type: str = "cut"
    durationSeconds: float = Field(ge=0, le=2)
    purpose: str


class SpeedRampPlan(BaseModel):
    segmentId: str
    sourceStart: float = Field(ge=0)
    sourceEnd: float = Field(gt=0)
    entrySpeed: float = Field(gt=0, le=8)
    peakSpeed: float = Field(gt=0, le=8)
    exitSpeed: float = Field(gt=0, le=8)
    easing: str = "ease-in-out"
    narrativePurpose: str


class Crop(BaseModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)


class ReframePlan(BaseModel):
    segmentId: str
    outputAspectRatio: str
    subjectTarget: str
    startCrop: Crop
    endCrop: Crop
    trackingMode: str = "static"
    safeArea: str = "platform-safe"


class AudioTreatmentPlan(BaseModel):
    segmentId: str
    gainDb: float = Field(ge=-60, le=12, default=0)
    denoise: bool = False
    preserveNaturalSound: bool = True
    jCutFromSegmentId: str | None = None
    lCutIntoSegmentId: str | None = None
    ducking: bool = False


class ColorStabilizationPlan(BaseModel):
    segmentId: str
    colorPreset: str = "identity"
    stabilizationMode: str = "none"
    cropAllowance: float = Field(ge=0, le=0.2, default=0)


class PacingBeat(BaseModel):
    beat: str
    targetDurationSeconds: float = Field(gt=0)
    energy: float = Field(ge=0, le=1)


class PlannedSegment(BaseModel):
    segmentId: str
    assetId: str
    sourceIn: float = Field(ge=0)
    sourceOut: float = Field(gt=0)
    timelineIn: float = Field(ge=0)
    timelineOut: float = Field(gt=0)
    beat: str
    reason: str
    addsNew: str
    playbackSpeed: float = Field(default=1.0, gt=0, le=8)
    expectedViewerEffect: str


class SoundEffect(BaseModel):
    segmentId: str
    effect: str
    reinforces: str


class AudioDesign(BaseModel):
    naturalSoundSegmentIds: list[str] = Field(default_factory=list)
    jCutSegmentIds: list[str] = Field(default_factory=list)
    lCutSegmentIds: list[str] = Field(default_factory=list)
    musicAvailable: bool
    musicPlan: str | None = None           # MUST be null when music unavailable
    soundEffects: list[SoundEffect] = Field(default_factory=list)


class ModelSelfAssessment(BaseModel):
    """ADVISORY ONLY — approval is decided by the deterministic gate."""
    hook: int = Field(ge=0, le=15)
    storyClarity: int = Field(ge=0, le=15)
    flowContinuity: int = Field(ge=0, le=10)
    pacing: int = Field(ge=0, le=10)
    clipSelection: int = Field(ge=0, le=10)
    payoff: int = Field(ge=0, le=10)
    visualVariety: int = Field(ge=0, le=5)
    creativeTreatment: int = Field(ge=0, le=10)
    soundDesign: int = Field(ge=0, le=5)
    platformFit: int = Field(ge=0, le=5)
    durationCompliance: int = Field(ge=0, le=5)
    hardFailures: list[str] = Field(default_factory=list)


class RetentionRisk(BaseModel):
    position: str
    risk: str
    mitigation: str


class MissingFootage(BaseModel):
    description: str
    purpose: str


class RenderInstructions(BaseModel):
    """Structured render TARGET only — never command strings."""
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fps: float = Field(gt=0)
    aspect: str


class Beat(BaseModel):
    key: str
    purpose: str


class EditorialPlan(BaseModel):
    schemaVersion: Literal[1]
    storySentence: str
    footageSummary: str
    intendedAudience: str
    viewerPromise: str
    options: list[StoryOption] = Field(min_length=3)
    chosenOption: int = Field(ge=0)
    hook: HookDesign
    beats: list[Beat] = Field(min_length=1)
    timeline: list[PlannedSegment] = Field(min_length=1)
    pacing: list[PacingBeat] = Field(min_length=1)
    pacingRationale: str
    transitions: list[TransitionPlan] = Field(default_factory=list)
    transitionsRationale: str
    speedRamps: list[SpeedRampPlan] = Field(default_factory=list)
    reframes: list[ReframePlan] = Field(default_factory=list)
    captions: list[Caption] = Field(default_factory=list)
    graphics: list[GraphicItem] = Field(default_factory=list)
    audio: AudioDesign
    audioTreatments: list[AudioTreatmentPlan] = Field(default_factory=list)
    colorStabilization: list[ColorStabilizationPlan] = Field(default_factory=list)
    plannedDurationSeconds: float = Field(gt=0)
    achievableDurationSeconds: float | None = None   # required for insufficient_footage
    technicalWarnings: list[str] = Field(default_factory=list)
    retentionReview: list[RetentionRisk] = Field(default_factory=list)
    modelSelfAssessment: ModelSelfAssessment
    missingFootage: list[MissingFootage] = Field(default_factory=list)
    render: RenderInstructions
    status: str  # "approved" | "insufficient_footage"


# ------------------------------------------------------------ evidence helpers
_GENERIC_WORDS = {"the", "this", "that", "watch", "see", "how", "why", "what",
                  "when", "before", "after", "step", "done", "final", "result",
                  "results", "start", "finish", "follow", "more", "now", "next",
                  "today", "here", "behind", "scenes", "day", "job", "work"}


def _evidence_pool(segments: list[Segment], constraints: dict) -> str:
    """Everything a factual token may legitimately come from, lowercased."""
    parts: list[str] = []
    for s in segments:
        for field in (s.transcript, s.action, s.location, s.searchText,
                      s.shotType, s.emotion):
            if field:
                parts.append(str(field))
        parts.extend(s.subjects or [])
        parts.extend(s.storyUses or [])
    for key in ("brief", "platform", "tone", "style"):
        if constraints.get(key):
            parts.append(str(constraints[key]))
    for key in ("mustInclude", "mustExclude"):
        parts.extend(str(x) for x in (constraints.get(key) or []))
    return " ".join(parts).lower()


def _unsupported_fact_tokens(text: str, pool: str) -> list[str]:
    """Digit tokens and mid-sentence capitalized tokens with no source."""
    bad: list[str] = []
    sentence_start = True
    for match in _WORD.finditer(text):
        token = match.group(0)
        prefix = text[:match.start()]
        if prefix.strip():
            sentence_start = bool(_SENTENCE_BREAK.search(prefix))
        else:
            sentence_start = True
        lowered = token.lower()
        if any(ch.isdigit() for ch in token):
            if lowered not in pool:
                bad.append(token)
        elif (token[0].isupper() and len(token) >= 3 and not sentence_start
                and lowered not in _GENERIC_WORDS and lowered not in pool):
            bad.append(token)
    return bad


def _aspect_for(value: str | None) -> str | None:
    if not value:
        return None
    v = value.strip().lower()
    for aspect, aliases in _ASPECTS.items():
        if v == aspect or v in aliases:
            return aspect
    return v            # an explicit "4:5"-style value is authoritative as-is


# ------------------------------------------------------------ grounding checks
def validate_plan(plan: EditorialPlan, segments: list[Segment],
                  constraints: dict, music_available: bool) -> list[str]:
    """Grounding + binding-constraint validation. The REQUEST (constraints,
    music_available) is authoritative — never the model's own restatement."""
    v: list[str] = []
    by_id = {s.segmentId: s for s in segments}
    pool = _evidence_pool(segments, constraints)

    if plan.status not in ("approved", "insufficient_footage"):
        v.append(f"unknown status {plan.status!r}")
    if plan.chosenOption >= len(plan.options):
        v.append("chosenOption is out of range")

    blob = plan.model_dump_json().lower()
    for token in BANNED_COMMAND_TOKENS:
        if token in blob:
            v.append(f"plan contains a forbidden command token: {token!r}")

    # -- timeline grounding
    planned_ids: set[str] = set()
    planned_cuts: dict[str, list[tuple[float, float]]] = {}
    seen_ranges: set[tuple[str, float, float]] = set()
    cursor = 0.0
    for i, seg in enumerate(plan.timeline):
        src = by_id.get(seg.segmentId)
        if src is None:
            v.append(f"timeline[{i}] references invented segment {seg.segmentId!r}")
            continue
        planned_ids.add(seg.segmentId)
        planned_cuts.setdefault(seg.segmentId, []).append((seg.sourceIn, seg.sourceOut))
        if seg.assetId != src.assetId:
            v.append(f"timeline[{i}] assetId does not match segment {seg.segmentId}")
        if seg.sourceIn < src.sourceStart - TIME_EPSILON \
                or seg.sourceOut > src.sourceEnd + TIME_EPSILON:
            v.append(f"timeline[{i}] trims outside the real source range of "
                     f"{seg.segmentId} ({src.sourceStart}-{src.sourceEnd})")
        if seg.sourceIn >= seg.sourceOut:
            v.append(f"timeline[{i}] has an empty or inverted source range")
        key = (seg.segmentId, round(seg.sourceIn, 2), round(seg.sourceOut, 2))
        if key in seen_ranges:
            v.append(f"timeline[{i}] repeats an identical range of {seg.segmentId}")
        seen_ranges.add(key)
        if abs(seg.timelineIn - cursor) > TIME_EPSILON:
            v.append(f"timeline[{i}] is not contiguous (gap or overlap at "
                     f"{seg.timelineIn}s, expected {round(cursor, 3)}s)")
        expected = (seg.sourceOut - seg.sourceIn) / seg.playbackSpeed
        if abs((seg.timelineOut - seg.timelineIn) - expected) > TIME_EPSILON:
            v.append(f"timeline[{i}] duration does not match its source range "
                     f"at speed {seg.playbackSpeed}")
        cursor = seg.timelineOut

    if plan.timeline and abs(plan.plannedDurationSeconds - cursor) > 2 * TIME_EPSILON:
        v.append("plannedDurationSeconds does not equal the timeline's real length")

    # -- hook grounding (A)
    hook_src = by_id.get(plan.hook.segmentId)
    if hook_src is None:
        v.append(f"hook references invented segment {plan.hook.segmentId!r}")
    else:
        if plan.hook.sourceIn < hook_src.sourceStart - TIME_EPSILON \
                or plan.hook.sourceOut > hook_src.sourceEnd + TIME_EPSILON:
            v.append("hook trims outside the real source range of its segment")
        if plan.hook.sourceIn >= plan.hook.sourceOut:
            v.append("hook has an empty or inverted source range")
        if abs((plan.hook.sourceOut - plan.hook.sourceIn)
               - plan.hook.durationSeconds) > TIME_EPSILON:
            v.append("hook durationSeconds does not match its source range")
    if plan.timeline:
        first = plan.timeline[0]
        if plan.hook.segmentId != first.segmentId:
            v.append("hook segment is not the first timeline segment")
        elif abs(plan.hook.sourceIn - first.sourceIn) > TIME_EPSILON:
            v.append("hook source range is not linked to the first timeline cut")
        if first.timelineIn > TIME_EPSILON:
            v.append("timeline does not start at 0 — the hook must be immediate")
    if plan.hook.text:
        bad = _unsupported_fact_tokens(plan.hook.text, pool)
        if bad:
            v.append(f"hook text carries unsupported factual tokens: {bad}")

    # -- BINDING duration from the REQUEST, never the model (2)
    lo, hi = constraints.get("durationMin"), constraints.get("durationMax")
    in_range = ((lo is None or plan.plannedDurationSeconds >= float(lo) - TIME_EPSILON)
                and (hi is None or plan.plannedDurationSeconds <= float(hi) + TIME_EPSILON))
    if plan.status == "approved" and not in_range:
        v.append(f"approved plan ({plan.plannedDurationSeconds}s) falls outside "
                 f"the REQUESTED duration range {lo}-{hi}s — extend the edit or "
                 "report insufficient_footage honestly")
    if plan.status == "insufficient_footage":
        if not plan.missingFootage:
            v.append("insufficient_footage requires the exact missing shots")
        if plan.achievableDurationSeconds is None:
            v.append("insufficient_footage requires achievableDurationSeconds")

    # -- BINDING platform / aspect from the REQUEST (2)
    want_aspect = _aspect_for(constraints.get("aspectRatio")) \
        or _aspect_for(constraints.get("platform"))
    if want_aspect and plan.render.aspect != want_aspect:
        v.append(f"render aspect {plan.render.aspect!r} violates the requested "
                 f"platform/aspect ({want_aspect})")
    if plan.render.aspect == "9:16" and plan.render.width >= plan.render.height:
        v.append("9:16 output must be taller than wide")
    if plan.render.aspect == "16:9" and plan.render.height >= plan.render.width:
        v.append("16:9 output must be wider than tall")

    # -- BINDING required / excluded moments (2)
    for needed in constraints.get("mustInclude") or []:
        token = str(needed).lower()
        covered = any(token in _segment_text(by_id[sid]) for sid in planned_ids
                      if sid in by_id)
        if not covered:
            v.append(f"required moment {needed!r} is not represented by any "
                     "selected segment")
    for banned in constraints.get("mustExclude") or []:
        token = str(banned).lower()
        for sid in sorted(planned_ids):
            if sid in by_id and token in _segment_text(by_id[sid]):
                v.append(f"excluded moment {banned!r} appears in selected "
                         f"segment {sid}")

    # -- caption grounding (B)
    for i, cap in enumerate(plan.captions):
        v.extend(_text_item_violations(
            cap.text, cap.type, cap.sourceSegmentId, cap.evidence,
            cap.timelineStart, cap.timelineEnd, by_id, pool,
            plan.plannedDurationSeconds, constraints, f"captions[{i}]"))

    # -- graphics grounding (B/4)
    for i, g in enumerate(plan.graphics):
        kind = "factual" if g.sourceSegmentId else "editorial_label"
        v.extend(_text_item_violations(
            g.text, kind, g.sourceSegmentId, g.evidence,
            g.timelineStart, g.timelineEnd, by_id, pool,
            plan.plannedDurationSeconds, constraints, f"graphics[{i}]"))
        if abs(g.durationSeconds - (g.timelineEnd - g.timelineStart)) > TIME_EPSILON:
            v.append(f"graphics[{i}] durationSeconds does not match its timing")

    # -- audio references (C)
    for field in ("naturalSoundSegmentIds", "jCutSegmentIds", "lCutSegmentIds"):
        for sid in getattr(plan.audio, field):
            if sid not in by_id:
                v.append(f"audio.{field} references invented segment {sid!r}")
            elif sid not in planned_ids:
                v.append(f"audio.{field} references unplanned segment {sid!r}")
    for i, fx in enumerate(plan.audio.soundEffects):
        if fx.segmentId not in planned_ids:
            v.append(f"soundEffects[{i}] references unplanned segment "
                     f"{fx.segmentId!r}")

    # -- music plan (D): availability is AUTHORITATIVE from the project
    if plan.audio.musicAvailable != music_available:
        v.append("audio.musicAvailable does not match the project's real "
                 "licensed-music availability — licensing is never fabricated")
    audio_blob = plan.audio.model_dump_json().lower()
    if not music_available:
        if plan.audio.musicPlan not in (None, "", "none", "disabled"):
            v.append("no licensed music exists — musicPlan must be null/none, "
                     "not a description of music that does not exist")
        for token in MUSIC_FABRICATION_TOKENS:
            if token in audio_blob:
                v.append(f"music-less plan fabricates music metadata: {token!r}")
    else:
        if not plan.audio.musicPlan:
            v.append("licensed music exists but musicPlan is empty")
        elif "licens" in plan.audio.musicPlan.lower():
            v.append("musicPlan restates licensing metadata — license data lives "
                     "only in the licensed music records, never in the plan")

    # -- structured department sections reference planned segments (4)
    tl_order = [seg.segmentId for seg in plan.timeline]
    for i, tr in enumerate(plan.transitions):
        adjacent = any(tl_order[j] == tr.fromSegmentId
                       and tl_order[j + 1] == tr.toSegmentId
                       for j in range(len(tl_order) - 1))
        if not adjacent:
            v.append(f"transitions[{i}] {tr.fromSegmentId}->{tr.toSegmentId} does "
                     "not join two adjacent timeline segments")
    for i, ramp in enumerate(plan.speedRamps):
        cuts = planned_cuts.get(ramp.segmentId)
        if not cuts:
            v.append(f"speedRamps[{i}] references unplanned segment "
                     f"{ramp.segmentId!r}")
        elif not any(ramp.sourceStart >= a - TIME_EPSILON
                     and ramp.sourceEnd <= b + TIME_EPSILON for a, b in cuts):
            v.append(f"speedRamps[{i}] range is outside the planned cut of "
                     f"{ramp.segmentId}")
    for i, rf in enumerate(plan.reframes):
        if rf.segmentId not in planned_ids:
            v.append(f"reframes[{i}] references unplanned segment {rf.segmentId!r}")
        if rf.outputAspectRatio != plan.render.aspect:
            v.append(f"reframes[{i}] aspect {rf.outputAspectRatio!r} does not "
                     f"match the render target {plan.render.aspect!r}")
    for i, at in enumerate(plan.audioTreatments):
        if at.segmentId not in planned_ids:
            v.append(f"audioTreatments[{i}] references unplanned segment "
                     f"{at.segmentId!r}")
        for ref in (at.jCutFromSegmentId, at.lCutIntoSegmentId):
            if ref and ref not in planned_ids:
                v.append(f"audioTreatments[{i}] J/L cut references unplanned "
                         f"segment {ref!r}")
    for i, cs in enumerate(plan.colorStabilization):
        if cs.segmentId not in planned_ids:
            v.append(f"colorStabilization[{i}] references unplanned segment "
                     f"{cs.segmentId!r}")
    beat_keys = {b.key for b in plan.beats}
    for i, pb in enumerate(plan.pacing):
        if pb.beat not in beat_keys:
            v.append(f"pacing[{i}] references unknown beat {pb.beat!r}")

    # -- story claims carry evidence (E)
    chosen = plan.options[plan.chosenOption] if plan.chosenOption < len(plan.options) \
        else None
    claim_texts = [("storySentence", plan.storySentence),
                   ("viewerPromise", plan.viewerPromise)]
    if chosen:
        claim_texts += [("chosen premise", chosen.premise),
                        ("chosen payoff", chosen.payoff)]
    for label, text in claim_texts:
        bad = _unsupported_fact_tokens(text, pool)
        if bad:
            v.append(f"{label} carries unsupported factual tokens "
                     f"(no transcript/catalog/user source): {bad}")
    return v


def _segment_text(s: Segment) -> str:
    return " ".join(str(x) for x in [s.action, s.transcript or "", s.location,
                                     s.searchText, " ".join(s.subjects or []),
                                     " ".join(s.storyUses or [])]).lower()


def _text_item_violations(text: str, kind: str, source_id: str | None,
                          evidence: str, start: float, end: float,
                          by_id: dict, pool: str, planned_duration: float,
                          constraints: dict, where: str) -> list[str]:
    out: list[str] = []
    if start >= end:
        out.append(f"{where} has an empty or inverted timeline range")
    if end > planned_duration + TIME_EPSILON:
        out.append(f"{where} extends past the end of the video")
    if kind == "factual":
        if not source_id:
            out.append(f"{where} is factual but has no sourceSegmentId")
        elif source_id not in by_id:
            out.append(f"{where} references invented segment {source_id!r}")
        else:
            src = by_id[source_id]
            user_text = " ".join(str(constraints.get(k) or "")
                                 for k in ("brief", "tone", "style")).lower()
            ev = evidence.strip().lower()
            supported = (src.transcript and ev in src.transcript.lower()) \
                or (ev and ev in user_text) or (ev and ev in _segment_text(src))
            if not supported:
                out.append(f"{where} evidence is not found in the segment's "
                           "transcript/metadata or the user's own words")
    bad = _unsupported_fact_tokens(text, pool)
    if bad:
        out.append(f"{where} text carries unsupported factual tokens: {bad}")
    return out


# ---------------------------------------------------- deterministic quality gate
def deterministic_gate(plan: EditorialPlan, segments: list[Segment],
                       constraints: dict, violations: list[str]) -> dict:
    """Independent pass/fail from VERIFIABLE properties. The model's own
    self-assessment never feeds this."""
    by_id = {s.segmentId: s for s in segments}
    used = [seg.segmentId for seg in plan.timeline]
    rules: list[dict] = []

    def rule(name: str, weight: int, hard: bool, passed: bool, detail: str):
        rules.append({"rule": name, "weight": weight, "hard": hard,
                      "passed": passed, "detail": detail})

    def no_violation(*needles: str) -> bool:
        return not any(any(n in viol for n in needles) for viol in violations)

    rule("hook_grounded", 15, True,
         no_violation("hook"), "hook exists, in range, first and immediate")
    rule("timeline_contiguous", 15, True,
         no_violation("contiguous", "invented segment", "source range",
                      "duration does not match", "timeline's real length"),
         "every cut is real, in range and contiguous from 0")
    rule("claims_grounded", 10, True,
         no_violation("unsupported factual tokens", "evidence is not found",
                      "invented", "fabricat"),
         "no invented names/numbers/locations/results/claims")
    lo, hi = constraints.get("durationMin"), constraints.get("durationMax")
    duration_ok = ((lo is None or plan.plannedDurationSeconds >= float(lo) - TIME_EPSILON)
                   and (hi is None
                        or plan.plannedDurationSeconds <= float(hi) + TIME_EPSILON)) \
        or plan.status == "insufficient_footage"
    rule("duration_compliant", 10, True, duration_ok,
         "planned duration honors the REQUEST (or shortfall reported honestly)")
    rule("story_structure", 10, True,
         len(plan.options) >= 3 and plan.chosenOption < len(plan.options)
         and len(plan.beats) >= 2 and bool(plan.storySentence.strip()),
         ">=3 scored options, chosen concept, beat arc, one-sentence story")
    payoff_words = ("payoff", "result", "reveal", "closing", "outcome", "after")
    rule("payoff_present", 10, True,
         any(any(w in seg.beat.lower() for w in payoff_words)
             for seg in plan.timeline[-2:])
         or any(any(w in b.key.lower() for w in payoff_words) for b in plan.beats),
         "the edit ends on a payoff beat, not an accidental stop")
    rule("music_grounded", 5, True,
         no_violation("music", "licens"),
         "music availability and licensing are real, never fabricated")
    rule("audio_refs_valid", 5, False,
         no_violation("audio.", "soundEffects", "audioTreatments"),
         "every audio reference points at a planned segment")
    reuse_ok = (not used) or max(used.count(u) for u in set(used)) <= MAX_SEGMENT_REUSE
    rule("no_redundant_reuse", 5, False,
         reuse_ok and no_violation("repeats an identical range"),
         f"no segment used more than {MAX_SEGMENT_REUSE}x, no duplicate ranges")
    shot_types = {by_id[u].shotType for u in set(used)
                  if u in by_id and by_id[u].shotType}
    variety_ok = len(set(used)) >= min(2, len(segments)) \
        and (len(shot_types) >= 2 if len(shot_types) else True)
    rule("visual_variety", 5, False, variety_ok,
         "multiple distinct segments / shot types where the catalog offers them")
    style_text = " ".join(str(constraints.get(k) or "")
                          for k in ("style", "tone")).lower()
    wants_treatment = any(w in style_text for w in
                          ("ramp", "speed", "montage", "cinematic", "dynamic"))
    rule("requested_treatments", 5, False,
         (not wants_treatment) or bool(plan.speedRamps or plan.transitions),
         "requested creative treatments are represented in the plan")
    flagged = [u for u in set(used) if u in by_id and by_id[u].problems]
    rule("technical_warnings_surfaced", 5, False,
         (not flagged) or bool(plan.technicalWarnings),
         "known segment problems surface as technical warnings")

    score = sum(r["weight"] for r in rules if r["passed"])
    hard_failures = [r["rule"] for r in rules if r["hard"] and not r["passed"]]
    return {"rules": rules, "score": score, "hardFailures": hard_failures,
            "passed": score >= APPROVAL_THRESHOLD and not hard_failures}


# ----------------------------------------------------------------- LLM harness
_SPEC = """You are Stromation's Senior Story Editor and Creative Director.
Transform the REAL segment catalog below into one intentionally-directed edit.
A technically valid but random, boring, confusing or padded edit is a failure.

Think before you cut:
1. UNDERSTAND every segment (what happens, who, where, narrative/emotional/
   informational value, quality, uniqueness, role: hook/payoff/context/setup/
   problem/process/escalation/reaction/result/proof/transition/CTA/unusable).
2. STATE the real story in one sentence. If no genuine change/question/payoff
   exists, choose the strongest HONEST format — never pretend.
3. GENERATE at least three distinct story options, score each 0-100 per
   dimension, choose the best option THE FOOTAGE ACTUALLY SUPPORTS.
4. DESIGN the hook: it IS the first timeline cut (same segment, same sourceIn),
   starts at 0, max 5 seconds, honest promise, legible without audio.
5. BUILD intentional beats; give every beat a pacing target (seconds + energy).
6. The USER CONSTRAINTS below are BINDING: duration range, platform/aspect,
   tone, style, required and excluded moments. You cannot restate, weaken or
   ignore them. If the footage cannot honor the duration, set status
   "insufficient_footage", give achievableDurationSeconds and list the exact
   missing shots.
7. SELECT for narrative value; enter late, exit early, use action matching.
8. Express EVERY execution decision as structured data:
   - transitions: {from,to,type,duration,purpose} joining ADJACENT cuts
   - speedRamps: {segmentId, sourceStart/End inside that cut, entry/peak/exit
     speed, easing, narrativePurpose}
   - reframes: {segmentId, outputAspectRatio matching the render target,
     subjectTarget, startCrop/endCrop as normalized x/y/width/height,
     trackingMode, safeArea}
   - audioTreatments: {segmentId, gainDb, denoise, preserveNaturalSound,
     jCutFromSegmentId, lCutIntoSegmentId, ducking}
   - colorStabilization: {segmentId, colorPreset, stabilizationMode,
     cropAllowance}
9. CAPTIONS and GRAPHICS carry information, never decoration, and every one is
   timed (timelineStart/End) with an evidence field:
   - type "factual": sourceSegmentId REQUIRED; evidence must be a literal span
     from that segment's transcript/metadata or the user's own words
   - type "editorial_label" / "cta": creative wording allowed but NO names,
     numbers, places, results or reactions that the footage/user text does not
     contain; set evidence to "editorial"
   NEVER invent facts, places, customer reactions, results or claims.
10. SOUND: preserve meaningful natural sound; J/L cuts by segment id. Set
    audio.musicAvailable EXACTLY as given. With no licensed music, musicPlan
    MUST be null and nothing music-related may be described — never fabricate
    tracks, BPM, beat grids or licenses.
11. Fill retentionReview (swipe-away points + mitigations) and technicalWarnings
    (surface every known problem of a segment you used).
12. modelSelfAssessment is ADVISORY ONLY — an independent deterministic gate
    decides approval from verifiable properties. Score honestly anyway.

MECHANICAL RULES (validated, violations are rejected):
- use ONLY segmentIds from the catalog; trim ONLY inside each segment's real
  sourceStart-sourceEnd; assetId must match the catalog
- the timeline is contiguous from 0; timelineOut-timelineIn equals
  (sourceOut-sourceIn)/playbackSpeed; plannedDurationSeconds equals the final
  timelineOut; the hook is timeline[0] with the same sourceIn
- never repeat an identical source range
- schemaVersion is exactly 1
- output structured data only — NO ffmpeg, filter strings or shell commands
"""


def _response_schema() -> dict:
    """Gemini structured-output schema (OpenAPI subset, uppercase types)."""
    def s(t, **kw):
        return {"type": t, **kw}
    scores = s("OBJECT", properties={k: s("INTEGER") for k in (
        "hookStrength", "storyClarity", "payoffStrength", "footageSupport",
        "emotionalInterest", "visualVariety", "platformFit", "durationFit",
        "originality")},
        required=["hookStrength", "storyClarity", "payoffStrength",
                  "footageSupport", "emotionalInterest", "visualVariety",
                  "platformFit", "durationFit", "originality"])
    option = s("OBJECT", properties={
        "premise": s("STRING"), "viewerPromise": s("STRING"), "hook": s("STRING"),
        "structure": s("STRING"), "payoff": s("STRING"),
        "idealDurationSeconds": s("NUMBER"),
        "strengths": s("ARRAY", items=s("STRING")),
        "weaknesses": s("ARRAY", items=s("STRING")),
        "footageCoverage": s("STRING"),
        "retentionRisks": s("ARRAY", items=s("STRING")), "scores": scores},
        required=["premise", "viewerPromise", "hook", "structure", "payoff",
                  "idealDurationSeconds", "strengths", "weaknesses",
                  "footageCoverage", "retentionRisks", "scores"])
    caption = s("OBJECT", properties={
        "type": s("STRING"), "text": s("STRING"), "sourceSegmentId": s("STRING"),
        "timelineStart": s("NUMBER"), "timelineEnd": s("NUMBER"),
        "styleTemplate": s("STRING"), "safeArea": s("STRING"),
        "evidence": s("STRING")},
        required=["type", "text", "timelineStart", "timelineEnd", "evidence"])
    graphic = s("OBJECT", properties={
        "graphicType": s("STRING"), "text": s("STRING"),
        "sourceSegmentId": s("STRING"), "timelineStart": s("NUMBER"),
        "timelineEnd": s("NUMBER"), "anchor": s("STRING"),
        "animation": s("STRING"), "durationSeconds": s("NUMBER"),
        "evidence": s("STRING")},
        required=["graphicType", "text", "timelineStart", "timelineEnd",
                  "durationSeconds", "evidence"])
    transition = s("OBJECT", properties={
        "fromSegmentId": s("STRING"), "toSegmentId": s("STRING"),
        "type": s("STRING"), "durationSeconds": s("NUMBER"),
        "purpose": s("STRING")},
        required=["fromSegmentId", "toSegmentId", "type", "durationSeconds",
                  "purpose"])
    ramp = s("OBJECT", properties={
        "segmentId": s("STRING"), "sourceStart": s("NUMBER"),
        "sourceEnd": s("NUMBER"), "entrySpeed": s("NUMBER"),
        "peakSpeed": s("NUMBER"), "exitSpeed": s("NUMBER"), "easing": s("STRING"),
        "narrativePurpose": s("STRING")},
        required=["segmentId", "sourceStart", "sourceEnd", "entrySpeed",
                  "peakSpeed", "exitSpeed", "easing", "narrativePurpose"])
    crop = s("OBJECT", properties={"x": s("NUMBER"), "y": s("NUMBER"),
                                   "width": s("NUMBER"), "height": s("NUMBER")},
             required=["x", "y", "width", "height"])
    reframe = s("OBJECT", properties={
        "segmentId": s("STRING"), "outputAspectRatio": s("STRING"),
        "subjectTarget": s("STRING"), "startCrop": crop, "endCrop": crop,
        "trackingMode": s("STRING"), "safeArea": s("STRING")},
        required=["segmentId", "outputAspectRatio", "subjectTarget",
                  "startCrop", "endCrop"])
    audio_treatment = s("OBJECT", properties={
        "segmentId": s("STRING"), "gainDb": s("NUMBER"), "denoise": s("BOOLEAN"),
        "preserveNaturalSound": s("BOOLEAN"),
        "jCutFromSegmentId": s("STRING"), "lCutIntoSegmentId": s("STRING"),
        "ducking": s("BOOLEAN")}, required=["segmentId"])
    color = s("OBJECT", properties={
        "segmentId": s("STRING"), "colorPreset": s("STRING"),
        "stabilizationMode": s("STRING"), "cropAllowance": s("NUMBER")},
        required=["segmentId"])
    pacing = s("OBJECT", properties={
        "beat": s("STRING"), "targetDurationSeconds": s("NUMBER"),
        "energy": s("NUMBER")},
        required=["beat", "targetDurationSeconds", "energy"])
    planned = s("OBJECT", properties={
        "segmentId": s("STRING"), "assetId": s("STRING"),
        "sourceIn": s("NUMBER"), "sourceOut": s("NUMBER"),
        "timelineIn": s("NUMBER"), "timelineOut": s("NUMBER"),
        "beat": s("STRING"), "reason": s("STRING"), "addsNew": s("STRING"),
        "playbackSpeed": s("NUMBER"), "expectedViewerEffect": s("STRING")},
        required=["segmentId", "assetId", "sourceIn", "sourceOut", "timelineIn",
                  "timelineOut", "beat", "reason", "addsNew",
                  "expectedViewerEffect"])
    assessment = s("OBJECT", properties={**{k: s("INTEGER") for k in (
        "hook", "storyClarity", "flowContinuity", "pacing", "clipSelection",
        "payoff", "visualVariety", "creativeTreatment", "soundDesign",
        "platformFit", "durationCompliance")},
        "hardFailures": s("ARRAY", items=s("STRING"))},
        required=["hook", "storyClarity", "flowContinuity", "pacing",
                  "clipSelection", "payoff", "visualVariety",
                  "creativeTreatment", "soundDesign", "platformFit",
                  "durationCompliance"])
    return s("OBJECT", properties={
        "schemaVersion": s("INTEGER"), "storySentence": s("STRING"),
        "footageSummary": s("STRING"), "intendedAudience": s("STRING"),
        "viewerPromise": s("STRING"),
        "options": s("ARRAY", items=option), "chosenOption": s("INTEGER"),
        "hook": s("OBJECT", properties={
            "segmentId": s("STRING"), "sourceIn": s("NUMBER"),
            "sourceOut": s("NUMBER"), "firstFrame": s("STRING"),
            "text": s("STRING"), "audioCue": s("STRING"),
            "durationSeconds": s("NUMBER"), "transitionOut": s("STRING"),
            "curiosityCreated": s("STRING"), "promiseToFulfill": s("STRING")},
            required=["segmentId", "sourceIn", "sourceOut", "firstFrame",
                      "audioCue", "durationSeconds", "transitionOut",
                      "curiosityCreated", "promiseToFulfill"]),
        "beats": s("ARRAY", items=s("OBJECT", properties={
            "key": s("STRING"), "purpose": s("STRING")},
            required=["key", "purpose"])),
        "timeline": s("ARRAY", items=planned),
        "pacing": s("ARRAY", items=pacing), "pacingRationale": s("STRING"),
        "transitions": s("ARRAY", items=transition),
        "transitionsRationale": s("STRING"),
        "speedRamps": s("ARRAY", items=ramp),
        "reframes": s("ARRAY", items=reframe),
        "captions": s("ARRAY", items=caption),
        "graphics": s("ARRAY", items=graphic),
        "audio": s("OBJECT", properties={
            "naturalSoundSegmentIds": s("ARRAY", items=s("STRING")),
            "jCutSegmentIds": s("ARRAY", items=s("STRING")),
            "lCutSegmentIds": s("ARRAY", items=s("STRING")),
            "musicAvailable": s("BOOLEAN"), "musicPlan": s("STRING"),
            "soundEffects": s("ARRAY", items=s("OBJECT", properties={
                "segmentId": s("STRING"), "effect": s("STRING"),
                "reinforces": s("STRING")},
                required=["segmentId", "effect", "reinforces"]))},
            required=["musicAvailable"]),
        "audioTreatments": s("ARRAY", items=audio_treatment),
        "colorStabilization": s("ARRAY", items=color),
        "plannedDurationSeconds": s("NUMBER"),
        "achievableDurationSeconds": s("NUMBER"),
        "technicalWarnings": s("ARRAY", items=s("STRING")),
        "retentionReview": s("ARRAY", items=s("OBJECT", properties={
            "position": s("STRING"), "risk": s("STRING"),
            "mitigation": s("STRING")},
            required=["position", "risk", "mitigation"])),
        "modelSelfAssessment": assessment,
        "missingFootage": s("ARRAY", items=s("OBJECT", properties={
            "description": s("STRING"), "purpose": s("STRING")},
            required=["description", "purpose"])),
        "render": s("OBJECT", properties={
            "width": s("INTEGER"), "height": s("INTEGER"), "fps": s("NUMBER"),
            "aspect": s("STRING")},
            required=["width", "height", "fps", "aspect"]),
        "status": s("STRING")},
        required=["schemaVersion", "storySentence", "footageSummary",
                  "intendedAudience", "viewerPromise", "options", "chosenOption",
                  "hook", "beats", "timeline", "pacing", "pacingRationale",
                  "transitionsRationale", "audio", "plannedDurationSeconds",
                  "modelSelfAssessment", "render", "status"])


def _catalog_json(segments: list[Segment]) -> str:
    return json.dumps([{
        "segmentId": s.segmentId, "assetId": s.assetId,
        "sourceStart": s.sourceStart, "sourceEnd": s.sourceEnd,
        "action": s.action, "shotType": s.shotType,
        "cameraMovement": s.cameraMovement, "location": s.location,
        "transcript": s.transcript, "storyUses": s.storyUses,
        "emotion": s.emotion, "motionIntensity": s.motionIntensity,
        "focusScore": s.focusScore, "stabilityScore": s.stabilityScore,
        "audioScore": s.audioScore, "problems": s.problems,
    } for s in segments], indent=None)


def _constraints_text(constraints: dict, music_available: bool) -> str:
    lines = ["USER CONSTRAINTS (BINDING — the validator enforces these against "
             "the ORIGINAL request; you cannot redefine them):"]
    for key in ("brief", "platform", "aspectRatio", "tone", "style",
                "durationMin", "durationMax", "mustInclude", "mustExclude"):
        if constraints.get(key) not in (None, "", []):
            lines.append(f"- {key}: {constraints[key]}")
    lines.append(f"- licensed music available for this project: {music_available}"
                 " (set audio.musicAvailable to exactly this value)")
    return "\n".join(lines)


class PlanRejected(RuntimeError):
    """The model could not produce a grounded plan within the attempt budget."""

    def __init__(self, violations_history: list[list[str]]):
        self.violations_history = violations_history
        last = violations_history[-1] if violations_history else []
        super().__init__("editorial plan rejected after "
                         f"{len(violations_history)} attempt(s); last "
                         f"violations: {'; '.join(last[:5])}")


def plan_editorial(segments: list[Segment], constraints: dict,
                   music_available: bool, generate,
                   max_attempts: int = MAX_ATTEMPTS) -> dict:
    """Produce a grounded, deterministically-gated EditorialPlan.

    ``generate(parts, schema) -> dict`` is the injected model call. Returns
    {"plan", "attempts", "status", "qualityScore" (deterministic),
     "deterministicGate", "violationsHistory"}. Raises PlanRejected when the
    attempt budget is exhausted.
    """
    if not segments:
        raise RuntimeError("no segment catalog — run analysis first")
    base_parts = [
        {"text": _SPEC},
        {"text": _constraints_text(constraints, music_available)},
        {"text": "SEGMENT CATALOG (the ONLY footage that exists):\n"
                 + _catalog_json(segments)},
    ]
    schema = _response_schema()
    history: list[list[str]] = []
    feedback: list[dict] = []
    for _attempt in range(1, max_attempts + 1):
        raw = generate(base_parts + feedback, schema)
        try:
            plan = EditorialPlan(**raw)
        except ValidationError as exc:
            violations = [f"schema: {e['loc']} {e['msg']}"
                          for e in exc.errors()[:10]]
            gate = None
        else:
            violations = validate_plan(plan, segments, constraints,
                                       music_available)
            gate = deterministic_gate(plan, segments, constraints, violations)
            # Approval is DETERMINISTIC: the model cannot self-approve.
            if plan.status == "approved" and not gate["passed"]:
                failed = [r for r in gate["rules"] if not r["passed"]]
                violations = violations + [
                    f"deterministic gate: {r['rule']} failed ({r['detail']})"
                    for r in failed] + [
                    f"deterministic gate score {gate['score']} "
                    f"(threshold {APPROVAL_THRESHOLD}); the model's "
                    "self-assessment does not decide approval"]
            if not violations:
                return {"plan": plan.model_dump(), "attempts": _attempt,
                        "qualityScore": gate["score"], "status": plan.status,
                        "deterministicGate": gate,
                        "violationsHistory": history}
        history.append(violations)
        feedback = [{"text": "YOUR PREVIOUS PLAN WAS REJECTED. Violations:\n- "
                             + "\n- ".join(violations)
                             + "\nRevise the plan to fix EVERY violation. Do "
                               "not invent footage, claims or music to fix "
                               "them — report insufficient_footage honestly "
                               "if needed."}]
    raise PlanRejected(history)


def gemini_generate(parts: list[dict], schema: dict) -> dict:
    """Production model call (kept tiny + injected so tests never hit it)."""
    from .gemini_common import generate_json
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not configured")
    model = os.environ.get("EDITORIAL_PLANNER_MODEL", "gemini-2.5-pro")
    return generate_json(model, parts, schema, api_key,
                         temperature=0.4, timeout=600)
