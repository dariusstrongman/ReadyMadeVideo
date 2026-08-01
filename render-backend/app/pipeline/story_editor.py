"""Coverage-aware story variants for supported short-form fitness edits."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .capture_quality import CaptureQualityReport
from .schemas import Segment

VariantId = Literal[
    "action_first",
    "build_and_payoff",
    "raw_intense",
    "cinematic",
    "social_retention",
]


class FootageRequirement(BaseModel):
    storyUsesAny: list[str] = Field(default_factory=list)
    shotSizesAny: list[str] = Field(default_factory=list)
    minimumMotion: float | None = Field(default=None, ge=0, le=1)
    cleanNaturalAudio: bool = False
    trackingMovement: bool = False


class StoryVariantBeat(BaseModel):
    name: str
    targetSeconds: float = Field(gt=0)
    requiredFootageProperties: FootageRequirement
    energyLevel: float = Field(ge=0, le=1)
    audioIntent: str
    graphicIntent: str
    transitionIntent: str
    endingRequirement: str | None = None


class StoryVariant(BaseModel):
    schemaVersion: int = 1
    variantId: VariantId
    label: str
    valid: bool
    rejectionReasons: list[str] = Field(default_factory=list)
    beats: list[StoryVariantBeat]
    structuralSignature: str
    editorialIntent: str

    @model_validator(mode="after")
    def valid_state_matches_rejections(self):
        if self.valid == bool(self.rejectionReasons):
            raise ValueError("valid variants cannot have rejection reasons")
        if not self.beats:
            raise ValueError("story variant requires at least one beat")
        return self


class StoryVariantSet(BaseModel):
    schemaVersion: int = 1
    targetDurationSeconds: float = Field(ge=15, le=60)
    variants: list[StoryVariant]
    validVariantIds: list[VariantId]
    rejectedVariantIds: list[VariantId]

    @model_validator(mode="after")
    def five_materially_distinct_variants(self):
        if len(self.variants) < 5:
            raise ValueError("at least five story variants are required")
        ids = {v.variantId for v in self.variants}
        signatures = {v.structuralSignature for v in self.variants}
        if len(ids) != len(self.variants) or len(signatures) != len(self.variants):
            raise ValueError("story variants must be materially distinct")
        return self


def _beat(
    name: str,
    share: float,
    energy: float,
    requirements: FootageRequirement,
    audio: str,
    graphic: str = "none",
    transition: str = "hard cut",
    ending: str | None = None,
) -> dict:
    return {
        "name": name,
        "share": share,
        "energy": energy,
        "requirements": requirements,
        "audio": audio,
        "graphic": graphic,
        "transition": transition,
        "ending": ending,
    }


def _definitions() -> list[dict]:
    return [
        {
            "id": "action_first",
            "label": "Action-first",
            "intent": "Open on decisive movement, then earn a short recovery payoff.",
            "beats": [
                _beat("immediate action hook", 0.18, 0.92,
                      FootageRequirement(storyUsesAny=["hook", "peak"], minimumMotion=0.55),
                      "impact-led natural sound"),
                _beat("challenge context", 0.20, 0.48,
                      FootageRequirement(storyUsesAny=["location", "early_effort"]),
                      "music establishes pulse"),
                _beat("effort acceleration", 0.32, 0.78,
                      FootageRequirement(storyUsesAny=["build", "early_effort"], minimumMotion=0.35),
                      "music builds; preserve useful impacts"),
                _beat("completion payoff", 0.30, 0.35,
                      FootageRequirement(storyUsesAny=["completion", "reflection"]),
                      "natural audio briefly leads", ending="visible completion or recovery"),
            ],
        },
        {
            "id": "build_and_payoff",
            "label": "Build-and-payoff",
            "intent": "Establish the challenge, escalate effort, peak, then resolve.",
            "beats": [
                _beat("preparation", 0.18, 0.24,
                      FootageRequirement(storyUsesAny=["location", "early_effort"]),
                      "restrained musical intro"),
                _beat("work begins", 0.20, 0.44,
                      FootageRequirement(storyUsesAny=["early_effort"]),
                      "steady rhythm"),
                _beat("intensity build", 0.25, 0.70,
                      FootageRequirement(storyUsesAny=["build"], minimumMotion=0.4),
                      "music rises by phrase"),
                _beat("peak action", 0.20, 1.0,
                      FootageRequirement(storyUsesAny=["peak"], minimumMotion=0.55),
                      "drop aligned with strongest action"),
                _beat("earned recovery", 0.17, 0.28,
                      FootageRequirement(storyUsesAny=["completion", "reflection"]),
                      "resolve music under breathing", ending="accomplishment is visually legible"),
            ],
        },
        {
            "id": "raw_intense",
            "label": "Raw/intense",
            "intent": "Favor authentic exertion and natural sound over polish.",
            "beats": [
                _beat("breath or impact cold open", 0.16, 0.88,
                      FootageRequirement(storyUsesAny=["hook", "peak"], cleanNaturalAudio=True),
                      "natural sound leads"),
                _beat("sustained work", 0.42, 0.74,
                      FootageRequirement(storyUsesAny=["early_effort", "build"], minimumMotion=0.35),
                      "sparse music beneath effort"),
                _beat("hardest repetition", 0.25, 1.0,
                      FootageRequirement(storyUsesAny=["peak"], minimumMotion=0.55,
                                         cleanNaturalAudio=True),
                      "full impact and breathing"),
                _beat("exhaustion", 0.17, 0.20,
                      FootageRequirement(storyUsesAny=["completion", "reflection"]),
                      "music recedes", ending="authentic recovery moment"),
            ],
        },
        {
            "id": "cinematic",
            "label": "Cinematic",
            "intent": "Use place, detail, scale changes and a controlled emotional release.",
            "beats": [
                _beat("visual promise", 0.12, 0.68,
                      FootageRequirement(storyUsesAny=["hook"], shotSizesAny=["close", "medium"]),
                      "atmospheric opening"),
                _beat("place and detail", 0.22, 0.25,
                      FootageRequirement(storyUsesAny=["location", "broll"],
                                         shotSizesAny=["wide", "close"]),
                      "texture and ambience", transition="motivated dissolve or hard cut"),
                _beat("movement through space", 0.22, 0.55,
                      FootageRequirement(storyUsesAny=["early_effort", "build"],
                                         trackingMovement=True),
                      "music expands"),
                _beat("peak detail", 0.27, 0.96,
                      FootageRequirement(storyUsesAny=["peak"],
                                         shotSizesAny=["close", "medium"], minimumMotion=0.5),
                      "musical and visual climax"),
                _beat("controlled release", 0.17, 0.22,
                      FootageRequirement(storyUsesAny=["completion", "reflection"]),
                      "clean musical resolution", ending="recovery image held long enough to read"),
            ],
        },
        {
            "id": "social_retention",
            "label": "Social-retention optimized",
            "intent": "Create immediate clarity, frequent visual resets and a loopable payoff.",
            "beats": [
                _beat("first-frame result promise", 0.10, 0.95,
                      FootageRequirement(storyUsesAny=["hook", "peak"], minimumMotion=0.55),
                      "recognizable transient", graphic="optional one-line identity"),
                _beat("context reset", 0.14, 0.42,
                      FootageRequirement(storyUsesAny=["location", "early_effort"]),
                      "beat establishes pace"),
                _beat("rapid contrast sequence", 0.30, 0.72,
                      FootageRequirement(storyUsesAny=["early_effort", "build"],
                                         shotSizesAny=["wide", "medium", "close"]),
                      "phrase-aware visual resets"),
                _beat("payoff preview", 0.16, 0.84,
                      FootageRequirement(storyUsesAny=["peak"], minimumMotion=0.5),
                      "brief pre-drop tension"),
                _beat("full payoff", 0.20, 1.0,
                      FootageRequirement(storyUsesAny=["peak", "completion"]),
                      "drop and impact"),
                _beat("loopable resolve", 0.10, 0.35,
                      FootageRequirement(storyUsesAny=["completion", "reflection"]),
                      "short resolved tail", graphic="optional minimal end mark",
                      ending="ending can return cleanly to the opening motion"),
            ],
        },
    ]


def _matches(segment: Segment, requirement: FootageRequirement) -> bool:
    if {"mostly_black", "mostly_frozen", "operator_unusable"} & set(segment.problems):
        return False
    if requirement.storyUsesAny and not set(requirement.storyUsesAny) & set(segment.storyUses):
        return False
    if requirement.shotSizesAny and not any(
        size in segment.shotType.lower() for size in requirement.shotSizesAny
    ):
        return False
    if requirement.minimumMotion is not None \
            and segment.motionIntensity < requirement.minimumMotion:
        return False
    if requirement.cleanNaturalAudio and (segment.audioScore < 0.6 or segment.transcript):
        return False
    if requirement.trackingMovement and not any(
        word in segment.cameraMovement.lower() for word in ("track", "follow", "gimbal")
    ):
        return False
    return True


def generate_story_variants(
    segments: list[Segment],
    capture_report: CaptureQualityReport,
    target_duration_seconds: float | None = None,
) -> StoryVariantSet:
    target = float(target_duration_seconds or capture_report.recommendedTargetDuration)
    target = max(15.0, min(60.0, target))
    variants: list[StoryVariant] = []
    for definition in _definitions():
        beats: list[StoryVariantBeat] = []
        rejected: list[str] = []
        for raw in definition["beats"]:
            requirement = raw["requirements"]
            matches = [s.segmentId for s in segments if _matches(s, requirement)]
            if not matches:
                rejected.append(f"{raw['name']}: unsupported by available coverage")
            beats.append(StoryVariantBeat(
                name=raw["name"],
                targetSeconds=round(target * raw["share"], 2),
                requiredFootageProperties=requirement,
                energyLevel=raw["energy"],
                audioIntent=raw["audio"],
                graphicIntent=raw["graphic"],
                transitionIntent=raw["transition"],
                endingRequirement=raw["ending"],
            ))
        signature = " > ".join(beat.name for beat in beats)
        variants.append(StoryVariant(
            variantId=definition["id"],
            label=definition["label"],
            valid=not rejected,
            rejectionReasons=rejected,
            beats=beats,
            structuralSignature=signature,
            editorialIntent=definition["intent"],
        ))
    return StoryVariantSet(
        targetDurationSeconds=target,
        variants=variants,
        validVariantIds=[v.variantId for v in variants if v.valid],
        rejectedVariantIds=[v.variantId for v in variants if not v.valid],
    )
