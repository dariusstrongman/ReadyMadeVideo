"""Milestone 8: story planner.

Receives the brief + target + segment CATALOG (never raw video) + a configurable
editorial template and emits a schema-validated StoryBlueprint. The v1 planner is
deterministic (template-driven allocation adjusted to the available footage);
an LLM-refined planner can implement the same output schema later.
"""
from __future__ import annotations

import json
import os
from typing import Optional

from pydantic import BaseModel, Field

from .schemas import Segment

TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")


class BeatPlan(BaseModel):
    key: str
    title: str
    order: int
    targetSeconds: float = Field(gt=0)
    required: bool
    wants: dict = {}
    clipSeconds: float          # suggested per-clip length for this beat
    notes: str = ""


class StoryBlueprint(BaseModel):
    schemaVersion: int = 1
    templateId: str
    brief: str
    platform: str = "vertical"          # vertical (9:16) | horizontal (16:9)
    targetDuration: float
    beats: list[BeatPlan]
    emotionalTrajectory: list[str] = []
    audioDirection: dict = {}
    pacingNotes: str = ""


def load_template(template_id: str = "fitness_v1") -> dict:
    with open(os.path.join(TEMPLATE_DIR, f"{template_id}.json"), encoding="utf-8") as f:
        return json.load(f)


def plan_story(brief: str,
               segments: list[Segment],
               target_duration: Optional[float] = None,
               platform: str = "vertical",
               template_id: str = "fitness_v1") -> StoryBlueprint:
    tpl = load_template(template_id)
    total_available = sum(s.sourceEnd - s.sourceStart for s in segments)

    target = float(target_duration or tpl["targetDuration"]["default"])
    target = max(tpl["targetDuration"]["min"] * tpl.get("shortFormFactor", 0.3),
                 min(target, tpl["targetDuration"]["max"]))
    # never plan longer than ~70% of available usable footage
    if total_available > 0:
        target = min(target, max(8.0, total_available * 0.7))

    pacing = tpl["pacing"]
    beats: list[BeatPlan] = []
    # drop optional beats when footage variety is scarce (fewer segments than beats)
    tpl_beats = tpl["beats"]
    if len(segments) < len(tpl_beats):
        keep = [b for b in tpl_beats if b.get("required")]
        optional = [b for b in tpl_beats if not b.get("required")]
        while len(keep) < min(len(tpl_beats), max(2, len(segments))) and optional:
            keep.append(optional.pop(0))
        keep.sort(key=lambda b: tpl_beats.index(b))
        tpl_beats = keep

    share_total = sum(b["share"] for b in tpl_beats)
    for i, b in enumerate(tpl_beats):
        secs = round(target * b["share"] / share_total, 2)
        clip_len = (pacing["fastClipSeconds"] if b["key"] in pacing["fasterBeats"]
                    else min(pacing["maxClipSeconds"], max(pacing["minClipSeconds"], secs)))
        beats.append(BeatPlan(key=b["key"], title=b["title"], order=i,
                              targetSeconds=max(pacing["minClipSeconds"], secs),
                              required=bool(b.get("required")),
                              wants=b.get("wants", {}),
                              clipSeconds=clip_len,
                              notes=b.get("notes", "")))

    return StoryBlueprint(
        templateId=tpl["templateId"], brief=brief, platform=platform,
        targetDuration=round(sum(b.targetSeconds for b in beats), 2),
        beats=beats,
        emotionalTrajectory=[b.key for b in beats],
        audioDirection=tpl.get("audio", {}),
        pacingNotes=f"faster cuts on {', '.join(pacing['fasterBeats'])}; "
                    f"clips {pacing['minClipSeconds']}-{pacing['maxClipSeconds']}s")
