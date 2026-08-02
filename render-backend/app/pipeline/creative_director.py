"""Creative Treatment: the shared contract for audiovisual departments."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .capture_quality import CaptureQualityReport
from .schemas import Segment
from .story_editor import StoryVariant, StoryVariantSet


class EnergyPoint(BaseModel):
    position: float = Field(ge=0, le=1)
    energy: float = Field(ge=0, le=1)
    intent: str


class NaturalAudioMoment(BaseModel):
    segmentId: str
    intent: str
    priority: Literal["low", "medium", "high"]


class CreativeTreatment(BaseModel):
    version: int = 1
    purpose: str
    audience: str
    targetDurationSeconds: float = Field(ge=15, le=60)
    orientation: Literal["9:16"] = "9:16"
    tone: list[str] = Field(min_length=1)
    selectedStoryVariant: str
    storyArc: list[str] = Field(min_length=2)
    visualEnergyCurve: list[EnergyPoint] = Field(min_length=2)
    musicEnergyCurve: list[EnergyPoint] = Field(min_length=2)
    naturalAudioMoments: list[NaturalAudioMoment] = Field(default_factory=list)
    motionGraphicsDensity: Literal["none", "low", "medium"]
    transitionStyle: str
    colorDirection: str
    endingIntent: str
    referenceStyle: str | None = None
    captureConstraints: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def curves_cover_the_piece(self):
        for name, curve in (
            ("visualEnergyCurve", self.visualEnergyCurve),
            ("musicEnergyCurve", self.musicEnergyCurve),
        ):
            positions = [point.position for point in curve]
            if positions != sorted(positions) or positions[0] != 0 or positions[-1] != 1:
                raise ValueError(f"{name} must be ordered and span position 0 to 1")
        return self


class CreativeBrief(BaseModel):
    purpose: str = "cinematic fitness recap"
    audience: str = "social fitness audience"
    targetDurationSeconds: float | None = Field(default=None, ge=15, le=60)
    referenceStyle: str | None = None
    tone: list[str] = Field(default_factory=lambda: [
        "intense", "authentic", "motivational",
    ])
    preferredVariant: str | None = None
    graphicsPreference: Literal["none", "low", "medium"] = "low"
    colorPreference: str = "high contrast natural warmth"


def _select_variant(variants: StoryVariantSet, preferred: str | None) -> StoryVariant:
    valid = [variant for variant in variants.variants if variant.valid]
    if preferred:
        match = next((v for v in valid if v.variantId == preferred), None)
        if match:
            return match
    if valid:
        return valid[0]
    # A treatment still records the least unsupported direction so the operator
    # can see the limitations. It is not permission to render that direction.
    return min(variants.variants, key=lambda variant: len(variant.rejectionReasons))


def _energy_curve(variant: StoryVariant, *, music: bool) -> list[EnergyPoint]:
    total = sum(beat.targetSeconds for beat in variant.beats)
    position = 0.0
    points = [EnergyPoint(
        position=0,
        energy=round(variant.beats[0].energyLevel * (0.9 if music else 1), 3),
        intent=variant.beats[0].audioIntent if music else variant.beats[0].name,
    )]
    for beat in variant.beats[1:]:
        position += variant.beats[variant.beats.index(beat) - 1].targetSeconds
        points.append(EnergyPoint(
            position=round(min(0.999, position / total), 3),
            energy=round(beat.energyLevel * (0.9 if music else 1), 3),
            intent=beat.audioIntent if music else beat.name,
        ))
    points.append(EnergyPoint(
        position=1,
        energy=round(variant.beats[-1].energyLevel * (0.75 if music else 1), 3),
        intent="musical resolution" if music else (variant.beats[-1].endingRequirement
                                                    or "intentional ending"),
    ))
    return points


def create_treatment(
    brief: CreativeBrief,
    segments: list[Segment],
    capture_report: CaptureQualityReport,
    variants: StoryVariantSet,
) -> CreativeTreatment:
    selected = _select_variant(variants, brief.preferredVariant)
    duration = brief.targetDurationSeconds or capture_report.recommendedTargetDuration
    natural_candidates = sorted(
        (s for s in segments if s.audioScore >= 0.6 and not s.transcript),
        key=lambda segment: (segment.audioScore, segment.motionIntensity),
        reverse=True,
    )[:4]
    natural_moments = [NaturalAudioMoment(
        segmentId=segment.segmentId,
        intent="preserve authentic effort or impact above the music bed",
        priority="high" if "peak" in segment.storyUses else "medium",
    ) for segment in natural_candidates]
    ending = next(
        (beat.endingRequirement for beat in reversed(selected.beats)
         if beat.endingRequirement),
        "exhausted but accomplished",
    )
    confidence = capture_report.estimateConfidence
    if not selected.valid:
        confidence *= 0.6
    return CreativeTreatment(
        purpose=brief.purpose,
        audience=brief.audience,
        targetDurationSeconds=duration,
        tone=brief.tone,
        selectedStoryVariant=selected.variantId,
        storyArc=[beat.name for beat in selected.beats],
        visualEnergyCurve=_energy_curve(selected, music=False),
        musicEnergyCurve=_energy_curve(selected, music=True),
        naturalAudioMoments=natural_moments,
        motionGraphicsDensity=brief.graphicsPreference,
        transitionStyle="mostly hard cuts; motivated alternatives only",
        colorDirection=brief.colorPreference,
        endingIntent=ending,
        referenceStyle=brief.referenceStyle,
        captureConstraints=capture_report.limitations,
        confidence=round(confidence, 3),
    )
