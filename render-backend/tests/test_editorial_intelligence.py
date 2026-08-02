"""Milestone 6 candidate, critic, revision, tournament, and render tests."""
import copy
import os
import subprocess

import pytest

from app.pipeline.editorial_intelligence import (
    RevisionInstruction,
    apply_bounded_revision,
    build_four_way_comparison,
    build_publishability_report,
    generate_initial_candidates,
    render_complete_candidate,
    run_specialized_critics,
    run_tournament,
)
from app.pipeline.music_supervisor import build_music_plan
from app.pipeline.picture_editor import PictureCandidateSummary
from app.renderer import FFMPEG
from tests.test_visual_finishing import _evidence


def _inputs():
    treatment, base, completed, segments, composition = _evidence()
    candidates = []
    original = copy.deepcopy(base.timeline)
    variants = [
        ("picture-original", original),
        ("picture-reversed", copy.deepcopy(original)),
        ("picture-alternate", copy.deepcopy(original)),
    ]
    variants[1][1]["tracks"][0]["clips"].reverse()
    cursor = 0
    for clip in variants[1][1]["tracks"][0]["clips"]:
        duration = clip["sourceEnd"] - clip["sourceStart"]
        clip["timelineStart"], clip["timelineEnd"] = cursor, cursor + duration
        cursor += duration
    variants[2][1]["tracks"][0]["clips"][0]["sourceEnd"] = 3.5
    variants[2][1]["tracks"][0]["clips"][0]["timelineEnd"] = 3.5
    variants[2][1]["tracks"][0]["clips"][1]["timelineStart"] = 3.5
    variants[2][1]["tracks"][0]["clips"][1]["timelineEnd"] = 7.5
    variants[2][1]["duration"] = 7.5
    for index, (candidate_id, timeline) in enumerate(variants):
        candidates.append(PictureCandidateSummary(
            candidateId=candidate_id, label=f"Picture {index + 1}",
            storyVariantId=f"story-{index + 1}", valid=True,
            durationSeconds=timeline["duration"], targetDurationSeconds=15,
            coverageRatio=.8 + index * .05, editorialScore=.75 + index * .05,
            structuralSignature=f"signature-{index + 1}", clipCount=2,
            timeline=timeline,
        ))
    plan = build_music_plan("preprod", "picture", treatment, candidates[0], segments)
    return treatment, candidates, completed, segments, composition, plan


def _manifests():
    treatment, candidates, completed, segments, composition, plan = _inputs()
    manifests = generate_initial_candidates(
        candidates, treatment, completed, plan, segments, composition, {},
    )
    for manifest in manifests:
        manifest.renderQc = {
            "videoStreamPresent": True, "audioStreamPresent": True,
            "durationSeconds": manifest.pictureTimeline["duration"],
            "fabricatedFootage": False,
        }
    return manifests, completed, segments, composition


def test_complete_candidates_are_materially_different_and_use_real_ancestry():
    manifests, _, _, _ = _manifests()
    assert len(manifests) == 3
    assert len({item.sourcePictureCandidateId for item in manifests}) == 3
    assert len({item.variant.musicSyncOffsetSeconds for item in manifests}) == 3
    assert len({item.variant.graphicsTimingOffsetSeconds for item in manifests}) == 3
    assert len({item.variant.captionLayout for item in manifests}) == 3
    assert len({item.variant.colorPreset for item in manifests}) == 3
    assert all(item.fabricatedFootage is False for item in manifests)
    assert all({clip["assetId"] for clip in item.pictureTimeline["tracks"][0]["clips"]}
               == set(item.sourceAssetIds) for item in manifests)


def test_single_valid_picture_still_yields_material_finished_variants():
    treatment, candidates, completed, segments, composition, plan = _inputs()
    manifests = generate_initial_candidates(
        candidates[:1], treatment, completed, plan, segments, composition, {},
    )
    assert len(manifests) == 3
    assert {item.sourcePictureCandidateId for item in manifests} == {
        candidates[0].candidateId
    }
    assert len({(item.variant.musicSyncOffsetSeconds,
                 item.variant.graphicsTimingOffsetSeconds,
                 item.variant.captionLayout, item.variant.colorPreset)
                for item in manifests}) == 3


def test_ten_independent_critics_are_structured_and_deterministically_consistent():
    manifests, completed, segments, composition = _manifests()
    first = run_specialized_critics(manifests[0], segments, composition, completed)
    second = run_specialized_critics(manifests[0], segments, composition, completed)
    assert len(first) == 10
    assert {item.criticKind for item in first} == {
        "hook_effectiveness", "story_structure", "pacing_retention",
        "picture_quality", "music_synchronization", "audio_quality",
        "motion_graphics", "captions", "color_finishing", "publishability",
    }
    assert [(item.score, item.consistencyHash) for item in first] == [
        (item.score, item.consistencyHash) for item in second
    ]
    assert all(item.evidence and all(e.sourceRef and e.explanation for e in item.evidence)
               for item in first)


