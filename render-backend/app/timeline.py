"""Timeline JSON model + validation.

Version-1 timeline contract (matches the spec example). The v1 RENDERER
additionally supports exactly one video clip and at most one text (title) clip:
title card first, then the trimmed footage. Validation enforces the general
contract; renderer-specific constraints are checked in plan_render().
"""
from __future__ import annotations

from typing import Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator, model_validator


class VideoClip(BaseModel):
    id: str
    assetId: str
    sourceStart: float = Field(ge=0)
    sourceEnd: float = Field(gt=0)
    timelineStart: float = Field(ge=0)
    timelineEnd: float = Field(gt=0)
    volume: float = Field(default=1.0, ge=0, le=2)

    @model_validator(mode="after")
    def _ranges(self):
        if self.sourceEnd <= self.sourceStart:
            raise ValueError("sourceEnd must be greater than sourceStart")
        if self.timelineEnd <= self.timelineStart:
            raise ValueError("timelineEnd must be greater than timelineStart")
        return self


class TextClip(BaseModel):
    id: str
    text: str = Field(min_length=1, max_length=200)
    timelineStart: float = Field(ge=0)
    timelineEnd: float = Field(gt=0)
    fontSize: int = Field(default=72, ge=12, le=300)
    position: Literal["center", "top", "bottom"] = "center"

    @model_validator(mode="after")
    def _ranges(self):
        if self.timelineEnd <= self.timelineStart:
            raise ValueError("timelineEnd must be greater than timelineStart")
        return self


class VideoTrack(BaseModel):
    id: str
    type: Literal["video"]
    clips: list[VideoClip]


class TextTrack(BaseModel):
    id: str
    type: Literal["text"]
    clips: list[TextClip]


Track = Union[VideoTrack, TextTrack]


class Timeline(BaseModel):
    version: Literal[1]
    width: int = Field(ge=16, le=7680)
    height: int = Field(ge=16, le=4320)
    fps: int = Field(ge=1, le=120)
    duration: float = Field(gt=0, le=3600)
    tracks: list[Track] = Field(min_length=1)

    @field_validator("tracks")
    @classmethod
    def _at_least_one_video_clip(cls, v):
        vclips = [c for t in v if t.type == "video" for c in t.clips]
        if not vclips:
            raise ValueError("timeline must contain at least one video clip")
        return v


class RenderPlan(BaseModel):
    """What the v1 renderer will actually do, derived from a valid Timeline."""
    width: int
    height: int
    fps: int
    asset_id: str
    source_start: float
    source_end: float
    title_text: Optional[str] = None
    title_duration: float = 0.0
    title_font_size: int = 72
    title_position: str = "center"


def plan_render(tl: Timeline) -> RenderPlan:
    """Map a validated Timeline onto the v1 renderer's capabilities.

    v1 limitations (documented): one video clip (the first clip of the first
    video track) and an optional single title text clip which is rendered as a
    leading title card. Complex multi-clip placement is rejected explicitly
    rather than silently mis-rendered.
    """
    vclips = [c for t in tl.tracks if t.type == "video" for c in t.clips]
    tclips = [c for t in tl.tracks if t.type == "text" for c in t.clips]
    if len(vclips) != 1:
        raise ValueError(f"v1 renderer supports exactly one video clip (got {len(vclips)})")
    if len(tclips) > 1:
        raise ValueError(f"v1 renderer supports at most one title clip (got {len(tclips)})")
    v = vclips[0]
    plan = RenderPlan(
        width=tl.width, height=tl.height, fps=tl.fps,
        asset_id=v.assetId, source_start=v.sourceStart, source_end=v.sourceEnd,
    )
    if tclips:
        t = tclips[0]
        plan.title_text = t.text
        plan.title_duration = max(0.5, min(10.0, t.timelineEnd - t.timelineStart))
        plan.title_font_size = t.fontSize
        plan.title_position = t.position
    return plan
