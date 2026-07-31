"""Milestone 14: conversational editing.

NL command -> {intent, scope (incl. protected ranges), proposed operations} via a
structured-output LLM -> validated by the deterministic op engine BEFORE anything
mutates. The project is only changed when validation passes and (in the app
flow) the user accepts the preview.
"""
from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod

from pydantic import BaseModel, Field, ValidationError

from ..timeline_ops import OpError, apply_operations, parse_operations
from . import gemini_common

NL_MODEL = os.environ.get("GEMINI_NL_MODEL", "gemini-2.5-flash")


class CommandPlan(BaseModel):
    intent: str
    scopeDescription: str = ""
    protectedRanges: list[list[float]] = []     # [[startSec, endSec], ...]
    operations: list[dict] = []
    clarificationNeeded: str = ""


class CommandProvider(ABC):
    @abstractmethod
    def plan(self, command: str, timeline: dict) -> CommandPlan: ...


_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "intent": {"type": "STRING"},
        "scopeDescription": {"type": "STRING"},
        "protectedRanges": {"type": "ARRAY", "items": {
            "type": "ARRAY", "items": {"type": "NUMBER"}}},
        "operations": {"type": "ARRAY", "items": {
            "type": "OBJECT",
            "properties": {
                "op": {"type": "STRING", "enum": [
                    "insert_clip", "replace_clip", "move_clip", "trim_clip",
                    "split_clip", "delete_clip", "change_speed", "change_volume",
                    "add_title", "add_caption", "duck_music"]},
                "clipId": {"type": "STRING"}, "trackId": {"type": "STRING"},
                "index": {"type": "INTEGER"}, "newIndex": {"type": "INTEGER"},
                "assetId": {"type": "STRING"},
                "sourceStart": {"type": "NUMBER"}, "sourceEnd": {"type": "NUMBER"},
                "atSourceTime": {"type": "NUMBER"},
                "speed": {"type": "NUMBER"}, "volume": {"type": "NUMBER"},
                "gainDb": {"type": "NUMBER"},
                "text": {"type": "STRING"},
                "timelineStart": {"type": "NUMBER"}, "timelineEnd": {"type": "NUMBER"},
                "durationSeconds": {"type": "NUMBER"},
                "fontSize": {"type": "INTEGER"}, "position": {"type": "STRING"},
                "comment": {"type": "STRING"},
            },
            "required": ["op"],
        }},
        "clarificationNeeded": {"type": "STRING"},
    },
    "required": ["intent", "operations"],
}


class GeminiCommandProvider(CommandProvider):
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY not configured")

    def plan(self, command: str, timeline: dict) -> CommandPlan:
        tl_summary = json.dumps({
            "duration": timeline.get("duration"),
            "clips": [{"id": c["id"], "beat": c.get("meta", {}).get("beat"),
                       "timeline": [c["timelineStart"], c["timelineEnd"]],
                       "source": [c["sourceStart"], c["sourceEnd"]],
                       "speed": c.get("speed", 1), "assetId": c["assetId"]}
                      for t in timeline["tracks"] if t["type"] == "video"
                      for c in t["clips"]],
            "text": [{"text": c["text"], "range": [c["timelineStart"], c["timelineEnd"]]}
                     for t in timeline["tracks"] if t["type"] == "text"
                     for c in t["clips"]],
        }, indent=1)
        prompt = (
            "You convert a video-editing request into structured timeline "
            "operations. Current timeline:\n" + tl_summary + "\n\n"
            f"User request: \"{command}\"\n\n"
            "Rules: use ONLY the allowed operations; reference existing clipIds; "
            "if the user asks to keep part of the video unchanged, include that "
            "part in protectedRanges (timeline seconds) and do not touch clips "
            "inside it; if the request is ambiguous or impossible, return zero "
            "operations and set clarificationNeeded.")
        raw = gemini_common.generate_json(NL_MODEL, [{"text": prompt}], _SCHEMA,
                                          self.api_key)
        try:
            return CommandPlan(**raw)
        except ValidationError as e:
            raise RuntimeError(f"command provider returned invalid plan: "
                               f"{e.errors()[:2]}")


def execute_command(command: str, timeline: dict,
                    provider: CommandProvider) -> dict:
    """Full conversational flow up to (not including) user acceptance."""
    plan = provider.plan(command, timeline)
    if plan.clarificationNeeded and not plan.operations:
        return {"status": "needs_clarification",
                "question": plan.clarificationNeeded, "plan": plan.model_dump()}
    try:
        ops = parse_operations(plan.operations)
        protected = [tuple(r) for r in plan.protectedRanges if len(r) == 2]
        result = apply_operations(timeline, ops, actor="user",
                                  protected=protected)
    except OpError as e:
        return {"status": "rejected", "error": str(e), "plan": plan.model_dump()}
    return {"status": "proposed", "plan": plan.model_dump(),
            "applied": result.applied, "rejectedOps": result.rejected,
            "timeline": result.timeline}


def get_command_provider() -> CommandProvider | None:
    if os.environ.get("GEMINI_API_KEY"):
        return GeminiCommandProvider()
    return None
