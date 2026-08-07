"""Milestone 1: composition, capture truth, story variants and treatment."""
import pytest
from pydantic import ValidationError

from app.pipeline.capture_quality import build_capture_quality_report
from app.pipeline.composition import (
    FrameGeometry,
    NormalizedBox,
    SubjectObservation,
    analyze_composition,
)
from app.pipeline.creative_director import (
    CreativeBrief,
    CreativeTreatment,
    EnergyPoint,
    create_treatment,
)
from app.pipeline.preproduction import build_preproduction_package
from app.pipeline.schemas import SCHEMA_VERSION, Segment
from app.pipeline.story_editor import generate_story_variants


def seg(
    sid,
    *,
    uses,
    shot="medium",
    motion=0.6,
    audio=0.7,
    transcript=None,
    action="training",
    angle="eye level",
    movement="static",
):
    return Segment(
        segmentId=sid,
        assetId=f"asset-{sid}",
        sourceStart=0,
        sourceEnd=5,
        storyUses=uses,
        shotType=shot,
        cameraAngle=angle,
        cameraMovement=movement,
        motionIntensity=motion,
        audioScore=audio,
        transcript=transcript,
        action=action,
        focusScore=0.8,
        exposureScore=0.8,
        stabilityScore=0.8,
        searchText=sid,
    )


@pytest.fixture()
def rich_catalog():
    return [
        seg("hook", uses=["hook", "peak"], shot="close", motion=0.95,
            action="sprint start", angle="low angle"),
        seg("place", uses=["location", "broll"], shot="wide", motion=0.1,
            action="gym establishing", angle="high angle"),
        seg("early", uses=["early_effort"], shot="medium", motion=0.5,
            action="running", movement="gimbal follow"),
        seg("build", uses=["build"], shot="medium", motion=0.75,
            action="sled push", movement="tracking"),
        seg("peak", uses=["peak"], shot="close", motion=1.0,
            action="tire impact", angle="ground level"),
        seg("done", uses=["completion", "reflection"], shot="medium", motion=0.25,
            action="recovery"),
    ]


def observation(x=0.38, y=0.08, width=0.24, height=0.72, confidence=0.95):
    return SubjectObservation(
        timestamp=1,
        subjectBox=NormalizedBox(x=x, y=y, width=width, height=height),
        actionBox=NormalizedBox(x=max(0, x - 0.04), y=y + 0.42,
                                width=min(0.96 - x, width + 0.10), height=0.25),
        faceBox=NormalizedBox(x=x + 0.07, y=y + 0.02, width=0.08, height=0.10),
        visibility=0.96,
        occlusion=0.05,
        confidence=confidence,
    )


def test_subject_prominence_and_composition_are_explicit():
    metrics = analyze_composition(
        [observation(), observation(x=0.40)],
        FrameGeometry(width=2160, height=3840),
    )
    assert metrics.measurementSource == "detected_bbox"
    assert metrics.subjectProminence == pytest.approx(0.1728, abs=0.001)
    assert metrics.actionVisibility > 0.8
    assert metrics.faceVisibility > 0.8
    assert 0 <= metrics.emptySpaceRatio <= 1


def test_safe_crop_respects_resolution_floor():
    high_resolution = analyze_composition(
        [observation()], FrameGeometry(width=2160, height=3840)
    )
    low_resolution = analyze_composition(
        [observation(height=0.82)], FrameGeometry(width=1080, height=1920)
    )
    assert high_resolution.safeCrop.feasible
    assert high_resolution.safeCrop.minimumOutputQualityMet
    assert not low_resolution.safeCrop.feasible
    assert "upscaling" in " ".join(low_resolution.safeCrop.reasons)


