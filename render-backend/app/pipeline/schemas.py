"""Versioned schemas for pipeline artifacts and the canonical segment record.

SCHEMA_VERSION bumps when the canonical segment shape changes. All provider
output (Gemini, Whisper, ...) is validated through these models before storage —
free-form prose is never stored as analysis data.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# v3 (2026-08-07): narrative substrate — Segment gains speechSpans/wordTimings/
# speechFreeRanges/motionPeaks/stationaryRanges/composition/continuity/
# naturalSoundValue/shotSize. All optional with defaults: v2 rows load unchanged.
SCHEMA_VERSION = 3


# ---------- probe ----------
class StreamInfo(BaseModel):
    codec: str | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    sample_rate: int | None = None
    channels: int | None = None


class ProbeArtifact(BaseModel):
    duration: float
    size_bytes: int
    container: str | None = None
    video: StreamInfo | None = None
    audio: StreamInfo | None = None
    rotation: int = 0
    creation_time: str | None = None
    has_audio: bool = False


# ---------- scenes ----------
class SceneRange(BaseModel):
    index: int
    start: float = Field(ge=0)
    end: float


class ScenesArtifact(BaseModel):
    detector: str
    threshold: float
    scenes: list[SceneRange]


# ---------- mechanical (visual) ----------
class SceneMechanical(BaseModel):
    index: int
    start: float
    end: float
    blur_laplacian_var: float          # higher = sharper
    focus_score: float = Field(ge=0, le=1)
    exposure_mean: float               # 0-255 mean luma
    exposure_clipped_low: float        # fraction of near-black pixels
    exposure_clipped_high: float       # fraction of near-white pixels
    exposure_score: float = Field(ge=0, le=1)
    black_frame_fraction: float
    frozen_frame_fraction: float
    motion_energy_mean: float          # mean abs frame diff (0-255 scale)
    motion_energy_peak: float
    shake_score: float = Field(ge=0, le=1)   # 1 = stable
    phash: str                         # perceptual hash of representative frame
    duplicate_group: int | None = None


class MechanicalArtifact(BaseModel):
    sampled_fps: float
    scenes: list[SceneMechanical]


# ---------- audio ----------
class SilenceRange(BaseModel):
    start: float
    end: float


class AudioArtifact(BaseModel):
    has_audio: bool
    integrated_lufs: float | None = None
    true_peak_db: float | None = None
    clipped: bool = False
    silence_ranges: list[SilenceRange] = []
    speech_like: bool | None = None


# ---------- transcript ----------
class Word(BaseModel):
    word: str
    start: float
    end: float


class Sentence(BaseModel):
    text: str
    start: float
    end: float


class TranscriptArtifact(BaseModel):
    provider: str
    language: str | None = None
    text: str = ""
    words: list[Word] = []
    sentences: list[Sentence] = []


# ---------- semantic (VideoUnderstandingProvider output) ----------
StoryUse = Literal["hook", "location", "early_effort", "build", "peak",
                   "completion", "reflection", "broll", "transition"]


class SemanticSegment(BaseModel):
    scene_index: int
    subjects: list[str] = []
    action: str = ""
    shot_type: str = ""
    camera_angle: str = ""
    camera_movement: str = ""
    location: str = ""
    composition: str = ""
    emotion: str = ""
    emotional_intensity: float = Field(default=0, ge=0, le=1)
    physical_intensity: float = Field(default=0, ge=0, le=1)
    usable_start: float | None = None
    usable_end: float | None = None
    story_uses: list[StoryUse] = []
    continuity: str = ""
    problems: list[str] = []
    natural_sound_value: float = Field(default=0, ge=0, le=1)
    search_description: str = ""


class SemanticArtifact(BaseModel):
    provider: str
    model: str
    segments: list[SemanticSegment]


# ---------- motion ----------
class MotionScene(BaseModel):
    index: int
    start: float
    end: float
    motion_intensity: float = Field(ge=0, le=1)
    peak_moments: list[float] = []          # timestamps of local maxima
    stationary_ranges: list[list[float]] = []


class MotionArtifact(BaseModel):
    method: str                              # "opencv-framediff" | "mediapipe-pose"
    sampled_fps: float
    scenes: list[MotionScene]


# ---------- canonical segment (catalog) ----------
class SpeechSpan(BaseModel):
    """One sentence-aligned speech interval, in ABSOLUTE asset time (it may
    extend past the segment's own range — consumers clamp, the truth doesn't)."""
    start: float
    end: float
    text: str = ""


class Segment(BaseModel):
    schemaVersion: int = SCHEMA_VERSION
    segmentId: str
    assetId: str
    sourceStart: float
    sourceEnd: float
    subjects: list[str] = []
    action: str = ""
    shotType: str = ""
    cameraAngle: str = ""
    cameraMovement: str = ""
    location: str = ""
    transcript: str | None = None
    storyUses: list[str] = []
    emotion: str = ""
    motionIntensity: float = 0
    focusScore: float = 0
    exposureScore: float = 0
    stabilityScore: float = 0
    audioScore: float = 0
    semanticRelevance: float = 0
    duplicateGroupId: str | None = None
    problems: list[str] = []
    searchText: str = ""
    # ---- narrative substrate (schema v3, all optional — v2 rows load
    # unchanged; every field is DERIVED from stored artifacts, never invented)
    speechSpans: list[SpeechSpan] = []       # sentences overlapping the segment
    wordTimings: list[Word] = []             # word-level ASR timings, absolute
    speechFreeRanges: list[SpeechSpan] = []  # in-segment gaps with no speech
    motionPeaks: list[float] = []            # motion local maxima (timestamps)
    stationaryRanges: list[list[float]] = []
    composition: str = ""                    # from the semantic provider
    continuity: str = ""                     # clothing/lighting/environment
    naturalSoundValue: float = 0
    shotSize: str = ""                       # normalized: close|medium|wide|""
