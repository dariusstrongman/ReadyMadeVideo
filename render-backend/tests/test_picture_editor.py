"""Audiovisual Milestone 2: deterministic picture-edit planning."""
import pytest

from app.pipeline.composition import (
    FrameGeometry,
    NormalizedBox,
    SubjectObservation,
    analyze_composition,
)
from app.pipeline.creative_director import CreativeBrief
from app.pipeline.picture_editor import build_picture_edit_package
from app.pipeline.preproduction import build_preproduction_package
from app.pipeline.schemas import Segment


def seg(sid, uses, *, shot="medium", motion=0.6, action="training",
        movement="static", duplicate=None):
    return Segment(
        segmentId=sid, assetId=f"asset-{sid}", sourceStart=0, sourceEnd=5,
        subjects=["athlete"], action=action, shotType=shot,
        cameraAngle="eye level", cameraMovement=movement, location="gym",
        storyUses=uses, motionIntensity=motion, focusScore=0.9,
        exposureScore=0.9, stabilityScore=0.9, audioScore=0.85,
        semanticRelevance=0.9, duplicateGroupId=duplicate, searchText=sid,
    )


@pytest.fixture()
def rich_segments():
    return [
        seg("hook-low", ["hook", "peak"], shot="close", motion=0.92,
            action="sprint launch"),
        seg("hook-high", ["hook", "peak"], shot="medium", motion=0.94,
            action="tire impact"),
        seg("place", ["location"], shot="wide", motion=0.12,
            action="gym exterior"),
        seg("detail", ["location", "broll"], shot="close", motion=0.2,
            action="hands preparation"),
        seg("early-track", ["early_effort"], shot="medium", motion=0.48,
            action="sled setup", movement="gimbal follow"),
        seg("early-wide", ["early_effort"], shot="wide", motion=0.52,
            action="running approach"),
        seg("build-track", ["build"], shot="medium", motion=0.72,
            action="sled push", movement="tracking"),
        seg("build-close", ["build"], shot="close", motion=0.78,
            action="rope pull"),
        seg("peak-close", ["peak"], shot="close", motion=1.0,
            action="tire strike"),
        seg("peak-medium", ["peak"], shot="medium", motion=0.96,
            action="box jump"),
        seg("done-medium", ["completion"], shot="medium", motion=0.25,
            action="finish line"),
        seg("done-close", ["reflection"], shot="close", motion=0.15,
            action="breathing recovery"),
        seg("done-wide", ["completion", "reflection"], shot="wide", motion=0.2,
            action="walk away"),
    ]


def measured(width=0.24, height=0.72):
    observation = SubjectObservation(
        timestamp=1,
        subjectBox=NormalizedBox(x=0.38, y=0.08, width=width, height=height),
        actionBox=NormalizedBox(x=0.34, y=0.48, width=0.34, height=0.24),
        faceBox=NormalizedBox(x=0.45, y=0.1, width=0.08, height=0.1),
        visibility=0.96, occlusion=0.03, confidence=0.96,
    )
    return analyze_composition(
        [observation], FrameGeometry(width=2160, height=3840),
    )


def package_for(segments, composition=None):
    preproduction = build_preproduction_package(
        CreativeBrief(targetDurationSeconds=30), segments, composition,
    )
    return build_picture_edit_package(
        "preproduction-1",
        preproduction.creativeTreatment,
        preproduction.captureQualityReport,
        preproduction.compositionBySegment,
        preproduction.storyVariants,
        segments,
    )


def test_three_candidates_have_materially_different_rhythm_and_structure(rich_segments):
    composition = {segment.segmentId: measured() for segment in rich_segments}
    result = package_for(rich_segments, composition)
    assert result.status == "ready"
    assert len(result.candidates) == 3
    assert len({candidate.structuralSignature for candidate in result.candidates}) == 3
    assert {plan.durationProfile for plan in result.visualRhythmPlans.values()} == {
        "kinetic", "balanced", "controlled",
    }
    assert len({candidate.storyVariantId for candidate in result.candidates}) == 3


def test_visual_rhythm_frontloads_hook_and_plans_energy_progression(rich_segments):
    result = package_for(
        rich_segments, {segment.segmentId: measured() for segment in rich_segments},
    )
    kinetic = result.visualRhythmPlans["kinetic_hook"]
    assert kinetic.beats[0].hookPriority
    assert kinetic.beats[0].targetShotSeconds <= 1.2
    assert kinetic.beats[-1].payoffPriority
    assert kinetic.beats[-1].targetShotSeconds >= 1.35
    assert kinetic.energyProgression[0] > kinetic.energyProgression[1]
    assert max(kinetic.energyProgression) == 1


