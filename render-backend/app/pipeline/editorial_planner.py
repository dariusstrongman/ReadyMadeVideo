"""Editorial Planner v1 — a separate, structured planning stage.

Sits BETWEEN analysis (the canonical segment catalog) and timeline generation.
Consumes only real, already-extracted metadata (segments, transcripts, quality
scores, binding constraints) — never raw video — and emits a schema-validated,
EXECUTION-READY EditorialPlan JSON that downstream picture-edit, graphics,
audio, color and render systems can consume.

Trust model (enforced, never assumed):
  * the USER'S REQUEST is authoritative — duration range, platform, aspect,
    structured creative policies (parsed from tone/style), required/excluded
    moments and music availability are passed into the validator separately;
    the model cannot redefine or weaken them
  * every planned reference must point at REAL catalog segments inside their
    real ranges, with execution-valid geometry (ramps, crops, transitions)
  * every FACTUAL claim (names, locations, numbers, reactions, results,
    outcomes — capitalized or lowercase) must carry structured evidence
    resolving to a real transcript span, catalog metadata value or the user's
    own words; editorial labels and CTAs may be creative but may not imply
    unsupported facts
  * an insufficient-footage report must be HONEST: the achievable duration is
    computed from the timeline (never trusted from the model), must fall
    below the requested minimum, and is rejected when unused grounded footage
    could still satisfy the request
  * music/licensing metadata is never fabricated
  * the plan is DATA — it never contains FFmpeg or filter commands
  * approval is decided by a DETERMINISTIC quality gate; every one of the
    above violation classes is a hard failure the model cannot override

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
_WORD = re.compile(r"[A-Za-z0-9''-]+")

_STOPWORDS = {"the", "a", "an", "and", "or", "but", "with", "for", "was",
              "were", "are", "is", "be", "been", "to", "of", "in", "on", "at",
              "by", "it", "its", "this", "that", "these", "those", "we", "our",
              "you", "your", "they", "their", "he", "she", "his", "her", "as",
              "from", "into", "about", "where", "leading", "how", "why",
              "what", "when", "who", "so", "not", "no", "more", "most", "very",
              "then", "than", "there", "here", "up", "out", "off", "over",
              "under", "all", "any", "each"}

# CLOSED allowlist of STRUCTURAL words a neutral editorial label / CTA may use
# without evidence. This is the safety boundary: it describes video STRUCTURE
# only (titles, steps, parts, viewing prompts). Anything outside it — any
# person, group, location, action, reaction, emotion, result, quantity,
# comparison, outcome or attribution — is a FACTUAL claim requiring evidence.
# (Deliberately NOT a synonym list of forbidden words: unknown words fail
# closed, so "cheered"/"celebrated"/"stunned" are factual by default.)
_NEUTRAL_LABEL_WORDS = {
    "setup", "step", "part", "chapter", "episode", "day", "take", "phase",
    "stage", "intro", "final", "push", "watch", "see", "look", "wait",
    "until", "end", "ending", "begin", "beginning", "start", "process",
    "reveal", "result", "story", "next", "coming", "soon", "stay", "tuned",
    "follow", "subscribe", "like", "share", "comment", "behind", "scenes",
    "before", "after", "during", "work", "title", "opening", "closing",
    "key", "takeaway", "takeaways", "tip", "tips", "quick", "bonus",
    "recap", "summary", "overview", "note", "notes", "highlight",
    "highlights", "first", "second", "third", "last",
}
# digit tokens are allowed in a neutral label ONLY next to a counter word
_COUNTER_WORDS = {"step", "part", "chapter", "episode", "day", "take",
                  "phase", "stage"}

# words that operational technical warnings may additionally use (warnings are
# system-surfaced honesty about constraints/quality, not viewer-facing text)
_WARNING_VOCAB = ("music licensed unavailable available requested required "
                  "missing footage quality shaky unstable blurry noise audio "
                  "resolution low cannot could unsupported tone style "
                  "directive policy phrase warning enforce enforced advisory "
                  "duration shorter longer range")


# ---------------------------------------------------------------- output schema
class EvidenceRef(BaseModel):
    sourceType: Literal["transcript", "segment_metadata", "user_input"]
    segmentId: str | None = None       # required for transcript/segment_metadata
    quoteOrValue: str = Field(min_length=1)


class GroundedText(BaseModel):
    text: str = Field(min_length=1, max_length=300)
    claimType: Literal["fact", "editorial_label", "cta"]
    evidence: list[EvidenceRef] = Field(default_factory=list)


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
    premise: GroundedText
    viewerPromise: str
    hook: str
    structure: str
    payoff: GroundedText
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
    text: GroundedText | None = None
    audioCue: str
    durationSeconds: float = Field(gt=0, le=5)
    transitionOut: str
    curiosityCreated: str
    promiseToFulfill: str


class Caption(BaseModel):
    claimType: Literal["fact", "editorial_label", "cta"]
    text: str = Field(min_length=1, max_length=90)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    timelineStart: float = Field(ge=0)
    timelineEnd: float = Field(gt=0)
    styleTemplate: str = "default"
    safeArea: str = "platform-safe"


class GraphicItem(BaseModel):
    graphicType: str
    claimType: Literal["fact", "editorial_label", "cta"]
    text: str = Field(min_length=1, max_length=90)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    timelineStart: float = Field(ge=0)
    timelineEnd: float = Field(gt=0)
    anchor: str = "bottom-center"
    animation: str = "fade"
    durationSeconds: float = Field(gt=0)


# Closed, execution-safe transition vocabulary. Hard cuts are represented as
# EXPLICIT objects with type "hard_cut" and duration 0 (not by omitting the
# object), so downstream consumers see every boundary decision. Unknown types
# ("teleport", ...) fail schema validation.
TransitionType = Literal["hard_cut", "dissolve", "dip_to_black", "dip_to_white",
                         "whip", "push", "slide", "zoom", "match_cut",
                         "masked_reveal", "audio_led_cut"]
AGGRESSIVE_TRANSITIONS = ("whip", "push", "slide", "zoom")


class TransitionPlan(BaseModel):
    fromSegmentId: str
    toSegmentId: str
    type: TransitionType = "hard_cut"
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
    musicPlan: str | None = None
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
    """A SPECIFIC shot recommendation — vague asks are rejected."""
    beat: str = Field(min_length=3)
    shotType: str = Field(min_length=3)
    recommendedDurationSeconds: float = Field(gt=0)
    why: str = Field(min_length=10)


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
    storySentence: GroundedText
    footageSummary: str
    intendedAudience: str
    viewerPromise: GroundedText
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
    achievableDurationSeconds: float | None = None
    technicalWarnings: list[GroundedText] = Field(default_factory=list)
    retentionReview: list[RetentionRisk] = Field(default_factory=list)
    modelSelfAssessment: ModelSelfAssessment
    missingFootage: list[MissingFootage] = Field(default_factory=list)
    render: RenderInstructions
    status: str  # "approved" | "insufficient_footage"


# ----------------------------------------------------- structured user policies
_POLICY_DEFAULTS = {
    "transitionPolicy": "expressive",     # none | hard_cuts_only | minimal | expressive
    "allowedTransitionTypes": [],
    "forbiddenTransitionTypes": [],
    "speedRampPolicy": "allowed",         # forbidden | allowed | encouraged
    "graphicsPolicy": "standard",         # none | minimal | standard | expressive
    "captionPolicy": "selective",         # none | selective | full
    "musicPolicy": "optional",            # none | optional | required
    "pacingProfile": "moderate",          # slow | moderate | fast | dynamic
    "toneTags": [],
}

_POLICY_PHRASES = [
    ("no transitions", ("transitionPolicy", "none")),
    ("hard cuts only", ("transitionPolicy", "hard_cuts_only")),
    ("hard cuts", ("transitionPolicy", "hard_cuts_only")),
    ("minimal transitions", ("transitionPolicy", "minimal")),
    ("no speed ramps", ("speedRampPolicy", "forbidden")),
    ("no ramps", ("speedRampPolicy", "forbidden")),
    ("no speed", ("speedRampPolicy", "forbidden")),
    ("speed ramp", ("speedRampPolicy", "encouraged")),
    ("no graphics", ("graphicsPolicy", "none")),
    ("no text", ("graphicsPolicy", "none")),
    ("no captions", ("captionPolicy", "none")),
    ("full captions", ("captionPolicy", "full")),
    ("captions required", ("captionPolicy", "full")),
    ("caption everything", ("captionPolicy", "full")),
    ("no music", ("musicPolicy", "none")),
    ("music required", ("musicPolicy", "required")),
    ("slow pacing", ("pacingProfile", "slow")),
    ("fast pacing", ("pacingProfile", "fast")),
    ("dynamic pacing", ("pacingProfile", "dynamic")),
]


# Deterministic tone lexicon: every recognized term maps to an enforceable
# energy bucket; anything else becomes an UNRESOLVED directive that fails
# approval (or, when the request marks tone advisory-only, a warned advisory).
_TONE_LOW = {"quiet", "somber", "calm", "soft", "gentle", "subdued", "mellow",
             "slow", "restrained", "serene", "minimal", "peaceful"}
_TONE_HIGH = {"playful", "energetic", "urgent", "hype", "aggressive", "intense",
              "dynamic", "fast", "exciting", "punchy", "bold", "upbeat"}
_TONE_NEUTRAL = {"warm", "professional", "cinematic", "uplifting", "tense",
                 "authentic", "honest", "clean", "modern", "simple",
                 "documentary", "raw"}
_TONE_FILLER = {"and", "or", "but", "with", "a", "an", "the", "very", "feel",
                "feeling", "vibe", "vibes", "mood", "tone", "style", "look",
                "pacing", "edit", "video", "make", "keep", "it", "of", "to",
                "in", "no", "only", "some", "more"}


def parse_creative_policies(constraints: dict) -> dict:
    """Deterministically parse the ORIGINAL request into binding structured
    policies + a tone profile. Every tone/style token ends in exactly one
    state: structured-and-enforced, advisory-with-warning, or unresolved
    (which fails approval). Explicit structured keys win over parsed prose;
    the model never defines these."""
    policies = dict(_POLICY_DEFAULTS)
    prose = " ".join(str(constraints.get(k) or "")
                     for k in ("style", "tone")).lower()
    consumed = prose
    for phrase, (key, value) in _POLICY_PHRASES:
        if phrase in prose:
            if policies[key] == _POLICY_DEFAULTS[key]:
                policies[key] = value
            consumed = consumed.replace(phrase, " ")
    low_hits, high_hits, tone_tags, unresolved = [], [], [], []
    for match in _WORD.finditer(consumed):
        token = match.group(0).lower()
        if token in _TONE_FILLER or token in _STOPWORDS or token.isdigit():
            continue
        if token in _TONE_LOW:
            low_hits.append(token)
            tone_tags.append(token)
        elif token in _TONE_HIGH:
            high_hits.append(token)
            tone_tags.append(token)
        elif token in _TONE_NEUTRAL:
            tone_tags.append(token)
        else:
            unresolved.append(token)
    energy = "medium"
    if low_hits and not high_hits:
        energy = "low"
    elif high_hits and not low_hits:
        energy = "high"
    policies["toneProfile"] = {
        "energy": energy,
        "emotionalTone": sorted(set(tone_tags)),
        "cutDensity": "low" if energy == "low"
                      else "high" if energy == "high" else "medium",
        "motionIntensity": "subtle" if energy == "low" else "moderate",
        "musicEnergy": "restrained" if energy == "low" else "moderate",
        "graphicsIntensity": "minimal" if energy == "low" else "standard",
    }
    policies["toneConflicts"] = ({"low": sorted(set(low_hits)),
                                  "high": sorted(set(high_hits))}
                                 if (low_hits and high_hits) else {})
    policies["unresolvedToneDirectives"] = sorted(set(unresolved))
    policies["toneAdvisoryOnly"] = bool(constraints.get("toneAdvisoryOnly"))
    policies["toneTags"] = policies["toneProfile"]["emotionalTone"]
    for key in _POLICY_DEFAULTS:            # explicit request keys are authoritative
        if constraints.get(key) not in (None, "", []):
            policies[key] = constraints[key]
    return policies


# ------------------------------------------------------------ evidence helpers
def _catalog_pool(segments: list[Segment], constraints: dict) -> str:
    """Everything a factual token may legitimately come from, lowercased."""
    parts: list[str] = []
    for s in segments:
        for field in (s.transcript, s.action, s.location, s.searchText,
                      s.shotType, s.emotion):
            if field:
                parts.append(str(field))
        parts.extend(s.subjects or [])
        parts.extend(s.storyUses or [])
        parts.extend(s.problems or [])
    for key in ("brief", "platform", "tone", "style"):
        if constraints.get(key):
            parts.append(str(constraints[key]))
    for key in ("mustInclude", "mustExclude"):
        parts.extend(str(x) for x in (constraints.get(key) or []))
    return " ".join(parts).lower()


def _segment_text(s: Segment) -> str:
    return " ".join(str(x) for x in [s.action, s.transcript or "", s.location,
                                     s.searchText, s.shotType, s.emotion,
                                     " ".join(s.subjects or []),
                                     " ".join(s.storyUses or []),
                                     " ".join(s.problems or [])]).lower()


def _user_text(constraints: dict) -> str:
    return " ".join([str(constraints.get(k) or "")
                     for k in ("brief", "platform", "tone", "style")]
                    + [str(x) for x in (constraints.get("mustInclude") or [])]
                    + [str(x) for x in (constraints.get("mustExclude") or [])]
                    ).lower()


def _non_neutral_tokens(text: str, extra_allowed: str = "") -> list[str]:
    """Tokens that make a label FACTUAL rather than neutral-structural.

    STRICTLY closed boundary: a neutral editorial label / CTA may only use the
    closed structural allowlist plus stopwords (and, for system-surfaced
    technical warnings only, a bounded operational vocabulary via
    ``extra_allowed``). Catalog/transcript words are deliberately NOT allowed
    here — real footage facts ("we finished the job") must be claimType=fact
    WITH evidence, never an evidence-free label. Everything outside the
    allowlist — people, groups, locations, actions, reactions, emotions,
    results, quantities, comparisons, outcomes, attributions, known or
    unknown, capitalized or lowercase — is factual and requires evidence."""
    tokens = [m.group(0) for m in _WORD.finditer(text)]
    lowered = [t.lower() for t in tokens]
    has_counter = any(t in _COUNTER_WORDS for t in lowered)
    bad: list[str] = []
    for token, low in zip(tokens, lowered, strict=True):
        if low in _STOPWORDS or low in _NEUTRAL_LABEL_WORDS:
            continue
        if extra_allowed and low in extra_allowed:
            continue                     # warnings: operational vocabulary
        if any(c.isdigit() for c in token):
            if has_counter and token.isdigit():
                continue                 # "Step 2" / "Part 1"
            bad.append(token)
            continue
        bad.append(token)
    return bad


def _content_tokens(text: str) -> list[str]:
    return [m.group(0).lower() for m in _WORD.finditer(text)
            if len(m.group(0)) >= 3 and m.group(0).lower() not in _STOPWORDS]


def _grounded_text_violations(item: GroundedText | Caption | GraphicItem,
                              segments_by_id: dict, pool: str,
                              constraints: dict, where: str,
                              extra_vocab: str = "") -> list[str]:
    """Structured-evidence validation for one grounded text item."""
    out: list[str] = []
    text, claim, evidence = item.text, item.claimType, item.evidence
    user_text = _user_text(constraints)
    if extra_vocab:
        pool = pool + " " + extra_vocab

    if claim == "fact":
        if not evidence:
            out.append(f"{where} is a factual claim with no evidence")
            return out
        supported_quotes: list[str] = []
        for j, ev in enumerate(evidence):
            quote = ev.quoteOrValue.strip().lower()
            if ev.sourceType in ("transcript", "segment_metadata"):
                if not ev.segmentId:
                    out.append(f"{where}.evidence[{j}] has no segmentId")
                    continue
                src = segments_by_id.get(ev.segmentId)
                if src is None:
                    out.append(f"{where}.evidence[{j}] references invented "
                               f"segment {ev.segmentId!r}")
                    continue
                haystack = (src.transcript or "").lower() \
                    if ev.sourceType == "transcript" else _segment_text(src)
                if quote not in haystack:
                    out.append(f"{where}.evidence[{j}] quote is not present in "
                               f"the segment's {ev.sourceType}")
                    continue
            else:  # user_input
                if quote not in user_text:
                    out.append(f"{where}.evidence[{j}] quote is not present in "
                               "the user's own words")
                    continue
            supported_quotes.append(quote)
        if not supported_quotes:
            out.append(f"{where} has no VALID evidence for its factual claim")
            return out
        # every content word of a factual claim must trace to its evidence,
        # the input pool, or neutral structural connectives — lowercase
        # inventions are rejected exactly like capitalized ones
        allowed = (pool + " " + " ".join(supported_quotes) + " "
                   + " ".join(_NEUTRAL_LABEL_WORDS))
        unsupported = [t for t in _content_tokens(text) if t not in allowed]
        if unsupported:
            out.append(f"{where} unsupported factual content: {unsupported}")
    else:  # editorial_label / cta: STRICTLY structural words — fail closed.
        # Catalog/transcript words do NOT pass here: real footage facts must
        # be claimType=fact with evidence, never an evidence-free label.
        # (extra_vocab + the user's own words apply only to system warnings.)
        extra = (extra_vocab + " " + user_text) if extra_vocab else ""
        bad = _non_neutral_tokens(text, extra)
        if bad:
            out.append(f"{where} ({claim}) implies unsupported factual "
                       f"content — {bad} is outside the neutral structural "
                       "vocabulary, so it must be a factual claim with evidence")
    return out


def _aspect_for(value: str | None) -> str | None:
    if not value:
        return None
    v = value.strip().lower()
    for aspect, aliases in _ASPECTS.items():
        if v == aspect or v in aliases:
            return aspect
    return v


# ------------------------------------------------------------ grounding checks
def validate_plan(plan: EditorialPlan, segments: list[Segment],
                  constraints: dict, music_available: bool) -> list[str]:
    """Grounding + binding-constraint + execution validation. The REQUEST
    (constraints, parsed policies, music_available) is authoritative."""
    v: list[str] = []
    by_id = {s.segmentId: s for s in segments}
    pool = _catalog_pool(segments, constraints)
    policies = parse_creative_policies(constraints)

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

    computed_duration = cursor
    if plan.timeline and abs(plan.plannedDurationSeconds - computed_duration) \
            > 2 * TIME_EPSILON:
        v.append("plannedDurationSeconds does not equal the timeline's real length")

    # -- hook grounding
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
        v.extend(_grounded_text_violations(plan.hook.text, by_id, pool,
                                           constraints, "hook.text"))

    # -- BINDING duration + HONEST shortfall (from the REQUEST + computed truth)
    lo, hi = constraints.get("durationMin"), constraints.get("durationMax")
    in_range = ((lo is None or plan.plannedDurationSeconds >= float(lo) - TIME_EPSILON)
                and (hi is None or plan.plannedDurationSeconds <= float(hi) + TIME_EPSILON))
    if plan.status == "approved" and not in_range:
        v.append(f"approved plan ({plan.plannedDurationSeconds}s) falls outside "
                 f"the REQUESTED duration range {lo}-{hi}s — extend the edit or "
                 "report insufficient_footage honestly")
    if plan.status == "insufficient_footage":
        if in_range and (lo is not None or hi is not None):
            v.append("shortfall: the timeline already satisfies the requested "
                     "range — insufficient_footage must be false")
        if not plan.missingFootage:
            v.append("shortfall: insufficient_footage requires specific "
                     "missing-shot recommendations")
        if plan.achievableDurationSeconds is None:
            v.append("shortfall: insufficient_footage requires "
                     "achievableDurationSeconds")
        else:
            # the achievable duration is COMPUTED from the timeline, never
            # trusted from the model
            if abs(plan.achievableDurationSeconds - computed_duration) \
                    > 2 * TIME_EPSILON:
                v.append(f"shortfall: achievableDurationSeconds "
                         f"({plan.achievableDurationSeconds}) does not equal the "
                         f"computed timeline duration ({round(computed_duration, 2)})")
            if lo is not None and plan.achievableDurationSeconds \
                    >= float(lo) - TIME_EPSILON:
                v.append("shortfall: achievable duration is not below the "
                         "requested minimum — the range is achievable")
        if lo is not None:
            catalog_total = sum(s.sourceEnd - s.sourceStart for s in segments)
            if catalog_total >= float(lo):
                v.append(f"shortfall: the catalog holds {round(catalog_total, 1)}s "
                         f"of grounded footage (requested minimum {lo}s) — use the "
                         "unused material instead of reporting insufficient_footage")

    # -- BINDING platform / aspect from the REQUEST
    want_aspect = _aspect_for(constraints.get("aspectRatio")) \
        or _aspect_for(constraints.get("platform"))
    if want_aspect and plan.render.aspect != want_aspect:
        v.append(f"render aspect {plan.render.aspect!r} violates the requested "
                 f"platform/aspect ({want_aspect})")
    if plan.render.aspect == "9:16" and plan.render.width >= plan.render.height:
        v.append("9:16 output must be taller than wide")
    if plan.render.aspect == "16:9" and plan.render.height >= plan.render.width:
        v.append("16:9 output must be wider than tall")

    # -- BINDING required / excluded moments
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

    # -- BINDING structured creative policies (parsed from the REQUEST)
    tp = policies["transitionPolicy"]
    non_cut = [t for t in plan.transitions if t.type != "hard_cut"]
    if tp == "none" and plan.transitions:
        v.append("policy: the request forbids transitions but the plan "
                 f"contains {len(plan.transitions)}")
    if tp == "hard_cuts_only" and non_cut:
        v.append("policy: the request allows hard cuts only but the plan "
                 f"contains {sorted({t.type for t in non_cut})}")
    if tp == "minimal" and len(non_cut) > 2:
        v.append("policy: the request allows minimal transitions but the plan "
                 f"contains {len(non_cut)} non-cut transitions")

    # -- BINDING tone profile: every directive is enforced, warned or rejected
    if policies["toneConflicts"]:
        v.append(f"policy: conflicting tone directives in the request — "
                 f"{policies['toneConflicts']} cannot both be honored; ask "
                 "the user to choose")
    if policies["unresolvedToneDirectives"]:
        phrases = policies["unresolvedToneDirectives"]
        if policies["toneAdvisoryOnly"]:
            if not any(any(p in w.text.lower() for p in phrases)
                       for w in plan.technicalWarnings):
                v.append("policy: advisory tone directives "
                         f"{phrases} were not converted into enforceable "
                         "policy — the plan must surface an explicit warning "
                         "naming them")
        else:
            v.append(f"policy: tone/style directives {phrases} could not be "
                     "converted into enforceable policy and were NOT marked "
                     "advisory-only — they cannot be silently ignored")
    if policies["toneProfile"]["energy"] == "low":
        loud = sorted({t.type for t in plan.transitions
                       if t.type in AGGRESSIVE_TRANSITIONS})
        if loud:
            v.append(f"policy: the requested low-energy tone forbids "
                     f"aggressive transitions but the plan contains {loud}")
        hot_ramps = [i for i, r in enumerate(plan.speedRamps)
                     if r.peakSpeed > 2.0]
        if hot_ramps:
            v.append("policy: the requested low-energy tone forbids "
                     f"aggressive speed ramps (peak > 2x) at {hot_ramps}")
        if len(plan.graphics) > 1:
            v.append("policy: the requested low-energy tone allows minimal "
                     f"graphics but the plan contains {len(plan.graphics)}")
        hot_beats = [pb.beat for pb in plan.pacing if pb.energy > 0.6]
        if hot_beats:
            v.append("policy: the requested low-energy tone requires "
                     f"restrained pacing but beats {hot_beats} exceed 0.6 "
                     "energy")
    if policies["toneProfile"]["energy"] == "high":
        # symmetric enforcement: a high-energy request must actually be
        # DELIVERED, not silently flattened into a listless edit
        energies = [pb.energy for pb in plan.pacing]
        if energies and max(energies) < 0.7:
            v.append("policy: the requested high-energy tone requires at "
                     "least one pacing beat at 0.7+ energy, but the plan "
                     f"peaks at {max(energies)}")
        if energies and sum(energies) / len(energies) < 0.5:
            v.append("policy: the requested high-energy tone requires an "
                     "average pacing energy of at least 0.5, but the plan "
                     f"averages {round(sum(energies) / len(energies), 2)}")
    allowed_types = policies["allowedTransitionTypes"]
    if allowed_types:
        for t in plan.transitions:
            if t.type not in allowed_types:
                v.append(f"policy: transition type {t.type!r} is not in the "
                         "allowed set")
    for t in plan.transitions:
        if t.type in policies["forbiddenTransitionTypes"]:
            v.append(f"policy: transition type {t.type!r} is forbidden by the "
                     "request")
    if policies["speedRampPolicy"] == "forbidden" and plan.speedRamps:
        v.append("policy: the request forbids speed ramps but the plan "
                 f"contains {len(plan.speedRamps)}")
    if policies["graphicsPolicy"] == "none" and plan.graphics:
        v.append("policy: the request forbids graphics but the plan "
                 f"contains {len(plan.graphics)}")
    if policies["captionPolicy"] == "none" and plan.captions:
        v.append("policy: the request forbids captions but the plan "
                 f"contains {len(plan.captions)}")
    if policies["captionPolicy"] == "full" and not plan.captions \
            and any(s.transcript for s in segments):
        v.append("policy: the request requires captions and the footage has "
                 "transcripts, but the plan contains none")
    if policies["musicPolicy"] == "none" \
            and plan.audio.musicPlan not in (None, "", "none", "disabled"):
        v.append("policy: the request forbids music but the plan describes it")
    if policies["musicPolicy"] == "required" and not music_available \
            and not any("music" in w.text.lower() for w in plan.technicalWarnings):
        v.append("policy: the request requires music but no licensed music "
                 "exists — the plan must surface this as a technical warning")

    # -- caption / graphic grounding + timing
    for i, cap in enumerate(plan.captions):
        where = f"captions[{i}]"
        if cap.timelineStart >= cap.timelineEnd:
            v.append(f"{where} has an empty or inverted timeline range")
        if cap.timelineEnd > plan.plannedDurationSeconds + TIME_EPSILON:
            v.append(f"{where} extends past the end of the video")
        v.extend(_grounded_text_violations(cap, by_id, pool, constraints, where))
    for i, g in enumerate(plan.graphics):
        where = f"graphics[{i}]"
        if g.timelineStart >= g.timelineEnd:
            v.append(f"{where} has an empty or inverted timeline range")
        if g.timelineEnd > plan.plannedDurationSeconds + TIME_EPSILON:
            v.append(f"{where} extends past the end of the video")
        if abs(g.durationSeconds - (g.timelineEnd - g.timelineStart)) > TIME_EPSILON:
            v.append(f"{where} durationSeconds does not match its timing")
        v.extend(_grounded_text_violations(g, by_id, pool, constraints, where))

    # -- audio references
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

    # -- music plan: availability is AUTHORITATIVE from the project
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
        if policies["musicPolicy"] != "none" and not plan.audio.musicPlan:
            v.append("licensed music exists but musicPlan is empty")
        if plan.audio.musicPlan and "licens" in plan.audio.musicPlan.lower():
            v.append("musicPlan restates licensing metadata — license data lives "
                     "only in the licensed music records, never in the plan")

    # -- EXECUTION geometry: transitions (one instruction per boundary)
    tl_order = [seg.segmentId for seg in plan.timeline]
    boundary_counts: dict[tuple[str, str], int] = {}
    for tr in plan.transitions:
        key = (tr.fromSegmentId, tr.toSegmentId)
        boundary_counts[key] = boundary_counts.get(key, 0) + 1
    for (frm, to), count in sorted(boundary_counts.items()):
        if count > 1:
            v.append(f"execution: {count} transition instructions target the "
                     f"same boundary {frm}->{to} — exactly one is allowed "
                     "(duplicates and conflicts are rejected, never merged)")
    for i, tr in enumerate(plan.transitions):
        join = None
        for j in range(len(tl_order) - 1):
            if tl_order[j] == tr.fromSegmentId and tl_order[j + 1] == tr.toSegmentId:
                join = j
                break
        if join is None:
            v.append(f"execution: transitions[{i}] {tr.fromSegmentId}->"
                     f"{tr.toSegmentId} does not join two adjacent timeline "
                     "segments")
            continue
        if tr.type == "hard_cut":
            if tr.durationSeconds > TIME_EPSILON:
                v.append(f"execution: transitions[{i}] is a hard cut but has a "
                         "duration")
        else:
            if tr.durationSeconds <= 0:
                v.append(f"execution: transitions[{i}] ({tr.type}) needs a "
                         "positive duration")
            else:
                out_seg, in_seg = plan.timeline[join], plan.timeline[join + 1]
                out_src, in_src = by_id.get(out_seg.segmentId), by_id.get(in_seg.segmentId)
                if out_src and in_src:
                    tail = out_src.sourceEnd - out_seg.sourceOut
                    head = in_seg.sourceIn - in_src.sourceStart
                    handle = min(tail, head)
                    if tr.durationSeconds > handle + TIME_EPSILON:
                        v.append(f"execution: transitions[{i}] duration "
                                 f"{tr.durationSeconds}s exceeds the available "
                                 f"source handles ({round(handle, 2)}s)")

    # -- EXECUTION geometry: speed ramps
    ramp_ranges: dict[str, list[tuple[float, float]]] = {}
    for i, ramp in enumerate(plan.speedRamps):
        where = f"speedRamps[{i}]"
        if ramp.sourceStart >= ramp.sourceEnd:
            v.append(f"execution: {where} has an inverted or empty source range")
            continue
        cuts = planned_cuts.get(ramp.segmentId)
        if not cuts:
            v.append(f"execution: {where} references unplanned segment "
                     f"{ramp.segmentId!r}")
            continue
        if not any(ramp.sourceStart >= a - TIME_EPSILON
                   and ramp.sourceEnd <= b + TIME_EPSILON for a, b in cuts):
            v.append(f"execution: {where} range is outside the planned cut of "
                     f"{ramp.segmentId}")
        for a, b in ramp_ranges.get(ramp.segmentId, []):
            if ramp.sourceStart < b and a < ramp.sourceEnd:
                v.append(f"execution: {where} overlaps another speed ramp on "
                         f"{ramp.segmentId}")
                break
        ramp_ranges.setdefault(ramp.segmentId, []).append(
            (ramp.sourceStart, ramp.sourceEnd))

    # -- EXECUTION geometry: crops
    for i, rf in enumerate(plan.reframes):
        where = f"reframes[{i}]"
        if rf.segmentId not in planned_ids:
            v.append(f"execution: {where} references unplanned segment "
                     f"{rf.segmentId!r}")
        if rf.outputAspectRatio != plan.render.aspect:
            v.append(f"execution: {where} aspect {rf.outputAspectRatio!r} does "
                     f"not match the render target {plan.render.aspect!r}")
        for name, crop in (("startCrop", rf.startCrop), ("endCrop", rf.endCrop)):
            if crop.x + crop.width > 1 + 1e-6:
                v.append(f"execution: {where}.{name} exceeds the right edge "
                         f"(x+width={round(crop.x + crop.width, 3)})")
            if crop.y + crop.height > 1 + 1e-6:
                v.append(f"execution: {where}.{name} exceeds the bottom edge "
                         f"(y+height={round(crop.y + crop.height, 3)})")
        # normalized zoom consistency: start and end crops must keep the same
        # shape (no distortion). Absolute pixel-aspect needs source dimensions,
        # which the catalog does not carry — documented limitation.
        s_ratio = rf.startCrop.width / rf.startCrop.height
        e_ratio = rf.endCrop.width / rf.endCrop.height
        if abs(s_ratio - e_ratio) > 0.05:
            v.append(f"execution: {where} startCrop/endCrop aspect mismatch "
                     "would distort the image")

    # -- remaining structured sections reference planned segments
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

    # -- story claims + technical warnings carry structured evidence
    chosen = plan.options[plan.chosenOption] \
        if plan.chosenOption < len(plan.options) else None
    grounded_fields = [("storySentence", plan.storySentence),
                       ("viewerPromise", plan.viewerPromise)]
    if chosen:
        grounded_fields += [("chosen premise", chosen.premise),
                            ("chosen payoff", chosen.payoff)]
    for label, item in grounded_fields:
        v.extend(_grounded_text_violations(item, by_id, pool, constraints, label))
    # warnings are system-surfaced honesty (not viewer-facing) — they may use
    # the operational vocabulary, but names/numbers still require evidence
    for i, w in enumerate(plan.technicalWarnings):
        v.extend(_grounded_text_violations(w, by_id, pool, constraints,
                                           f"technicalWarnings[{i}]",
                                           extra_vocab=_WARNING_VOCAB))
    return v


# ---------------------------------------------------- deterministic quality gate
def deterministic_gate(plan: EditorialPlan, segments: list[Segment],
                       constraints: dict, violations: list[str]) -> dict:
    """Independent pass/fail from VERIFIABLE properties. Every violation class
    (claims, shortfall, policy, execution geometry) is a HARD failure the
    model's self-assessment cannot override."""
    by_id = {s.segmentId: s for s in segments}
    used = [seg.segmentId for seg in plan.timeline]
    rules: list[dict] = []

    def rule(name: str, weight: int, hard: bool, passed: bool, detail: str):
        rules.append({"rule": name, "weight": weight, "hard": hard,
                      "passed": passed, "detail": detail})

    def no_violation(*needles: str) -> bool:
        return not any(any(n in viol for n in needles) for viol in violations)

    rule("hook_grounded", 10, True,
         no_violation("hook"), "hook exists, in range, first and immediate")
    rule("timeline_contiguous", 10, True,
         no_violation("contiguous", "invented segment", "source range",
                      "duration does not match", "timeline's real length"),
         "every cut is real, in range and contiguous from 0")
    rule("claims_grounded", 10, True,
         no_violation("factual claim", "unsupported factual", "evidence",
                      "implies unsupported"),
         "every factual claim carries verified structured evidence")
    lo, hi = constraints.get("durationMin"), constraints.get("durationMax")
    duration_ok = no_violation("REQUESTED duration range", "shortfall:") and (
        ((lo is None or plan.plannedDurationSeconds >= float(lo) - TIME_EPSILON)
         and (hi is None or plan.plannedDurationSeconds <= float(hi) + TIME_EPSILON))
        or plan.status == "insufficient_footage")
    rule("duration_compliant", 10, True, duration_ok,
         "duration honors the REQUEST; any shortfall is computed and honest")
    rule("story_structure", 10, True,
         len(plan.options) >= 3 and plan.chosenOption < len(plan.options)
         and len(plan.beats) >= 2 and bool(plan.storySentence.text.strip()),
         ">=3 scored options, chosen concept, beat arc, one-sentence story")
    payoff_words = ("payoff", "result", "reveal", "closing", "outcome", "after")
    rule("payoff_present", 10, True,
         any(any(w in seg.beat.lower() for w in payoff_words)
             for seg in plan.timeline[-2:])
         or any(any(w in b.key.lower() for w in payoff_words) for b in plan.beats),
         "the edit ends on a payoff beat, not an accidental stop")
    rule("creative_policy_honored", 10, True,
         no_violation("policy:"),
         "the request's structured creative policies are honored exactly")
    rule("execution_geometry", 10, True,
         no_violation("execution:"),
         "ramps, crops and transitions are executable as specified")
    rule("music_grounded", 5, True,
         no_violation("music", "licens"),
         "music availability and licensing are real, never fabricated")
    rule("audio_refs_valid", 5, False,
         no_violation("audio.", "soundEffects", "audioTreatments"),
         "every audio reference points at a planned segment")
    reuse_ok = (not used) or max(used.count(u) for u in set(used)) <= MAX_SEGMENT_REUSE
    rule("no_redundant_reuse", 2, False,
         reuse_ok and no_violation("repeats an identical range"),
         f"no segment used more than {MAX_SEGMENT_REUSE}x, no duplicate ranges")
    shot_types = {by_id[u].shotType for u in set(used)
                  if u in by_id and by_id[u].shotType}
    variety_ok = len(set(used)) >= min(2, len(segments)) \
        and (len(shot_types) >= 2 if len(shot_types) else True)
    rule("visual_variety", 2, False, variety_ok,
         "multiple distinct segments / shot types where the catalog offers them")
    flagged = [u for u in set(used) if u in by_id and by_id[u].problems]
    rule("technical_warnings_surfaced", 3, False,
         (not flagged) or bool(plan.technicalWarnings),
         "known segment problems surface as technical warnings")
    # advisory-only unresolved tone directives never score full compliance,
    # even when warned (soft: the plan can still pass, visibly reduced)
    policies = parse_creative_policies(constraints)
    rule("tone_fully_resolved", 3, False,
         not policies["unresolvedToneDirectives"],
         "every tone/style directive maps to enforceable structured policy")

    score = sum(r["weight"] for r in rules if r["passed"])
    hard_failures = [r["rule"] for r in rules if r["hard"] and not r["passed"]]
    return {"rules": rules, "score": score, "hardFailures": hard_failures,
            "passed": score >= APPROVAL_THRESHOLD and not hard_failures}


