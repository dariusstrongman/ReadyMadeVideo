"""Constrained timeline-operation engine (Milestone 10/13/14 backbone).

AI systems NEVER execute FFmpeg or mutate timelines directly — they emit
operations from the allowed set below. This engine validates and applies them
deterministically. Every application produces a NEW timeline version with an
attribution record; the previous version is untouched (undo = restore_version).
"""
from __future__ import annotations

import copy
from typing import Literal, Union

from pydantic import BaseModel, Field, ValidationError

from .product_editor import _sync_audio_from

Actor = Literal["user", "editor_agent", "critic", "revision_agent", "system_rule"]


class OpBase(BaseModel):
    comment: str = ""


class InsertClip(OpBase):
    op: Literal["insert_clip"]
    trackId: str = "video-1"
    index: int = Field(ge=0)              # position within the track's clip list
    clipId: str
    assetId: str
    sourceStart: float = Field(ge=0)
    sourceEnd: float
    volume: float = 1.0
    speed: float = Field(default=1.0, gt=0.24, lt=4.01)


class ReplaceClip(OpBase):
    op: Literal["replace_clip"]
    clipId: str
    assetId: str
    sourceStart: float = Field(ge=0)
    sourceEnd: float


class MoveClip(OpBase):
    op: Literal["move_clip"]
    clipId: str
    newIndex: int = Field(ge=0)


class TrimClip(OpBase):
    op: Literal["trim_clip"]
    clipId: str
    sourceStart: float | None = None
    sourceEnd: float | None = None


class SplitClip(OpBase):
    op: Literal["split_clip"]
    clipId: str
    atSourceTime: float


class DeleteClip(OpBase):
    op: Literal["delete_clip"]
    clipId: str


class ChangeSpeed(OpBase):
    op: Literal["change_speed"]
    clipId: str
    speed: float = Field(gt=0.24, lt=4.01)


class ChangeVolume(OpBase):
    op: Literal["change_volume"]
    clipId: str
    volume: float = Field(ge=0, le=2)


class AddTitle(OpBase):
    op: Literal["add_title"]
    text: str = Field(min_length=1, max_length=200)
    durationSeconds: float = Field(default=2.0, gt=0.3, le=10)
    fontSize: int = Field(default=72, ge=12, le=300)
    position: Literal["center", "top", "bottom"] = "center"


class AddCaption(OpBase):
    op: Literal["add_caption"]
    text: str = Field(min_length=1, max_length=200)
    timelineStart: float = Field(ge=0)
    timelineEnd: float
    position: Literal["center", "top", "bottom"] = "bottom"
    fontSize: int = Field(default=48, ge=12, le=200)


class DuckMusic(OpBase):
    op: Literal["duck_music"]
    gainDb: float = Field(ge=-30, le=0)


Operation = Union[InsertClip, ReplaceClip, MoveClip, TrimClip, SplitClip,
                  DeleteClip, ChangeSpeed, ChangeVolume, AddTitle, AddCaption,
                  DuckMusic]


class OpResult(BaseModel):
    applied: list[dict]
    rejected: list[dict]        # {op, error}
    timeline: dict
    actor: Actor


class OpError(Exception):
    pass


def _lock_donor_duration(c: dict) -> None:
    """Phase 3 audio-under: keep the donor audio window exactly as long as
    the clip's output duration, in donor time. Called after any op that
    changes the picture's output length without moving its source range
    (speed change, wholesale replace) — otherwise the a/v concat drifts."""
    donor = c.get("audioFrom")
    if not isinstance(donor, dict):
        return
    out_dur = (float(c["sourceEnd"]) - float(c["sourceStart"])) \
        / float(c.get("speed") or 1.0)
    d_speed = float(donor.get("speed") or 1.0)
    donor["sourceEnd"] = round(
        float(donor.get("sourceStart") or 0.0) + out_dur * d_speed, 3)


def _find_clip(tl: dict, clip_id: str):
    for track in tl["tracks"]:
        if track["type"] != "video":
            continue
        for i, c in enumerate(track["clips"]):
            if c["id"] == clip_id:
                return track, i, c
    raise OpError(f"clip {clip_id} not found")