def test_hook_selection_weights_measured_subject_prominence(rich_segments):
    composition = {segment.segmentId: measured() for segment in rich_segments}
    composition["hook-low"] = measured(width=0.12, height=0.55)
    composition["hook-high"] = measured(width=0.32, height=0.75)
    result = package_for(rich_segments, composition)
    kinetic = next(c for c in result.candidates if c.candidateId == "kinetic_hook")
    first = kinetic.timeline["tracks"][0]["clips"][0]
    assert first["segmentId"] == "hook-high"
    assert first["meta"]["selectionScores"]["prominence"] == 1


def test_valid_candidates_end_on_completion_or_recovery(rich_segments):
    result = package_for(
        rich_segments, {segment.segmentId: measured() for segment in rich_segments},
    )
    by_id = {segment.segmentId: segment for segment in rich_segments}
    for candidate in result.candidates:
        if not candidate.valid:
            continue
        last = candidate.timeline["tracks"][0]["clips"][-1]
        assert set(by_id[last["segmentId"]].storyUses) & {"completion", "reflection"}


def test_repetition_controls_duplicate_groups_and_action_overuse(rich_segments):
    rich_segments[6].duplicateGroupId = "same-build"
    rich_segments[7].duplicateGroupId = "same-build"
    rich_segments[6].action = rich_segments[7].action = "repeated push"
    result = package_for(
        rich_segments, {segment.segmentId: measured() for segment in rich_segments},
    )
    by_id = {segment.segmentId: segment for segment in rich_segments}
    for candidate in result.candidates:
        clips = candidate.timeline["tracks"][0]["clips"]
        groups = [by_id[clip["segmentId"]].duplicateGroupId for clip in clips
                  if by_id[clip["segmentId"]].duplicateGroupId]
        assert len(groups) == len(set(groups))


def test_overlapping_source_ranges_are_not_reused(rich_segments):
    rich_segments[6].assetId = "shared-build-source"
    rich_segments[7].assetId = "shared-build-source"
    result = package_for(
        rich_segments, {segment.segmentId: measured() for segment in rich_segments},
    )
    for candidate in result.candidates:
        shared = [clip for clip in candidate.timeline["tracks"][0]["clips"]
                  if clip["assetId"] == "shared-build-source"]
        assert len(shared) <= 1


def test_safe_virtual_reframe_is_stored_only_for_measured_safe_crop(rich_segments):
    measured_result = package_for(
        rich_segments, {segment.segmentId: measured() for segment in rich_segments},
    )
    measured_clips = measured_result.candidates[0].timeline["tracks"][0]["clips"]
    assert measured_clips
    assert all(clip["virtualReframe"]["mode"] == "safe_crop"
               for clip in measured_clips)
    assert all(clip["virtualReframe"]["cropBox"] for clip in measured_clips)

    fallback_result = package_for(rich_segments)
    fallback_clips = fallback_result.candidates[0].timeline["tracks"][0]["clips"]
    assert all(clip["virtualReframe"]["mode"] == "none" for clip in fallback_clips)
    assert all(clip["virtualReframe"]["cropBox"] is None for clip in fallback_clips)


def test_picture_candidates_do_not_add_later_department_output(rich_segments):
    result = package_for(rich_segments)
    for candidate in result.candidates:
        timeline = candidate.timeline
        assert [track["type"] for track in timeline["tracks"]] == ["video"]
        assert "music" not in timeline
        assert "captions" not in timeline
        assert "color" not in timeline
        assert all(clip["volume"] == 1 and clip["speed"] == 1
                   for clip in timeline["tracks"][0]["clips"])


def test_unsupported_beats_are_rejected_without_inventing_clips():
    sparse = [seg("only", ["early_effort"], shot="wide", motion=0.2,
                  action="same exercise")]
    result = package_for(sparse)
    assert result.status == "insufficient_coverage"
    assert result.selectedCandidateId is None
    assert all(not candidate.valid for candidate in result.candidates)
    assert all(candidate.rejectionReasons for candidate in result.candidates)
    assert all(candidate.coverageRatio < 1 for candidate in result.candidates)
