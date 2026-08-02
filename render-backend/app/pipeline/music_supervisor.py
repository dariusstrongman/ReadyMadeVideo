"""Milestone 3 deterministic music and sound supervisor.

Consumes a Creative Treatment and the selected Milestone 2 picture candidate.
It emits inspectable mix/synchronization instructions only: it does not choose a
licensed track, render audio, or add any later-department output.
"""
from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .creative_director import CreativeTreatment, EnergyPoint
from .picture_editor import PictureCandidateSummary
from .schemas import Segment


class MusicSupervisorError(ValueError):
    pass


class BeatMarker(BaseModel):
    timeSeconds: float = Field(ge=0)
    beatIndex: int = Field(ge=0)
    barIndex: int = Field(ge=0)
    beatInBar: int = Field(ge=1, le=4)
    kind: Literal["downbeat", "beat"]


class PhraseWindow(BaseModel):
    phraseIndex: int = Field(ge=0)
    startSeconds: float = Field(ge=0)
    endSeconds: float = Field(gt=0)
    bars: int = Field(ge=1, le=4)
    targetEnergy: float = Field(ge=0, le=1)
    intent: str
    isEndingPhrase: bool = False


class BeatPhraseAnalysis(BaseModel):
    analysisSource: Literal["treatment_derived_music_brief"] = (
        "treatment_derived_music_brief"
    )
    tempoBpm: int = Field(ge=80, le=160)
    timeSignature: Literal["4/4"] = "4/4"
    beatIntervalSeconds: float = Field(gt=0)
    beatsPerBar: Literal[4] = 4
    phraseBars: Literal[4] = 4
    markers: list[BeatMarker] = Field(min_length=1)
    phrases: list[PhraseWindow] = Field(min_length=1)


class MusicEnergyAlignment(BaseModel):
    position: float = Field(ge=0, le=1)
    pictureTimeSeconds: float = Field(ge=0)
    musicTimeSeconds: float = Field(ge=0)
    targetEnergy: float = Field(ge=0, le=1)
    intent: str
    alignment: Literal["downbeat", "beat", "phrase_boundary"]


class NaturalAudioEvent(BaseModel):
    clipId: str
    segmentId: str
    timelineStart: float = Field(ge=0)
    timelineEnd: float = Field(gt=0)
    classification: Literal[
        "clean_natural", "effort", "impact", "background_chatter", "unusable",
    ]
    audioScore: float = Field(ge=0, le=1)
    chatterDetected: bool
    treatmentPriority: Literal["none", "low", "medium", "high"] = "none"
    reason: str


class SourceAudioInstruction(BaseModel):
    clipId: str
    startSeconds: float = Field(ge=0)
    endSeconds: float = Field(gt=0)
    action: Literal[
        "preserve", "emphasize_impact", "reduce_chatter", "mute_unusable",
    ]
    targetGainDb: float = Field(ge=-60, le=6)
    fadeInSeconds: float = Field(ge=0, le=2)
    fadeOutSeconds: float = Field(ge=0, le=2)
    reason: str


class MusicDuckingInstruction(BaseModel):
    startSeconds: float = Field(ge=0)
    endSeconds: float = Field(gt=0)
    musicGainDb: float = Field(ge=-24, le=0)
    attackSeconds: float = Field(ge=0.01, le=1)
    releaseSeconds: float = Field(ge=0.01, le=2)
    trigger: Literal["natural_audio", "effort", "impact"]
    clipId: str


class AudioFadePlan(BaseModel):
    musicFadeInSeconds: float = Field(ge=0, le=4)
    musicFadeOutSeconds: float = Field(ge=0, le=4)
    sourceCrossfadeSeconds: float = Field(ge=0, le=1)
    preserveFinalNaturalTailSeconds: float = Field(ge=0, le=4)


class LoudnessTargets(BaseModel):
    integratedLufs: float = Field(ge=-24, le=-10)
    truePeakDbtp: float = Field(ge=-3, le=-0.1)
    loudnessRangeLufs: tuple[float, float]
    measurementStandard: Literal["ITU-R BS.1770-4"] = "ITU-R BS.1770-4"
    normalizationPass: Literal["two_pass"] = "two_pass"


