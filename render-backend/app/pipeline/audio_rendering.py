"""Milestone 4 real licensed-track analysis, mixing, rendering, and QC."""
from __future__ import annotations

import copy
import json
import math
import os
import re
import subprocess
from typing import Literal

import numpy as np
from pydantic import BaseModel, Field, model_validator

from ..renderer import FFMPEG, FFPROBE
from ..renderer2 import render_timeline
from .music_supervisor import MusicPlan
from .picture_editor import PictureCandidateSummary

ANALYSIS_RATE = 8000
ALLOWED_EXTENSIONS = {".wav", ".mp3", ".m4a", ".aac", ".flac"}
ALLOWED_CONTENT_TYPES = {
    "audio/wav", "audio/x-wav", "audio/mpeg", "audio/mp4", "audio/aac", "audio/flac",
}


class AudioRenderingError(ValueError):
    pass


class LicenseMetadata(BaseModel):
    provider: str = Field(min_length=2, max_length=120)
    licenseType: str = Field(min_length=2, max_length=120)
    licenseReference: str = Field(min_length=3, max_length=500)
    confirmedByOperator: Literal[True]


class TrackMediaInfo(BaseModel):
    codec: str
    durationSeconds: float = Field(gt=0, le=600)
    channels: int = Field(ge=1, le=2)
    sampleRateHz: int = Field(ge=32000, le=96000)
    bitRate: int | None = Field(default=None, ge=0)
    contentType: str
    filename: str


class ActualBeat(BaseModel):
    timeSeconds: float = Field(ge=0)
    strength: float = Field(ge=0, le=1)
    beatIndex: int = Field(ge=0)
    beatInBar: int = Field(ge=1, le=4)
    barIndex: int = Field(ge=0)
    isDownbeat: bool


class ActualEnergyPoint(BaseModel):
    timeSeconds: float = Field(ge=0)
    energy: float = Field(ge=0, le=1)


class ActualWaveformAnalysis(BaseModel):
    analysisSource: Literal["actual_waveform"] = "actual_waveform"
    method: Literal["pcm_onset_energy_v1"] = "pcm_onset_energy_v1"
    bpm: float = Field(ge=60, le=200)
    bpmConfidence: float = Field(ge=0, le=1)
    beatLocations: list[ActualBeat] = Field(min_length=4)
    downbeatLocations: list[float] = Field(min_length=1)
    barLocations: list[float] = Field(min_length=1)
    phraseBoundaries: list[float] = Field(min_length=1)
    phraseInferenceMethod: Literal["four_bar_energy_phase_inference"] = (
        "four_bar_energy_phase_inference"
    )
    energyEnvelope: list[ActualEnergyPoint] = Field(min_length=2)


class DuckingEnvelope(BaseModel):
    startSeconds: float = Field(ge=0)
    endSeconds: float = Field(gt=0)
    gainDb: float = Field(ge=-18, le=0)
    attackSeconds: float = Field(default=0.08, ge=0.01, le=0.5)
    releaseSeconds: float = Field(default=0.22, ge=0.01, le=1)
    triggers: list[str]


class ActualSyncInstruction(BaseModel):
    clipId: str
    pictureCutSeconds: float = Field(ge=0)
    trackBeatSeconds: float = Field(ge=0)
    mixedTimelineBeatSeconds: float = Field(ge=0)
    offsetSeconds: float
    instruction: Literal["align_track_edit_to_locked_picture", "preserve_picture_timing"]


class TrackMatch(BaseModel):
    targetAnalysisSource: Literal["treatment_derived_music_brief"]
    actualAnalysisSource: Literal["actual_waveform"] = "actual_waveform"
    targetBpm: float
    actualBpm: float
    bpmDifference: float
    musicSourceStartSeconds: float = Field(ge=0)
    musicSourceEndSeconds: float = Field(gt=0)
    endingPhraseBoundarySeconds: float = Field(ge=0)
    phraseResolvedEnding: bool
    syncInstructions: list[ActualSyncInstruction]
    energyComparison: list[dict]


class AudioQC(BaseModel):
    integratedLufs: float | None
    truePeakDbtp: float | None
    clippingDetected: bool
    silenceRanges: list[dict]
    abruptGainChanges: list[dict]
    missingAudioStream: bool
    missingVideoStream: bool
    durationSeconds: float | None
    passed: bool


