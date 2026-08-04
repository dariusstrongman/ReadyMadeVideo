"""Editorial Planner v1 — a separate, structured planning stage.

Sits BETWEEN analysis (the canonical segment catalog) and timeline generation.
Consumes only real, already-extracted metadata (segments, transcripts, quality
scores, constraints) — never raw video — and emits a schema-validated
EditorialPlan JSON that downstream picture-edit, graphics, audio, color and
render systems can consume.

Hard rules (enforced by the grounding validator, not by trust in the model):
  * every planned segment references a REAL catalog segment and stays inside
    its real source range — invented footage is rejected
  * music may only be planned when the project actually has licensed music —
    licensing metadata is never fabricated
  * graphic/caption text must declare an honest claim source (transcript /
    user prompt / visible footage / branding asset)
  * the plan is DATA — it never contains FFmpeg commands or filter strings
  * a plan is approved only at quality-gate >= 80 with no hard failures;
    otherwise the model revises (bounded) or reports insufficient_footage
    honestly with the exact missing shots

The production autoedit pipeline is NOT modified: this stage is additive and
optional. The model call is injected (``generate``) so the module is fully
unit-testable without network access.
"""
from __future__ import annotations

import json
import os

from pydantic import BaseModel, Field, ValidationError

from .schemas import Segment

SCHEMA_VERSION = 1
MAX_ATTEMPTS = 3
APPROVAL_THRESHOLD = 80
TIME_EPSILON = 0.05      # seconds of tolerance on range / continuity checks
BANNED_COMMAND_TOKENS = ("ffmpeg", "filter_complex", "-vf ", "libx264")
CLAIM_SOURCES = ("transcript", "user_prompt", "footage_visible", "branding_asset")


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


class SpeedRamp(BaseModel):
    purpose: str
    entrySpeed: float = Field(gt=0, le=8)
    peakSpeed: float = Field(gt=0, le=8)
    exitSpeed: float = Field(gt=0, le=8)
    easing: str
    soundCue: str | None = None
    whyNotACut: str


class Graphic(BaseModel):
    text: str = Field(min_length=1, max_length=90)
    purpose: str
    claimSource: str          # validated against CLAIM_SOURCES + segment truth
    placement: str = "platform-safe"


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
    reframe: str | None = None
    speedRamp: SpeedRamp | None = None
    transitionOut: str = "cut"
    graphic: Graphic | None = None
    audioTreatment: str = "natural"
    expectedViewerEffect: str


class SoundEffect(BaseModel):
    segmentId: str
    effect: str
    reinforces: str           # must reinforce REAL movement/emphasis


class AudioDesign(BaseModel):
    naturalSoundSegmentIds: list[str] = Field(default_factory=list)
    jCutSegmentIds: list[str] = Field(default_factory=list)
    lCutSegmentIds: list[str] = Field(default_factory=list)
    musicAvailable: bool
    musicPlan: str
    soundEffects: list[SoundEffect] = Field(default_factory=list)


class QualityGate(BaseModel):
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

    @property
    def total(self) -> int:
        return (self.hook + self.storyClarity + self.flowContinuity + self.pacing
                + self.clipSelection + self.payoff + self.visualVariety
                + self.creativeTreatment + self.soundDesign + self.platformFit
                + self.durationCompliance)


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
    schemaVersion: int = SCHEMA_VERSION
    storySentence: str
    footageSummary: str
    intendedAudience: str
    viewerPromise: str
    options: list[StoryOption] = Field(min_length=3)
    chosenOption: int = Field(ge=0)
    hook: HookDesign
    beats: list[Beat] = Field(min_length=1)
    timeline: list[PlannedSegment] = Field(min_length=1)
    pacingProfile: str
    transitionsRationale: str
    captions: list[Graphic] = Field(default_factory=list)
    audio: AudioDesign
    colorReframeNotes: str
    requestedDurationMin: float | None = None
    requestedDurationMax: float | None = None
    plannedDurationSeconds: float = Field(gt=0)
    technicalWarnings: list[str] = Field(default_factory=list)
    retentionReview: list[RetentionRisk] = Field(default_factory=list)
    qualityGate: QualityGate
    missingFootage: list[MissingFootage] = Field(default_factory=list)
    render: RenderInstructions
    status: str  # "approved" | "insufficient_footage"