class MusicalEndingPlan(BaseModel):
    endingPhraseStartSeconds: float = Field(ge=0)
    resolveAtSeconds: float = Field(gt=0)
    finalDownbeatSeconds: float = Field(ge=0)
    strategy: Literal["phrase_resolve_with_natural_tail"] = (
        "phrase_resolve_with_natural_tail"
    )
    allowAbruptTruncation: Literal[False] = False
    allowUnresolvedLoop: Literal[False] = False
    instruction: str


class PictureMusicSyncInstruction(BaseModel):
    clipId: str
    pictureCutSeconds: float = Field(ge=0)
    musicAnchorSeconds: float = Field(ge=0)
    offsetSeconds: float
    anchorKind: Literal["downbeat", "beat", "phrase_boundary"]
    instruction: Literal[
        "align_music_to_picture", "accent_picture_cut", "preserve_picture_timing",
    ]
    reason: str


class MusicPlan(BaseModel):
    schemaVersion: int = 1
    preproductionRunId: str
    pictureEditRunId: str
    selectedCandidateId: str
    pictureDurationSeconds: float = Field(gt=0)
    trackBrief: dict
    beatPhraseAnalysis: BeatPhraseAnalysis
    energyAlignment: list[MusicEnergyAlignment]
    naturalAudioEvents: list[NaturalAudioEvent]
    sourceAudioInstructions: list[SourceAudioInstruction]
    musicDucking: list[MusicDuckingInstruction]
    impactEmphasis: list[SourceAudioInstruction]
    fades: AudioFadePlan
    loudnessTargets: LoudnessTargets
    musicalEnding: MusicalEndingPlan
    pictureMusicSync: list[PictureMusicSyncInstruction]
    boundaries: list[str]

    @model_validator(mode="after")
    def plan_stays_inside_milestone_three(self):
        if self.musicalEnding.resolveAtSeconds != self.pictureDurationSeconds:
            raise ValueError("music must resolve at the selected picture duration")
        forbidden = {"captions", "motion_graphics", "color", "critic", "tournament"}
        if forbidden.intersection(self.trackBrief):
            raise ValueError("Music Plan contains a later-department field")
        return self


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _energy_at(curve: list[EnergyPoint], position: float) -> tuple[float, str]:
    before = curve[0]
    after = curve[-1]
    for point in curve:
        if point.position <= position:
            before = point
        if point.position >= position:
            after = point
            break
    if after.position == before.position:
        return before.energy, before.intent
    ratio = (position - before.position) / (after.position - before.position)
    energy = before.energy + (after.energy - before.energy) * ratio
    return round(_clamp(energy, 0, 1), 3), after.intent


def _tempo_for(treatment: CreativeTreatment) -> int:
    average = sum(point.energy for point in treatment.musicEnergyCurve) / len(
        treatment.musicEnergyCurve
    )
    tone = " ".join(treatment.tone).lower()
    adjustment = 6 if any(word in tone for word in ("intense", "urgent", "kinetic")) else 0
    if any(word in tone for word in ("calm", "reflective", "restrained")):
        adjustment -= 8
    raw = _clamp(88 + average * 58 + adjustment, 80, 160)
    return int(round(raw / 2) * 2)


def _analyze_beats_and_phrases(
    treatment: CreativeTreatment, duration: float,
) -> BeatPhraseAnalysis:
    bpm = _tempo_for(treatment)
    interval = 60 / bpm
    marker_count = math.floor(duration / interval) + 1
    markers = [BeatMarker(
        timeSeconds=round(min(duration, index * interval), 3),
        beatIndex=index,
        barIndex=index // 4,
        beatInBar=index % 4 + 1,
        kind="downbeat" if index % 4 == 0 else "beat",
    ) for index in range(marker_count)]
    if markers[-1].timeSeconds < duration:
        markers.append(BeatMarker(
            timeSeconds=round(duration, 3), beatIndex=marker_count,
            barIndex=marker_count // 4, beatInBar=marker_count % 4 + 1,
            kind="downbeat" if marker_count % 4 == 0 else "beat",
        ))

    phrase_seconds = interval * 16
    phrase_count = max(1, math.ceil(duration / phrase_seconds))
    phrases = []
    for index in range(phrase_count):
        start = index * phrase_seconds
        end = min(duration, (index + 1) * phrase_seconds)
        midpoint = ((start + end) / 2) / duration
        energy, intent = _energy_at(treatment.musicEnergyCurve, midpoint)
        bars = max(1, min(4, math.ceil((end - start) / (interval * 4))))
        phrases.append(PhraseWindow(
            phraseIndex=index, startSeconds=round(start, 3),
            endSeconds=round(end, 3), bars=bars, targetEnergy=energy,
            intent=intent, isEndingPhrase=index == phrase_count - 1,
        ))
    return BeatPhraseAnalysis(
        tempoBpm=bpm, beatIntervalSeconds=round(interval, 6),
        markers=markers, phrases=phrases,
    )


