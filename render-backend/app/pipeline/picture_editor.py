"""Milestone 2 deterministic picture editor.

Consumes Milestone 1 evidence and emits multiple inspectable timeline candidates.
It does not add or choose audio, graphics, captions, color, or critic output.
"""
from __future__ import annotations

import math
from collections import Counter
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .capture_quality import CaptureQualityReport
from .composition import CompositionMetrics
from .creative_director import CreativeTreatment
from .schemas import Segment
from .story_editor import FootageRequirement, StoryVariant, StoryVariantSet


class PictureEditorError(ValueError):
    pass


class VirtualReframeInstruction(BaseModel):
    mode: Literal["safe_crop", "none"]
    outputAspect: Literal["9:16"] = "9:16"
    measurementSource: str
    cropBox: dict[str, float] | None = None
    sourcePixelsPerOutputPixel: float = Field(default=0, ge=0)
    confidence: float = Field(ge=0, le=1)
    reason: str

    @model_validator(mode="after")
    def safe_crop_requires_measured_box(self):
        if self.mode == "safe_crop":
            if self.measurementSource != "detected_bbox" or not self.cropBox:
                raise ValueError("safe virtual reframing requires measured crop bounds")
            if self.sourcePixelsPerOutputPixel < 1:
                raise ValueError("safe virtual reframing cannot require upscaling")
        return self


class RhythmBeat(BaseModel):
    beatName: str
    order: int = Field(ge=0)
    targetSeconds: float = Field(gt=0)
    energyLevel: float = Field(ge=0, le=1)
    targetShotSeconds: float = Field(ge=0.65, le=4.5)
    plannedShotCount: int = Field(ge=1)
    pacingIntent: str
    hookPriority: bool = False
    payoffPriority: bool = False


class VisualRhythmPlan(BaseModel):
    schemaVersion: int = 1
    candidateId: str
    storyVariantId: str
    durationProfile: Literal["kinetic", "balanced", "controlled"]
    targetDurationSeconds: float = Field(ge=15, le=60)
    captureCeiling: float = Field(ge=1, le=10)
    repetitionRisk: float = Field(ge=0, le=1)
    beats: list[RhythmBeat]
    energyProgression: list[float]
    pacingSummary: str

    @model_validator(mode="after")
    def rhythm_is_complete(self):
        if not self.beats:
            raise ValueError("visual rhythm plan requires beats")
        if len(self.energyProgression) != len(self.beats):
            raise ValueError("energy progression must cover every beat")
        return self


class PictureCandidateSummary(BaseModel):
    schemaVersion: int = 1
    candidateId: str
    label: str
    storyVariantId: str
    valid: bool
    rejectionReasons: list[str] = Field(default_factory=list)
    durationSeconds: float = Field(ge=0)
    targetDurationSeconds: float = Field(ge=15, le=60)
    coverageRatio: float = Field(ge=0, le=1)
    editorialScore: float = Field(ge=0, le=1)
    structuralSignature: str
    clipCount: int = Field(ge=0)
    timeline: dict
    selectionEvidence: list[dict] = Field(default_factory=list)

    @model_validator(mode="after")
    def picture_only_timeline(self):
        if any(track.get("type") != "video" for track in self.timeline.get("tracks", [])):
            raise ValueError("Milestone 2 timelines may contain video tracks only")
        if self.timeline.get("music") or self.timeline.get("captions"):
            raise ValueError("Milestone 2 cannot add music or captions")
        return self


class PictureEditPackage(BaseModel):
    schemaVersion: int = 1
    status: Literal["ready", "insufficient_coverage"]
    preproductionRunId: str
    visualRhythmPlans: dict[str, VisualRhythmPlan]
    candidates: list[PictureCandidateSummary]
    selectedCandidateId: str | None = None
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def candidates_are_materially_different(self):
        if len(self.candidates) != 3:
            raise ValueError("Milestone 2 requires exactly three picture candidates")
        signatures = {candidate.structuralSignature for candidate in self.candidates}
        if len(signatures) != len(self.candidates):
            raise ValueError("picture candidates must be materially different")
        if self.selectedCandidateId and self.selectedCandidateId not in {
            candidate.candidateId for candidate in self.candidates if candidate.valid
        }:
            raise ValueError("selected picture candidate must be valid")
        return self