def _recompute_timeline_positions(tl: dict) -> None:
    """Video clips are sequential (v1 model): recompute timelineStart/End."""
    title_offset = 0.0
    for track in tl["tracks"]:
        if track["type"] == "text":
            for c in track["clips"]:
                if c.get("timelineStart", 0) == 0 and c.get("role") == "title_card":
                    title_offset = max(title_offset, c["timelineEnd"])
    t = title_offset
    for track in tl["tracks"]:
        if track["type"] != "video":
            continue
        for c in track["clips"]:
            dur = (c["sourceEnd"] - c["sourceStart"]) / c.get("speed", 1.0)
            c["timelineStart"] = round(t, 3)
            c["timelineEnd"] = round(t + dur, 3)
            t = c["timelineEnd"]
    tl["duration"] = round(t, 3)


def _op_targets_protected(op, tl: dict,
                          protected: list[tuple[float, float]]) -> str:
    """An op may not TARGET a clip/range inside a protected timeline range
    (checked against pre-op positions). Global reflow of later clips caused by
    earlier edits is expected and allowed — 'unchanged' means the same content,
    not frozen timestamps."""
    if not protected:
        return ""

    def overlaps(lo_hi):
        lo, hi = lo_hi
        return lambda a, b: a < hi and b > lo

    target_range = None
    clip_id = getattr(op, "clipId", None)
    if clip_id:
        try:
            _, _, c = _find_clip(tl, clip_id)
            target_range = (c.get("timelineStart", 0), c.get("timelineEnd", 0))
        except OpError:
            return ""  # missing clip handled by the op itself
    elif isinstance(op, AddCaption):
        target_range = (op.timelineStart, op.timelineEnd)
    elif isinstance(op, InsertClip):
        # inserting shifts everything after the insertion point; find neighbor
        for track in tl["tracks"]:
            if track["id"] == op.trackId and track["type"] == "video":
                if op.index < len(track["clips"]):
                    c = track["clips"][op.index]
                    target_range = (c.get("timelineStart", 0),
                                    c.get("timelineStart", 0))
    if target_range is None:
        return ""  # global ops (title/duck) are allowed
    for (lo, hi) in protected:
        if target_range[0] < hi and target_range[1] > lo:
            return (f"operation {getattr(op, 'op', '?')} targets protected "
                    f"range {lo}-{hi}s")
    return ""


def parse_operations(raw_ops: list[dict]) -> list[Operation]:
    """Validate raw op dicts against the allowed set; raises OpError."""
    parsed: list[Operation] = []
    for i, raw in enumerate(raw_ops):
        matched = None
        for cls in (InsertClip, ReplaceClip, MoveClip, TrimClip, SplitClip,
                    DeleteClip, ChangeSpeed, ChangeVolume, AddTitle, AddCaption,
                    DuckMusic):
            if raw.get("op") == cls.model_fields["op"].annotation.__args__[0]:
                try:
                    matched = cls(**raw)
                except ValidationError as e:
                    raise OpError(f"op[{i}] {raw.get('op')}: {e.errors()[:1]}")
                break
        if matched is None:
            raise OpError(f"op[{i}]: '{raw.get('op')}' is not an allowed operation")
        parsed.append(matched)
    return parsed


