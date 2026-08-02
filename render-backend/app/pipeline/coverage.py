"""P7: rules-based footage-coverage validator.

Reviews the segment catalog against the fitness capture checklist and reports
LIKELY missing categories. Never invents footage — absence is reported honestly.
"""
from __future__ import annotations

from pydantic import BaseModel

from .schemas import Segment

CATEGORIES = [
    ("establishing", "Establishing location shot"),
    ("preparation", "Preparation / gearing up"),
    ("detail", "Close-up details (shoes, hands, equipment)"),
    ("wide_action", "Wide action shots"),
    ("medium_action", "Medium action shots"),
    ("closeup_effort", "Close-ups showing effort"),
    ("tracking", "Tracking / follow shots"),
    ("peak", "Peak-effort moment"),
    ("completion", "Completion moment"),
    ("natural_audio", "Clean natural audio (breathing, footsteps)"),
    ("reflection", "Reflection / payoff footage"),
    ("drone", "Drone footage (optional)"),
    ("narration", "Spoken narration (optional)"),
]
OPTIONAL = {"drone", "narration"}


class CoverageItem(BaseModel):
    category: str
    label: str
    present: bool
    optional: bool
    matchingSegments: list[str]
    note: str = ""


class CoverageReport(BaseModel):
    items: list[CoverageItem]
    missingRequired: list[str]
    warnings: list[str]
    segmentCount: int
    usableSegmentCount: int


def _matches(cat: str, s: Segment) -> bool:
    shot = s.shotType.lower()
    move = s.cameraMovement.lower()
    uses = set(s.storyUses)
    action = s.action.lower()
    if cat == "establishing":
        return "location" in uses or ("wide" in shot and s.motionIntensity < 0.3)
    if cat == "preparation":
        return "location" in uses and s.motionIntensity < 0.5 or "prepar" in action
    if cat == "detail":
        return "close" in shot and s.motionIntensity < 0.6
    if cat == "wide_action":
        return "wide" in shot and s.motionIntensity >= 0.4
    if cat == "medium_action":
        return "medium" in shot and s.motionIntensity >= 0.4
    if cat == "closeup_effort":
        return "close" in shot and s.motionIntensity >= 0.5
    if cat == "tracking":
        return any(k in move for k in ("track", "follow", "handheld"))
    if cat == "peak":
        return "peak" in uses or s.motionIntensity >= 0.75
    if cat == "completion":
        return "completion" in uses
    if cat == "natural_audio":
        return s.audioScore >= 0.5 and not s.transcript
    if cat == "reflection":
        return "reflection" in uses or ("broll" in uses and s.motionIntensity < 0.3)
    if cat == "drone":
        return "drone" in move or "aerial" in shot
    if cat == "narration":
        return bool(s.transcript)
    return False


def validate_coverage(segments: list[Segment]) -> CoverageReport:
    usable = [s for s in segments
              if not ({"mostly_black", "mostly_frozen"} & set(s.problems))]
    items: list[CoverageItem] = []
    for cat, label in CATEGORIES:
        matches = [s.segmentId for s in usable if _matches(cat, s)]
        items.append(CoverageItem(category=cat, label=label,
                                  present=bool(matches),
                                  optional=cat in OPTIONAL,
                                  matchingSegments=matches[:8]))
    missing = [i.label for i in items if not i.present and not i.optional]

    warnings: list[str] = []
    wides = [s for s in usable if "wide" in s.shotType.lower()]
    if len(usable) >= 6 and len(wides) / max(1, len(usable)) > 0.6:
        warnings.append("Too many similar wide shots — add mediums and close-ups")
    dup_groups = {s.duplicateGroupId for s in usable if s.duplicateGroupId}
    if dup_groups and len(usable) - len(dup_groups) < len(usable) * 0.5:
        warnings.append("Many near-duplicate segments — vary angles and moments")
    if not any(s.audioScore >= 0.5 for s in usable):
        warnings.append("No clean natural-audio segment found")
    return CoverageReport(items=items, missingRequired=missing, warnings=warnings,
                          segmentCount=len(segments),
                          usableSegmentCount=len(usable))