_PROFILES = (
    {
        "id": "kinetic_hook",
        "label": "Kinetic hook",
        "duration": "kinetic",
        "factor": 0.72,
        "variants": ("social_retention", "action_first"),
        "weights": {"story": 0.20, "motion": 0.22, "technical": 0.10,
                    "prominence": 0.20, "composition": 0.08,
                    "variety": 0.10, "role": 0.10},
    },
    {
        "id": "treatment_arc",
        "label": "Treatment-led arc",
        "duration": "balanced",
        "factor": 1.0,
        "variants": (),
        "weights": {"story": 0.28, "motion": 0.14, "technical": 0.14,
                    "prominence": 0.12, "composition": 0.10,
                    "variety": 0.10, "role": 0.12},
    },
    {
        "id": "controlled_payoff",
        "label": "Controlled cinematic payoff",
        "duration": "controlled",
        "factor": 1.32,
        "variants": ("cinematic", "build_and_payoff"),
        "weights": {"story": 0.20, "motion": 0.08, "technical": 0.16,
                    "prominence": 0.14, "composition": 0.18,
                    "variety": 0.12, "role": 0.12},
    },
)


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _shot_family(value: str) -> str:
    value = value.lower()
    return next((name for name in ("close", "medium", "wide") if name in value),
                "unknown")


def _action_family(value: str) -> str:
    ignored = {"a", "an", "the", "person", "athlete", "man", "woman"}
    tokens = [token for token in value.lower().replace("-", " ").split()
              if token not in ignored]
    return " ".join(tokens[:4]) or "unknown"


def _supports(segment: Segment, requirement: FootageRequirement) -> bool:
    if {"mostly_black", "mostly_frozen", "operator_unusable"} & set(segment.problems):
        return False
    if requirement.storyUsesAny \
            and not set(requirement.storyUsesAny) & set(segment.storyUses):
        return False
    if requirement.shotSizesAny and not any(
        size in segment.shotType.lower() for size in requirement.shotSizesAny
    ):
        return False
    if requirement.minimumMotion is not None \
            and segment.motionIntensity < requirement.minimumMotion:
        return False
    if requirement.cleanNaturalAudio \
            and (segment.audioScore < 0.6 or segment.transcript):
        return False
    if requirement.trackingMovement and not any(
        word in segment.cameraMovement.lower() for word in ("track", "follow", "gimbal")
    ):
        return False
    return True


def _variant_for_profile(
    profile: dict, treatment: CreativeTreatment, variants: StoryVariantSet,
) -> StoryVariant:
    valid = [variant for variant in variants.variants if variant.valid]
    preferred = tuple(profile["variants"])
    if profile["id"] == "treatment_arc":
        preferred = (treatment.selectedStoryVariant, "build_and_payoff", "action_first")
    for variant_id in preferred:
        match = next((variant for variant in valid if variant.variantId == variant_id), None)
        if match:
            return match
    if valid:
        return valid[0]
    return min(variants.variants, key=lambda item: len(item.rejectionReasons))