# ----------------------------------------------------------------- LLM harness
_SPEC = """You are Stromation's Senior Story Editor and Creative Director.
Transform the REAL segment catalog below into one intentionally-directed edit.
A technically valid but random, boring, confusing or padded edit is a failure.

Think before you cut:
1. UNDERSTAND every segment; STATE the real story in one sentence; GENERATE at
   least three scored story options; choose the one THE FOOTAGE SUPPORTS.
2. The hook IS the first timeline cut (same segment, same sourceIn), starts at
   0, max 5 seconds, honest promise.
3. The USER CONSTRAINTS and CREATIVE POLICIES below are BINDING and validated
   against the ORIGINAL request: duration range, platform/aspect, transition/
   speed-ramp/graphics/caption/music policies, required and excluded moments.
   You cannot restate, weaken or ignore them.
4. HONEST SHORTFALL: if the footage cannot honor the duration range, set
   status "insufficient_footage"; achievableDurationSeconds MUST equal your
   timeline's actual computed length and be below the requested minimum; give
   SPECIFIC missing shots {beat, shotType, recommendedDurationSeconds, why}.
   Never report a shortfall while usable catalog footage remains unused.
5. EVERY factual statement — anything describing a person, group, location,
   action, reaction, emotion, result, quantity, comparison, outcome or
   attribution, in ANY capitalization — must be claimType "fact" with
   structured evidence:
   {sourceType: transcript|segment_metadata|user_input, segmentId, quoteOrValue}
   where the quote is literally present in that source and every content word
   of the text traces to it. editorial_label / cta text is limited to a
   STRICTLY CLOSED structural vocabulary (e.g. "The Setup", "Step 2",
   "The Final Push", "Watch Until the End", "Key Takeaway", "Quick Tip",
   "Bonus") — ANY other word, including words from the footage itself,
   makes it a factual claim requiring evidence.
   storySentence, viewerPromise, premise, payoff, hook.text, captions,
   graphics and technicalWarnings all use this grounded structure.
5b. The STRUCTURED CREATIVE POLICIES include a binding tone profile parsed
   from the user's tone/style. A low-energy tone forbids aggressive
   transitions (whip/push/slide/zoom), speed ramps above 2x, more than one
   graphic, and pacing beats above 0.6 energy. A high-energy tone must be
   DELIVERED: at least one pacing beat at 0.7+ and average energy 0.5+.
   Tone words that could not be
   parsed appear in unresolvedToneDirectives — you must NOT ignore them.
6. EXECUTION-VALID geometry only:
   - speed ramps: sourceStart < sourceEnd, inside the planned cut, speeds in
     (0, 8], no overlapping ramps on one segment
   - crops: normalized, x+width <= 1, y+height <= 1, startCrop/endCrop keep
     the same shape (no distortion)
   - transitions: type is EXACTLY one of hard_cut, dissolve, dip_to_black,
     dip_to_white, whip, push, slide, zoom, match_cut, masked_reveal,
     audio_led_cut; they join ADJACENT cuts with AT MOST ONE transition per
     boundary; hard_cut has duration 0; other types need a positive duration
     that fits inside both segments' unused source handles
7. SOUND: J/L cuts by planned segment id; audio.musicAvailable EXACTLY as
   given; with no licensed music, musicPlan MUST be null and nothing
   music-related may be described (no tracks, BPM, beat grids, licenses).
8. modelSelfAssessment is ADVISORY ONLY — a deterministic gate decides
   approval from verifiable properties. Score honestly anyway.

MECHANICAL RULES (validated, violations are rejected):
- only catalog segmentIds, trimmed inside their real ranges; timeline
  contiguous from 0; plannedDurationSeconds equals the final timelineOut;
  the hook is timeline[0]; never repeat an identical source range
- schemaVersion is exactly 1
- output structured data only — NO ffmpeg, filter strings or shell commands
"""


