"""Milestone 12: multimodal critic. Watches the PREVIEW render against the brief
and answers the fixed question set with structured, timestamped JSON only.
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod

from pydantic import BaseModel, Field, ValidationError

from . import gemini_common
from .planner import StoryBlueprint

CRITIC_MODEL = os.environ.get("GEMINI_CRITIC_MODEL", "gemini-2.5-flash")


class RevisionRequest(BaseModel):
    timelineStart: float = Field(ge=0)
    timelineEnd: float
    issue: str
    suggestion: str
    beatKey: str = ""
    severity: str = "minor"      # minor | major


class CriticVerdict(BaseModel):
    provider: str = ""
    hookStrong: bool
    storyUnderstandable: bool
    intensityBuilds: bool
    enoughShotVariety: bool
    importantActionsVisible: bool
    repetitiveClips: bool
    awkwardCuts: bool
    dialogueIntact: bool
    naturalSoundEffective: bool
    musicBalanced: bool
    endingPayoff: bool
    overallScore: float = Field(ge=0, le=1)
    summary: str = ""
    revisionRequests: list[RevisionRequest] = []


class CriticProvider(ABC):
    name = "abstract"

    @abstractmethod
    def critique(self, preview_path: str, blueprint: StoryBlueprint,
                 timeline: dict) -> CriticVerdict: ...


_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        **{k: {"type": "BOOLEAN"} for k in (
            "hookStrong", "storyUnderstandable", "intensityBuilds",
            "enoughShotVariety", "importantActionsVisible", "repetitiveClips",
            "awkwardCuts", "dialogueIntact", "naturalSoundEffective",
            "musicBalanced", "endingPayoff")},
        "overallScore": {"type": "NUMBER"},
        "summary": {"type": "STRING"},
        "revisionRequests": {"type": "ARRAY", "items": {
            "type": "OBJECT",
            "properties": {
                "timelineStart": {"type": "NUMBER"},
                "timelineEnd": {"type": "NUMBER"},
                "issue": {"type": "STRING"},
                "suggestion": {"type": "STRING"},
                "beatKey": {"type": "STRING"},
                "severity": {"type": "STRING", "enum": ["minor", "major"]},
            },
            "required": ["timelineStart", "timelineEnd", "issue", "suggestion"],
        }},
    },
    "required": ["hookStrong", "storyUnderstandable", "intensityBuilds",
                 "enoughShotVariety", "importantActionsVisible", "repetitiveClips",
                 "awkwardCuts", "dialogueIntact", "naturalSoundEffective",
                 "musicBalanced", "endingPayoff", "overallScore", "summary",
                 "revisionRequests"],
}


class GeminiCritic(CriticProvider):
    name = "gemini-critic"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY not configured")

    def critique(self, preview_path: str, blueprint: StoryBlueprint,
                 timeline: dict) -> CriticVerdict:
        file = gemini_common.upload_file(preview_path, self.api_key)
        try:
            return self._critique_uploaded(file, blueprint, timeline)
        finally:
            gemini_common.delete_file(file["name"], self.api_key)

    def _critique_uploaded(self, file: dict, blueprint: StoryBlueprint,
                           timeline: dict) -> CriticVerdict:
        beats = "\n".join(
            f"- {c['meta'].get('beat', '?')}: {c['timelineStart']:.1f}-"
            f"{c['timelineEnd']:.1f}s"
            for t in timeline["tracks"] if t["type"] == "video"
            for c in t["clips"])
        prompt = (
            "You are a demanding short-form video editor reviewing a FIRST CUT "
            f"against this brief: \"{blueprint.brief}\". Target platform: "
            f"{blueprint.platform}, target duration {blueprint.targetDuration:.0f}s.\n"
            f"The intended story beats and their timeline positions:\n{beats}\n\n"
            "Watch the video and answer every field honestly. For every concrete "
            "weakness add a revisionRequest with the EXACT timeline range in "
            "seconds, what is wrong, and a specific actionable suggestion "
            "(e.g. 'replace with a higher-motion shot', 'trim the first second', "
            "'this repeats the earlier shot'). Be specific, not polite.")
        raw = gemini_common.generate_json(
            CRITIC_MODEL,
            [{"file_data": {"file_uri": file["uri"], "mime_type": "video/mp4"}},
             {"text": prompt}],
            _SCHEMA, self.api_key)
        # normalize provider score drift: models sometimes answer 0-10 or 0-100
        s = raw.get("overallScore")
        if isinstance(s, (int, float)):
            if s > 1 and s <= 10:
                raw["overallScore"] = round(s / 10, 3)
            elif s > 10:
                raw["overallScore"] = round(min(1.0, s / 100), 3)
            raw["overallScore"] = max(0.0, min(1.0, raw["overallScore"]))
        try:
            v = CriticVerdict(**raw)
        except ValidationError as e:
            raise RuntimeError(f"critic returned invalid JSON: {e.errors()[:2]}")
        v.provider = f"{self.name}/{CRITIC_MODEL}"
        return v


def get_critic() -> CriticProvider | None:
    if os.environ.get("GEMINI_API_KEY"):
        return GeminiCritic()
    return None