class CompletedAudioMix(BaseModel):
    schemaVersion: int = 1
    analysis: ActualWaveformAnalysis
    targetVsActual: TrackMatch
    mergedDuckingEnvelopes: list[DuckingEnvelope]
    sourceAudioInstructions: list[dict]
    loudnessMeasurementPass: dict
    qc: AudioQC
    previewStoragePath: str | None = None
    pictureTimingChanged: Literal[False] = False
    excludedDepartments: list[str]

    @model_validator(mode="after")
    def completed_mix_is_audio_only(self):
        if self.pictureTimingChanged:
            raise ValueError("Milestone 4 may not change selected picture timing")
        return self


def probe_music_file(
    path: str, *, filename: str, content_type: str, picture_duration: float,
) -> TrackMediaInfo:
    extension = os.path.splitext(filename.lower())[1]
    if extension not in ALLOWED_EXTENSIONS or content_type not in ALLOWED_CONTENT_TYPES:
        raise AudioRenderingError("licensed music must be WAV, MP3, M4A, AAC, or FLAC")
    try:
        result = subprocess.run(
            [FFPROBE, "-v", "error", "-print_format", "json", "-show_format",
             "-show_streams", path], capture_output=True, check=True, timeout=60,
        )
        data = json.loads(result.stdout)
    except (subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        raise AudioRenderingError("licensed music is malformed or unreadable") from exc
    audio = next((stream for stream in data.get("streams", [])
                  if stream.get("codec_type") == "audio"), None)
    if not audio:
        raise AudioRenderingError("licensed music has no audio stream")
    duration = float(data.get("format", {}).get("duration") or audio.get("duration") or 0)
    channels = int(audio.get("channels") or 0)
    sample_rate = int(audio.get("sample_rate") or 0)
    if duration < picture_duration:
        raise AudioRenderingError(
            f"licensed music duration {duration:.2f}s is shorter than picture {picture_duration:.2f}s"
        )
    if duration > 600:
        raise AudioRenderingError("licensed music exceeds the 10-minute validation limit")
    if channels not in {1, 2}:
        raise AudioRenderingError("licensed music must be mono or stereo")
    if not 32000 <= sample_rate <= 96000:
        raise AudioRenderingError("licensed music sample rate must be 32-96 kHz")
    return TrackMediaInfo(
        codec=audio.get("codec_name") or "unknown", durationSeconds=round(duration, 3),
        channels=channels, sampleRateHz=sample_rate,
        bitRate=int(audio.get("bit_rate") or 0) or None,
        contentType=content_type, filename=filename,
    )


def _decode_pcm(path: str, sample_rate: int = ANALYSIS_RATE) -> np.ndarray:
    try:
        result = subprocess.run(
            [FFMPEG, "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar",
             str(sample_rate), "-f", "f32le", "-"],
            capture_output=True, check=True, timeout=120,
        )
    except subprocess.CalledProcessError as exc:
        raise AudioRenderingError("could not decode licensed music waveform") from exc
    samples = np.frombuffer(result.stdout, dtype=np.float32)
    if samples.size < sample_rate:
        raise AudioRenderingError("licensed music waveform is too short to analyze")
    return samples


def analyze_actual_waveform(path: str) -> ActualWaveformAnalysis:
    samples = _decode_pcm(path)
    frame, hop = 320, 80
    count = 1 + (len(samples) - frame) // hop
    energy = np.empty(count, dtype=np.float64)
    for index in range(count):
        window = samples[index * hop:index * hop + frame]
        energy[index] = math.sqrt(float(np.mean(window * window)) + 1e-12)
    smooth = np.convolve(energy, np.ones(5) / 5, mode="same")
    novelty = np.maximum(0, np.diff(smooth, prepend=smooth[0]))
    threshold = max(float(np.percentile(novelty, 86)), float(np.mean(novelty) + np.std(novelty)))
    candidates = [index for index in range(1, len(novelty) - 1)
                  if novelty[index] >= threshold
                  and novelty[index] >= novelty[index - 1]
                  and novelty[index] > novelty[index + 1]]
    minimum = int(0.28 * ANALYSIS_RATE / hop)
    selected: list[int] = []
    for index in candidates:
        if not selected or index - selected[-1] >= minimum:
            selected.append(index)
        elif novelty[index] > novelty[selected[-1]]:
            selected[-1] = index
    if len(selected) < 4:
        raise AudioRenderingError("actual waveform has no reliable beat sequence")
    times = np.array(selected, dtype=float) * hop / ANALYSIS_RATE
    intervals = np.diff(times)
    median_interval = float(np.median(intervals))
    bpm = 60 / median_interval
    while bpm < 70:
        bpm *= 2
    while bpm > 180:
        bpm /= 2
    expected = 60 / bpm
    filtered = [0]
    for index in range(1, len(times)):
        if times[index] - times[filtered[-1]] >= expected * 0.6:
            filtered.append(index)
    times = times[filtered]
    strengths_raw = novelty[np.array(selected)[filtered]]
    strengths = strengths_raw / max(float(np.max(strengths_raw)), 1e-9)
    if len(times) < 4:
        raise AudioRenderingError("actual waveform beat sequence is unstable")
    phase_scores = [float(np.sum(strengths[phase::4])) for phase in range(4)]
    downbeat_phase = int(np.argmax(phase_scores))
    beat_rows = []
    downbeats = []
    for index, (time_value, strength) in enumerate(zip(times, strengths, strict=True)):
        relative = index - downbeat_phase
        is_downbeat = relative >= 0 and relative % 4 == 0
        if is_downbeat:
            downbeats.append(round(float(time_value), 3))
        beat_rows.append(ActualBeat(
            timeSeconds=round(float(time_value), 3), strength=round(float(strength), 4),
            beatIndex=index, beatInBar=(relative % 4) + 1 if relative >= 0 else index % 4 + 1,
            barIndex=max(0, relative // 4), isDownbeat=is_downbeat,
        ))
    if not downbeats:
        downbeats = [beat_rows[0].timeSeconds]
    phrases = [beat.timeSeconds for beat in beat_rows
               if beat.isDownbeat and (beat.beatIndex - downbeat_phase) % 16 == 0]
    if not phrases:
        phrases = [downbeats[0]]
    duration = len(samples) / ANALYSIS_RATE
    envelope = []
    block = ANALYSIS_RATE // 2
    block_energy = [math.sqrt(float(np.mean(samples[i:i + block] ** 2)) + 1e-12)
                    for i in range(0, len(samples), block) if len(samples[i:i + block])]
    ceiling = max(block_energy) or 1
    for index, value in enumerate(block_energy):
        envelope.append(ActualEnergyPoint(
            timeSeconds=round(min(duration, index * 0.5), 3),
            energy=round(float(value / ceiling), 4),
        ))
    consistency = 1 - min(1, float(np.std(intervals) / max(median_interval, 1e-6)))
    return ActualWaveformAnalysis(
        bpm=round(bpm, 3), bpmConfidence=round(max(0, consistency), 4),
        beatLocations=beat_rows, downbeatLocations=downbeats,
        barLocations=downbeats, phraseBoundaries=phrases,
        energyEnvelope=envelope,
    )


def merge_ducking_envelopes(raw: list[dict], duration: float) -> list[DuckingEnvelope]:
    intervals = []
    for item in raw:
        start = max(0, min(duration, float(item["startSeconds"])))
        end = max(start, min(duration, float(item["endSeconds"])))
        if end <= start:
            continue
        intervals.append((start, end, max(-18, min(0, float(item["musicGainDb"]))),
                          str(item.get("clipId", "unknown"))))
    points = sorted({point for start, end, _, _ in intervals for point in (start, end)})
    merged: list[DuckingEnvelope] = []
    for left, right in zip(points, points[1:], strict=False):
        active = [item for item in intervals if item[0] < right and item[1] > left]
        if not active:
            continue
        gain = min(item[2] for item in active)
        triggers = sorted({item[3] for item in active})
        if merged and merged[-1].endSeconds == round(left, 3) \
                and merged[-1].gainDb == gain and merged[-1].triggers == triggers:
            merged[-1].endSeconds = round(right, 3)
        else:
            merged.append(DuckingEnvelope(
                startSeconds=round(left, 3), endSeconds=round(right, 3),
                gainDb=gain, triggers=triggers,
            ))
    return merged


def _energy_at_actual(analysis: ActualWaveformAnalysis, time_seconds: float) -> float:
    return min(analysis.energyEnvelope,
               key=lambda point: abs(point.timeSeconds - time_seconds)).energy


def match_picture_to_actual_track(
    candidate: PictureCandidateSummary, plan: MusicPlan,
    analysis: ActualWaveformAnalysis, track_duration: float,
) -> TrackMatch:
    duration = candidate.durationSeconds
    eligible = [value for value in analysis.phraseBoundaries
                if duration <= value <= track_duration]
    if not eligible:
        raise AudioRenderingError(
            "actual track has no phrase boundary that can cleanly resolve the picture"
        )
    ending = eligible[0]
    source_start = max(0, ending - duration)
    source_end = source_start + duration
    beats = [beat.timeSeconds for beat in analysis.beatLocations
             if source_start <= beat.timeSeconds <= source_end]
    if not beats:
        raise AudioRenderingError("no actual track beats overlap the picture duration")
    clips = candidate.timeline["tracks"][0]["clips"]
    sync = []
    for clip in clips:
        cut = float(clip["timelineStart"])
        absolute_target = source_start + cut
        anchor = min(beats, key=lambda value: abs(value - absolute_target))
        mixed = anchor - source_start
        offset = mixed - cut
        sync.append(ActualSyncInstruction(
            clipId=clip["id"], pictureCutSeconds=round(cut, 3),
            trackBeatSeconds=round(anchor, 3), mixedTimelineBeatSeconds=round(mixed, 3),
            offsetSeconds=round(offset, 3),
            instruction="align_track_edit_to_locked_picture" if abs(offset) <= 0.2
            else "preserve_picture_timing",
        ))
    energy_comparison = []
    for target in plan.energyAlignment:
        actual_time = source_start + target.pictureTimeSeconds
        energy_comparison.append({
            "pictureTimeSeconds": target.pictureTimeSeconds,
            "targetEnergy": target.targetEnergy,
            "actualEnergy": _energy_at_actual(analysis, actual_time),
            "targetIntent": target.intent,
        })
    target_bpm = float(plan.beatPhraseAnalysis.tempoBpm)
    return TrackMatch(
        targetAnalysisSource=plan.beatPhraseAnalysis.analysisSource,
        targetBpm=target_bpm, actualBpm=analysis.bpm,
        bpmDifference=round(analysis.bpm - target_bpm, 3),
        musicSourceStartSeconds=round(source_start, 3),
        musicSourceEndSeconds=round(source_end, 3),
        endingPhraseBoundarySeconds=round(ending, 3),
        phraseResolvedEnding=True, syncInstructions=sync,
        energyComparison=energy_comparison,
    )


def _volume_expression(envelopes: list[DuckingEnvelope]) -> str:
    expression = "1"
    for item in reversed(envelopes):
        gain = 10 ** (item.gainDb / 20)
        start, end = item.startSeconds, item.endSeconds
        attack_start = max(0, start - item.attackSeconds)
        release_end = end + item.releaseSeconds
        expression = (
            f"if(between(t,{attack_start:.3f},{start:.3f}),"
            f"1-(1-{gain:.6f})*(t-{attack_start:.3f})/{max(item.attackSeconds, .01):.3f},"
            f"if(between(t,{start:.3f},{end:.3f}),{gain:.6f},"
            f"if(between(t,{end:.3f},{release_end:.3f}),"
            f"{gain:.6f}+(1-{gain:.6f})*(t-{end:.3f})/{item.releaseSeconds:.3f},"
            f"{expression})))"
        )
    return expression


def _loudnorm_measure(path: str, target_i: float = -14, target_tp: float = -1) -> dict:
    result = subprocess.run(
        [FFMPEG, "-hide_banner", "-nostats", "-i", path, "-af",
         f"loudnorm=I={target_i}:TP={target_tp}:LRA=11:print_format=json",
         "-f", "null", os.devnull], capture_output=True, timeout=120,
    )
    text = result.stderr.decode(errors="replace")
    matches = re.findall(r"\{\s*\"input_i\".*?\}", text, re.S)
    if result.returncode or not matches:
        raise AudioRenderingError("FFmpeg loudness measurement failed")
    measured = json.loads(matches[-1])
    if measured.get("input_i") in {"-inf", "inf"}:
        raise AudioRenderingError("mixed audio is silent and cannot be normalized")
    return measured


def _apply_source_gains(timeline: dict, instructions: list[dict]) -> dict:
    output = copy.deepcopy(timeline)
    by_clip = {item["clipId"]: item for item in instructions}
    for track in output["tracks"]:
        if track["type"] != "video":
            continue
        for clip in track["clips"]:
            item = by_clip.get(clip["id"])
            if item:
                clip["volume"] = round(10 ** (float(item["targetGainDb"]) / 20), 6)
    return output


def render_completed_mix(
    candidate: PictureCandidateSummary, sources: dict[str, str], music_path: str,
    plan: MusicPlan, match: TrackMatch, output_path: str, workdir: str,
    music_gain_db: float = -8.0,
) -> tuple[dict, list[DuckingEnvelope]]:
    duration = candidate.durationSeconds
    ducking = merge_ducking_envelopes(
        [item.model_dump() for item in plan.musicDucking], duration,
    )
    adjusted = _apply_source_gains(candidate.timeline, [
        item.model_dump() for item in plan.sourceAudioInstructions
    ])
    picture_source = os.path.join(workdir, "locked-picture-source-audio.mp4")
    render_timeline(adjusted, sources, picture_source, profile="preview")
    premix = os.path.join(workdir, "premix.wav")
    expression = _volume_expression(ducking)
    fade_out_start = max(0, duration - plan.fades.musicFadeOutSeconds)
    filter_graph = (
        f"[0:a]atrim=duration={duration:.3f},asetpts=PTS-STARTPTS,"
        "aresample=48000,aformat=channel_layouts=stereo,afade=t=in:st=0:d=0.05[src];"
        f"[1:a]atrim=start={match.musicSourceStartSeconds:.3f}:"
        f"end={match.musicSourceEndSeconds:.3f},asetpts=PTS-STARTPTS,"
        f"aresample=48000,aformat=channel_layouts=stereo,volume={music_gain_db:.3f}dB,"
        f"volume='{expression}':eval=frame,"
        f"afade=t=in:st=0:d={plan.fades.musicFadeInSeconds:.3f},"
        f"afade=t=out:st={fade_out_start:.3f}:d={plan.fades.musicFadeOutSeconds:.3f}[music];"
        "[src][music]amix=inputs=2:duration=first:normalize=0[premix]"
    )
    subprocess.run(
        [FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-i", picture_source,
         "-i", music_path, "-filter_complex", filter_graph, "-map", "[premix]",
         "-c:a", "pcm_s24le", premix], check=True, timeout=300,
    )
    measured = _loudnorm_measure(premix)
    loudnorm = (
        "loudnorm=I=-14:TP=-1:LRA=11:linear=true:"
        f"measured_I={measured['input_i']}:measured_TP={measured['input_tp']}:"
        f"measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}:"
        f"offset={measured['target_offset']},alimiter=limit=0.891251"
    )
    subprocess.run(
        [FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-i", picture_source,
         "-i", premix, "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
         "-af", loudnorm, "-c:a", "aac", "-b:a", "192k", "-shortest",
         "-movflags", "+faststart", output_path], check=True, timeout=300,
    )
    return measured, ducking


def analyze_audio_qc(path: str) -> AudioQC:
    probe_result = subprocess.run(
        [FFPROBE, "-v", "error", "-print_format", "json", "-show_format",
         "-show_streams", path], capture_output=True, timeout=60,
    )
    try:
        data = json.loads(probe_result.stdout) if probe_result.returncode == 0 else {}
    except json.JSONDecodeError:
        data = {}
    streams = data.get("streams", [])
    missing_audio = not any(item.get("codec_type") == "audio" for item in streams)
    missing_video = not any(item.get("codec_type") == "video" for item in streams)
    duration = float(data.get("format", {}).get("duration") or 0) or None
    measured = None
    if not missing_audio:
        try:
            measured = _loudnorm_measure(path)
        except AudioRenderingError:
            measured = None
    integrated = float(measured["input_i"]) if measured else None
    true_peak = float(measured["input_tp"]) if measured else None
    silence_result = subprocess.run(
        [FFMPEG, "-hide_banner", "-i", path, "-af", "silencedetect=n=-50dB:d=0.5",
         "-f", "null", os.devnull], capture_output=True, timeout=120,
    )
    silence_text = silence_result.stderr.decode(errors="replace")
    starts = [float(value) for value in re.findall(r"silence_start: ([0-9.]+)", silence_text)]
    ends = [float(value) for value in re.findall(r"silence_end: ([0-9.]+)", silence_text)]
    silence = [{"startSeconds": round(start, 3), "endSeconds": round(end, 3)}
               for start, end in zip(starts, ends, strict=False)]
    abrupt = []
    if not missing_audio:
        samples = _decode_pcm(path)
        block = ANALYSIS_RATE // 10
        rms = np.array([math.sqrt(float(np.mean(samples[i:i + block] ** 2)) + 1e-12)
                        for i in range(0, len(samples), block) if len(samples[i:i + block])])
        db = 20 * np.log10(np.maximum(rms, 1e-7))
        for index, change in enumerate(np.diff(db), start=1):
            if abs(change) > 12:
                abrupt.append({"timeSeconds": round(index * 0.1, 3),
                               "changeDb": round(float(change), 3)})
    clipping = true_peak is not None and true_peak > -0.1
    passed = (not missing_audio and not missing_video and not clipping
              and integrated is not None and -15.0 <= integrated <= -13.0
              and true_peak is not None and true_peak <= -0.8)
    return AudioQC(
        integratedLufs=integrated, truePeakDbtp=true_peak,
        clippingDetected=clipping, silenceRanges=silence,
        abruptGainChanges=abrupt, missingAudioStream=missing_audio,
        missingVideoStream=missing_video, durationSeconds=duration, passed=passed,
    )