def test_capture_ceiling_is_evidence_driven(rich_catalog):
    measured = {
        segment.segmentId: analyze_composition(
            [observation()], FrameGeometry(width=2160, height=3840)
        ) for segment in rich_catalog
    }
    strong = build_capture_quality_report(rich_catalog, measured)
    weak_segments = [
        seg(f"wide-{i}", uses=["early_effort"], shot="wide", motion=0.4,
            audio=0.2, transcript="background conversation", action="same exercise",
            angle="", movement="static")
        for i in range(8)
    ]
    weak = build_capture_quality_report(weak_segments, {})
    assert strong.estimatedEditCeiling > weak.estimatedEditCeiling
    assert strong.coverageScore > weak.coverageScore
    assert weak.recommendedStyle == "raw training recap"
    assert "intentional completion or recovery/payoff shot" in weak.missingShots
    assert weak.estimateConfidence < strong.estimateConfidence


def test_five_story_variants_are_materially_distinct(rich_catalog):
    report = build_capture_quality_report(rich_catalog)
    variants = generate_story_variants(rich_catalog, report, 30)
    assert len(variants.variants) == 5
    assert len({v.structuralSignature for v in variants.variants}) == 5
    assert set(variants.validVariantIds) == {
        "action_first", "build_and_payoff", "raw_intense", "cinematic",
        "social_retention",
    }


def test_unsupported_beats_reject_variant_instead_of_inventing_coverage():
    sparse = [seg("only", uses=["early_effort"], shot="wide", motion=0.3,
                  audio=0.1, transcript="chat", angle="")]
    report = build_capture_quality_report(sparse)
    variants = generate_story_variants(sparse, report, 20)
    assert not variants.validVariantIds
    assert set(variants.rejectedVariantIds) == {v.variantId for v in variants.variants}
    assert all(v.rejectionReasons for v in variants.variants)


def test_creative_treatment_is_schema_validated_and_shared(rich_catalog):
    report = build_capture_quality_report(rich_catalog)
    variants = generate_story_variants(rich_catalog, report, 30)
    treatment = create_treatment(CreativeBrief(targetDurationSeconds=30),
                                 rich_catalog, report, variants)
    assert treatment.orientation == "9:16"
    assert treatment.visualEnergyCurve[0].position == 0
    assert treatment.visualEnergyCurve[-1].position == 1
    assert treatment.storyArc
    assert treatment.captureConstraints == report.limitations
    with pytest.raises(ValidationError):
        CreativeTreatment(
            purpose="x", audience="x", targetDurationSeconds=30,
            tone=["authentic"], selectedStoryVariant="x", storyArc=["a", "b"],
            visualEnergyCurve=[EnergyPoint(position=0.2, energy=0.5, intent="bad"),
                               EnergyPoint(position=1, energy=0.2, intent="end")],
            musicEnergyCurve=[EnergyPoint(position=0, energy=0.2, intent="start"),
                              EnergyPoint(position=1, energy=0.2, intent="end")],
            motionGraphicsDensity="low", transitionStyle="hard cuts",
            colorDirection="natural", endingIntent="complete", confidence=0.5,
        )


def test_preproduction_package_does_not_select_or_render(rich_catalog):
    package = build_preproduction_package(
        CreativeBrief(targetDurationSeconds=28), rich_catalog
    )
    assert package.schemaVersion == 1
    assert package.creativeTreatment.targetDurationSeconds == 28
    assert set(package.compositionBySegment) == {s.segmentId for s in rich_catalog}
    assert any("low-confidence" in warning for warning in package.warnings)
    dumped = package.model_dump()
    assert "selection" not in dumped and "timeline" not in dumped and "render" not in dumped


def test_canonical_segment_schema_preserves_camera_angle():
    segment = seg("angle", uses=["hook"], angle="low angle")
    # v3 = Phase 2 narrative substrate (all additive; see schemas.py header).
    # This pin exists so a schema bump is always a CONSCIOUS act.
    assert segment.schemaVersion == SCHEMA_VERSION == 3
    assert segment.model_dump()["cameraAngle"] == "low angle"