def plan_visual_rhythm(
    candidate_id: str,
    duration_profile: Literal["kinetic", "balanced", "controlled"],
    shot_duration_factor: float,
    treatment: CreativeTreatment,
    capture: CaptureQualityReport,
    variant: StoryVariant,
) -> VisualRhythmPlan:
    beats: list[RhythmBeat] = []
    last_index = len(variant.beats) - 1
    for index, beat in enumerate(variant.beats):
        target_shot = (3.2 - 2.3 * beat.energyLevel) * shot_duration_factor
        if index == 0:
            target_shot = min(target_shot, 1.2 if duration_profile == "kinetic" else 1.45)
        if index == last_index and beat.endingRequirement:
            target_shot = max(target_shot, 1.35 if duration_profile == "kinetic" else 1.8)
        target_shot = round(max(0.65, min(4.5, target_shot)), 3)
        planned_count = max(1, math.ceil(beat.targetSeconds / target_shot))
        if index == 0:
            intent = "front-load immediate readable action; no establishing pre-roll"
        elif index == last_index:
            intent = "hold a completion or recovery payoff long enough to read"
        elif beat.energyLevel >= 0.75:
            intent = "accelerate with shorter, contrasting shots"
        else:
            intent = "create breathing room without losing forward motion"
        beats.append(RhythmBeat(
            beatName=beat.name,
            order=index,
            targetSeconds=beat.targetSeconds,
            energyLevel=beat.energyLevel,
            targetShotSeconds=target_shot,
            plannedShotCount=planned_count,
            pacingIntent=intent,
            hookPriority=index == 0,
            payoffPriority=index == last_index,
        ))
    return VisualRhythmPlan(
        candidateId=candidate_id,
        storyVariantId=variant.variantId,
        durationProfile=duration_profile,
        targetDurationSeconds=treatment.targetDurationSeconds,
        captureCeiling=capture.estimatedEditCeiling,
        repetitionRisk=capture.metrics.repetitionRatio,
        beats=beats,
        energyProgression=[beat.energyLevel for beat in variant.beats],
        pacingSummary=(
            f"{duration_profile} rhythm; hook <= {beats[0].targetShotSeconds:.2f}s; "
            f"payoff target {beats[-1].targetShotSeconds:.2f}s; "
            f"capture repetition risk {capture.metrics.repetitionRatio:.2f}"
        ),
    )


def _reframe(metric: CompositionMetrics | None) -> VirtualReframeInstruction:
    if metric and metric.measurementSource == "detected_bbox" \
            and metric.safeCrop.feasible and metric.safeCrop.cropBox \
            and metric.safeCrop.sourcePixelsPerOutputPixel >= 1:
        return VirtualReframeInstruction(
            mode="safe_crop",
            measurementSource=metric.measurementSource,
            cropBox=metric.safeCrop.cropBox.model_dump(),
            sourcePixelsPerOutputPixel=metric.safeCrop.sourcePixelsPerOutputPixel,
            confidence=metric.confidence,
            reason="measured subject/action bounds fit 9:16 at the resolution floor",
        )
    return VirtualReframeInstruction(
        mode="none",
        measurementSource=metric.measurementSource if metric else "unavailable",
        confidence=metric.confidence if metric else 0,
        reason=("safe crop was not measured; preserve the full source frame"
                if metric else "composition evidence is unavailable; preserve the full source frame"),
    )


def _role_score(segment: Segment, *, hook: bool, payoff: bool) -> float:
    uses = set(segment.storyUses)
    if hook:
        return _clamp(0.55 * bool(uses & {"hook", "peak"})
                      + 0.30 * segment.motionIntensity + 0.15 * segment.semanticRelevance)
    if payoff:
        return _clamp(0.70 * bool(uses & {"completion", "reflection"})
                      + 0.20 * segment.semanticRelevance
                      + 0.10 * (1 - segment.motionIntensity))
    return 0.5 + 0.5 * segment.semanticRelevance


def _score_segment(
    segment: Segment,
    requirement: FootageRequirement,
    metric: CompositionMetrics | None,
    energy: float,
    previous: Segment | None,
    profile: dict,
    *,
    hook: bool,
    payoff: bool,
) -> tuple[float, dict[str, float]]:
    story = 1.0 if not requirement.storyUsesAny \
        or set(requirement.storyUsesAny) & set(segment.storyUses) else 0.0
    technical = (segment.focusScore + segment.exposureScore + segment.stabilityScore) / 3
    motion = 1 - abs(segment.motionIntensity - energy)
    prominence = _clamp((metric.subjectProminence if metric else 0) / 0.22)
    composition = metric.compositionQuality if metric else 0
    variety = 1.0
    if previous:
        if previous.assetId == segment.assetId:
            variety -= 0.35
        if _shot_family(previous.shotType) == _shot_family(segment.shotType):
            variety -= 0.35
        if _action_family(previous.action) == _action_family(segment.action):
            variety -= 0.30
    role = _role_score(segment, hook=hook, payoff=payoff)
    scores = {
        "story": round(_clamp(story), 4),
        "motion": round(_clamp(motion), 4),
        "technical": round(_clamp(technical), 4),
        "prominence": round(prominence, 4),
        "composition": round(_clamp(composition), 4),
        "variety": round(_clamp(variety), 4),
        "role": round(role, 4),
    }
    total = sum(scores[key] * profile["weights"][key] for key in profile["weights"])
    return round(_clamp(total), 4), scores


