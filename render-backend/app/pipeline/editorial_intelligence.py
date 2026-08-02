"""Milestone 6 deterministic editorial intelligence and tournament selection.

Complete candidates are composed only from existing picture candidates, source
clips, completed audio, and validated finishing instructions. Critics emit
scored evidence; revisions are allowlisted and never create footage.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import statistics
import subprocess
from itertools import combinations
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from ..renderer import FFMPEG
from ..renderer2 import render_timeline
from .audio_rendering import CompletedAudioMix
from .composition import CompositionMetrics
from .creative_director import CreativeTreatment
from .music_supervisor import MusicPlan, NaturalAudioEvent
from .picture_editor import PictureCandidateSummary
from .schemas import Segment, TranscriptArtifact
from .visual_finishing import (
    CaptionPackage,
    ColorPackage,
    GraphicsPackage,
    NormalizedRegion,
    build_caption_package,
    build_color_package,
    build_graphics_package,
    render_finishing_preview,
)


class EditorialIntelligenceError(ValueError):
    pass


CriticKind = Literal[
    "hook_effectiveness", "story_structure", "pacing_retention",
    "picture_quality", "music_synchronization", "audio_quality",
    "motion_graphics", "captions", "color_finishing", "publishability",
]
RevisionOperation = Literal[
    "change_hook", "reorder_picture", "shift_music_timing",
    "shift_graphics_timing", "change_caption_layout", "change_color_instructions",
]


class VariantConfig(BaseModel):
    hookStrategy: Literal["picture_original", "strongest_supported_hook"]
    pacingProfile: str
    musicSyncOffsetSeconds: float = Field(ge=-.15, le=.15)
    graphicsTimingOffsetSeconds: float = Field(ge=-.35, le=.35)
    captionLayout: Literal["adaptive", "top", "bottom"]
    colorPreset: Literal["none", "clean_warm", "cool_contrast", "neutral_social"]


class RevisionInstruction(BaseModel):
    operation: RevisionOperation
    field: str
    currentValue: Any
    proposedValue: Any
    bound: str
    evidenceMetric: str


class CompleteCandidateManifest(BaseModel):
    schemaVersion: int = 1
    candidateKey: str
    generationKind: Literal["initial", "revised"]
    parentCandidateKey: str | None = None
    sourcePictureCandidateId: str
    sourceAssetIds: list[str]
    variant: VariantConfig
    pictureTimeline: dict
    graphics: GraphicsPackage
    captions: CaptionPackage
    color: ColorPackage
    revisionInstructions: list[RevisionInstruction] = Field(default_factory=list)
    renderQc: dict = Field(default_factory=dict)
    previewStoragePath: str | None = None
    fabricatedFootage: Literal[False] = False

    @model_validator(mode="after")
    def complete_and_bounded(self):
        tracks = self.pictureTimeline.get("tracks", [])
        clips = [clip for track in tracks if track.get("type") == "video"
                 for clip in track.get("clips", [])]
        if not clips:
            raise ValueError("complete candidate requires picture clips")
        if {clip["assetId"] for clip in clips} != set(self.sourceAssetIds):
            raise ValueError("candidate source ancestry does not match its picture clips")
        if self.graphics.pictureTimingChanged or self.captions.pictureTimingChanged:
            raise ValueError("finishing instructions cannot retime picture")
        if not self.color.nonDestructive:
            raise ValueError("candidate color instructions must be non-destructive")
        if self.generationKind == "revised" and not self.parentCandidateKey:
            raise ValueError("revised candidate requires immutable parent ancestry")
        return self


class CriticEvidence(BaseModel):
    metric: str
    observed: Any
    target: Any
    sourceRef: str
    weight: float = Field(gt=0, le=1)
    contribution: float = Field(ge=0, le=100)
    explanation: str


class CriticReport(BaseModel):
    schemaVersion: int = 1
    criticKind: CriticKind
    candidateKey: str
    score: float = Field(ge=0, le=100)
    passed: bool
    evidence: list[CriticEvidence] = Field(min_length=1)
    issues: list[str]
    revisionRequests: list[RevisionInstruction]
    consistencyHash: str


class PublishabilityDimension(BaseModel):
    score: float = Field(ge=0, le=100)
    evidenceRefs: list[str] = Field(min_length=1)
    explanation: str


class PublishabilityReport(BaseModel):
    schemaVersion: int = 1
    candidateKey: str
    dimensions: dict[str, PublishabilityDimension]
    overallPublishabilityScore: float = Field(ge=0, le=100)
    publishable: bool
    blockingIssues: list[str]
    technicalQcPassed: bool


class PairwiseComparison(BaseModel):
    leftCandidateKey: str
    rightCandidateKey: str
    winnerCandidateKey: str
    leftScore: float
    rightScore: float
    dimensionDeltas: dict[str, float]
    decisiveEvidence: list[str]


class TournamentMatch(BaseModel):
    roundNumber: int = Field(ge=1)
    matchNumber: int = Field(ge=1)
    leftCandidateKey: str
    rightCandidateKey: str | None = None
    winnerCandidateKey: str
    eliminationReason: str


class TournamentResult(BaseModel):
    schemaVersion: int = 1
    candidateKeys: list[str] = Field(min_length=2)
    pairwiseComparisons: list[PairwiseComparison]
    bracket: list[TournamentMatch]
    eliminatedCandidateKeys: list[str]
    winnerCandidateKey: str
    winnerReasoning: list[str]


def _clips(timeline: dict) -> list[dict]:
    return [clip for track in timeline.get("tracks", []) if track.get("type") == "video"
            for clip in track.get("clips", [])]


def _reflow(clips: list[dict]) -> list[dict]:
    cursor = 0.0
    out = []
    for clip in clips:
        item = copy.deepcopy(clip)
        duration = (float(item["sourceEnd"]) - float(item["sourceStart"])) / float(
            item.get("speed", 1)
        )
        item["timelineStart"] = round(cursor, 3)
        cursor += duration
        item["timelineEnd"] = round(cursor, 3)
        out.append(item)
    return out


def _hook_score(clip: dict, segment_by_id: dict[str, Segment]) -> float:
    segment = segment_by_id.get(clip.get("segmentId"))
    if not segment:
        return 0
    role = 1 if "hook" in segment.storyUses else .55 if "peak" in segment.storyUses else 0
    return round(.45 * role + .25 * segment.motionIntensity
                 + .2 * segment.semanticRelevance + .1 * segment.focusScore, 4)


def _timeline_with_hook(candidate: PictureCandidateSummary, segments: list[Segment],
                        strongest: bool) -> dict:
    timeline = copy.deepcopy(candidate.timeline)
    clips = _clips(timeline)
    if strongest and clips:
        segment_by_id = {item.segmentId: item for item in segments}
        best = max(range(len(clips)), key=lambda index: _hook_score(clips[index], segment_by_id))
        if best:
            clips = [clips[best], *clips[:best], *clips[best + 1:]]
    timeline["tracks"][0]["clips"] = _reflow(clips)
    timeline["duration"] = timeline["tracks"][0]["clips"][-1]["timelineEnd"]
    return timeline


def _candidate_copy(source: PictureCandidateSummary, key: str, timeline: dict) -> PictureCandidateSummary:
    clips = _clips(timeline)
    return source.model_copy(update={
        "candidateId": key,
        "label": f"Editorial {source.label}",
        "durationSeconds": float(timeline["duration"]),
        "clipCount": len(clips),
        "structuralSignature": ">".join(clip.get("segmentId", clip["id"]) for clip in clips),
        "timeline": timeline,
    })


def _remap_audio_events(candidate: PictureCandidateSummary, plan: MusicPlan,
                        segments: list[Segment]) -> list[NaturalAudioEvent]:
    by_segment = {item.segmentId: item for item in plan.naturalAudioEvents}
    segment_by_id = {item.segmentId: item for item in segments}
    events = []
    for clip in _clips(candidate.timeline):
        source = by_segment.get(clip.get("segmentId"))
        segment = segment_by_id[clip["segmentId"]]
        if source:
            classification, chatter, reason = (
                source.classification, source.chatterDetected, source.reason,
            )
        elif segment.transcript:
            classification, chatter, reason = (
                "background_chatter", True,
                "Transcript exists outside the selected Milestone 3 clip map",
            )
        elif segment.audioScore < .35:
            classification, chatter, reason = "unusable", False, "Audio score is unusable"
        else:
            classification, chatter, reason = "clean_natural", False, "Usable natural audio"
        events.append(NaturalAudioEvent(
            clipId=clip["id"], segmentId=segment.segmentId,
            timelineStart=clip["timelineStart"], timelineEnd=clip["timelineEnd"],
            classification=classification, audioScore=segment.audioScore,
            chatterDetected=chatter,
            treatmentPriority=source.treatmentPriority if source else "none",
            reason=reason,
        ))
    return events


def _shift_graphics(package: GraphicsPackage, offset: float, duration: float) -> GraphicsPackage:
    result = package.model_copy(deep=True)
    for event in result.events:
        if event.kind == "progress_bar":
            continue
        length = event.endSeconds - event.startSeconds
        start = max(0., min(duration - length, event.startSeconds + offset))
        event.startSeconds = round(start, 3)
        event.endSeconds = round(start + length, 3)
        event.phraseAligned = any(abs(event.startSeconds - point) <= .08
                                  for point in result.phraseBoundaries)
    return result


def _caption_layout(package: CaptionPackage, graphics: GraphicsPackage,
                    layout: str) -> CaptionPackage:
    result = package.model_copy(deep=True)
    safe = graphics.platform.safeTitle
    for index, group in enumerate(result.groups):
        position = layout
        if layout == "adaptive":
            position = "bottom" if index % 2 == 0 else "top"
        y = safe.y + .04 if position == "top" else safe.y + safe.height - .20
        group.region = NormalizedRegion(
            x=safe.x + .04, y=y, width=safe.width - .08, height=.14,
        )
    return result


def generate_initial_candidates(
    picture_candidates: list[PictureCandidateSummary], treatment: CreativeTreatment,
    completed: CompletedAudioMix, music_plan: MusicPlan, segments: list[Segment],
    composition: dict[str, CompositionMetrics], transcripts: dict[str, TranscriptArtifact],
) -> list[CompleteCandidateManifest]:
    valid = [item for item in picture_candidates if item.valid]
    if not valid:
        raise EditorialIntelligenceError("tournament requires at least one valid picture candidate")
    # Weak-capture projects may yield only one valid Milestone 2 candidate. The
    # tournament still needs materially different complete edits, so reuse that
    # real picture ancestry while varying the other five finishing dimensions.
    sources = [valid[index % len(valid)] for index in range(3)]
    presets = ("clean_warm", "cool_contrast", "neutral_social")
    layouts = ("bottom", "top", "adaptive")
    offsets = (-.08, 0., .08)
    manifests = []
    for index, source in enumerate(sources):
        key = f"editorial-initial-{index + 1}"
        strongest = index != 0
        timeline = _timeline_with_hook(source, segments, strongest)
        candidate = _candidate_copy(source, key, timeline)
        graphics = build_graphics_package(
            treatment, candidate, completed, composition, segments,
        )
        graphics_offset = (-.18, 0., .18)[index % 3]
        graphics = _shift_graphics(graphics, graphics_offset, candidate.durationSeconds)
        events = _remap_audio_events(candidate, music_plan, segments)
        captions = build_caption_package(
            candidate, segments, graphics, transcripts, events,
        )
        captions = _caption_layout(captions, graphics, layouts[index % 3])
        color = build_color_package(candidate, segments, lut=presets[index % 3])
        manifests.append(CompleteCandidateManifest(
            candidateKey=key, generationKind="initial",
            sourcePictureCandidateId=source.candidateId,
            sourceAssetIds=sorted({clip["assetId"] for clip in _clips(timeline)}),
            variant=VariantConfig(
                hookStrategy="strongest_supported_hook" if strongest else "picture_original",
                pacingProfile=source.label, musicSyncOffsetSeconds=offsets[index % 3],
                graphicsTimingOffsetSeconds=graphics_offset,
                captionLayout=layouts[index % 3], colorPreset=presets[index % 3],
            ),
            pictureTimeline=timeline, graphics=graphics, captions=captions, color=color,
        ))
    return manifests


def render_complete_candidate(
    manifest: CompleteCandidateManifest, source_paths: dict[str, str],
    completed_audio_preview: str, output_path: str, workdir: str,
) -> dict:
    """Render real picture, bounded music offset, and complete finishing layer."""
    picture_path = os.path.join(workdir, f"{manifest.candidateKey}-picture.mp4")
    muxed_path = os.path.join(workdir, f"{manifest.candidateKey}-audio.mp4")
    render_timeline(manifest.pictureTimeline, source_paths, picture_path, profile="preview")
    duration = float(manifest.pictureTimeline["duration"])
    offset = manifest.variant.musicSyncOffsetSeconds
    if offset >= 0:
        delay = int(round(offset * 1000))
        audio_filter = (
            f"[1:a]adelay={delay}|{delay},apad,atrim=duration={duration:.3f}[a]"
        )
    else:
        audio_filter = (
            f"[1:a]atrim=start={abs(offset):.3f},asetpts=PTS-STARTPTS,"
            f"apad,atrim=duration={duration:.3f}[a]"
        )
    try:
        subprocess.run([
            FFMPEG, "-y", "-loglevel", "error", "-i", picture_path,
            "-i", completed_audio_preview, "-filter_complex", audio_filter,
            "-map", "0:v:0", "-map", "[a]", "-c:v", "copy", "-c:a", "aac",
            "-b:a", "192k", "-t", f"{duration:.3f}", muxed_path,
        ], check=True, capture_output=True, timeout=240)
    except subprocess.CalledProcessError as exc:
        raise EditorialIntelligenceError(
            f"candidate audio synchronization failed: {exc.stderr[-400:]!r}"
        ) from exc
    qc = render_finishing_preview(
        muxed_path, output_path, manifest.graphics, manifest.captions, manifest.color,
    )
    qc.update({
        "musicSyncOffsetSeconds": offset,
        "sourcePictureCandidateId": manifest.sourcePictureCandidateId,
        "fabricatedFootage": False,
    })
    return qc


def _metric_evidence(metric: str, observed: Any, target: Any, source: str,
                     weight: float, normalized: float, explanation: str) -> CriticEvidence:
    return CriticEvidence(
        metric=metric, observed=observed, target=target, sourceRef=source,
        weight=weight, contribution=round(max(0., min(100., normalized * 100)), 3),
        explanation=explanation,
    )


def _report(kind: CriticKind, candidate: CompleteCandidateManifest,
            evidence: list[CriticEvidence], requests: list[RevisionInstruction] | None = None,
            issues: list[str] | None = None) -> CriticReport:
    total_weight = sum(item.weight for item in evidence)
    score = round(sum(item.contribution * item.weight for item in evidence) / total_weight, 3)
    payload = [{"metric": item.metric, "observed": item.observed,
                "target": item.target, "contribution": item.contribution}
               for item in evidence]
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return CriticReport(
        criticKind=kind, candidateKey=candidate.candidateKey, score=score,
        passed=score >= 70, evidence=evidence, issues=issues or [],
        revisionRequests=requests or [], consistencyHash=digest,
    )


def run_specialized_critics(
    candidate: CompleteCandidateManifest, segments: list[Segment],
    composition: dict[str, CompositionMetrics], completed: CompletedAudioMix,
) -> list[CriticReport]:
    clips = _clips(candidate.pictureTimeline)
    segment_by_id = {item.segmentId: item for item in segments}
    selected = [segment_by_id.get(clip.get("segmentId")) for clip in clips]
    durations = [clip["timelineEnd"] - clip["timelineStart"] for clip in clips]
    first = selected[0] if selected else None
    last = selected[-1] if selected else None
    reports: list[CriticReport] = []

    hook_role = bool(first and set(first.storyUses) & {"hook", "peak"})
    hook_motion = first.motionIntensity if first else 0
    hook_semantic = first.semanticRelevance if first else 0
    hook_requests = []
    if not hook_role or hook_motion < .55:
        hook_requests.append(RevisionInstruction(
            operation="change_hook", field="pictureTimeline.firstClip",
            currentValue=clips[0]["id"], proposedValue="highest_supported_hook_clip",
            bound="existing selected source clips only", evidenceMetric="hook_role_motion",
        ))
    reports.append(_report("hook_effectiveness", candidate, [
        _metric_evidence("supported_hook_role", hook_role, True, "segment.storyUses", .45,
                         1 if hook_role else 0, "Opening must have supported hook/peak evidence"),
        _metric_evidence("opening_motion", hook_motion, ">=0.55", "segment.motionIntensity",
                         .3, hook_motion, "Visual motion supports immediate attention"),
        _metric_evidence("opening_semantic_relevance", hook_semantic, ">=0.65",
                         "segment.semanticRelevance", .25, hook_semantic,
                         "Opening action must be relevant to the story"),
    ], hook_requests))

    ending = bool(last and set(last.storyUses) & {"completion", "reflection"})
    coverage = len({role for item in selected if item for role in item.storyUses}
                   .intersection({"hook", "early_effort", "build", "peak",
                                  "completion", "reflection"})) / 6
    reports.append(_report("story_structure", candidate, [
        _metric_evidence("story_role_coverage", round(coverage, 3), ">=0.67",
                         "segment.storyUses", .6, min(1, coverage / .67),
                         "Supported story roles create a complete arc"),
        _metric_evidence("ending_payoff", ending, True, "final segment.storyUses", .4,
                         1 if ending else 0, "Ending must resolve with completion/reflection"),
    ], [] if ending else [RevisionInstruction(
        operation="reorder_picture", field="pictureTimeline.lastClip",
        currentValue=clips[-1]["id"], proposedValue="best_existing_completion_clip",
        bound="reorder existing clips; no trims or fabricated footage",
        evidenceMetric="ending_payoff",
    )]))

    mean_duration = statistics.mean(durations)
    duration_score = max(0., 1 - abs(mean_duration - 2.2) / 2.2)
    early = durations[:max(1, len(durations) // 3)]
    early_energy = statistics.mean(
        item.motionIntensity for item in selected[:len(early)] if item
    ) if any(selected[:len(early)]) else 0
    reports.append(_report("pacing_retention", candidate, [
        _metric_evidence("mean_shot_seconds", round(mean_duration, 3), "1.2-3.2",
                         "pictureTimeline", .55, duration_score,
                         "Shot length should support social retention without strobing"),
        _metric_evidence("early_energy", round(early_energy, 3), ">=0.55",
                         "segment.motionIntensity", .45, early_energy,
                         "Early pacing should carry visual energy"),
    ]))

    technical = [statistics.mean((item.focusScore, item.exposureScore,
                                  item.stabilityScore)) for item in selected if item]
    comp_scores = [composition[item.segmentId].compositionQuality for item in selected
                   if item and item.segmentId in composition]
    reports.append(_report("picture_quality", candidate, [
        _metric_evidence("technical_quality", round(statistics.mean(technical), 3)
                         if technical else 0, ">=0.7", "segment mechanical scores", .65,
                         statistics.mean(technical) if technical else 0,
                         "Focus, exposure, and stability must remain usable"),
        _metric_evidence("composition_quality", round(statistics.mean(comp_scores), 3)
                         if comp_scores else 0, ">=0.65", "composition evidence", .35,
                         statistics.mean(comp_scores) if comp_scores else 0,
                         "Framing must keep subject/action readable"),
    ]))

    sync_abs = abs(candidate.variant.musicSyncOffsetSeconds)
    phrase_ratio = (sum(event.phraseAligned for event in candidate.graphics.events)
                    / max(1, len(candidate.graphics.events)))
    sync_requests = []
    if sync_abs > .05:
        sync_requests.append(RevisionInstruction(
            operation="shift_music_timing", field="variant.musicSyncOffsetSeconds",
            currentValue=candidate.variant.musicSyncOffsetSeconds, proposedValue=0,
            bound="absolute offset <= 0.15 seconds", evidenceMetric="music_sync_offset",
        ))
    reports.append(_report("music_synchronization", candidate, [
        _metric_evidence("music_sync_offset", sync_abs, "<=0.05s", "variant config", .55,
                         max(0, 1 - sync_abs / .15),
                         "Music offset is bounded against locked picture"),
        _metric_evidence("graphics_phrase_alignment", round(phrase_ratio, 3), ">=0.4",
                         "Milestone 4 actual phrases", .25, min(1, phrase_ratio / .4),
                         "Finishing events should reinforce musical phrases"),
        _metric_evidence("phrase_resolved_ending", completed.targetVsActual.phraseResolvedEnding,
                         True, "audio_mix.targetVsActual", .2,
                         1 if completed.targetVsActual.phraseResolvedEnding else 0,
                         "Completed music must retain its resolved ending"),
    ], sync_requests))

    qc = completed.qc
    loudness = 1 if qc.integratedLufs is not None and abs(qc.integratedLufs + 14) <= 1 else .4
    peak = 1 if qc.truePeakDbtp is not None and qc.truePeakDbtp <= -.8 else 0
    reports.append(_report("audio_quality", candidate, [
        _metric_evidence("audio_qc_passed", qc.passed, True, "audio_mix.audio_qc", .45,
                         1 if qc.passed else 0, "Milestone 4 technical audio QC must pass"),
        _metric_evidence("integrated_lufs", qc.integratedLufs, "-14 +/- 1 LUFS",
                         "audio_mix.audio_qc", .3, loudness,
                         "Loudness should match the documented delivery target"),
        _metric_evidence("true_peak_dbtp", qc.truePeakDbtp, "<= -0.8 dBTP",
                         "audio_mix.audio_qc", .25, peak,
                         "True peak must retain headroom"),
    ]))

    max_occlusion = max((item.subjectOcclusionRisk for item in candidate.graphics.events), default=0)
    collisions = sum(item.captionCollision for item in candidate.graphics.events)
    graphics_requests = []
    if phrase_ratio < .4:
        graphics_requests.append(RevisionInstruction(
            operation="shift_graphics_timing", field="graphics.events.startSeconds",
            currentValue="current", proposedValue="nearest_actual_phrase",
            bound="shift <= 0.35 seconds; stay inside picture duration",
            evidenceMetric="graphics_phrase_alignment",
        ))
    reports.append(_report("motion_graphics", candidate, [
        _metric_evidence("max_subject_occlusion_risk", max_occlusion, "<=0.25",
                         "graphics.events", .5, max(0, 1 - max_occlusion / .25),
                         "Graphics must not cover important subject/action areas"),
        _metric_evidence("caption_collisions", collisions, 0, "graphics.events", .3,
                         1 if collisions == 0 else 0, "Graphics and captions cannot overlap"),
        _metric_evidence("phrase_aligned_event_ratio", round(phrase_ratio, 3), ">=0.4",
                         "graphics.events", .2, min(1, phrase_ratio / .4),
                         "Graphic timing should respect actual music phrases"),
    ], graphics_requests))

    included = [item for item in candidate.captions.evidenceDecisions
                if item.decision == "included"]
    valid_exclusions = all(item.reasonCode in {
        "milestone3_background_chatter", "unusable_audio",
        "non_speech_natural_audio", "no_transcript",
    } for item in candidate.captions.evidenceDecisions if item.decision == "excluded")
    exact_ratio = (sum(item.timingSource == "transcript_word_timestamps" for item in included)
                   / len(included)) if included else 1
    reports.append(_report("captions", candidate, [
        _metric_evidence("caption_overlaps", candidate.captions.overlapsDetected, 0,
                         "caption package", .4,
                         1 if candidate.captions.overlapsDetected == 0 else 0,
                         "Caption groups must never overlap"),
        _metric_evidence("valid_audio_evidence_decisions", valid_exclusions, True,
                         "caption evidence decisions", .35, 1 if valid_exclusions else 0,
                         "Chatter/unusable/non-speech exclusions must be explicit"),
        _metric_evidence("exact_timing_ratio", round(exact_ratio, 3), "measured when available",
                         "transcript artifacts", .25, .8 + .2 * exact_ratio,
                         "Fallback timing is permitted only when clearly labeled"),
    ]))

    bounded = all(-1 <= item.exposureEv <= 1 and .9 <= item.contrast <= 1.15
                  and .85 <= item.saturation <= 1.15
                  for item in candidate.color.instructions)
    avg_correction = statistics.mean(abs(item.exposureEv)
                                     for item in candidate.color.instructions)
    reports.append(_report("color_finishing", candidate, [
        _metric_evidence("bounded_color_instructions", bounded, True, "color package", .5,
                         1 if bounded else 0, "Grade must remain inside safe limits"),
        _metric_evidence("non_destructive", candidate.color.nonDestructive, True,
                         "color package", .3, 1 if candidate.color.nonDestructive else 0,
                         "Source media cannot be destructively changed"),
        _metric_evidence("mean_exposure_correction", round(avg_correction, 3), "<=0.6 EV",
                         "color instructions", .2, max(0, 1 - avg_correction / 1.2),
                         "Large corrections signal weak capture consistency"),
    ]))

    render_qc = candidate.renderQc
    stream_ok = bool(render_qc.get("videoStreamPresent") and render_qc.get("audioStreamPresent"))
    duration = float(candidate.pictureTimeline.get("duration") or 0)
    reports.append(_report("publishability", candidate, [
        _metric_evidence("required_streams", stream_ok, True, "candidate render QC", .4,
                         1 if stream_ok else 0, "Publishable video needs picture and audio"),
        _metric_evidence("social_duration", duration, "15-60 seconds",
                         "candidate timeline", .3, 1 if 15 <= duration <= 60 else .3,
                         "Output duration must match the social brief"),
        _metric_evidence("fabricated_footage", candidate.fabricatedFootage, False,
                         "candidate manifest", .3, 1 if not candidate.fabricatedFootage else 0,
                         "Editorial intelligence may only reuse real source clips"),
    ]))
    return reports


def build_publishability_report(candidate: CompleteCandidateManifest,
                                critics: list[CriticReport]) -> PublishabilityReport:
    by_kind = {item.criticKind: item for item in critics}
    mapping = {
        "hook_quality": ("hook_effectiveness",),
        "pacing": ("pacing_retention",),
        "emotional_payoff": ("story_structure",),
        "clarity": ("story_structure", "captions"),
        "graphics_quality": ("motion_graphics",),
        "caption_quality": ("captions",),
        "music_fit": ("music_synchronization",),
        "audio_quality": ("audio_quality",),
        "technical_qc": ("picture_quality", "audio_quality", "publishability"),
    }
    dimensions = {}
    for name, kinds in mapping.items():
        reports = [by_kind[kind] for kind in kinds]
        score = round(statistics.mean(item.score for item in reports), 3)
        dimensions[name] = PublishabilityDimension(
            score=score,
            evidenceRefs=[f"{item.criticKind}:{item.consistencyHash}" for item in reports],
            explanation=f"Derived from structured {', '.join(kinds)} critic evidence",
        )
    weights = {
        "hook_quality": .16, "pacing": .13, "emotional_payoff": .12,
        "clarity": .1, "graphics_quality": .09, "caption_quality": .08,
        "music_fit": .1, "audio_quality": .1, "technical_qc": .12,
    }
    overall = round(sum(dimensions[key].score * weight for key, weight in weights.items()), 3)
    blockers = [f"{item.criticKind}: {issue}" for item in critics for issue in item.issues]
    technical = dimensions["technical_qc"].score >= 70
    return PublishabilityReport(
        candidateKey=candidate.candidateKey, dimensions=dimensions,
        overallPublishabilityScore=overall,
        publishable=overall >= 75 and technical and not blockers,
        blockingIssues=blockers, technicalQcPassed=technical,
    )


def apply_bounded_revision(
    parent: CompleteCandidateManifest, requests: list[RevisionInstruction],
    segments: list[Segment],
) -> CompleteCandidateManifest:
    allowed = {
        "change_hook", "reorder_picture", "shift_music_timing",
        "shift_graphics_timing", "change_caption_layout", "change_color_instructions",
    }
    selected: list[RevisionInstruction] = []
    seen = set()
    for request in requests:
        if request.operation in allowed and request.operation not in seen:
            selected.append(request)
            seen.add(request.operation)
    result = parent.model_copy(deep=True)
    result.candidateKey = f"{parent.candidateKey}-revised"
    result.generationKind = "revised"
    result.parentCandidateKey = parent.candidateKey
    result.previewStoragePath = None
    result.renderQc = {}
    result.revisionInstructions = selected
    for request in selected:
        if request.operation == "change_hook":
            source = PictureCandidateSummary(
                candidateId=result.sourcePictureCandidateId, label="revision source",
                storyVariantId="revision", valid=True,
                durationSeconds=float(result.pictureTimeline["duration"]),
                targetDurationSeconds=max(15, float(result.pictureTimeline["duration"])),
                coverageRatio=1, editorialScore=1,
                structuralSignature="revision", clipCount=len(_clips(result.pictureTimeline)),
                timeline=result.pictureTimeline,
            )
            result.pictureTimeline = _timeline_with_hook(source, segments, True)
            result.variant.hookStrategy = "strongest_supported_hook"
        elif request.operation == "shift_music_timing":
            result.variant.musicSyncOffsetSeconds = max(
                -.15, min(.15, float(request.proposedValue))
            )
        elif request.operation == "shift_graphics_timing":
            result.graphics = _shift_graphics(
                result.graphics, -result.variant.graphicsTimingOffsetSeconds,
                float(result.pictureTimeline["duration"]),
            )
            result.variant.graphicsTimingOffsetSeconds = 0
        elif request.operation == "change_caption_layout":
            result.captions = _caption_layout(result.captions, result.graphics, "adaptive")
            result.variant.captionLayout = "adaptive"
        elif request.operation == "change_color_instructions":
            result.variant.colorPreset = "neutral_social"
            result.color.lutPreset = "neutral_social"
            for instruction in result.color.instructions:
                instruction.lutPreset = "neutral_social"
        elif request.operation == "reorder_picture":
            clips = _clips(result.pictureTimeline)
            segment_by_id = {item.segmentId: item for item in segments}
            completions = [index for index, clip in enumerate(clips)
                           if set(segment_by_id.get(clip.get("segmentId"), Segment(
                               segmentId="missing", assetId="missing", sourceStart=0,
                               sourceEnd=1)).storyUses) & {"completion", "reflection"}]
            if completions and completions[-1] != len(clips) - 1:
                chosen = clips.pop(completions[-1])
                clips.append(chosen)
                result.pictureTimeline["tracks"][0]["clips"] = _reflow(clips)
    result.sourceAssetIds = sorted({clip["assetId"] for clip in _clips(result.pictureTimeline)})
    return CompleteCandidateManifest(**result.model_dump())


def _compare(left: PublishabilityReport, right: PublishabilityReport) -> PairwiseComparison:
    deltas = {key: round(left.dimensions[key].score - right.dimensions[key].score, 3)
              for key in left.dimensions}
    left_tuple = (left.overallPublishabilityScore, left.candidateKey)
    right_tuple = (right.overallPublishabilityScore, right.candidateKey)
    winner = left if left_tuple >= right_tuple else right
    strongest = sorted(deltas, key=lambda key: abs(deltas[key]), reverse=True)[:3]
    return PairwiseComparison(
        leftCandidateKey=left.candidateKey, rightCandidateKey=right.candidateKey,
        winnerCandidateKey=winner.candidateKey,
        leftScore=left.overallPublishabilityScore,
        rightScore=right.overallPublishabilityScore,
        dimensionDeltas=deltas,
        decisiveEvidence=[f"{key}: left-right {deltas[key]:+.3f}" for key in strongest],
    )


def run_tournament(reports: list[PublishabilityReport]) -> TournamentResult:
    if len(reports) < 2:
        raise EditorialIntelligenceError("tournament requires at least two reports")
    by_key = {item.candidateKey: item for item in reports}
    pairwise = [_compare(left, right) for left, right in combinations(reports, 2)]
    seeds = sorted(reports, key=lambda item: (
        item.overallPublishabilityScore, item.candidateKey,
    ), reverse=True)
    current = [item.candidateKey for item in seeds]
    bracket = []
    eliminated = []
    round_number = 1
    while len(current) > 1:
        next_round = []
        for match_index in range(0, len(current), 2):
            left = current[match_index]
            if match_index + 1 >= len(current):
                bracket.append(TournamentMatch(
                    roundNumber=round_number, matchNumber=match_index // 2 + 1,
                    leftCandidateKey=left, winnerCandidateKey=left,
                    eliminationReason="top seed advances on bye",
                ))
                next_round.append(left)
                continue
            right = current[match_index + 1]
            comparison = _compare(by_key[left], by_key[right])
            loser = right if comparison.winnerCandidateKey == left else left
            bracket.append(TournamentMatch(
                roundNumber=round_number, matchNumber=match_index // 2 + 1,
                leftCandidateKey=left, rightCandidateKey=right,
                winnerCandidateKey=comparison.winnerCandidateKey,
                eliminationReason=(
                    f"{loser} eliminated: {comparison.winnerCandidateKey} scored "
                    f"{by_key[comparison.winnerCandidateKey].overallPublishabilityScore:.3f}"
                ),
            ))
            eliminated.append(loser)
            next_round.append(comparison.winnerCandidateKey)
        current = next_round
        round_number += 1
    winner = by_key[current[0]]
    strongest = sorted(winner.dimensions.items(), key=lambda item: item[1].score,
                       reverse=True)[:3]
    return TournamentResult(
        candidateKeys=[item.candidateKey for item in reports],
        pairwiseComparisons=pairwise, bracket=bracket,
        eliminatedCandidateKeys=eliminated, winnerCandidateKey=winner.candidateKey,
        winnerReasoning=[
            f"Overall publishability {winner.overallPublishabilityScore:.3f}",
            *[f"{name} {dimension.score:.3f}: {dimension.explanation}"
              for name, dimension in strongest],
        ],
    )


def build_four_way_comparison(human_report: dict | None,
                              winner: CompleteCandidateManifest,
                              publishability: PublishabilityReport) -> dict:
    winner_metrics = {
        "candidate_key": winner.candidateKey,
        "duration_seconds": float(winner.pictureTimeline["duration"]),
        "video_clip_count": len(_clips(winner.pictureTimeline)),
        "source_asset_count": len(winner.sourceAssetIds),
        "overall_publishability_score": publishability.overallPublishabilityScore,
        "publishable": publishability.publishable,
    }
    if not human_report:
        return {
            "schema_version": 1, "status": "human_ceiling_unavailable",
            "missing_versions": ["autonomous_initial", "human_approved"],
            "versions": {"editorial_intelligence_winner": winner_metrics},
            "measurable_improvements": {},
        }
    versions = copy.deepcopy(human_report.get("versions", {}))
    versions["editorial_intelligence_winner"] = winner_metrics

    def rating(lineage: str) -> float | None:
        card = versions.get(lineage, {}).get("scorecard") or {}
        value = card.get("overall_rating")
        return None if value is None else float(value) * 10

    initial = versions.get("autonomous_initial")
    human = versions.get("human_approved")
    revised = versions.get("autonomous_revised")
    improvements = {
        "winner_vs_initial_score_points": None if rating("autonomous_initial") is None
        else round(publishability.overallPublishabilityScore - rating("autonomous_initial"), 3),
        "winner_vs_revised_score_points": None if rating("autonomous_revised") is None
        else round(publishability.overallPublishabilityScore - rating("autonomous_revised"), 3),
        "winner_vs_human_score_points": None if rating("human_approved") is None
        else round(publishability.overallPublishabilityScore - rating("human_approved"), 3),
        "winner_vs_initial_duration_seconds": None if not initial else round(
            winner_metrics["duration_seconds"] - initial["duration_seconds"], 3),
        "winner_vs_revised_duration_seconds": None if not revised else round(
            winner_metrics["duration_seconds"] - revised["duration_seconds"], 3),
        "winner_vs_human_duration_seconds": None if not human else round(
            winner_metrics["duration_seconds"] - human["duration_seconds"], 3),
        "human_correction_minutes": human_report.get("human_work", {}).get(
            "server_measured_minutes"
        ),
    }
    required = {"autonomous_initial", "human_approved"}
    missing = sorted(required - versions.keys())
    return {
        "schema_version": 1, "status": "complete" if not missing else "incomplete",
        "missing_versions": missing, "versions": versions,
        "measurable_improvements": improvements,
    }