def apply_operations(timeline: dict, ops: list[Operation], actor: Actor,
                     protected: list[tuple[float, float]] | None = None) -> OpResult:
    tl = copy.deepcopy(timeline)
    applied, rejected = [], []

    for op in ops:
        try:
            violation = _op_targets_protected(op, tl, protected or [])
            if violation:
                raise OpError(violation)
            if isinstance(op, InsertClip):
                track = next((t for t in tl["tracks"]
                              if t["id"] == op.trackId and t["type"] == "video"), None)
                if track is None:
                    raise OpError(f"video track {op.trackId} not found")
                if op.sourceEnd <= op.sourceStart:
                    raise OpError("sourceEnd must exceed sourceStart")
                track["clips"].insert(min(op.index, len(track["clips"])), {
                    "id": op.clipId, "assetId": op.assetId,
                    "sourceStart": op.sourceStart, "sourceEnd": op.sourceEnd,
                    "timelineStart": 0, "timelineEnd": 0,
                    "volume": op.volume, "speed": op.speed})
            elif isinstance(op, ReplaceClip):
                _, i, c = _find_clip(tl, op.clipId)
                if op.sourceEnd <= op.sourceStart:
                    raise OpError("sourceEnd must exceed sourceStart")
                c.update({"assetId": op.assetId, "sourceStart": op.sourceStart,
                          "sourceEnd": op.sourceEnd})
                _lock_donor_duration(c)
            elif isinstance(op, MoveClip):
                track, i, c = _find_clip(tl, op.clipId)
                track["clips"].pop(i)
                track["clips"].insert(min(op.newIndex, len(track["clips"])), c)
            elif isinstance(op, TrimClip):
                _, _, c = _find_clip(tl, op.clipId)
                old_start = c["sourceStart"]
                s = op.sourceStart if op.sourceStart is not None else c["sourceStart"]
                e = op.sourceEnd if op.sourceEnd is not None else c["sourceEnd"]
                if e <= s or s < 0:
                    raise OpError("invalid trim range")
                c["sourceStart"], c["sourceEnd"] = s, e
                _sync_audio_from(c, old_start, s, e)
            elif isinstance(op, SplitClip):
                track, i, c = _find_clip(tl, op.clipId)
                if not (c["sourceStart"] < op.atSourceTime < c["sourceEnd"]):
                    raise OpError("split point outside clip source range")
                old_start = c["sourceStart"]
                right = copy.deepcopy(c)
                right["id"] = c["id"] + "_b"
                right["sourceStart"] = op.atSourceTime
                c["sourceEnd"] = op.atSourceTime
                _sync_audio_from(c, old_start, old_start, op.atSourceTime)
                _sync_audio_from(right, old_start, op.atSourceTime,
                                 right["sourceEnd"])
                track["clips"].insert(i + 1, right)
            elif isinstance(op, DeleteClip):
                track, i, _ = _find_clip(tl, op.clipId)
                track["clips"].pop(i)
                if not any(t["type"] == "video" and t["clips"] for t in tl["tracks"]):
                    raise OpError("cannot delete the last video clip")
            elif isinstance(op, ChangeSpeed):
                _, _, c = _find_clip(tl, op.clipId)
                c["speed"] = op.speed
                _lock_donor_duration(c)
            elif isinstance(op, ChangeVolume):
                _, _, c = _find_clip(tl, op.clipId)
                c["volume"] = op.volume
            elif isinstance(op, AddTitle):
                text_track = next((t for t in tl["tracks"] if t["type"] == "text"), None)
                if text_track is None:
                    text_track = {"id": "text-1", "type": "text", "clips": []}
                    tl["tracks"].append(text_track)
                text_track["clips"] = [c for c in text_track["clips"]
                                       if c.get("role") != "title_card"]
                text_track["clips"].insert(0, {
                    "id": "title-1", "role": "title_card", "text": op.text,
                    "timelineStart": 0, "timelineEnd": op.durationSeconds,
                    "fontSize": op.fontSize, "position": op.position})
            elif isinstance(op, AddCaption):
                if op.timelineEnd <= op.timelineStart:
                    raise OpError("caption end must exceed start")
                text_track = next((t for t in tl["tracks"] if t["type"] == "text"), None)
                if text_track is None:
                    text_track = {"id": "text-1", "type": "text", "clips": []}
                    tl["tracks"].append(text_track)
                text_track["clips"].append({
                    "id": f"cap-{len(text_track['clips'])}", "role": "caption",
                    "text": op.text, "timelineStart": op.timelineStart,
                    "timelineEnd": op.timelineEnd, "fontSize": op.fontSize,
                    "position": op.position})
            elif isinstance(op, DuckMusic):
                tl.setdefault("music", {})["duckDb"] = op.gainDb
            applied.append(op.model_dump())
        except OpError as e:
            rejected.append({"op": op.model_dump(), "error": str(e)})

    _recompute_timeline_positions(tl)
    # a protected-range violation invalidates the whole batch
    for r in rejected:
        if "protected" in r["error"]:
            raise OpError(r["error"])
    return OpResult(applied=applied, rejected=rejected, timeline=tl, actor=actor)