def test_revision_loop_is_bounded_and_never_fabricates_footage():
    manifests, _, segments, _ = _manifests()
    parent = manifests[0]
    original_assets = set(parent.sourceAssetIds)
    requests = [
        RevisionInstruction(operation="change_hook", field="first", currentValue="a",
                            proposedValue="best", bound="existing clips",
                            evidenceMetric="hook"),
        RevisionInstruction(operation="shift_music_timing", field="music", currentValue=.1,
                            proposedValue=0, bound="<=.15", evidenceMetric="sync"),
        RevisionInstruction(operation="shift_graphics_timing", field="graphics",
                            currentValue=.2, proposedValue=0, bound="<=.35",
                            evidenceMetric="phrase"),
        RevisionInstruction(operation="change_caption_layout", field="caption",
                            currentValue="bottom", proposedValue="adaptive",
                            bound="safe title", evidenceMetric="collision"),
        RevisionInstruction(operation="change_color_instructions", field="color",
                            currentValue="warm", proposedValue="neutral_social",
                            bound="allowlist", evidenceMetric="color"),
    ]
    revised = apply_bounded_revision(parent, requests, segments)
    assert revised.generationKind == "revised"
    assert revised.parentCandidateKey == parent.candidateKey
    assert set(revised.sourceAssetIds) == original_assets
    assert revised.fabricatedFootage is False
    assert revised.variant.musicSyncOffsetSeconds == 0
    assert revised.variant.captionLayout == "adaptive"
    assert revised.variant.colorPreset == "neutral_social"
    assert parent.generationKind == "initial" and parent.previewStoragePath is None


def test_publishability_and_tournament_compare_every_candidate_and_select_maximum():
    manifests, completed, segments, composition = _manifests()
    reports = []
    for manifest in manifests:
        critics = run_specialized_critics(manifest, segments, composition, completed)
        report = build_publishability_report(manifest, critics)
        assert set(report.dimensions) == {
            "hook_quality", "pacing", "emotional_payoff", "clarity",
            "graphics_quality", "caption_quality", "music_fit", "audio_quality",
            "technical_qc",
        }
        assert all(item.evidenceRefs and item.explanation
                   for item in report.dimensions.values())
        reports.append(report)
    tournament = run_tournament(reports)
    assert len(tournament.pairwiseComparisons) == 3
    assert len(tournament.eliminatedCandidateKeys) == 2
    expected = max(reports, key=lambda item: (
        item.overallPublishabilityScore, item.candidateKey,
    )).candidateKey
    assert tournament.winnerCandidateKey == expected
    assert all(item.decisiveEvidence for item in tournament.pairwiseComparisons)


def test_four_way_human_ceiling_comparison_reports_measurable_improvements():
    manifests, completed, segments, composition = _manifests()
    winner = manifests[0]
    publication = build_publishability_report(
        winner, run_specialized_critics(winner, segments, composition, completed),
    )
    human = {
        "versions": {
            "autonomous_initial": {"duration_seconds": 7.8, "video_clip_count": 2,
                                   "source_asset_count": 1,
                                   "scorecard": {"overall_rating": 6}},
            "autonomous_revised": {"duration_seconds": 8, "video_clip_count": 2,
                                   "source_asset_count": 1,
                                   "scorecard": {"overall_rating": 7}},
            "human_approved": {"duration_seconds": 8.2, "video_clip_count": 3,
                               "source_asset_count": 1,
                               "scorecard": {"overall_rating": 8}},
        },
        "human_work": {"server_measured_minutes": 12.5},
    }
    comparison = build_four_way_comparison(human, winner, publication)
    assert comparison["status"] == "complete"
    assert set(comparison["versions"]) == {
        "autonomous_initial", "autonomous_revised", "human_approved",
        "editorial_intelligence_winner",
    }
    assert comparison["measurable_improvements"]["human_correction_minutes"] == 12.5
    assert "winner_vs_human_score_points" in comparison["measurable_improvements"]


def test_real_complete_candidate_fixture_render_has_audio_video_and_no_fabrication(tmp_path):
    manifests, _, _, _ = _manifests()
    source = str(tmp_path / "source.mp4")
    output = str(tmp_path / "candidate.mp4")
    subprocess.run([
        FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i",
        "testsrc=size=360x640:rate=24:duration=9", "-f", "lavfi", "-i",
        "sine=frequency=220:sample_rate=48000:duration=9", "-c:v", "libx264",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", source,
    ], check=True, timeout=120)
    qc = render_complete_candidate(
        manifests[0], {"asset-a": source}, source, output, str(tmp_path),
    )
    assert os.path.getsize(output) > 0
    assert qc["videoStreamPresent"] and qc["audioStreamPresent"]
    assert qc["fabricatedFootage"] is False
    assert qc["sourcePictureCandidateId"] == manifests[0].sourcePictureCandidateId
    assert qc["durationSeconds"] == pytest.approx(
        manifests[0].pictureTimeline["duration"], abs=.15,
    )
