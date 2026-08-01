"""Honest capture-quality and edit-ceiling assessment.

The report describes what the footage can support before creative planning. It
uses normalized, inspectable inputs and never upgrades a missing measurement to
a confident claim. Scores are decision aids, not promises of final quality.
"""
from __future__ import annotations

from collections import Counter

from pydantic import BaseModel, Field

from .composition import CompositionMetrics
from .schemas import Segment


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


class CaptureMetrics(BaseModel):
    cameraAngleCount: int = Field(ge=0)
    shotSizeDiversity: float = Field(ge=0, le=1)
    subjectProminence: float = Field(ge=0, le=1)
    closeUpPresence: bool
    trackingShotPresence: bool
    establishingShotPresence: bool
    completionShotPresence: bool
    recoveryPayoffPresence: bool
    naturalAudioQuality: float = Field(ge=0, le=1)
    repetitionRatio: float = Field(ge=0, le=1)
    deadTimeRatio: float = Field(ge=0, le=1)
    cropPotential: float = Field(ge=0, le=1)
    measuredCompositionRatio: float = Field(ge=0, le=1)
    usableDurationSeconds: float = Field(ge=0)
    distinctActionCount: int = Field(ge=0)


class CaptureQualityReport(BaseModel):
    schemaVersion: int = 1
    coverageScore: float = Field(ge=0, le=10)
    estimatedEditCeiling: float = Field(ge=1, le=10)
    estimateConfidence: float = Field(ge=0, le=1)
    strengths: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    missingShots: list[str] = Field(default_factory=list)
    recommendedTargetDuration: int = Field(ge=15, le=60)
    recommendedStyle: str
    metrics: CaptureMetrics
    evidence: dict[str, list[str]] = Field(default_factory=dict)


def _usable(segment: Segment) -> bool:
    return not ({"mostly_black", "mostly_frozen", "operator_unusable"}
                & set(segment.problems))


def _shot_family(value: str) -> str:
    value = value.lower()
    if "close" in value:
        return "close"
    if "medium" in value:
        return "medium"
    if "wide" in value:
        return "wide"
    return "unknown"


def _action_family(value: str) -> str:
    tokens = [t for t in value.lower().replace("-", " ").split()
              if t not in {"a", "an", "the", "person", "athlete", "man", "woman"}]
    return " ".join(tokens[:4]) or "unknown"


