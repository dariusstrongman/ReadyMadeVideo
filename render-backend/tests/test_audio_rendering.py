"""Milestone 4 real waveform analysis and FFmpeg audio-mix tests."""
import copy
import os
import subprocess

import pytest
from pydantic import ValidationError

from app.pipeline.audio_rendering import (
    AudioRenderingError,
    LicenseMetadata,
    analyze_actual_waveform,
    analyze_audio_qc,
    match_picture_to_actual_track,
    merge_ducking_envelopes,
    probe_music_file,
    render_completed_mix,
)
from app.pipeline.creative_director import CreativeTreatment, EnergyPoint, NaturalAudioMoment
from app.pipeline.music_supervisor import build_music_plan
from app.pipeline.picture_editor import PictureCandidateSummary
from app.pipeline.schemas import Segment
from app.renderer import FFMPEG


@pytest.fixture(scope="module")
def real_media(tmp_path_factory):
    root = tmp_path_factory.mktemp("audio-m4")
    music = str(root / "licensed.wav")
    tone = str(root / "unusable.wav")
    malformed = str(root / "malformed.mp3")
    video = str(root / "source.mp4")
    click_expr = "if(lt(mod(t\\,0.5)\\,0.025)\\,0.9\\,0)"
    subprocess.run(
        [FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i",
         f"aevalsrc={click_expr}:s=48000:d=32", "-f", "lavfi", "-i",
         "sine=frequency=220:sample_rate=48000:duration=32", "-filter_complex",
         "[0:a][1:a]amix=inputs=2:weights='1 0.18',alimiter=limit=0.9[a]",
         "-map", "[a]", "-ac", "2", music], check=True, timeout=120,
    )
    subprocess.run(
        [FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i",
         "sine=frequency=330:sample_rate=48000:duration=20", "-ac", "2", tone],
        check=True, timeout=120,
    )
    subprocess.run(
        [FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i",
         "testsrc=size=360x640:rate=30:duration=16", "-f", "lavfi", "-i",
         "sine=frequency=110:sample_rate=48000:duration=16", "-c:v", "libx264",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", video],
        check=True, timeout=120,
    )
    with open(malformed, "wb") as handle:
        handle.write(b"not media" * 100)
    return {"root": str(root), "music": music, "tone": tone,
            "malformed": malformed, "video": video}


def _evidence(*, impact=True):
    segments = [
        Segment(
            segmentId="s1", assetId="asset-a", sourceStart=0, sourceEnd=8,
            subjects=["athlete"], action="tire impact" if impact else "warmup",
            shotType="medium", cameraAngle="eye", cameraMovement="static",
            location="gym", storyUses=["peak"] if impact else ["hook"],
            motionIntensity=0.8, focusScore=0.9, exposureScore=0.9,
            stabilityScore=0.9, audioScore=0.85, semanticRelevance=0.9,
        ),
        Segment(
            segmentId="s2", assetId="asset-a", sourceStart=8, sourceEnd=16,
            subjects=["athlete"], action="breathing recovery", shotType="medium",
            cameraAngle="eye", cameraMovement="static", location="gym",
            storyUses=["reflection"], motionIntensity=0.2, focusScore=0.9,
            exposureScore=0.9, stabilityScore=0.9, audioScore=0.85,
            semanticRelevance=0.9,
        ),
    ]
    treatment = CreativeTreatment(
        purpose="workout recap", audience="fitness", targetDurationSeconds=16,
        tone=["intense"], selectedStoryVariant="treatment_arc",
        storyArc=["peak", "resolve"],
        visualEnergyCurve=[EnergyPoint(position=0, energy=0.9, intent="peak"),
                           EnergyPoint(position=1, energy=0.2, intent="resolve")],
        musicEnergyCurve=[EnergyPoint(position=0, energy=0.8, intent="drive"),
                          EnergyPoint(position=0.5, energy=1, intent="peak"),
                          EnergyPoint(position=1, energy=0.2, intent="resolve")],
        naturalAudioMoments=[
            NaturalAudioMoment(segmentId="s1", intent="impact", priority="high"),
            NaturalAudioMoment(segmentId="s2", intent="breath", priority="high"),
        ], motionGraphicsDensity="low", transitionStyle="hard cuts",
        colorDirection="natural", endingIntent="recovery", confidence=0.9,
    )
    clips = [
        {"id": "c1", "assetId": "asset-a", "segmentId": "s1",
         "sourceStart": 0, "sourceEnd": 8, "timelineStart": 0,
         "timelineEnd": 8, "volume": 1, "speed": 1},
        {"id": "c2", "assetId": "asset-a", "segmentId": "s2",
         "sourceStart": 8, "sourceEnd": 16, "timelineStart": 8,
         "timelineEnd": 16, "volume": 1, "speed": 1},
    ]
    candidate = PictureCandidateSummary(
        candidateId="treatment_arc", label="Treatment Arc",
        storyVariantId="treatment_arc", valid=True, durationSeconds=16,
        targetDurationSeconds=16, coverageRatio=1, editorialScore=0.9,
        structuralSignature="s1>s2", clipCount=2,
        timeline={"version": 1, "width": 1080, "height": 1920, "fps": 30,
                  "duration": 16,
                  "tracks": [{"id": "video-1", "type": "video", "clips": clips}]},
    )
    plan = build_music_plan("preprod", "picture", treatment, candidate, segments)
    return treatment, candidate, segments, plan


def test_licensed_metadata_and_media_validation(real_media):
    metadata = LicenseMetadata(provider="Artlist", licenseType="commercial",
                               licenseReference="license-123", confirmedByOperator=True)
    assert metadata.confirmedByOperator
    info = probe_music_file(real_media["music"], filename="track.wav",
                            content_type="audio/wav", picture_duration=16)
    assert info.channels == 2 and info.sampleRateHz == 48000 and info.durationSeconds >= 32
    with pytest.raises(ValidationError):
        LicenseMetadata(provider="x", licenseType="", licenseReference="",
                        confirmedByOperator=False)


def test_malformed_and_unusable_audio_are_rejected(real_media):
    with pytest.raises(AudioRenderingError, match="malformed"):
        probe_music_file(real_media["malformed"], filename="bad.mp3",
                         content_type="audio/mpeg", picture_duration=16)
    with pytest.raises(AudioRenderingError, match="no reliable beat"):
        analyze_actual_waveform(real_media["tone"])
    with pytest.raises(AudioRenderingError, match="must be WAV"):
        probe_music_file(real_media["music"], filename="track.exe",
                         content_type="application/octet-stream", picture_duration=16)


def test_actual_waveform_detects_beats_downbeats_bars_phrases_and_energy(real_media):
    analysis = analyze_actual_waveform(real_media["music"])
    assert analysis.analysisSource == "actual_waveform"
    assert analysis.bpm == pytest.approx(120, abs=3)
    assert len(analysis.beatLocations) >= 40
    assert analysis.downbeatLocations == analysis.barLocations
    assert len(analysis.phraseBoundaries) >= 3
    assert max(point.energy for point in analysis.energyEnvelope) == 1


def test_overlapping_ducking_is_merged_and_bounded():
    merged = merge_ducking_envelopes([
        {"startSeconds": 1, "endSeconds": 4, "musicGainDb": -8, "clipId": "a"},
        {"startSeconds": 2, "endSeconds": 5, "musicGainDb": -24, "clipId": "b"},
    ], 10)
    assert [(item.startSeconds, item.endSeconds, item.gainDb) for item in merged] == [
        (1, 2, -8), (2, 4, -18), (4, 5, -18),
    ]
    assert all(-18 <= item.gainDb <= 0 for item in merged)


def test_actual_track_match_preserves_picture_and_resolves_on_phrase(real_media):
    _, candidate, _, plan = _evidence()
    original = copy.deepcopy(candidate.timeline)
    analysis = analyze_actual_waveform(real_media["music"])
    match = match_picture_to_actual_track(candidate, plan, analysis, 32)
    assert match.actualAnalysisSource == "actual_waveform"
    assert match.targetAnalysisSource == "treatment_derived_music_brief"
    assert match.phraseResolvedEnding
    assert match.musicSourceEndSeconds - match.musicSourceStartSeconds == 16
    assert candidate.timeline == original


def test_track_without_usable_phrase_resolution_is_rejected(real_media):
    _, candidate, _, plan = _evidence()
    analysis = analyze_actual_waveform(real_media["music"])
    shortened = analysis.model_copy(update={"phraseBoundaries": [0, 8]})
    with pytest.raises(AudioRenderingError, match="no phrase boundary"):
        match_picture_to_actual_track(candidate, plan, shortened, 20)


def test_no_impact_footage_produces_no_impact_emphasis():
    _, _, _, plan = _evidence(impact=False)
    assert plan.impactEmphasis == []


def test_real_mix_is_normalized_clip_safe_phrase_resolved_and_has_streams(real_media, tmp_path):
    _, candidate, _, plan = _evidence()
    analysis = analyze_actual_waveform(real_media["music"])
    match = match_picture_to_actual_track(candidate, plan, analysis, 32)
    output = str(tmp_path / "mixed-preview.mp4")
    measured, ducking = render_completed_mix(
        candidate, {"asset-a": real_media["video"]}, real_media["music"],
        plan, match, output, str(tmp_path),
    )
    qc = analyze_audio_qc(output)
    assert os.path.getsize(output) > 0
    assert measured["input_i"] != "-inf"
    assert ducking and all(item.gainDb >= -18 for item in ducking)
    assert match.phraseResolvedEnding
    assert not qc.missingAudioStream and not qc.missingVideoStream
    assert qc.integratedLufs == pytest.approx(-14, abs=1)
    assert qc.truePeakDbtp <= -0.8
    assert not qc.clippingDetected
    assert qc.passed