def _response_schema() -> dict:
    """Gemini structured-output schema (OpenAPI subset, uppercase types)."""
    def s(t, **kw):
        return {"type": t, **kw}
    evidence = s("OBJECT", properties={
        "sourceType": s("STRING"), "segmentId": s("STRING"),
        "quoteOrValue": s("STRING")}, required=["sourceType", "quoteOrValue"])
    grounded = s("OBJECT", properties={
        "text": s("STRING"),
        # the exact pydantic contract — without the enum here, a pruned wire
        # schema left the model inventing claimType values
        "claimType": s("STRING", enum=["fact", "editorial_label", "cta"]),
        "evidence": s("ARRAY", items=evidence)},
        required=["text", "claimType"])
    scores = s("OBJECT", properties={k: s("INTEGER") for k in (
        "hookStrength", "storyClarity", "payoffStrength", "footageSupport",
        "emotionalInterest", "visualVariety", "platformFit", "durationFit",
        "originality")},
        required=["hookStrength", "storyClarity", "payoffStrength",
                  "footageSupport", "emotionalInterest", "visualVariety",
                  "platformFit", "durationFit", "originality"])
    option = s("OBJECT", properties={
        "premise": grounded, "viewerPromise": s("STRING"), "hook": s("STRING"),
        "structure": s("STRING"), "payoff": grounded,
        "idealDurationSeconds": s("NUMBER"),
        "strengths": s("ARRAY", items=s("STRING")),
        "weaknesses": s("ARRAY", items=s("STRING")),
        "footageCoverage": s("STRING"),
        "retentionRisks": s("ARRAY", items=s("STRING")), "scores": scores},
        required=["premise", "viewerPromise", "hook", "structure", "payoff",
                  "idealDurationSeconds", "strengths", "weaknesses",
                  "footageCoverage", "retentionRisks", "scores"])
    caption = s("OBJECT", properties={
        "claimType": s("STRING"), "text": s("STRING"),
        "evidence": s("ARRAY", items=evidence),
        "timelineStart": s("NUMBER"), "timelineEnd": s("NUMBER"),
        "styleTemplate": s("STRING"), "safeArea": s("STRING")},
        required=["claimType", "text", "timelineStart", "timelineEnd"])
    graphic = s("OBJECT", properties={
        "graphicType": s("STRING"), "claimType": s("STRING"),
        "text": s("STRING"), "evidence": s("ARRAY", items=evidence),
        "timelineStart": s("NUMBER"), "timelineEnd": s("NUMBER"),
        "anchor": s("STRING"), "animation": s("STRING"),
        "durationSeconds": s("NUMBER")},
        required=["graphicType", "claimType", "text", "timelineStart",
                  "timelineEnd", "durationSeconds"])
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
    missing = s("OBJECT", properties={
        "beat": s("STRING"), "shotType": s("STRING"),
        "recommendedDurationSeconds": s("NUMBER"), "why": s("STRING")},
        required=["beat", "shotType", "recommendedDurationSeconds", "why"])
    return s("OBJECT", properties={
        "schemaVersion": s("INTEGER"), "storySentence": grounded,
        "footageSummary": s("STRING"), "intendedAudience": s("STRING"),
        "viewerPromise": grounded,
        "options": s("ARRAY", items=option), "chosenOption": s("INTEGER"),
        "hook": s("OBJECT", properties={
            "segmentId": s("STRING"), "sourceIn": s("NUMBER"),
            "sourceOut": s("NUMBER"), "firstFrame": s("STRING"),
            "text": grounded, "audioCue": s("STRING"),
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
        "technicalWarnings": s("ARRAY", items=grounded),
        "retentionReview": s("ARRAY", items=s("OBJECT", properties={
            "position": s("STRING"), "risk": s("STRING"),
            "mitigation": s("STRING")},
            required=["position", "risk", "mitigation"])),
        "modelSelfAssessment": assessment,
        "missingFootage": s("ARRAY", items=missing),
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
    lines = ["USER CONSTRAINTS (BINDING — validated against the ORIGINAL "
             "request; you cannot redefine them):"]
    for key in ("brief", "platform", "aspectRatio", "tone", "style",
                "durationMin", "durationMax", "mustInclude", "mustExclude"):
        if constraints.get(key) not in (None, "", []):
            lines.append(f"- {key}: {constraints[key]}")
    lines.append("STRUCTURED CREATIVE POLICIES (parsed from the request, "
                 "BINDING): " + json.dumps(parse_creative_policies(constraints)))
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
        else:
            violations = validate_plan(plan, segments, constraints,
                                       music_available)
            gate = deterministic_gate(plan, segments, constraints, violations)
            # Approval is DETERMINISTIC; rule-level failures always join the
            # feedback so the revise loop learns WHICH rule broke (a dishonest
            # insufficient_footage report included).
            if not gate["passed"] and (violations or plan.status == "approved"):
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
        feedback = [{"text": "YOUR PREVIOUS PLAN WAS REJECTED. Rule-level "
                             "violations:\n- " + "\n- ".join(violations)
                             + "\nRevise the plan to fix EVERY violation. Do "
                               "not invent footage, claims, evidence or music "
                               "to fix them — report insufficient_footage "
                               "honestly if needed."}]
    raise PlanRejected(history)


def gemini_generate(parts: list[dict], schema: dict) -> dict:
    """Production model call (kept tiny + injected so tests never hit it)."""
    from .gemini_common import generate_json
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not configured")
    model = os.environ.get("EDITORIAL_PLANNER_MODEL", "gemini-2.5-pro")
    # Ladder tuned to THIS schema: the deliberation subtree (`options`, three
    # full scored alternatives) is the fattest branch and the only part the
    # downstream engine never reads — so it is sacrificed first, keeping the
    # load-bearing contract (hook, timeline, pacing, execution) wire-enforced
    # for as long as the state budget allows. Production showed uniform depth
    # pruning strips `options`' required-field enforcement and the model then
    # drops those fields even with the contract in the prompt.
    from .gemini_common import prune_schema
    import copy
    options_light = copy.deepcopy(schema)
    opts = options_light.get("properties", {}).get("options")
    if opts is not None:
        options_light["properties"]["options"] = \
            {"type": "ARRAY", "items": {"type": "OBJECT"}}
    ladder = [schema, options_light, prune_schema(options_light, 4),
              prune_schema(schema, 2), {"type": "OBJECT"}]
    return generate_json(model, parts, schema, api_key,
                         temperature=0.4, timeout=600, ladder=ladder)