def _nearest_marker(
    analysis: BeatPhraseAnalysis, time_seconds: float, *, downbeat: bool = False,
) -> BeatMarker:
    choices = [marker for marker in analysis.markers if not downbeat or marker.kind == "downbeat"]
    return min(choices, key=lambda marker: abs(marker.timeSeconds - time_seconds))


def _natural_audio_event(
    clip: dict, segment: Segment, priorities: dict[str, str],
) -> NaturalAudioEvent:
    transcript = (segment.transcript or "").strip()
    action = segment.action.lower()
    uses = set(segment.storyUses)
    priority = priorities.get(segment.segmentId, "none")
    if transcript:
        classification = "background_chatter"
        reason = "Transcript evidence indicates speech-like background chatter"
    elif segment.audioScore < 0.35:
        classification = "unusable"
        reason = "Source audio quality is below the usable threshold"
    elif (uses.intersection({"peak", "completion"})
          or any(word in action for word in ("impact", "strike", "land", "hit"))):
        classification = "impact"
        reason = "Picture action supports a short synchronous impact emphasis"
    elif ("reflection" in uses
          or any(word in action for word in ("breath", "recovery", "effort"))):
        classification = "effort"
        reason = "Authentic effort/recovery sound can carry emotion"
    else:
        classification = "clean_natural"
        reason = "No chatter is detected and source audio is usable"
    return NaturalAudioEvent(
        clipId=clip["id"], segmentId=segment.segmentId,
        timelineStart=clip["timelineStart"], timelineEnd=clip["timelineEnd"],
        classification=classification, audioScore=segment.audioScore,
        chatterDetected=bool(transcript), treatmentPriority=priority, reason=reason,
    )


def _source_instruction(event: NaturalAudioEvent) -> SourceAudioInstruction:
    duration = event.timelineEnd - event.timelineStart
    fade = round(min(0.18, duration / 4), 3)
    action_map = {
        "background_chatter": ("reduce_chatter", -18.0),
        "unusable": ("mute_unusable", -60.0),
        "impact": ("emphasize_impact", 2.0),
        "effort": ("preserve", 0.0),
        "clean_natural": ("preserve", -2.0),
    }
    action, gain = action_map[event.classification]
    return SourceAudioInstruction(
        clipId=event.clipId, startSeconds=event.timelineStart,
        endSeconds=event.timelineEnd, action=action, targetGainDb=gain,
        fadeInSeconds=fade, fadeOutSeconds=fade, reason=event.reason,
    )