# ------------------------------------------------------------ grounding checks
def validate_plan(plan: EditorialPlan, segments: list[Segment],
                  music_available: bool) -> list[str]:
    """Every violated rule becomes one message; an empty list means grounded."""
    v: list[str] = []
    by_id = {s.segmentId: s for s in segments}

    if plan.status not in ("approved", "insufficient_footage"):
        v.append(f"unknown status {plan.status!r}")
    if plan.chosenOption >= len(plan.options):
        v.append("chosenOption is out of range")

    # -- no command strings anywhere (the plan is data, not FFmpeg)
    blob = plan.model_dump_json().lower()
    for token in BANNED_COMMAND_TOKENS:
        if token in blob:
            v.append(f"plan contains a forbidden command token: {token!r}")

    # -- timeline grounding: real segments, real ranges, real continuity
    seen_ranges: set[tuple[str, float, float]] = set()
    cursor = 0.0
    for i, seg in enumerate(plan.timeline):
        src = by_id.get(seg.segmentId)
        if src is None:
            v.append(f"timeline[{i}] references invented segment {seg.segmentId!r}")
            continue
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
        if seg.graphic:
            v.extend(_graphic_violations(seg.graphic, src, f"timeline[{i}].graphic"))

    if plan.timeline and abs(plan.plannedDurationSeconds - cursor) > 2 * TIME_EPSILON:
        v.append("plannedDurationSeconds does not equal the timeline's real length")

    # -- the hook is the first thing the viewer sees
    if plan.timeline:
        first = plan.timeline[0]
        if plan.hook.segmentId != first.segmentId:
            v.append("hook segment is not the first timeline segment")
        if first.timelineIn > TIME_EPSILON:
            v.append("timeline does not start at 0 — the hook must be immediate")
    if plan.hook.segmentId not in by_id:
        v.append(f"hook references invented segment {plan.hook.segmentId!r}")

    # -- duration is a hard creative constraint
    lo, hi = plan.requestedDurationMin, plan.requestedDurationMax
    in_range = ((lo is None or plan.plannedDurationSeconds >= lo - TIME_EPSILON)
                and (hi is None or plan.plannedDurationSeconds <= hi + TIME_EPSILON))
    if plan.status == "approved" and not in_range:
        v.append("approved plan falls outside the requested duration range — "
                 "either extend the edit or report insufficient_footage with the "
                 "exact missing shots")
    if plan.status == "insufficient_footage" and not plan.missingFootage:
        v.append("insufficient_footage requires the exact missing shots")

    # -- never fabricate music or licensing
    if plan.audio.musicAvailable != music_available:
        v.append("audio.musicAvailable does not match the project's real "
                 "licensed-music availability — licensing is never fabricated")
    for i, fx in enumerate(plan.audio.soundEffects):
        if fx.segmentId not in by_id:
            v.append(f"soundEffects[{i}] references invented segment {fx.segmentId!r}")

    # -- captions carry honest claim sources
    for i, cap in enumerate(plan.captions):
        v.extend(_graphic_violations(cap, None, f"captions[{i}]"))

    # -- the quality gate is binding
    if plan.status == "approved":
        if plan.qualityGate.hardFailures:
            v.append(f"approved plan carries hard failures: "
                     f"{plan.qualityGate.hardFailures}")
        if plan.qualityGate.total < APPROVAL_THRESHOLD:
            v.append(f"approved plan scores {plan.qualityGate.total} — below the "
                     f"{APPROVAL_THRESHOLD} gate; revise or report honestly")
    return v


def _graphic_violations(g: Graphic, src: Segment | None, where: str) -> list[str]:
    out: list[str] = []
    if g.claimSource not in CLAIM_SOURCES:
        out.append(f"{where} claimSource {g.claimSource!r} is not an honest source "
                   f"({'/'.join(CLAIM_SOURCES)})")
    if g.claimSource == "transcript" and src is not None and not src.transcript:
        out.append(f"{where} claims a transcript source but segment "
                   f"{src.segmentId} has no transcript")
    return out