def build_capture_quality_report(
    segments: list[Segment],
    composition_by_segment: dict[str, CompositionMetrics] | None = None,
) -> CaptureQualityReport:
    composition_by_segment = composition_by_segment or {}
    usable = [segment for segment in segments if _usable(segment)]
    if not usable:
        metrics = CaptureMetrics(
            cameraAngleCount=0,
            shotSizeDiversity=0,
            subjectProminence=0,
            closeUpPresence=False,
            trackingShotPresence=False,
            establishingShotPresence=False,
            completionShotPresence=False,
            recoveryPayoffPresence=False,
            naturalAudioQuality=0,
            repetitionRatio=1,
            deadTimeRatio=1,
            cropPotential=0,
            measuredCompositionRatio=0,
            usableDurationSeconds=0,
            distinctActionCount=0,
        )
        return CaptureQualityReport(
            coverageScore=0,
            estimatedEditCeiling=1,
            estimateConfidence=0,
            limitations=["No usable segments were available for assessment"],
            missingShots=["usable action footage", "completion/payoff footage"],
            recommendedTargetDuration=15,
            recommendedStyle="insufficient footage",
            metrics=metrics,
        )

    shot_families = {_shot_family(s.shotType) for s in usable} - {"unknown"}
    angle_values = {
        str(getattr(s, "cameraAngle", "")).strip().lower()
        for s in usable if str(getattr(s, "cameraAngle", "")).strip()
    }
    movements = [s.cameraMovement.lower() for s in usable]
    action_families = [_action_family(s.action) for s in usable]
    action_counts = Counter(action_families)
    dominant_action = max(action_counts.values()) / len(usable)
    duplicate_counts = Counter(s.duplicateGroupId for s in usable if s.duplicateGroupId)
    duplicate_extra = sum(max(0, count - 1) for count in duplicate_counts.values())
    repetition = max(dominant_action, duplicate_extra / len(usable))

    durations = [max(0.0, s.sourceEnd - s.sourceStart) for s in usable]
    total_duration = sum(durations)
    dead_duration = sum(
        duration for segment, duration in zip(usable, durations, strict=True)
        if segment.motionIntensity < 0.15
        and not ({"location", "reflection", "completion", "broll"}
                 & set(segment.storyUses))
    )
    comps = [composition_by_segment[s.segmentId] for s in usable
             if s.segmentId in composition_by_segment]
    measured = [c for c in comps if c.measurementSource == "detected_bbox"]
    prominence = sum(c.subjectProminence for c in comps) / len(comps) if comps else 0
    crop_potential = sum(c.cropPotential for c in comps) / len(comps) if comps else 0
    measured_ratio = len(measured) / len(usable)

    closeup = "close" in shot_families
    tracking = any(any(word in movement for word in ("track", "follow", "gimbal"))
                   for movement in movements)
    establishing = any("location" in s.storyUses for s in usable)
    completion = any("completion" in s.storyUses for s in usable)
    recovery = any("reflection" in s.storyUses for s in usable)
    natural_audio = max((s.audioScore for s in usable if not s.transcript), default=0)
    peak = any("peak" in s.storyUses for s in usable)
    shot_diversity = len(shot_families) / 3
    angle_diversity = min(1.0, len(angle_values) / 3)
    prominence_score = _clamp(prominence / 0.22)
    story_coverage = sum((establishing, peak, completion, recovery)) / 4
    diversity_score = 1 - repetition
    visual_coverage = (
        0.25 * angle_diversity
        + 0.25 * shot_diversity
        + 0.30 * prominence_score
        + 0.20 * crop_potential
    )
    coverage = (
        0.40 * visual_coverage
        + 0.25 * story_coverage
        + 0.15 * natural_audio
        + 0.20 * diversity_score
    )
    # The ceiling deliberately tops out below 10 from capture evidence alone:
    # direction, editing and finishing still have to earn the remaining point.
    ceiling = 3.0 + 2.5 * visual_coverage + 1.5 * story_coverage \
        + natural_audio + diversity_score
    ceiling = max(1.0, min(9.0, ceiling))
    estimate_confidence = _clamp(0.45 + 0.35 * measured_ratio
                                 + 0.20 * min(1, len(usable) / 12))

    metrics = CaptureMetrics(
        cameraAngleCount=len(angle_values),
        shotSizeDiversity=round(shot_diversity, 3),
        subjectProminence=round(prominence, 3),
        closeUpPresence=closeup,
        trackingShotPresence=tracking,
        establishingShotPresence=establishing,
        completionShotPresence=completion,
        recoveryPayoffPresence=recovery,
        naturalAudioQuality=round(natural_audio, 3),
        repetitionRatio=round(repetition, 3),
        deadTimeRatio=round(dead_duration / max(total_duration, 0.001), 3),
        cropPotential=round(crop_potential, 3),
        measuredCompositionRatio=round(measured_ratio, 3),
        usableDurationSeconds=round(total_duration, 3),
        distinctActionCount=len(set(action_families) - {"unknown"}),
    )

    strengths: list[str] = []
    limitations: list[str] = []
    missing: list[str] = []
    if peak:
        strengths.append("Peak-action coverage is present")
    else:
        missing.append("clear peak-action moment")
    if completion or recovery:
        strengths.append("Completion or recovery footage can support an ending")
    else:
        missing.append("intentional completion or recovery/payoff shot")
    if natural_audio >= 0.6:
        strengths.append("At least one useful natural-audio segment is available")
    else:
        limitations.append("Natural audio is weak, absent, or speech-contaminated")
        missing.append("clean effort and impact audio")
    if not closeup:
        limitations.append("No close-up coverage was identified")
        missing.append("close-up effort and detail shots")
    if len(angle_values) < 2:
        limitations.append("Camera-angle diversity is unverified or insufficient")
        missing.append("a second intentional camera angle")
    if repetition > 0.65:
        limitations.append("Most usable segments repeat the same action or shot family")
    if measured_ratio < 1:
        limitations.append("Some composition scores are semantic estimates, not measured boxes")
    if crop_potential == 0:
        limitations.append("No crop has passed subject-bound and resolution safety checks")

    non_repetitive_duration = total_duration * (1 - 0.65 * repetition)
    target = round(max(15, min(60, 12 + 0.35 * non_repetitive_duration)))
    if ceiling < 5.8:
        style = "raw training recap"
    elif ceiling < 7.2:
        style = "energetic fitness montage"
    else:
        style = "cinematic fitness recap"
    return CaptureQualityReport(
        coverageScore=round(coverage * 10, 2),
        estimatedEditCeiling=round(ceiling, 2),
        estimateConfidence=round(estimate_confidence, 3),
        strengths=strengths,
        limitations=limitations,
        missingShots=list(dict.fromkeys(missing)),
        recommendedTargetDuration=target,
        recommendedStyle=style,
        metrics=metrics,
        evidence={
            "peak": [s.segmentId for s in usable if "peak" in s.storyUses],
            "completion": [s.segmentId for s in usable if "completion" in s.storyUses],
            "reflection": [s.segmentId for s in usable if "reflection" in s.storyUses],
            "closeups": [s.segmentId for s in usable if _shot_family(s.shotType) == "close"],
            "tracking": [s.segmentId for s in usable if any(
                word in s.cameraMovement.lower() for word in ("track", "follow", "gimbal"))],
        },
    )
