"""Milestone 5 visual-finishing contracts and real FFmpeg fixture render."""
import os
import subprocess

import pytest
from pydantic import ValidationError

from app.pipeline.audio_rendering import (
    ActualBeat,
    ActualEnergyPoint,
    ActualWaveformAnalysis,
    AudioQC,
    CompletedAudioMix,
    TrackMatch,
)
from app.pipeline.composition import (
    CompositionMetrics,
    CropRecommendation,
    NormalizedBox,
)
from app.pipeline.creative_director import CreativeTreatment, EnergyPoint
from app.pipeline.music_supervisor import NaturalAudioEvent
from app.pipeline.picture_editor import PictureCandidateSummary
from app.pipeline.schemas import Segment, TranscriptArtifact, Word
from app.pipeline.visual_finishing import (
    BrandTemplate,
    VisualFinishingError,
    build_caption_package,
    build_color_package,
    build_graphics_package,
    contrast_ratio,
    render_finishing_preview,
)
from app.renderer import FFMPEG
from tests.fake_supa import FakeSupabase


def _evidence():
    clips = [
        {"id": "clip-1", "assetId": "asset-a", "segmentId": "seg-1",
         "sourceStart": 0, "sourceEnd": 4, "timelineStart": 0, "timelineEnd": 4,
         "volume": 1, "speed": 1},
        {"id": "clip-2", "assetId": "asset-a", "segmentId": "seg-2",
         "sourceStart": 4, "sourceEnd": 8, "timelineStart": 4, "timelineEnd": 8,
         "volume": 1, "speed": 1},
    ]
    candidate = PictureCandidateSummary(
        candidateId="treatment_arc", label="Treatment arc", storyVariantId="arc",
        valid=True, durationSeconds=8, targetDurationSeconds=15, coverageRatio=1,
        editorialScore=.9, structuralSignature="seg-1>seg-2", clipCount=2,
        timeline={"version": 1, "width": 1080, "height": 1920, "fps": 30,
                  "duration": 8, "tracks": [{"id": "video", "type": "video", "clips": clips}]},
    )
    treatment = CreativeTreatment(
        purpose="social recap", audience="social", targetDurationSeconds=15,
        tone=["clean"], selectedStoryVariant="arc", storyArc=["hook", "payoff"],
        visualEnergyCurve=[EnergyPoint(position=0, energy=.8, intent="hook"),
                           EnergyPoint(position=1, energy=.4, intent="resolve")],
        musicEnergyCurve=[EnergyPoint(position=0, energy=.8, intent="hook"),
                          EnergyPoint(position=1, energy=.4, intent="resolve")],
        motionGraphicsDensity="medium", transitionStyle="hard cuts",
        colorDirection="natural", endingIntent="clean", confidence=.9,
    )
    beats = [ActualBeat(timeSeconds=float(i), strength=.8, beatIndex=i,
                        beatInBar=i % 4 + 1, barIndex=i // 4, isDownbeat=i % 4 == 0)
             for i in range(12)]
    analysis = ActualWaveformAnalysis(
        bpm=120, bpmConfidence=.9, beatLocations=beats,
        downbeatLocations=[0, 4, 8], barLocations=[0, 4, 8],
        phraseBoundaries=[0, 4, 8, 12],
        energyEnvelope=[ActualEnergyPoint(timeSeconds=0, energy=.5),
                        ActualEnergyPoint(timeSeconds=12, energy=.7)],
    )
    match = TrackMatch(
        targetAnalysisSource="treatment_derived_music_brief", targetBpm=120,
        actualBpm=120, bpmDifference=0, musicSourceStartSeconds=0,
        musicSourceEndSeconds=8, endingPhraseBoundarySeconds=8,
        phraseResolvedEnding=True, syncInstructions=[], energyComparison=[],
    )
    qc = AudioQC(integratedLufs=-14, truePeakDbtp=-1, clippingDetected=False,
                 silenceRanges=[], abruptGainChanges=[], missingAudioStream=False,
                 missingVideoStream=False, durationSeconds=8, passed=True)
    completed = CompletedAudioMix(
        analysis=analysis, targetVsActual=match, mergedDuckingEnvelopes=[],
        sourceAudioInstructions=[], loudnessMeasurementPass={}, qc=qc,
        pictureTimingChanged=False, excludedDepartments=[],
    )
    segments = [
        Segment(segmentId="seg-1", assetId="asset-a", sourceStart=0, sourceEnd=4,
                transcript="Push through every rep", action="coach voiceover over sled push",
                storyUses=["hook"], exposureScore=.25, audioScore=.9,
                semanticRelevance=.9),
        Segment(segmentId="seg-2", assetId="asset-a", sourceStart=4, sourceEnd=8,
                transcript="Finish strong today", action="athlete speaking to camera after hold 4 seconds",
                storyUses=["completion"], exposureScore=.95, audioScore=.9,
                semanticRelevance=.9),
    ]
    measured = CompositionMetrics(
        subjectProminence=.4, actionVisibility=.9, compositionQuality=.8,
        cropPotential=.7, faceVisibility=.6, emptySpaceRatio=.3, headroomScore=.8,
        occlusion=0, digitalZoomSafety=1, measurementSource="detected_bbox",
        confidence=.9, humanReviewRecommended=False,
        safeCrop=CropRecommendation(feasible=True,
            cropBox=NormalizedBox(x=.25, y=.12, width=.5, height=.72),
            sourcePixelsPerOutputPixel=1.2, resolutionLoss=.4,
            minimumOutputQualityMet=True),
    )
    return treatment, candidate, completed, segments, {"seg-1": measured, "seg-2": measured}


def _audio_events(segments, classifications=("background_chatter", "background_chatter")):
    return [NaturalAudioEvent(
        clipId=f"clip-{index + 1}", segmentId=segment.segmentId,
        timelineStart=index * 4, timelineEnd=(index + 1) * 4,
        classification=classification, audioScore=segment.audioScore,
        chatterDetected=classification == "background_chatter",
        reason=f"fixture {classification}",
    ) for index, (segment, classification) in enumerate(
        zip(segments, classifications, strict=True)
    )]


def test_brand_palette_typography_and_contrast_are_validated():
    template = BrandTemplate(templateId="brand-v1", name="Brand")
    assert template.fontFamily == "DejaVu Sans"
    assert contrast_ratio(template.primary, template.secondary) >= 4.5
    with pytest.raises((ValidationError, VisualFinishingError)):
        BrandTemplate(templateId="bad", name="Bad", primary="#777777", secondary="#777777")
    with pytest.raises((ValidationError, VisualFinishingError)):
        BrandTemplate(templateId="bad", name="Bad", accent="not-a-color")


@pytest.mark.parametrize("aspect,dimensions", [
    ("9:16", (1080, 1920)), ("1:1", (1080, 1080)), ("16:9", (1920, 1080)),
])
def test_graphics_respect_platform_safe_title_music_phrases_and_subjects(aspect, dimensions):
    treatment, candidate, completed, segments, composition = _evidence()
    package = build_graphics_package(
        treatment, candidate, completed, composition, segments=segments, aspect=aspect,
    )
    assert (package.platform.width, package.platform.height) == dimensions
    assert package.phraseBoundaries == [0, 4, 8]
    assert any(event.phraseAligned for event in package.events)
    assert set(package.templateCatalog) == {
        "animated_title", "lower_third", "callout", "exercise_label",
        "section_header", "progress_bar", "rep_counter", "timer", "intro", "outro",
    }
    assert all(event.subjectOcclusionRisk <= .25 for event in package.events)
    safe = package.platform.safeTitle
    for event in package.events:
        assert event.region.x >= safe.x
        assert event.region.x + event.region.width <= safe.x + safe.width + .00001
    assert package.pictureTimingChanged is False and package.audioChanged is False


def test_evidence_driven_templates_emit_intro_title_timer_and_outro_without_clutter():
    treatment, candidate, completed, segments, _ = _evidence()
    package = build_graphics_package(treatment, candidate, completed, {}, segments)
    kinds = [event.kind for event in package.events]
    assert {"intro", "animated_title", "timer", "outro", "progress_bar"}.issubset(kinds)
    assert all(sum(event.startSeconds < other.endSeconds and event.endSeconds > other.startSeconds
                   for other in package.events if other.kind != "progress_bar") <= 2
               for event in package.events if event.kind != "progress_bar")


def test_captions_use_exact_word_timing_group_highlight_and_resolve_collisions():
    treatment, candidate, completed, segments, composition = _evidence()
    graphics = build_graphics_package(treatment, candidate, completed, composition, segments)
    artifact = TranscriptArtifact(provider="fixture", words=[
        Word(word="Push", start=0, end=.4), Word(word="through", start=.4, end=.8),
        Word(word="every", start=.8, end=1.2), Word(word="rep", start=1.2, end=1.6),
        Word(word="Finish", start=4, end=4.4), Word(word="strong", start=4.4, end=4.8),
        Word(word="today", start=4.8, end=5.2),
    ])
    captions = build_caption_package(
        candidate, segments, graphics, {"asset-a": artifact}, _audio_events(segments),
    )
    assert captions.timingProvenance == ["transcript_word_timestamps"]
    assert captions.overlapsDetected == 0
    assert all(len(group.words) <= 5 and len(group.text) <= 42 for group in captions.groups)
    assert all(group.highlightWordIndexes == list(range(len(group.words)))
               for group in captions.groups)
    assert all(item.timingSource == "transcript_word_timestamps"
               for item in captions.evidenceDecisions if item.decision == "included")
    assert {item.reasonCode for item in captions.evidenceDecisions} == {
        "meaningful_narration_supported", "meaningful_dialogue_supported",
    }
    assert all(right.startSeconds >= left.endSeconds
               for left, right in zip(captions.groups, captions.groups[1:], strict=False))
    assert not any(event.captionCollision for event in graphics.events)


def test_caption_fallback_is_explicit_and_empty_speech_is_supported():
    treatment, candidate, completed, segments, composition = _evidence()
    graphics = build_graphics_package(treatment, candidate, completed, composition, segments)
    captions = build_caption_package(
        candidate, segments, graphics, natural_audio_events=_audio_events(segments),
    )
    assert captions.timingProvenance == ["segment_distributed"]
    assert all(item.timingSource == "segment_distributed"
               for item in captions.evidenceDecisions if item.decision == "included")
    empty = [item.model_copy(update={"transcript": None}) for item in segments]
    assert build_caption_package(candidate, empty, graphics).groups == []


def test_incidental_chatter_is_excluded_but_meaningful_dialogue_is_captioned():
    treatment, candidate, completed, segments, composition = _evidence()
    segments[0] = segments[0].model_copy(update={
        "action": "people talking in background", "storyUses": ["broll"],
        "semanticRelevance": .2,
    })
    segments[1] = segments[1].model_copy(update={
        "action": "athlete speaking to camera", "storyUses": ["reflection"],
        "semanticRelevance": .95, "audioScore": .9,
    })
    graphics = build_graphics_package(treatment, candidate, completed, composition, segments)
    captions = build_caption_package(
        candidate, segments, graphics,
        natural_audio_events=_audio_events(segments),
    )
    decisions = {item.segmentId: item for item in captions.evidenceDecisions}
    assert decisions["seg-1"].decision == "excluded"
    assert decisions["seg-1"].reasonCode == "milestone3_background_chatter"
    assert decisions["seg-2"].decision == "included"
    assert decisions["seg-2"].reasonCode == "meaningful_dialogue_supported"
    assert any("FINISH" in group.text.upper() for group in captions.groups)
    assert not any("PUSH" in group.text.upper() for group in captions.groups)


def test_unusable_audio_and_non_speech_effort_are_not_captioned():
    treatment, candidate, completed, segments, composition = _evidence()
    segments[0] = segments[0].model_copy(update={"audioScore": .2})
    segments[1] = segments[1].model_copy(update={
        "action": "breathing recovery effort", "storyUses": ["reflection"],
    })
    graphics = build_graphics_package(treatment, candidate, completed, composition, segments)
    events = _audio_events(segments, ("unusable", "effort"))
    captions = build_caption_package(
        candidate, segments, graphics, natural_audio_events=events,
    )
    decisions = {item.segmentId: item for item in captions.evidenceDecisions}
    assert decisions["seg-1"].reasonCode == "unusable_audio"
    assert decisions["seg-2"].reasonCode == "non_speech_natural_audio"
    assert captions.groups == []


def test_color_normalization_is_bounded_protective_and_non_destructive():
    _, candidate, _, segments, _ = _evidence()
    package = build_color_package(candidate, segments, lut="clean_warm")
    assert package.nonDestructive and package.pictureTimingChanged is False
    assert package.normalizationTarget["highlightShadowProtection"]
    assert all(-1 <= item.exposureEv <= 1 and .9 <= item.contrast <= 1.15
               and .85 <= item.saturation <= 1.15 and item.nonDestructive
               for item in package.instructions)
    assert package.instructions[1].highlightCompression > 0
    with pytest.raises(ValidationError):
        build_color_package(candidate, segments, lut="../../unsafe.cube")


def test_visual_run_version_collisions_are_rejected_by_fake_database():
    fake = FakeSupabase()
    for table in ("graphics_runs", "caption_runs", "color_runs"):
        row = {"project_id": "project", "version": 1}
        assert fake.insert(table, row).status_code == 201
        assert fake.insert(table, row).status_code == 409
    fake.conflict_once_tables.add("graphics_runs")
    assert fake.insert("graphics_runs", {"project_id": "other", "version": 1}).status_code == 409


def test_real_fixture_finishing_render_has_locked_audio_and_expected_platform(tmp_path):
    source = str(tmp_path / "source.mp4")
    output = str(tmp_path / "finished.mp4")
    subprocess.run([
        FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i",
        "testsrc=size=360x640:rate=24:duration=8", "-f", "lavfi", "-i",
        "sine=frequency=220:sample_rate=48000:duration=8", "-c:v", "libx264",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", source,
    ], check=True, timeout=120)
    treatment, candidate, completed, segments, composition = _evidence()
    graphics = build_graphics_package(treatment, candidate, completed, composition, segments)
    captions = build_caption_package(candidate, segments, graphics)
    color = build_color_package(candidate, segments, lut="neutral_social")
    qc = render_finishing_preview(source, output, graphics, captions, color)
    assert os.path.getsize(output) > 0
    assert qc["videoStreamPresent"] and qc["audioStreamPresent"]
    assert (qc["width"], qc["height"]) == (1080, 1920)
    assert qc["durationSeconds"] == pytest.approx(8, abs=.1)
    assert qc["pictureTimingChanged"] is False and qc["audioChanged"] is False