# ----------------------------------------------------------------- LLM harness
_SPEC = """You are Stromation's Senior Story Editor and Creative Director.
Transform the REAL segment catalog below into one intentionally-directed edit.
A technically valid but random, boring, confusing or padded edit is a failure.

Think before you cut:
1. UNDERSTAND every segment (what happens, who, where, narrative/emotional/
   informational value, quality, uniqueness, role: hook/payoff/context/setup/
   problem/process/escalation/reaction/result/proof/transition/CTA/unusable).
2. STATE the real story in one sentence: "This is a story about X, where Y,
   leading to Z." If no genuine change/question/payoff exists, choose the
   strongest HONEST format (process montage, step-by-step, day-in-the-life,
   transformation, results-first, educational) — never pretend.
3. GENERATE at least three distinct story options, score each 0-100 on hook
   strength, story clarity, payoff, footage support, emotional interest,
   visual variety, platform fit, duration fit, originality. Choose the best
   option THE FOOTAGE ACTUALLY SUPPORTS.
4. DESIGN the hook: it plays at second zero, legible without audio, honestly
   related to the payoff, max 5 seconds. No slow greetings, logos or dead air.
5. BUILD intentional beats — every beat must create curiosity, give context,
   advance action, raise tension, prove, vary energy, pay off or close.
   Remove beats that repeat without adding emotion, clarity or momentum.
6. RESPECT the requested duration as a hard constraint. Do not pad with dead
   footage; do not come up short when supporting moments exist. If the footage
   truly cannot support the range, set status to "insufficient_footage" and
   list the exact missing shots.
7. SELECT for narrative value, not just technical cleanliness. Enter shots
   late, exit as soon as their purpose is fulfilled. Use action matching.
8. PACE with intention: fast hook, brief context, building momentum, slower
   payoff hold, decisive ending. Never a fixed cut length.
9. Use SPEED RAMPS, transitions and reframes only to solve storytelling
   problems — state entry/peak/exit speed and why a plain cut is weaker.
10. GRAPHICS carry information or energy, never decoration. Every text states
    its claim source: transcript, user_prompt, footage_visible or
    branding_asset. NEVER invent facts, places, reactions, results or claims.
11. SOUND is part of the story: preserve meaningful natural sound, use J/L
    cuts deliberately. Set musicAvailable exactly as given — NEVER fabricate
    music or licensing metadata.
12. Fill the retention review (likely swipe-away points + mitigations) and
    score the quality gate honestly: hook 15, story clarity 15, flow 10,
    pacing 10, clip selection 10, payoff 10, visual variety 5, creative
    treatment 10, sound design 5, platform fit 5, duration compliance 5.
    Approve only at 80+, with zero hard failures.

MECHANICAL RULES (validated, violations are rejected):
- use ONLY segmentIds from the catalog; trim ONLY inside each segment's real
  sourceStart-sourceEnd; assetId must match the catalog
- the timeline is contiguous from 0; timelineOut-timelineIn equals
  (sourceOut-sourceIn)/playbackSpeed; plannedDurationSeconds equals the final
  timelineOut; the hook segment is timeline[0]
- never repeat an identical source range
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
    graphic = s("OBJECT", properties={
        "text": s("STRING"), "purpose": s("STRING"), "claimSource": s("STRING"),
        "placement": s("STRING")}, required=["text", "purpose", "claimSource"])
    ramp = s("OBJECT", properties={
        "purpose": s("STRING"), "entrySpeed": s("NUMBER"),
        "peakSpeed": s("NUMBER"), "exitSpeed": s("NUMBER"), "easing": s("STRING"),
        "soundCue": s("STRING"), "whyNotACut": s("STRING")},
        required=["purpose", "entrySpeed", "peakSpeed", "exitSpeed", "easing",
                  "whyNotACut"])
    planned = s("OBJECT", properties={
        "segmentId": s("STRING"), "assetId": s("STRING"),
        "sourceIn": s("NUMBER"), "sourceOut": s("NUMBER"),
        "timelineIn": s("NUMBER"), "timelineOut": s("NUMBER"),
        "beat": s("STRING"), "reason": s("STRING"), "addsNew": s("STRING"),
        "playbackSpeed": s("NUMBER"), "reframe": s("STRING"),
        "speedRamp": ramp, "transitionOut": s("STRING"), "graphic": graphic,
        "audioTreatment": s("STRING"), "expectedViewerEffect": s("STRING")},
        required=["segmentId", "assetId", "sourceIn", "sourceOut", "timelineIn",
                  "timelineOut", "beat", "reason", "addsNew",
                  "expectedViewerEffect"])
    gate = s("OBJECT", properties={**{k: s("INTEGER") for k in (
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
        "pacingProfile": s("STRING"), "transitionsRationale": s("STRING"),
        "captions": s("ARRAY", items=graphic),
        "audio": s("OBJECT", properties={
            "naturalSoundSegmentIds": s("ARRAY", items=s("STRING")),
            "jCutSegmentIds": s("ARRAY", items=s("STRING")),
            "lCutSegmentIds": s("ARRAY", items=s("STRING")),
            "musicAvailable": s("BOOLEAN"), "musicPlan": s("STRING"),
            "soundEffects": s("ARRAY", items=s("OBJECT", properties={
                "segmentId": s("STRING"), "effect": s("STRING"),
                "reinforces": s("STRING")},
                required=["segmentId", "effect", "reinforces"]))},
            required=["musicAvailable", "musicPlan"]),
        "colorReframeNotes": s("STRING"),
        "requestedDurationMin": s("NUMBER"), "requestedDurationMax": s("NUMBER"),
        "plannedDurationSeconds": s("NUMBER"),
        "technicalWarnings": s("ARRAY", items=s("STRING")),
        "retentionReview": s("ARRAY", items=s("OBJECT", properties={
            "position": s("STRING"), "risk": s("STRING"),
            "mitigation": s("STRING")},
            required=["position", "risk", "mitigation"])),
        "qualityGate": gate,
        "missingFootage": s("ARRAY", items=s("OBJECT", properties={
            "description": s("STRING"), "purpose": s("STRING")},
            required=["description", "purpose"])),
        "render": s("OBJECT", properties={
            "width": s("INTEGER"), "height": s("INTEGER"), "fps": s("NUMBER"),
            "aspect": s("STRING")},
            required=["width", "height", "fps", "aspect"]),
        "status": s("STRING")},
        required=["storySentence", "footageSummary", "intendedAudience",
                  "viewerPromise", "options", "chosenOption", "hook", "beats",
                  "timeline", "pacingProfile", "transitionsRationale", "audio",
                  "colorReframeNotes", "plannedDurationSeconds", "qualityGate",
                  "render", "status"])


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
    lines = ["USER CONSTRAINTS (binding unless physically impossible; never "
             "silently ignore any of these):"]
    for key in ("brief", "platform", "tone", "style", "durationMin",
                "durationMax", "mustInclude", "mustExclude"):
        if constraints.get(key) not in (None, "", []):
            lines.append(f"- {key}: {constraints[key]}")
    lines.append(f"- licensed music available for this project: {music_available}"
                 " (set audio.musicAvailable to exactly this value)")
    if constraints.get("durationMin") or constraints.get("durationMax"):
        lines.append("- set requestedDurationMin/requestedDurationMax to these "
                     "values in the plan")
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
    """Produce a grounded EditorialPlan.

    ``generate(parts: list[dict], schema: dict) -> dict`` is the injected model
    call (production: Gemini structured output; tests: a stub). Returns
    {"plan": <validated dict>, "attempts": n, "violationsHistory": [...]}.
    Raises PlanRejected when the attempt budget is exhausted.
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
        else:
            violations = validate_plan(plan, segments, music_available)
            if not violations:
                return {"plan": plan.model_dump(), "attempts": _attempt,
                        "qualityScore": plan.qualityGate.total,
                        "status": plan.status,
                        "violationsHistory": history}
        history.append(violations)
        feedback = [{"text": "YOUR PREVIOUS PLAN WAS REJECTED. Violations:\n- "
                             + "\n- ".join(violations)
                             + "\nRevise the plan to fix EVERY violation. Do "
                               "not invent footage to fix duration — report "
                               "insufficient_footage honestly if needed."}]
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