def build_music_plan(
    preproduction_run_id: str,
    picture_edit_run_id: str,
    treatment: CreativeTreatment,
    candidate: PictureCandidateSummary,
    segments: list[Segment],
) -> MusicPlan:
    """Build an auditable music/sound plan without mutating picture data."""
    if not candidate.valid:
        raise MusicSupervisorError("selected picture candidate must be valid")
    clips = candidate.timeline.get("tracks", [{}])[0].get("clips", [])
    if not clips or candidate.durationSeconds <= 0:
        raise MusicSupervisorError("selected picture candidate has no usable clips")
    segment_by_id = {segment.segmentId: segment for segment in segments}
    missing = [clip.get("segmentId") for clip in clips
               if clip.get("segmentId") not in segment_by_id]
    if missing:
        raise MusicSupervisorError(
            f"selected picture candidate references missing segments: {', '.join(missing)}"
        )

    duration = candidate.durationSeconds
    analysis = _analyze_beats_and_phrases(treatment, duration)
    priorities = {moment.segmentId: moment.priority
                  for moment in treatment.naturalAudioMoments}
    events = [_natural_audio_event(clip, segment_by_id[clip["segmentId"]], priorities)
              for clip in clips]
    source_instructions = [_source_instruction(event) for event in events]
    impact = [instruction for event, instruction in zip(
        events, source_instructions, strict=True,
    )
              if event.classification == "impact"]
    ducking = []
    for event in events:
        if event.classification not in {"clean_natural", "effort", "impact"}:
            continue
        gain = -6.0 if event.classification == "impact" else (
            -10.0 if event.treatmentPriority == "high" else -8.0
        )
        ducking.append(MusicDuckingInstruction(
            startSeconds=max(0, round(event.timelineStart - 0.08, 3)),
            endSeconds=min(duration, round(event.timelineEnd + 0.12, 3)),
            musicGainDb=gain, attackSeconds=0.08, releaseSeconds=0.22,
            trigger="natural_audio" if event.classification == "clean_natural"
            else event.classification, clipId=event.clipId,
        ))

    alignments = []
    for point in treatment.musicEnergyCurve:
        picture_time = point.position * duration
        marker = _nearest_marker(analysis, picture_time, downbeat=True)
        alignments.append(MusicEnergyAlignment(
            position=point.position, pictureTimeSeconds=round(picture_time, 3),
            musicTimeSeconds=marker.timeSeconds, targetEnergy=point.energy,
            intent=point.intent,
            alignment="phrase_boundary" if point.position in {0, 1} else "downbeat",
        ))

    sync = []
    for index, clip in enumerate(clips):
        cut = float(clip["timelineStart"])
        marker = _nearest_marker(analysis, cut, downbeat=index in {0, len(clips) - 1})
        offset = round(marker.timeSeconds - cut, 3)
        sync.append(PictureMusicSyncInstruction(
            clipId=clip["id"], pictureCutSeconds=round(cut, 3),
            musicAnchorSeconds=marker.timeSeconds, offsetSeconds=offset,
            anchorKind="downbeat" if marker.kind == "downbeat" else "beat",
            instruction="align_music_to_picture" if abs(offset) <= 0.2
            else "preserve_picture_timing",
            reason="Music edit follows the locked picture cut; picture timing is never changed",
        ))

    ending_phrase = analysis.phrases[-1]
    final_downbeat = _nearest_marker(analysis, duration, downbeat=True)
    final_event = events[-1]
    natural_tail = 1.0 if final_event.classification in {"effort", "clean_natural"} else 0.35
    return MusicPlan(
        preproductionRunId=preproduction_run_id,
        pictureEditRunId=picture_edit_run_id,
        selectedCandidateId=candidate.candidateId,
        pictureDurationSeconds=duration,
        trackBrief={
            "purpose": treatment.purpose,
            "tone": treatment.tone,
            "tempoBpm": analysis.tempoBpm,
            "meter": "4/4",
            "structure": "four-bar phrases with an editable resolved ending",
            "energyArc": [point.model_dump() for point in treatment.musicEnergyCurve],
            "licensingInstruction": "operator must attach an appropriately licensed track",
        },
        beatPhraseAnalysis=analysis,
        energyAlignment=alignments,
        naturalAudioEvents=events,
        sourceAudioInstructions=source_instructions,
        musicDucking=ducking,
        impactEmphasis=impact,
        fades=AudioFadePlan(
            musicFadeInSeconds=0.75, musicFadeOutSeconds=0.85,
            sourceCrossfadeSeconds=0.08,
            preserveFinalNaturalTailSeconds=natural_tail,
        ),
        loudnessTargets=LoudnessTargets(
            integratedLufs=-14, truePeakDbtp=-1,
            loudnessRangeLufs=(5, 12),
        ),
        musicalEnding=MusicalEndingPlan(
            endingPhraseStartSeconds=ending_phrase.startSeconds,
            resolveAtSeconds=duration,
            finalDownbeatSeconds=final_downbeat.timeSeconds,
            instruction=(
                "Use a resolved edit or supplied alternate ending; do not hard-cut or "
                "leave a loop harmonically unresolved. Let the final natural-audio tail read."
            ),
        ),
        pictureMusicSync=sync,
        boundaries=[
            "planning instructions only; no audio is rendered",
            "selected picture timing remains immutable",
            "no motion graphics, captions, color, critics, or tournament selection",
        ],
    )
