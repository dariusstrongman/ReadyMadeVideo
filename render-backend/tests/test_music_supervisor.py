"""Audiovisual Milestone 3: deterministic music and sound planning."""
import copy

import pytest

from app.audio_library.ingestion import AudioIngestor
from app.audio_library.integration import AudioLibraryAdapter
from app.audio_library.licenses import LicensePolicy
from app.audio_library.store import AudioLibraryPaths, ManifestStore
from app.pipeline.creative_director import (
    CreativeTreatment,
    EnergyPoint,
    NaturalAudioMoment,
)
from app.pipeline.music_supervisor import MusicSupervisorError, build_music_plan
from app.pipeline.picture_editor import PictureCandidateSummary
from app.pipeline.schemas import Segment
from tests.test_audio_library import FIXED_TIME, FileProvider, asset, make_tone


def segment(sid, *, action, uses, audio=0.8, transcript=None):
    return Segment(
        segmentId=sid, assetId="asset-a", sourceStart=0, sourceEnd=4.5,
        subjects=["athlete"], action=action, shotType="medium",
        cameraAngle="eye level", cameraMovement="static", location="gym",
        transcript=transcript, storyUses=uses, motionIntensity=0.7,
        focusScore=0.9, exposureScore=0.9, stabilityScore=0.9,
        audioScore=audio, semanticRelevance=0.9, searchText=action,
    )


@pytest.fixture()
def evidence():
    segments = [
        segment("clean", action="equipment preparation", uses=["hook"]),
        segment("impact", action="tire impact", uses=["peak"]),
        segment("chatter", action="training", uses=["build"],
                transcript="people talking behind the athlete"),
        segment("effort", action="breathing recovery", uses=["reflection"]),
    ]
    treatment = CreativeTreatment(
        purpose="authentic training recap", audience="social fitness audience",
        targetDurationSeconds=18, tone=["intense", "authentic"],
        selectedStoryVariant="treatment_arc", storyArc=["hook", "build", "payoff"],
        visualEnergyCurve=[
            EnergyPoint(position=0, energy=0.6, intent="hook"),
            EnergyPoint(position=1, energy=0.3, intent="payoff"),
        ],
        musicEnergyCurve=[
            EnergyPoint(position=0, energy=0.55, intent="establish pulse"),
            EnergyPoint(position=0.45, energy=0.9, intent="build"),
            EnergyPoint(position=0.78, energy=1, intent="impact"),
            EnergyPoint(position=1, energy=0.25, intent="resolve"),
        ],
        naturalAudioMoments=[
            NaturalAudioMoment(segmentId="impact", intent="preserve impact", priority="high"),
            NaturalAudioMoment(segmentId="effort", intent="preserve breath", priority="high"),
        ],
        motionGraphicsDensity="low", transitionStyle="hard cuts",
        colorDirection="natural", endingIntent="resolved recovery", confidence=0.9,
    )
    clips = []
    for index, item in enumerate(segments):
        clips.append({
            "id": f"clip-{index}", "assetId": item.assetId,
            "segmentId": item.segmentId, "sourceStart": 0, "sourceEnd": 4.5,
            "timelineStart": index * 4.5, "timelineEnd": (index + 1) * 4.5,
            "volume": 1, "speed": 1,
        })
    candidate = PictureCandidateSummary(
        candidateId="treatment_arc", label="Treatment Arc",
        storyVariantId="treatment_arc", valid=True, durationSeconds=18,
        targetDurationSeconds=18, coverageRatio=1, editorialScore=0.9,
        structuralSignature="clean>impact>chatter>effort", clipCount=4,
        timeline={
            "version": 1, "duration": 18,
            "tracks": [{"id": "video-1", "type": "video", "clips": clips}],
        },
    )
    return treatment, candidate, segments


def plan_for(evidence):
    treatment, candidate, segments = evidence
    return build_music_plan("preprod-1", "picture-1", treatment, candidate, segments)


def test_music_plan_has_beat_bar_and_phrase_analysis(evidence):
    plan = plan_for(evidence)
    analysis = plan.beatPhraseAnalysis
    assert 80 <= analysis.tempoBpm <= 160
    assert analysis.markers[0].kind == "downbeat"
    assert analysis.markers[4].barIndex == 1
    assert analysis.phrases[-1].isEndingPhrase
    assert analysis.analysisSource == "treatment_derived_music_brief"


def test_music_energy_curve_aligns_to_picture_and_music_anchors(evidence):
    plan = plan_for(evidence)
    assert [item.targetEnergy for item in plan.energyAlignment] == [0.55, 0.9, 1, 0.25]
    assert plan.energyAlignment[0].alignment == "phrase_boundary"
    assert plan.energyAlignment[-1].pictureTimeSeconds == 18


