"""Milestone 5 deterministic motion graphics, captions, and color finishing.

The selected picture and completed audio preview are immutable inputs.  This
module emits bounded, inspectable overlay/grading instructions and can apply
them to a preview without changing picture timing or audio content.
"""
from __future__ import annotations

import json
import re
import statistics
import subprocess
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from ..renderer import FFMPEG, FFPROBE, _default_font, _ff_escape
from .audio_rendering import CompletedAudioMix
from .composition import CompositionMetrics
from .creative_director import CreativeTreatment
from .music_supervisor import NaturalAudioEvent
from .picture_editor import PictureCandidateSummary
from .schemas import Segment, TranscriptArtifact


class VisualFinishingError(ValueError):
    pass


class NormalizedRegion(BaseModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def inside_frame(self):
        if self.x + self.width > 1.000001 or self.y + self.height > 1.000001:
            raise ValueError("region extends outside frame")
        return self


class PlatformPreset(BaseModel):
    aspect: Literal["9:16", "1:1", "16:9"]
    width: int
    height: int
    safeTitle: NormalizedRegion


PRESETS = {
    "9:16": PlatformPreset(aspect="9:16", width=1080, height=1920,
                           safeTitle=NormalizedRegion(x=.07, y=.08, width=.86, height=.80)),
    "1:1": PlatformPreset(aspect="1:1", width=1080, height=1080,
                          safeTitle=NormalizedRegion(x=.07, y=.08, width=.86, height=.84)),
    "16:9": PlatformPreset(aspect="16:9", width=1920, height=1080,
                           safeTitle=NormalizedRegion(x=.06, y=.07, width=.88, height=.86)),
}


def _hex(value: str) -> str:
    value = value.upper()
    if not re.fullmatch(r"#[0-9A-F]{6}", value):
        raise VisualFinishingError(f"invalid palette color {value!r}")
    return value


def contrast_ratio(foreground: str, background: str) -> float:
    def luminance(value: str) -> float:
        raw = _hex(value)[1:]
        channels = [int(raw[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        linear = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4
                  for c in channels]
        return .2126 * linear[0] + .7152 * linear[1] + .0722 * linear[2]
    a, b = sorted((luminance(foreground), luminance(background)), reverse=True)
    return round((a + .05) / (b + .05), 2)


class BrandTemplate(BaseModel):
    templateId: str
    name: str
    fontFamily: Literal["DejaVu Sans", "DejaVu Sans Condensed"] = "DejaVu Sans"
    primary: str = "#FFFFFF"
    secondary: str = "#101820"
    accent: str = "#00E5FF"
    captionStyle: Literal["clean", "boxed", "kinetic"] = "kinetic"
    titleCase: Literal["upper", "sentence"] = "upper"
    minimumContrastRatio: float = Field(default=4.5, ge=4.5)

    @model_validator(mode="after")
    def accessible_palette(self):
        self.primary, self.secondary, self.accent = (
            _hex(self.primary), _hex(self.secondary), _hex(self.accent)
        )
        if contrast_ratio(self.primary, self.secondary) < self.minimumContrastRatio:
            raise ValueError("primary/secondary palette contrast is below WCAG AA")
        return self


class GraphicEvent(BaseModel):
    eventId: str
    kind: Literal["animated_title", "lower_third", "callout", "exercise_label",
                  "section_header", "progress_bar", "rep_counter", "timer",
                  "intro", "outro"]
    text: str
    startSeconds: float = Field(ge=0)
    endSeconds: float = Field(gt=0)
    region: NormalizedRegion
    animation: Literal["fade", "slide", "scale", "fill"]
    phraseAligned: bool
    subjectOcclusionRisk: float = Field(ge=0, le=1)
    captionCollision: bool = False

    @model_validator(mode="after")
    def valid_duration(self):
        if self.endSeconds <= self.startSeconds:
            raise ValueError("graphic event end must follow start")
        return self


class CaptionWord(BaseModel):
    text: str
    startSeconds: float = Field(ge=0)
    endSeconds: float = Field(gt=0)
    speakerId: str = "speaker-1"


class CaptionGroup(BaseModel):
    groupId: str
    words: list[CaptionWord] = Field(min_length=1, max_length=5)
    text: str
    startSeconds: float
    endSeconds: float
    region: NormalizedRegion
    highlightWordIndexes: list[int]
    timingSource: Literal["transcript_word_timestamps", "segment_distributed"]

    @model_validator(mode="after")
    def timing_and_text(self):
        if self.endSeconds <= self.startSeconds:
            raise ValueError("caption end must follow start")
        if len(self.text) > 42:
            raise ValueError("caption group exceeds safe reading width")
        return self


class CaptionEvidenceDecision(BaseModel):
    clipId: str
    segmentId: str
    decision: Literal["included", "excluded"]
    reasonCode: Literal[
        "meaningful_dialogue_supported", "meaningful_narration_supported",
        "milestone3_background_chatter", "unusable_audio",
        "non_speech_natural_audio", "no_transcript",
    ]
    milestone3Classification: str | None = None
    audioScore: float = Field(ge=0, le=1)
    semanticRelevance: float = Field(ge=0, le=1)
    timingSource: Literal["transcript_word_timestamps", "segment_distributed"] | None = None
    explanation: str


class ColorInstruction(BaseModel):
    clipId: str
    segmentId: str
    startSeconds: float = Field(ge=0)
    endSeconds: float = Field(gt=0)
    exposureEv: float = Field(ge=-1, le=1)
    temperatureShift: float = Field(ge=-.2, le=.2)
    contrast: float = Field(ge=.9, le=1.15)
    saturation: float = Field(ge=.85, le=1.15)
    highlightCompression: float = Field(ge=0, le=.35)
    shadowLift: float = Field(ge=0, le=.2)
    lutPreset: Literal["none", "clean_warm", "cool_contrast", "neutral_social"]
    nonDestructive: Literal[True] = True
    confidence: float = Field(ge=0, le=1)


class GraphicsPackage(BaseModel):
    schemaVersion: int = 1
    platform: PlatformPreset
    brandTemplate: BrandTemplate
    templateCatalog: dict[str, dict]
    events: list[GraphicEvent]
    phraseBoundaries: list[float]
    pictureTimingChanged: Literal[False] = False
    audioChanged: Literal[False] = False
    excludedDepartments: list[str]


class CaptionPackage(BaseModel):
    schemaVersion: int = 1
    groups: list[CaptionGroup]
    generatedFrom: Literal["automatic_transcript_pipeline"] = "automatic_transcript_pipeline"
    timingProvenance: list[str]
    evidenceDecisions: list[CaptionEvidenceDecision]
    overlapsDetected: int = 0
    pictureTimingChanged: Literal[False] = False


class ColorPackage(BaseModel):
    schemaVersion: int = 1
    instructions: list[ColorInstruction]
    normalizationTarget: dict
    lutPreset: str
    nonDestructive: Literal[True] = True
    pictureTimingChanged: Literal[False] = False


def _overlap(a: NormalizedRegion, b: NormalizedRegion) -> float:
    x = max(0., min(a.x + a.width, b.x + b.width) - max(a.x, b.x))
    y = max(0., min(a.y + a.height, b.y + b.height) - max(a.y, b.y))
    return x * y


def _subject_region(composition: CompositionMetrics | None) -> NormalizedRegion | None:
    if not composition or composition.measurementSource != "detected_bbox":
        return None
    crop = composition.safeCrop.cropBox
    if not crop:
        return None
    # The measured safe crop is a conservative protected proxy for subject/action.
    return NormalizedRegion(**crop.model_dump())


def _choose_lane(preset: PlatformPreset, protected: NormalizedRegion | None,
                 *, caption: bool = False) -> tuple[NormalizedRegion, float]:
    safe = preset.safeTitle
    candidates = ([
        NormalizedRegion(x=safe.x + .04, y=safe.y + safe.height - .20,
                         width=safe.width - .08, height=.14),
        NormalizedRegion(x=safe.x + .04, y=safe.y + .04,
                         width=safe.width - .08, height=.14),
    ] if caption else [
        NormalizedRegion(x=safe.x, y=safe.y, width=safe.width, height=.11),
        NormalizedRegion(x=safe.x, y=safe.y + safe.height - .12,
                         width=safe.width, height=.11),
        NormalizedRegion(x=safe.x, y=safe.y + .18, width=.36, height=.12),
    ])
    if protected is None:
        # Unknown subject geometry: reserve center and use the outermost safe lane.
        return candidates[0], .25
    chosen = min(candidates, key=lambda item: _overlap(item, protected))
    risk = _overlap(chosen, protected) / (chosen.width * chosen.height)
    return chosen, round(min(1., risk), 4)


def _phrases(completed: CompletedAudioMix, duration: float) -> list[float]:
    offset = completed.targetVsActual.musicSourceStartSeconds
    points = [round(point - offset, 3) for point in completed.analysis.phraseBoundaries
              if 0 <= point - offset <= duration]
    return sorted(set([0., *points, round(duration, 3)]))


def _snap(value: float, phrases: list[float], tolerance: float = .45) -> tuple[float, bool]:
    closest = min(phrases, key=lambda point: abs(point - value))
    return (closest, True) if abs(closest - value) <= tolerance else (value, False)


def build_graphics_package(
    treatment: CreativeTreatment, candidate: PictureCandidateSummary,
    completed: CompletedAudioMix, composition: dict[str, CompositionMetrics],
    segments: list[Segment] | None = None,
    *, aspect: Literal["9:16", "1:1", "16:9"] = "9:16",
    template: BrandTemplate | None = None,
) -> GraphicsPackage:
    preset = PRESETS[aspect]
    template = template or BrandTemplate(templateId="stromation-social-v1",
                                         name="Stromation Social")
    clips = candidate.timeline["tracks"][0]["clips"]
    segment_by_id = {item.segmentId: item for item in (segments or [])}
    duration = candidate.durationSeconds
    phrases = _phrases(completed, duration)
    events: list[GraphicEvent] = []
    density_step = 2 if treatment.motionGraphicsDensity == "low" else 1
    for index, clip in enumerate(clips):
        if index % density_step:
            continue
        start, aligned = _snap(float(clip["timelineStart"]), phrases)
        end = min(float(clip["timelineEnd"]), start + (2.6 if index else 3.2))
        if end - start < .6:
            continue
        comp = composition.get(clip.get("segmentId", ""))
        region, risk = _choose_lane(preset, _subject_region(comp))
        if comp and comp.measurementSource == "detected_bbox" and risk > .2:
            # No measured clear lane: omission is safer than covering the subject.
            continue
        segment = segment_by_id.get(clip.get("segmentId"))
        clip_start, clip_end = float(clip["timelineStart"]), float(clip["timelineEnd"])
        if index == 0 and clip_end - clip_start >= 1.6:
            events.append(GraphicEvent(
                eventId="graphic-intro", kind="intro", text=template.name[:48],
                startSeconds=round(clip_start, 3), endSeconds=round(clip_start + 1.05, 3),
                region=region, animation="scale", phraseAligned=clip_start in phrases,
                subjectOcclusionRisk=risk,
            ))
            start = max(start, clip_start + 1.1)
            end = min(end, clip_end - .15)
        evidence_text = " ".join(filter(None, [
            segment.action if segment else "",
            segment.transcript if segment else "",
        ]))
        rep_match = re.search(r"\b(\d{1,3})\s*(?:reps?|x)\b", evidence_text, re.I)
        timed = bool(re.search(r"\b(?:hold|timer|interval|seconds?|secs?)\b", evidence_text, re.I))
        if index == 0:
            kind, text = "animated_title", treatment.purpose
        elif rep_match:
            kind, text = "rep_counter", f"{rep_match.group(1)} REPS"
        elif timed:
            kind, text = "timer", f"{clip_end - clip_start:.0f} SEC"
        elif segment and set(segment.storyUses) & {"hook", "peak", "completion"}:
            kind, text = "callout", segment.action or segment.storyUses[0]
        elif segment and segment.action:
            kind, text = "exercise_label", segment.action
        elif segment and segment.subjects:
            kind, text = "lower_third", segment.subjects[0]
        else:
            kind = "section_header"
            text = clip.get("beatName") or clip.get("segmentId") or f"SECTION {index + 1}"
        if template.titleCase == "upper":
            text = text.replace("_", " ").upper()
        if end - start >= .6:
            events.append(GraphicEvent(
                eventId=f"graphic-{index + 1}", kind=kind, text=text[:48],
                startSeconds=round(start, 3), endSeconds=round(end, 3), region=region,
                animation="slide" if index else "scale", phraseAligned=aligned,
                subjectOcclusionRisk=risk,
            ))
        if index == len(clips) - 1 and clip_end - clip_start >= 1.4:
            outro_start, outro_aligned = _snap(max(clip_start, clip_end - 1.25), phrases)
            events.append(GraphicEvent(
                eventId="graphic-outro", kind="outro",
                text=treatment.endingIntent[:48].upper(),
                startSeconds=round(outro_start, 3), endSeconds=round(clip_end, 3),
                region=region, animation="fade", phraseAligned=outro_aligned,
                subjectOcclusionRisk=risk,
            ))
    # Progress is a low-density persistent finishing element, not a picture edit.
    progress_region = NormalizedRegion(x=preset.safeTitle.x,
                                       y=preset.safeTitle.y + preset.safeTitle.height + .025,
                                       width=preset.safeTitle.width, height=.018)
    events.append(GraphicEvent(
        eventId="graphic-progress", kind="progress_bar", text="PROGRESS",
        startSeconds=0, endSeconds=round(duration, 3), region=progress_region,
        animation="fill", phraseAligned=True, subjectOcclusionRisk=0,
    ))
    return GraphicsPackage(
        platform=preset, brandTemplate=template, events=events,
        templateCatalog={
            "animated_title": {"animation": "scale", "evidenceRule": "opening hook"},
            "lower_third": {"animation": "slide", "evidenceRule": "named speaker/subject"},
            "callout": {"animation": "fade", "evidenceRule": "supported key fact or beat"},
            "exercise_label": {"animation": "slide", "evidenceRule": "catalog action label"},
            "section_header": {"animation": "slide", "evidenceRule": "story beat boundary"},
            "progress_bar": {"animation": "fill", "evidenceRule": "locked picture duration"},
            "rep_counter": {"animation": "scale", "evidenceRule": "measured/detected repetitions"},
            "timer": {"animation": "fade", "evidenceRule": "supported timed interval"},
            "intro": {"animation": "scale", "evidenceRule": "brand intro requested"},
            "outro": {"animation": "fade", "evidenceRule": "clean ending/payoff"},
        },
        phraseBoundaries=phrases,
        excludedDepartments=["specialized_critics", "tournament_selection"],
    )


def _clip_words(clip: dict, segment: Segment, artifact: TranscriptArtifact | None) -> list[CaptionWord]:
    source_start, source_end = float(clip["sourceStart"]), float(clip["sourceEnd"])
    timeline_start = float(clip["timelineStart"])
    exact = []
    if artifact:
        for word in artifact.words:
            if word.end > source_start and word.start < source_end:
                exact.append(CaptionWord(
                    text=word.word, startSeconds=max(timeline_start, timeline_start + word.start - source_start),
                    endSeconds=min(float(clip["timelineEnd"]), timeline_start + word.end - source_start),
                ))
    if exact:
        return [word for word in exact if word.endSeconds > word.startSeconds]
    words = re.findall(r"[\w'’-]+", segment.transcript or "")
    if not words:
        return []
    duration = float(clip["timelineEnd"]) - timeline_start
    step = duration / len(words)
    return [CaptionWord(text=word, startSeconds=round(timeline_start + index * step, 3),
                        endSeconds=round(timeline_start + (index + 1) * step, 3))
            for index, word in enumerate(words)]


def build_caption_package(
    candidate: PictureCandidateSummary, segments: list[Segment],
    graphics: GraphicsPackage, transcripts: dict[str, TranscriptArtifact] | None = None,
    natural_audio_events: list[NaturalAudioEvent] | None = None,
) -> CaptionPackage:
    segment_by_id = {item.segmentId: item for item in segments}
    transcripts = transcripts or {}
    event_by_clip = {item.clipId: item for item in (natural_audio_events or [])}
    groups: list[CaptionGroup] = []
    decisions: list[CaptionEvidenceDecision] = []
    sources: set[str] = set()
    for clip in candidate.timeline["tracks"][0]["clips"]:
        segment = segment_by_id.get(clip.get("segmentId"))
        if not segment:
            continue
        event = event_by_clip.get(clip["id"])
        classification = event.classification if event else None
        transcript = (segment.transcript or "").strip()
        action = segment.action.lower()
        problems = {item.lower() for item in segment.problems}
        narration_marker = any(term in action for term in (
            "narrat", "voiceover", "voice over",
        ))
        background_context = any(term in action for term in (
            "background", "off camera", "incidental", "crowd", "room chatter",
        ))
        speech_marker = not background_context and (
            narration_marker or any(term in action for term in (
                "speak", "talk", "dialogue", "interview", "to camera",
                "explains", "coach says",
            ))
        )
        non_speech_marker = any(term in action for term in (
            "impact", "strike", "land", "hit", "breath", "recovery", "effort",
            "grunt", "clap", "slam",
        )) and not speech_marker
        supported_speech = (
            segment.audioScore >= .5
            and segment.semanticRelevance >= .6
            and speech_marker
            and not non_speech_marker
        )

        reason: str | None = None
        explanation = ""
        if not transcript:
            reason = "non_speech_natural_audio" if classification in {"effort", "impact"} else "no_transcript"
            explanation = (
                "Effort/impact audio remains available to the mix but contains no captionable speech"
                if reason == "non_speech_natural_audio"
                else "No transcript evidence exists for this selected clip"
            )
        elif (classification == "unusable" or segment.audioScore < .35
              or problems.intersection({"unusable_audio", "operator_unusable"})):
            reason = "unusable_audio"
            explanation = "Milestone 3 or source-audio evidence marks this speech as unusable"
        elif classification in {"effort", "impact"} or non_speech_marker:
            reason = "non_speech_natural_audio"
            explanation = "Effort or impact audio belongs in the mix, not in captions"
        elif classification == "background_chatter" and not supported_speech:
            reason = "milestone3_background_chatter"
            explanation = "Milestone 3 chatter lacks sufficient intentional-speech evidence"

        if reason:
            decisions.append(CaptionEvidenceDecision(
                clipId=clip["id"], segmentId=segment.segmentId, decision="excluded",
                reasonCode=reason, milestone3Classification=classification,
                audioScore=segment.audioScore,
                semanticRelevance=segment.semanticRelevance, explanation=explanation,
            ))
            continue
        artifact = transcripts.get(segment.assetId)
        words = _clip_words(clip, segment, artifact)
        source = "transcript_word_timestamps" if artifact and artifact.words else "segment_distributed"
        reason = (
            "meaningful_narration_supported"
            if narration_marker else "meaningful_dialogue_supported"
        )
        decisions.append(CaptionEvidenceDecision(
            clipId=clip["id"], segmentId=segment.segmentId, decision="included",
            reasonCode=reason, milestone3Classification=classification,
            audioScore=segment.audioScore, semanticRelevance=segment.semanticRelevance,
            timingSource=source,
            explanation=(
                "Usable audio, semantic relevance, and explicit dialogue evidence support captions"
                if not narration_marker else
                "Usable audio, semantic relevance, and explicit narration evidence support captions"
            ),
        ))
        sources.add(source)
        for offset in range(0, len(words), 4):
            batch = words[offset:offset + 4]
            text = " ".join(word.text for word in batch)
            while len(text) > 42 and len(batch) > 1:
                batch = batch[:-1]
                text = " ".join(word.text for word in batch)
            region, _ = _choose_lane(graphics.platform, None, caption=True)
            # Captions own the lower lane; move conflicting graphics to top.
            for event in graphics.events:
                time_overlap = event.startSeconds < batch[-1].endSeconds and event.endSeconds > batch[0].startSeconds
                if time_overlap and _overlap(event.region, region) > 0:
                    event.captionCollision = True
                    event.region, event.subjectOcclusionRisk = _choose_lane(graphics.platform, None)
                    event.captionCollision = False
            groups.append(CaptionGroup(
                groupId=f"caption-{len(groups) + 1}", words=batch, text=text,
                startSeconds=batch[0].startSeconds, endSeconds=batch[-1].endSeconds,
                region=region, highlightWordIndexes=list(range(len(batch))),
                timingSource=source,
            ))
    groups.sort(key=lambda item: item.startSeconds)
    for left, right in zip(groups, groups[1:], strict=False):
        if right.startSeconds < left.endSeconds:
            right.startSeconds = left.endSeconds
            right.words[0].startSeconds = max(right.words[0].startSeconds, left.endSeconds)
            if right.endSeconds <= right.startSeconds:
                raise VisualFinishingError("caption timing cannot be resolved without overlap")
    return CaptionPackage(
        groups=groups, timingProvenance=sorted(sources), evidenceDecisions=decisions,
        overlapsDetected=0,
    )


def build_color_package(candidate: PictureCandidateSummary, segments: list[Segment],
                        *, lut: Literal["none", "clean_warm", "cool_contrast",
                                               "neutral_social"] = "none") -> ColorPackage:
    segment_by_id = {item.segmentId: item for item in segments}
    selected = [segment_by_id.get(clip.get("segmentId"))
                for clip in candidate.timeline["tracks"][0]["clips"]]
    measured = [item.exposureScore for item in selected if item]
    target = statistics.median(measured) if measured else .75
    instructions = []
    for clip, segment in zip(candidate.timeline["tracks"][0]["clips"], selected, strict=True):
        score = segment.exposureScore if segment else target
        delta = max(-1., min(1., (target - score) * 1.2))
        instructions.append(ColorInstruction(
            clipId=clip["id"], segmentId=clip.get("segmentId", "unknown"),
            startSeconds=float(clip["timelineStart"]),
            endSeconds=float(clip["timelineEnd"]),
            exposureEv=round(delta, 3), temperatureShift=.04 if lut == "clean_warm" else 0,
            contrast=1.06 if lut in {"clean_warm", "cool_contrast"} else 1,
            saturation=1.04 if lut in {"clean_warm", "neutral_social"} else 1,
            highlightCompression=round(max(0., score - .82) * .5, 3),
            shadowLift=round(max(0., .42 - score) * .25, 3), lutPreset=lut,
            confidence=.75 if segment else .2,
        ))
    return ColorPackage(
        instructions=instructions, lutPreset=lut,
        normalizationTarget={"method": "selected_clip_median_exposure_v1",
                             "exposureScore": round(target, 3),
                             "whiteBalance": "bounded_neutral_or_named_preset",
                             "highlightShadowProtection": True},
    )


def _escape_drawtext(text: str) -> str:
    return _ff_escape(text)


def render_finishing_preview(input_path: str, output_path: str,
                             graphics: GraphicsPackage, captions: CaptionPackage,
                             color: ColorPackage) -> dict:
    """Render fixed-schema finishing instructions; audio is stream-copied."""
    preset = graphics.platform
    filters = [
        f"scale={preset.width}:{preset.height}:force_original_aspect_ratio=decrease",
        f"pad={preset.width}:{preset.height}:(ow-iw)/2:(oh-ih)/2:black",
    ]
    for instruction in color.instructions:
        enable = f"between(t,{instruction.startSeconds:.3f},{instruction.endSeconds:.3f})"
        filters.append(
            f"eq=brightness={instruction.exposureEv * .08:.4f}:"
            f"contrast={instruction.contrast:.4f}:saturation={instruction.saturation:.4f}:"
            f"enable='{enable}'"
        )
        if instruction.temperatureShift:
            shift = instruction.temperatureShift
            filters.append(
                f"colorbalance=rs={shift:.4f}:bs={-shift:.4f}:enable='{enable}'"
            )
    font = _escape_drawtext(_default_font())
    for event in graphics.events:
        r = event.region
        x, y, w, h = int(r.x * preset.width), int(r.y * preset.height), int(r.width * preset.width), int(r.height * preset.height)
        enable = f"between(t,{event.startSeconds:.3f},{event.endSeconds:.3f})"
        if event.kind == "progress_bar":
            filters.append(f"drawbox=x={x}:y={y}:w='min({w},{w}*t/{event.endSeconds:.3f})':h={max(4, h)}:color=0x00E5FF@0.9:t=fill:enable='{enable}'")
            continue
        text = _escape_drawtext(event.text)
        filters.append(f"drawbox=x={x}:y={y}:w={w}:h={h}:color=0x101820@0.72:t=fill:enable='{enable}'")
        filters.append(f"drawtext=fontfile='{font}':text='{text}':fontcolor=white:fontsize={max(22, int(h * .36))}:x={x + int(w * .04)}:y={y + int(h * .30)}:enable='{enable}'")
    for group in captions.groups:
        r = group.region
        x, y, w, h = int(r.x * preset.width), int(r.y * preset.height), int(r.width * preset.width), int(r.height * preset.height)
        enable = f"between(t,{group.startSeconds:.3f},{group.endSeconds:.3f})"
        filters.append(f"drawbox=x={x}:y={y}:w={w}:h={h}:color=black@0.68:t=fill:enable='{enable}'")
        filters.append(f"drawtext=fontfile='{font}':text='{_escape_drawtext(group.text.upper())}':fontcolor=white:fontsize={max(24, int(h * .28))}:x=(w-text_w)/2:y={y + int(h * .34)}:enable='{enable}'")
        for word in group.words:
            word_enable = f"between(t,{word.startSeconds:.3f},{word.endSeconds:.3f})"
            filters.append(f"drawtext=fontfile='{font}':text='{_escape_drawtext(word.text.upper())}':fontcolor=0x00E5FF:box=1:boxcolor=black@0.8:fontsize={max(24, int(h * .28))}:x=(w-text_w)/2:y={y + int(h * .34)}:enable='{word_enable}'")
    command = [FFMPEG, "-y", "-loglevel", "error", "-i", input_path, "-vf", ",".join(filters),
               "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "libx264", "-preset", "veryfast",
               "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart", output_path]
    try:
        subprocess.run(command, check=True, capture_output=True, timeout=240)
        probe = subprocess.run([FFPROBE, "-v", "error", "-print_format", "json",
                                "-show_streams", "-show_format", output_path],
                               check=True, capture_output=True, timeout=30)
        data = json.loads(probe.stdout)
    except (subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        detail = getattr(exc, "stderr", b"")
        raise VisualFinishingError(f"finishing preview render failed: {detail[-500:]!r}") from exc
    streams = data.get("streams", [])
    return {"durationSeconds": round(float(data["format"]["duration"]), 3),
            "videoStreamPresent": any(item.get("codec_type") == "video" for item in streams),
            "audioStreamPresent": any(item.get("codec_type") == "audio" for item in streams),
            "width": preset.width, "height": preset.height,
            "pictureTimingChanged": False, "audioChanged": False,
            "graphicsEvents": len(graphics.events), "captionGroups": len(captions.groups)}
