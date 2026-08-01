"""Composition and safe-crop analysis for vertical fitness edits.

The analyzer consumes normalized detections rather than owning a detector. This
keeps the scoring deterministic and lets the adaptive inspector (Milestone 2)
provide observations from any schema-validated vision provider. When detections
are unavailable, callers must use the explicitly low-confidence semantic
estimate instead of pretending that a safe crop was measured.
"""
from __future__ import annotations

import statistics
from typing import Literal

from pydantic import BaseModel, Field, model_validator


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


class NormalizedBox(BaseModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def inside_frame(self):
        if self.x + self.width > 1.000001 or self.y + self.height > 1.000001:
            raise ValueError("normalized box extends outside the frame")
        return self

    @property
    def area(self) -> float:
        return self.width * self.height

    @property
    def center(self) -> tuple[float, float]:
        return self.x + self.width / 2, self.y + self.height / 2


class FrameGeometry(BaseModel):
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class SubjectObservation(BaseModel):
    timestamp: float = Field(ge=0)
    subjectBox: NormalizedBox
    actionBox: NormalizedBox | None = None
    faceBox: NormalizedBox | None = None
    visibility: float = Field(default=1, ge=0, le=1)
    occlusion: float = Field(default=0, ge=0, le=1)
    confidence: float = Field(default=1, ge=0, le=1)


class CropRecommendation(BaseModel):
    feasible: bool
    cropBox: NormalizedBox | None = None
    sourcePixelsPerOutputPixel: float = Field(default=0, ge=0)
    resolutionLoss: float = Field(default=1, ge=0, le=1)
    minimumOutputQualityMet: bool = False
    reasons: list[str] = Field(default_factory=list)


class CompositionMetrics(BaseModel):
    subjectProminence: float = Field(ge=0, le=1)
    actionVisibility: float = Field(ge=0, le=1)
    compositionQuality: float = Field(ge=0, le=1)
    cropPotential: float = Field(ge=0, le=1)
    faceVisibility: float = Field(ge=0, le=1)
    emptySpaceRatio: float = Field(ge=0, le=1)
    headroomScore: float = Field(ge=0, le=1)
    occlusion: float = Field(ge=0, le=1)
    digitalZoomSafety: float = Field(ge=0, le=1)
    measurementSource: Literal["detected_bbox", "semantic_shot_type", "unavailable"]
    confidence: float = Field(ge=0, le=1)
    humanReviewRecommended: bool
    safeCrop: CropRecommendation


def _union(a: NormalizedBox, b: NormalizedBox | None) -> NormalizedBox:
    if b is None:
        return a
    x = min(a.x, b.x)
    y = min(a.y, b.y)
    right = max(a.x + a.width, b.x + b.width)
    bottom = max(a.y + a.height, b.y + b.height)
    return NormalizedBox(x=x, y=y, width=right - x, height=bottom - y)


def _crop_for_observation(
    observation: SubjectObservation,
    source: FrameGeometry,
    output: FrameGeometry,
    minimum_pixels_per_output_pixel: float,
) -> CropRecommendation:
    required = _union(observation.subjectBox, observation.actionBox)
    # Keep breathing room around the complete person/action. The subject box is
    # required to include head and feet; the action box includes equipment.
    need_w = min(1.0, required.width * 1.18)
    need_h = min(1.0, required.height * 1.10)
    normalized_aspect = (output.width / output.height) / (source.width / source.height)
    crop_h = max(need_h, need_w / normalized_aspect)
    crop_w = crop_h * normalized_aspect
    reasons: list[str] = []
    if crop_w > 1.0 or crop_h > 1.0:
        return CropRecommendation(
            feasible=False,
            reasons=["subject/action bounds cannot fit the requested output aspect"],
        )

    cx, cy = required.center
    x = _clamp(cx - crop_w / 2, 0, 1 - crop_w)
    y = _clamp(cy - crop_h / 2, 0, 1 - crop_h)
    crop = NormalizedBox(x=x, y=y, width=crop_w, height=crop_h)
    if not (
        crop.x <= required.x
        and crop.y <= required.y
        and crop.x + crop.width >= required.x + required.width
        and crop.y + crop.height >= required.y + required.height
    ):
        return CropRecommendation(
            feasible=False,
            cropBox=crop,
            reasons=["safe-zone crop would cut off the subject or action"],
        )

    pixel_ratio = min(
        crop.width * source.width / output.width,
        crop.height * source.height / output.height,
    )
    quality_ok = pixel_ratio >= minimum_pixels_per_output_pixel
    if not quality_ok:
        reasons.append("crop would require upscaling beyond the configured quality floor")
    return CropRecommendation(
        feasible=quality_ok,
        cropBox=crop,
        sourcePixelsPerOutputPixel=round(pixel_ratio, 4),
        resolutionLoss=round(1 - crop.area, 4),
        minimumOutputQualityMet=quality_ok,
        reasons=reasons,
    )


def analyze_composition(
    observations: list[SubjectObservation],
    source: FrameGeometry,
    output: FrameGeometry | None = None,
    minimum_pixels_per_output_pixel: float = 1.0,
) -> CompositionMetrics:
    """Score subject visibility and verify a crop without inventing detail."""
    if not observations:
        raise ValueError("at least one subject observation is required")
    output = output or FrameGeometry(width=1080, height=1920)
    crops = [
        _crop_for_observation(o, source, output, minimum_pixels_per_output_pixel)
        for o in observations
    ]
    prominence = statistics.median(o.subjectBox.area for o in observations)
    action_visibility = statistics.mean(
        o.visibility * (1 - o.occlusion) * o.confidence for o in observations
    )
    empty_space = statistics.mean(1 - _union(o.subjectBox, o.actionBox).area
                                  for o in observations)
    occlusion = statistics.mean(o.occlusion for o in observations)
    face_visibility = statistics.mean(
        (1.0 if o.faceBox else 0.0) * o.visibility * o.confidence for o in observations
    )
    headroom = statistics.mean(
        1 - _clamp(abs(o.subjectBox.y - 0.06) / 0.35) for o in observations
    )
    centered = statistics.mean(
        1 - _clamp((abs(o.subjectBox.center[0] - 0.5) / 0.5) * 0.7
                   + (abs(o.subjectBox.center[1] - 0.5) / 0.5) * 0.3)
        for o in observations
    )
    composition_quality = 0.4 * centered + 0.25 * headroom + 0.35 * action_visibility
    safe = all(c.feasible for c in crops)
    worst_ratio = min(c.sourcePixelsPerOutputPixel for c in crops)
    zoom_safety = _clamp(worst_ratio / max(1.0, minimum_pixels_per_output_pixel))
    # A safe crop that makes the subject materially larger has higher value.
    crop_gain = statistics.mean(
        o.subjectBox.area / max(c.cropBox.area, 0.0001)
        if c.cropBox else 0 for o, c in zip(observations, crops, strict=True)
    )
    crop_potential = _clamp(crop_gain * zoom_safety) if safe else 0.0
    centers = [o.subjectBox.center[0] for o in observations]
    tracking_variance = max(centers) - min(centers)
    confidence = statistics.mean(o.confidence for o in observations)
    representative = min(crops, key=lambda c: c.sourcePixelsPerOutputPixel)
    return CompositionMetrics(
        subjectProminence=round(prominence, 4),
        actionVisibility=round(action_visibility, 4),
        compositionQuality=round(_clamp(composition_quality), 4),
        cropPotential=round(crop_potential, 4),
        faceVisibility=round(face_visibility, 4),
        emptySpaceRatio=round(_clamp(empty_space), 4),
        headroomScore=round(_clamp(headroom), 4),
        occlusion=round(_clamp(occlusion), 4),
        digitalZoomSafety=round(zoom_safety, 4),
        measurementSource="detected_bbox",
        confidence=round(confidence, 4),
        humanReviewRecommended=confidence < 0.75 or tracking_variance > 0.35 or not safe,
        safeCrop=representative,
    )


def estimate_composition_from_shot_type(shot_type: str) -> CompositionMetrics:
    """Conservative fallback; never claims that a crop was measured or safe."""
    shot = shot_type.lower()
    if "close" in shot:
        prominence, action, empty = 0.42, 0.78, 0.42
    elif "medium" in shot:
        prominence, action, empty = 0.20, 0.72, 0.62
    elif "wide" in shot:
        prominence, action, empty = 0.06, 0.45, 0.84
    else:
        prominence, action, empty = 0.0, 0.0, 1.0
    source = "semantic_shot_type" if shot else "unavailable"
    return CompositionMetrics(
        subjectProminence=prominence,
        actionVisibility=action,
        compositionQuality=round((prominence + action) / 2, 4),
        cropPotential=0,
        faceVisibility=0.35 if "close" in shot else 0,
        emptySpaceRatio=empty,
        headroomScore=0,
        occlusion=0,
        digitalZoomSafety=0,
        measurementSource=source,
        confidence=0.25 if shot else 0,
        humanReviewRecommended=True,
        safeCrop=CropRecommendation(
            feasible=False,
            reasons=["no bounding-box observations; crop safety was not measured"],
        ),
    )