def test_natural_audio_classifies_chatter_impacts_and_effort(evidence):
    plan = plan_for(evidence)
    by_segment = {event.segmentId: event for event in plan.naturalAudioEvents}
    assert by_segment["clean"].classification == "clean_natural"
    assert by_segment["impact"].classification == "impact"
    assert by_segment["chatter"].classification == "background_chatter"
    assert by_segment["chatter"].chatterDetected is True
    assert by_segment["effort"].classification == "effort"


def test_chatter_reduction_ducking_and_impact_emphasis_are_explicit(evidence):
    plan = plan_for(evidence)
    instructions = {item.clipId: item for item in plan.sourceAudioInstructions}
    assert instructions["clip-2"].action == "reduce_chatter"
    assert instructions["clip-2"].targetGainDb == -18
    assert [item.clipId for item in plan.impactEmphasis] == ["clip-1"]
    assert all(item.musicGainDb < 0 for item in plan.musicDucking)
    assert not any(item.clipId == "clip-2" for item in plan.musicDucking)


def test_fades_loudness_and_clean_musical_ending(evidence):
    plan = plan_for(evidence)
    assert plan.fades.musicFadeInSeconds > 0
    assert plan.fades.preserveFinalNaturalTailSeconds == 1
    assert plan.loudnessTargets.integratedLufs == -14
    assert plan.loudnessTargets.truePeakDbtp == -1
    assert plan.musicalEnding.resolveAtSeconds == plan.pictureDurationSeconds
    assert plan.musicalEnding.allowAbruptTruncation is False
    assert plan.musicalEnding.allowUnresolvedLoop is False


def test_sync_instructions_preserve_locked_picture_timing(evidence):
    treatment, candidate, segments = evidence
    original = copy.deepcopy(candidate.timeline)
    plan = build_music_plan("preprod-1", "picture-1", treatment, candidate, segments)
    assert len(plan.pictureMusicSync) == candidate.clipCount
    assert all(item.instruction in {"align_music_to_picture", "preserve_picture_timing"}
               for item in plan.pictureMusicSync)
    assert candidate.timeline == original


def test_plan_excludes_later_departments(evidence):
    plan = plan_for(evidence).model_dump()
    assert any("planning instructions only" in boundary for boundary in plan["boundaries"])
    assert not ({"captionPlan", "motionGraphicsPlan", "colorGrade",
                 "specializedCritics", "tournamentSelection"} & plan.keys())


def test_milestone_three_selects_deterministic_eligible_asset_with_provenance(
    evidence, tmp_path,
):
    paths = AudioLibraryPaths(tmp_path / "audio")
    store = ManifestStore(paths)
    source = tmp_path / "credited-music.wav"
    make_tone(source, duration=18.2)
    music = asset(
        assetType="music", category="energetic", filename=source.name,
        title="Credited pulse", mood=["intense"], energy=0.7, bpm=134,
        licenseName="CC BY 4.0",
        licenseUrl="https://creativecommons.org/licenses/by/4.0/",
        attributionText="Credited pulse by Creator, CC BY 4.0",
    )
    AudioIngestor(
        store=store, policy=LicensePolicy(), clock=lambda: FIXED_TIME,
    ).ingest(FileProvider({"one": source}), [music])
    treatment, candidate, segments = evidence
    adapter = AudioLibraryAdapter(store)
    first = build_music_plan(
        "preprod-1", "picture-1", treatment, candidate, segments,
        audio_library=adapter,
    )
    second = build_music_plan(
        "preprod-1", "picture-1", treatment, candidate, segments,
        audio_library=adapter,
    )
    assert first.libraryAssetSelection == second.libraryAssetSelection
    selection = first.libraryAssetSelection
    assert selection["licenseEligible"] is True
    assert selection["validationStatus"] == "accepted"
    assert selection["attributionStatus"] == "requires_attribution"
    assert selection["attribution"]["title"] == "Credited pulse"
    assert selection["attribution"]["creator"] == "Creator"
    assert selection["sourceAssetId"] == "one"


def test_invalid_or_missing_picture_evidence_is_rejected(evidence):
    treatment, candidate, segments = evidence
    invalid = candidate.model_copy(update={"valid": False})
    with pytest.raises(MusicSupervisorError, match="must be valid"):
        build_music_plan("preprod-1", "picture-1", treatment, invalid, segments)
    with pytest.raises(MusicSupervisorError, match="missing segments"):
        build_music_plan("preprod-1", "picture-1", treatment, candidate, segments[:-1])