def _build_candidate(
    profile: dict,
    treatment: CreativeTreatment,
    capture: CaptureQualityReport,
    variant: StoryVariant,
    segments: list[Segment],
    composition: dict[str, CompositionMetrics],
) -> tuple[VisualRhythmPlan, PictureCandidateSummary]:
    plan = plan_visual_rhythm(
        profile["id"], profile["duration"], profile["factor"],
        treatment, capture, variant,
    )
    segment_by_id = {segment.segmentId: segment for segment in segments}
    used_segments: set[str] = set()
    used_duplicates: set[str] = set()
    used_ranges: dict[str, list[tuple[float, float]]] = {}
    action_counts: Counter[str] = Counter()
    max_action_uses = 1 if capture.metrics.repetitionRatio > 0.65 else 2
    previous: Segment | None = None
    chosen: list[dict] = []
    evidence: list[dict] = []
    rejected: list[str] = list(variant.rejectionReasons)

    for beat_index, (beat, rhythm) in enumerate(zip(variant.beats, plan.beats, strict=True)):
        beat_choices: list[dict] = []
        remaining = beat.targetSeconds
        for _shot_index in range(rhythm.plannedShotCount):
            ranked: list[tuple[float, Segment, dict[str, float]]] = []
            for segment in segments:
                if segment.segmentId in used_segments or not _supports(
                    segment, beat.requiredFootageProperties,
                ):
                    continue
                if any(
                    segment.sourceStart < end and segment.sourceEnd > start
                    for start, end in used_ranges.get(segment.assetId, [])
                ):
                    continue
                if segment.duplicateGroupId and segment.duplicateGroupId in used_duplicates:
                    continue
                action = _action_family(segment.action)
                if action_counts[action] >= max_action_uses:
                    continue
                total, scores = _score_segment(
                    segment, beat.requiredFootageProperties,
                    composition.get(segment.segmentId), beat.energyLevel,
                    previous, profile,
                    hook=beat_index == 0,
                    payoff=beat_index == len(variant.beats) - 1,
                )
                ranked.append((total, segment, scores))
            ranked.sort(key=lambda item: (item[0], item[1].segmentId), reverse=True)
            if not ranked:
                break
            total, segment, scores = ranked[0]
            available = segment.sourceEnd - segment.sourceStart
            duration = min(available, rhythm.targetShotSeconds, max(0.65, remaining))
            if duration < 0.65:
                break
            item = {
                "segmentId": segment.segmentId,
                "beat": beat.name,
                "energy": beat.energyLevel,
                "sourceStart": segment.sourceStart,
                "sourceEnd": round(segment.sourceStart + duration, 3),
                "duration": round(duration, 3),
                "score": total,
                "scores": scores,
                "payoffStrength": _role_score(segment, hook=False, payoff=True),
                "virtualReframe": _reframe(composition.get(segment.segmentId)).model_dump(),
            }
            beat_choices.append(item)
            evidence.append({
                "beat": beat.name, "chosen": segment.segmentId,
                "score": total, "scores": scores,
                "reason": ("strongest supported hook" if beat_index == 0 else
                           "strongest supported ending/payoff" if beat_index == len(variant.beats) - 1
                           else "highest rhythm/profile score"),
            })
            used_segments.add(segment.segmentId)
            used_ranges.setdefault(segment.assetId, []).append(
                (item["sourceStart"], item["sourceEnd"]),
            )
            if segment.duplicateGroupId:
                used_duplicates.add(segment.duplicateGroupId)
            action_counts[_action_family(segment.action)] += 1
            previous = segment
            remaining -= duration
            if remaining <= 0.15:
                break
        if not beat_choices:
            rejected.append(f"{beat.name}: no non-repeating supported clip remained")
        if beat_index == len(variant.beats) - 1:
            beat_choices.sort(key=lambda item: item["payoffStrength"])
        chosen.extend(beat_choices)

    timeline_clips: list[dict] = []
    cursor = 0.0
    for index, item in enumerate(chosen):
        segment = segment_by_id[item["segmentId"]]
        duration = item["duration"]
        timeline_clips.append({
            "id": f"{profile['id']}-clip-{index + 1}",
            "assetId": segment.assetId,
            "segmentId": segment.segmentId,
            "sourceStart": item["sourceStart"],
            "sourceEnd": item["sourceEnd"],
            "timelineStart": round(cursor, 3),
            "timelineEnd": round(cursor + duration, 3),
            "volume": 1.0,
            "speed": 1.0,
            "virtualReframe": item["virtualReframe"],
            "meta": {
                "beat": item["beat"],
                "energy": item["energy"],
                "selectionScore": item["score"],
                "selectionScores": item["scores"],
                "actor": "picture_editor_v1",
            },
        })
        cursor += duration

    required_beats = len(variant.beats)
    filled_beats = len({item["beat"] for item in chosen})
    coverage_ratio = filled_beats / max(1, required_beats)
    valid = variant.valid and coverage_ratio == 1
    average_score = (sum(item["score"] for item in chosen) / len(chosen)
                     if chosen else 0)
    timeline = {
        "version": 1,
        "width": 1080,
        "height": 1920,
        "fps": 30,
        "duration": round(cursor, 3),
        "tracks": [{"id": "video-1", "type": "video", "clips": timeline_clips}],
        "meta": {
            "milestone": "audiovisual_picture_editor_v1",
            "candidateId": profile["id"],
            "storyVariantId": variant.variantId,
            "durationProfile": profile["duration"],
            "creativeTreatmentVersion": treatment.version,
            "captureCeiling": capture.estimatedEditCeiling,
            "visualRhythmPlan": plan.model_dump(),
            "pictureOnly": True,
        },
    }
    signature = (
        f"{variant.variantId}|{profile['duration']}|"
        + ">".join(item["segmentId"] for item in chosen)
        + "|" + ",".join(f"{item['duration']:.2f}" for item in chosen)
    )
    return plan, PictureCandidateSummary(
        candidateId=profile["id"],
        label=profile["label"],
        storyVariantId=variant.variantId,
        valid=valid,
        rejectionReasons=list(dict.fromkeys(rejected)),
        durationSeconds=round(cursor, 3),
        targetDurationSeconds=treatment.targetDurationSeconds,
        coverageRatio=round(coverage_ratio, 4),
        editorialScore=round(_clamp(average_score * coverage_ratio), 4),
        structuralSignature=signature,
        clipCount=len(chosen),
        timeline=timeline,
        selectionEvidence=evidence,
    )


