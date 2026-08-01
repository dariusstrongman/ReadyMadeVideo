"""Milestone 1 orchestration: footage truth -> variants -> treatment."""
from __future__ import annotations

from pydantic import BaseModel, Field

from .capture_quality import CaptureQualityReport, build_capture_quality_report
from .composition import CompositionMetrics, estimate_composition_from_shot_type
from .creative_director import CreativeBrief, CreativeTreatment, create_treatment
from .schemas import Segment
from .story_editor import StoryVariantSet, generate_story_variants


class PreproductionPackage(BaseModel):
    schemaVersion: int = 1
    status: str
    creativeTreatment: CreativeTreatment
    captureQualityReport: CaptureQualityReport
    compositionBySegment: dict[str, CompositionMetrics]
    storyVariants: StoryVariantSet
    warnings: list[str] = Field(default_factory=list)


def build_preproduction_package(
    brief: CreativeBrief,
    segments: list[Segment],
    measured_composition: dict[str, CompositionMetrics] | None = None,
) -> PreproductionPackage:
    """Build inspectable planning artifacts without selecting or rendering clips."""
    composition = dict(measured_composition or {})
    for segment in segments:
        composition.setdefault(
            segment.segmentId,
            estimate_composition_from_shot_type(segment.shotType),
        )
    capture = build_capture_quality_report(segments, composition)
    target = brief.targetDurationSeconds or capture.recommendedTargetDuration
    variants = generate_story_variants(segments, capture, target)
    treatment = create_treatment(brief, segments, capture, variants)
    warnings: list[str] = []
    if not variants.validVariantIds:
        warnings.append(
            "No complete story direction is supported by current coverage; operator review required"
        )
    measured_count = sum(
        metric.measurementSource == "detected_bbox" for metric in composition.values()
    )
    if measured_count < len(composition):
        warnings.append(
            "Composition contains low-confidence shot-type estimates until focused inspection runs"
        )
    status = "ready" if variants.validVariantIds else "insufficient_coverage"
    return PreproductionPackage(
        status=status,
        creativeTreatment=treatment,
        captureQualityReport=capture,
        compositionBySegment=composition,
        storyVariants=variants,
        warnings=warnings,
    )
