"""Milestone 6 candidate, critic, revision, tournament, and render tests."""
import copy
import os
import random
import subprocess

import pytest

from app.pipeline.editorial_intelligence import (
    CompleteCandidateManifest,
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


def test_cc_by_without_rendered_attribution_is_blocked_and_cannot_win():
    manifests, completed, segments, composition = _manifests()
    cc0 = manifests[0].model_copy(deep=True)
    cc_by = manifests[0].model_copy(deep=True)
    cc0.candidateKey = "cc0-eligible"
    cc_by.candidateKey = "cc-by-blocked"
    cc0.musicAssetSelection = {
        "assetId": "music-cc0", "attributionRequired": False,
        "attributionStatus": "not_required",
        "attribution": {"title": "CC0 pulse", "license": "CC0 1.0"},
    }
    cc_by.musicAssetSelection = {
        "assetId": "music-cc-by", "attributionRequired": True,
        "attributionStatus": "requires_attribution",
        "attribution": {
            "title": "Credited pulse", "creator": "Creator",
            "sourceUrl": "https://provider.example/music",
            "license": "CC BY 4.0",
            "licenseUrl": "https://creativecommons.org/licenses/by/4.0/",
            "text": "Credited pulse by Creator, CC BY 4.0",
        },
    }
    baseline = manifests[0]
    baseline_report = build_publishability_report(
        baseline, run_specialized_critics(
            baseline, segments, composition, completed,
        ),
    )
    cc0_report = build_publishability_report(
        cc0, run_specialized_critics(cc0, segments, composition, completed),
    )
    cc_by_report = build_publishability_report(
        cc_by, run_specialized_critics(cc_by, segments, composition, completed),
    )
    assert cc0_report.attributionCompliancePassed is True
    assert cc0_report.publishable == baseline_report.publishable
    assert cc0_report.tournamentEligible is True
    assert cc_by_report.publishable is False
    assert cc_by_report.attributionCompliancePassed is False
    assert cc_by_report.tournamentEligible is False
    assert "attribution: required_attribution_not_rendered" in cc_by_report.blockingIssues
    assert cc_by_report.attributionCompliance["status"] == "requires_attribution"
    tournament = run_tournament([cc_by_report, cc0_report])
    assert tournament.winnerCandidateKey == cc0.candidateKey
    assert cc_by.candidateKey in tournament.ineligibleCandidateKeys


def test_music_selection_provenance_flows_into_every_complete_candidate():
    treatment, candidates, completed, segments, composition, plan = _inputs()
    selection = {
        "assetId": "music-cc-by", "sourceProvider": "manual",
        "sourceAssetId": "licensed-1", "attributionRequired": True,
        "attributionStatus": "requires_attribution",
        "attribution": {
            "title": "Credited pulse", "creator": "Creator",
            "sourceUrl": "https://provider.example/music",
            "license": "CC BY 4.0",
            "licenseUrl": "https://creativecommons.org/licenses/by/4.0/",
            "text": "Credited pulse by Creator, CC BY 4.0",
        },
    }
    plan.libraryAssetSelection = selection
    manifests = generate_initial_candidates(
        candidates, treatment, completed, plan, segments, composition, {},
    )
    assert all(item.musicAssetSelection == selection for item in manifests)
    assert all(item.attributionRendered is False for item in manifests)


@pytest.mark.parametrize(
    ("qc_change", "blocking_code"),
    [
        ({"videoStreamPresent": False}, "missing_video_stream"),
        ({"audioStreamPresent": False}, "missing_audio_stream"),
        ({"durationSeconds": 99}, "duration_mismatch"),
        ({"passed": False}, "explicit_blocking_failure"),
    ],
    ids=["missing-video", "missing-audio", "duration-mismatch", "blocking-render-failure"],
)
def test_failed_rendered_media_is_non_publishable_and_cannot_win(
    qc_change, blocking_code,
):
    manifests, completed, segments, composition = _manifests()
    good = manifests[0]
    failed = manifests[0].model_copy(deep=True)
    good.candidateKey = "eligible-a"
    failed.candidateKey = "ineligible-z"
    failed.renderQc.update(qc_change)
    good_report = build_publishability_report(
        good, run_specialized_critics(good, segments, composition, completed),
    )
    failed_report = build_publishability_report(
        failed, run_specialized_critics(failed, segments, composition, completed),
    )
    assert failed_report.publishable is False
    assert failed_report.renderedMediaQcPassed is False
    assert failed_report.tournamentEligible is False
    assert any(blocking_code in item for item in failed_report.blockingIssues)
    tournament = run_tournament([failed_report, good_report])
    assert tournament.winnerCandidateKey == good_report.candidateKey
    assert failed_report.candidateKey in tournament.ineligibleCandidateKeys
    comparison = tournament.pairwiseComparisons[0]
    assert comparison.winnerCandidateKey == good_report.candidateKey
    assert comparison.tournamentEligibilityApplied is True


def test_exact_score_tie_is_explicit_and_uses_documented_candidate_key_rule():
    manifests, completed, segments, composition = _manifests()
    left = manifests[0].model_copy(deep=True)
    right = manifests[0].model_copy(deep=True)
    left.candidateKey = "tie-a"
    right.candidateKey = "tie-z"
    reports = [build_publishability_report(
        item, run_specialized_critics(item, segments, composition, completed),
    ) for item in (left, right)]
    tournament = run_tournament(reports)
    comparison = tournament.pairwiseComparisons[0]
    assert comparison.tieOccurred is True
    assert comparison.tiebreakRule == "lexicographically_greater_candidate_key"
    assert comparison.winnerCandidateKey == "tie-z"
    assert tournament.winnerCandidateKey == "tie-z"
    assert any("Tie detected" in item and "winning_key=tie-z" in item
               for item in tournament.winnerReasoning)
    assert "Exact score tie" in tournament.bracket[0].eliminationReason


def test_shuffled_candidate_order_produces_identical_tournament():
    manifests, completed, segments, composition = _manifests()
    reports = [build_publishability_report(
        item, run_specialized_critics(item, segments, composition, completed),
    ) for item in manifests]
    expected = run_tournament(reports)
    shuffled = reports.copy()
    random.Random(481516).shuffle(shuffled)
    actual = run_tournament(shuffled)
    assert actual.winnerCandidateKey == expected.winnerCandidateKey
    assert actual.bracket == expected.bracket
    assert actual.pairwiseComparisons == expected.pairwiseComparisons
    assert actual.winnerReasoning == expected.winnerReasoning


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


def test_missing_human_ceiling_is_explicit_and_has_only_null_deltas():
    manifests, completed, segments, composition = _manifests()
    winner = manifests[0]
    publication = build_publishability_report(
        winner, run_specialized_critics(winner, segments, composition, completed),
    )
    comparison = build_four_way_comparison(None, winner, publication)
    assert comparison["status"] == "human_ceiling_unavailable"
    assert comparison["missing_versions"] == ["autonomous_initial", "human_approved"]
    assert all(value is None
               for value in comparison["measurable_improvements"].values())


def test_candidate_manifest_rejects_fabrication_and_source_ancestry_mismatch():
    manifests, _, _, _ = _manifests()
    payload = manifests[0].model_dump()
    payload["fabricatedFootage"] = True
    with pytest.raises(ValueError, match="False"):
        CompleteCandidateManifest(**payload)
    payload = manifests[0].model_dump()
    payload["sourceAssetIds"] = ["foreign-asset"]
    with pytest.raises(ValueError, match="source ancestry"):
        CompleteCandidateManifest(**payload)


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