def build_picture_edit_package(
    preproduction_run_id: str,
    treatment: CreativeTreatment,
    capture: CaptureQualityReport,
    composition: dict[str, CompositionMetrics],
    variants: StoryVariantSet,
    segments: list[Segment],
) -> PictureEditPackage:
    """Create three auditable picture candidates from Milestone 1 evidence."""
    plans: dict[str, VisualRhythmPlan] = {}
    candidates: list[PictureCandidateSummary] = []
    for profile in _PROFILES:
        variant = _variant_for_profile(profile, treatment, variants)
        plan, candidate = _build_candidate(
            profile, treatment, capture, variant, segments, composition,
        )
        plans[candidate.candidateId] = plan
        candidates.append(candidate)

    valid = [candidate for candidate in candidates if candidate.valid]
    selected = next(
        (candidate for candidate in valid
         if candidate.storyVariantId == treatment.selectedStoryVariant),
        valid[0] if valid else None,
    )
    warnings = list(capture.limitations)
    if not any(
        metric.measurementSource == "detected_bbox" and metric.safeCrop.feasible
        for metric in composition.values()
    ):
        warnings.append("No measured safe virtual reframes are available; full frames are preserved")
    if not valid:
        warnings.append("No complete non-repeating picture candidate is supported")
    return PictureEditPackage(
        status="ready" if valid else "insufficient_coverage",
        preproductionRunId=preproduction_run_id,
        visualRhythmPlans=plans,
        candidates=candidates,
        selectedCandidateId=selected.candidateId if selected else None,
        warnings=list(dict.fromkeys(warnings)),
    )
