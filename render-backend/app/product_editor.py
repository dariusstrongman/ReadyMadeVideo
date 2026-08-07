"""Canonical Product Editor document and deterministic operation engine.

The browser and conversational assistant submit the same typed operations.  No
operation contains filesystem paths or executable instructions; persisted
documents are immutable snapshots and the operation log is append-only.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Annotated, Literal, Union
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, TypeAdapter, field_validator, model_validator

MAX_OPERATIONS_PER_REQUEST = 50
MAX_TRACK_ITEMS = 500
MAX_DURATION_SECONDS = 3600.0


class EditorError(ValueError):
    pass


class EditorDocument(BaseModel):
    schemaVersion: Literal[1] = 1
    projectId: UUID
    candidateRunId: UUID
    width: int = Field(ge=16, le=7680)
    height: int = Field(ge=16, le=4320)
    # float: real footage runs at fractional rates (23.976, 29.97). Declaring
    # int made editor/start reject every V2 candidate cut from NTSC-rate
    # sources while the engine itself handled them fine.
    fps: float = Field(ge=1, le=120)
    duration: float = Field(gt=0, le=MAX_DURATION_SECONDS)
    tracks: list[dict]
    sourceAssetIds: list[UUID] = Field(min_length=1)
    attribution: list[dict] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_tracks(self):
        allowed = {"picture", "captions", "music", "sfx", "graphics"}
        types = [track.get("type") for track in self.tracks]
        if set(types) != allowed or len(types) != len(allowed):
            raise ValueError("document requires one picture, captions, music, sfx, and graphics track")
        if sum(len(track.get("items", [])) for track in self.tracks) > MAX_TRACK_ITEMS:
            raise ValueError("document exceeds track item limit")
        pictures = next(track for track in self.tracks if track["type"] == "picture")["items"]
        if not pictures:
            raise ValueError("document requires at least one picture clip")
        ancestry = {str(value) for value in self.sourceAssetIds}
        if {str(item.get("assetId")) for item in pictures} - ancestry:
            raise ValueError("picture clip is outside source asset ancestry")
        # audio-under donors must obey the same ancestry rule — a forged
        # donor assetId otherwise persisted an immutable timeline that only
        # failed at export time (render-side ownership checks still stand)
        donors = {str((item.get("audioFrom") or {}).get("assetId"))
                  for item in pictures
                  if isinstance(item.get("audioFrom"), dict)
                  and (item.get("audioFrom") or {}).get("assetId")}
        if donors - ancestry:
            raise ValueError("audio-under donor is outside source asset "
                             "ancestry")
        return self


Actor = Literal["user", "ai"]


class OperationBase(BaseModel):
    operationId: UUID = Field(default_factory=uuid4)
    actor: Actor
    targetId: str = Field(min_length=1, max_length=160)
    baseVersion: int = Field(ge=1)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    proposalId: UUID | None = None


class ReorderClip(OperationBase):
    type: Literal["reorder_clip"]
    toIndex: int = Field(ge=0)


class TrimClip(OperationBase):
    type: Literal["trim_clip"]
    sourceStart: float = Field(ge=0)
    sourceEnd: float = Field(gt=0)


class SplitClip(OperationBase):
    type: Literal["split_clip"]
    sourceTime: float = Field(gt=0)


class DeleteClip(OperationBase):
    type: Literal["delete_clip"]


class RestoreClip(OperationBase):
    type: Literal["restore_clip"]
    clip: dict
    toIndex: int = Field(ge=0)


class UpdateCaption(OperationBase):
    type: Literal["update_caption"]
    text: str = Field(min_length=1, max_length=240)


class SetMusicGain(OperationBase):
    type: Literal["set_music_gain"]
    gainDb: float = Field(ge=-60, le=6)


class ToggleGraphic(OperationBase):
    type: Literal["toggle_graphic"]
    enabled: bool


EditorOperation = Annotated[
    Union[ReorderClip, TrimClip, SplitClip, DeleteClip, RestoreClip, UpdateCaption,
          SetMusicGain, ToggleGraphic],
    Field(discriminator="type"),
]
OPERATION_ADAPTER = TypeAdapter(EditorOperation)


class OperationBatch(BaseModel):
    expectedVersion: int = Field(ge=1)
    operations: list[EditorOperation] = Field(min_length=1, max_length=MAX_OPERATIONS_PER_REQUEST)

    @field_validator("operations")
    @classmethod
    def one_base_version(cls, operations):
        if operations and len({item.baseVersion for item in operations}) != 1:
            raise ValueError("all operations must target one base version")
        return operations


def _track(document: dict, kind: str) -> dict:
    return next(item for item in document["tracks"] if item["type"] == kind)


def _find(document: dict, kind: str, target_id: str) -> tuple[list[dict], int, dict]:
    items = _track(document, kind)["items"]
    for index, item in enumerate(items):
        if item.get("id") == target_id:
            return items, index, item
    raise EditorError(f"{kind} item {target_id} not found")


def _reflow(document: dict) -> None:
    cursor = 0.0
    for clip in _track(document, "picture")["items"]:
        duration = (float(clip["sourceEnd"]) - float(clip["sourceStart"])) / float(
            clip.get("speed", 1.0)
        )
        clip["timelineStart"] = round(cursor, 3)
        cursor += duration
        clip["timelineEnd"] = round(cursor, 3)
    document["duration"] = round(cursor, 3)


def apply_operation(document: dict, operation: EditorOperation) -> dict:
    result = copy.deepcopy(document)
    if isinstance(operation, ReorderClip):
        items, index, clip = _find(result, "picture", operation.targetId)
        items.pop(index)
        items.insert(min(operation.toIndex, len(items)), clip)
    elif isinstance(operation, TrimClip):
        _, _, clip = _find(result, "picture", operation.targetId)
        if operation.sourceEnd <= operation.sourceStart:
            raise EditorError("trim end must exceed trim start")
        asset_duration = float(clip.get("assetDuration", clip["sourceEnd"]))
        if operation.sourceEnd > asset_duration:
            raise EditorError("trim exceeds source asset duration")
        old_start = float(clip["sourceStart"])
        clip["sourceStart"], clip["sourceEnd"] = operation.sourceStart, operation.sourceEnd
        _sync_audio_from(clip, old_start,
                         operation.sourceStart, operation.sourceEnd)
    elif isinstance(operation, SplitClip):
        items, index, clip = _find(result, "picture", operation.targetId)
        if not float(clip["sourceStart"]) < operation.sourceTime < float(clip["sourceEnd"]):
            raise EditorError("split point is outside the clip")
        right = copy.deepcopy(clip)
        right["id"] = f"{clip['id']}-split-{str(operation.operationId)[:8]}"
        old_start = float(clip["sourceStart"])
        right["sourceStart"] = operation.sourceTime
        clip["sourceEnd"] = operation.sourceTime
        _sync_audio_from(clip, old_start, old_start, operation.sourceTime)
        _sync_audio_from(right, old_start, operation.sourceTime,
                         float(right["sourceEnd"]))
        items.insert(index + 1, right)
    elif isinstance(operation, DeleteClip):
        items, index, _ = _find(result, "picture", operation.targetId)
        if len(items) == 1:
            raise EditorError("cannot delete the last picture clip")
        items.pop(index)
    elif isinstance(operation, RestoreClip):
        items = _track(result, "picture")["items"]
        clip = copy.deepcopy(operation.clip)
        if clip.get("id") != operation.targetId:
            raise EditorError("restored clip identity does not match target")
        if any(item.get("id") == operation.targetId for item in items):
            raise EditorError("restored clip already exists")
        if str(clip.get("assetId")) not in {str(value) for value in result["sourceAssetIds"]}:
            raise EditorError("restored clip is outside source asset ancestry")
        source_start = float(clip.get("sourceStart", -1))
        source_end = float(clip.get("sourceEnd", -1))
        asset_duration = float(clip.get("assetDuration", source_end))
        if source_start < 0 or source_end <= source_start or source_end > asset_duration:
            raise EditorError("restored clip has invalid source bounds")
        items.insert(min(operation.toIndex, len(items)), clip)
    elif isinstance(operation, UpdateCaption):
        _, _, caption = _find(result, "captions", operation.targetId)
        caption["text"] = operation.text
    elif isinstance(operation, SetMusicGain):
        _, _, music = _find(result, "music", operation.targetId)
        music["gainDb"] = operation.gainDb
    elif isinstance(operation, ToggleGraphic):
        _, _, graphic = _find(result, "graphics", operation.targetId)
        graphic["enabled"] = operation.enabled
    else:  # pragma: no cover - discriminated validation prevents this
        raise EditorError("unsupported operation")
    _reflow(result)
    return EditorDocument(**result).model_dump(mode="json")


def apply_batch(document: dict, operations: list[EditorOperation]) -> dict:
    result = document
    for operation in operations:
        result = apply_operation(result, operation)
    return result


def document_from_candidate(project_id: str, candidate: dict,
                            asset_durations: dict[str, float]) -> dict:
    manifest = candidate.get("manifest") or {}
    timeline = manifest.get("pictureTimeline") or {}
    picture = [copy.deepcopy(clip) for track in timeline.get("tracks", [])
               if track.get("type") == "video" for clip in track.get("clips", [])]
    for clip in picture:
        clip["assetDuration"] = asset_durations.get(str(clip["assetId"]), clip["sourceEnd"])
    captions = [copy.deepcopy(item) for item in
                (manifest.get("captions", {}).get("groups") or [])]
    for index, item in enumerate(captions):
        item.setdefault("id", f"caption-{index + 1}")
        item.setdefault("text", item.get("displayText") or item.get("words") or "Caption")
    graphics = [copy.deepcopy(item) for item in
                (manifest.get("graphics", {}).get("events") or [])]
    for index, item in enumerate(graphics):
        item.setdefault("id", f"graphic-{index + 1}")
        item.setdefault("enabled", True)
    # Music track reflects real ancestry: a completed audio mix (M4) yields the
    # lockable music-main item; a bridged/no-music candidate yields an honest empty
    # track (no fabricated music). EditorDocument permits an empty music track.
    if candidate.get("audio_mix_run_id"):
        music = [{"id": "music-main", "gainDb": -12.0,
                  "source": "completed_audio_mix", "lockedAncestry": True}]
    else:
        music = []
    selection = manifest.get("musicAssetSelection")
    attribution = []
    if isinstance(selection, dict):
        evidence = selection.get("attribution") or {}
        required = bool(selection.get("attributionRequired"))
        attribution = [{
            "assetId": selection.get("assetId"),
            "required": required,
            # Phase 1 does not render attribution. Never accept satisfaction from
            # candidate/client JSON; a future trusted renderer must persist it.
            "rendered": False,
            "status": "requires_attribution" if required else "not_required",
            "sourceUrl": evidence.get("sourceUrl") or selection.get("sourceUrl"),
            "creator": evidence.get("creator") or selection.get("creatorName"),
            "title": evidence.get("title") or selection.get("title"),
            "license": evidence.get("license") or selection.get("licenseName"),
            "licenseUrl": evidence.get("licenseUrl") or selection.get("licenseUrl"),
            "attributionText": evidence.get("text") or selection.get("attributionText"),
        }]
    doc = {
        "schemaVersion": 1, "projectId": project_id,
        "candidateRunId": candidate["id"],
        "width": timeline.get("width", 1080), "height": timeline.get("height", 1920),
        "fps": timeline.get("fps", 30), "duration": timeline.get("duration", 1),
        "tracks": [
            {"id": "picture", "type": "picture", "items": picture},
            {"id": "captions", "type": "captions", "items": captions},
            {"id": "music", "type": "music", "items": music},
            {"id": "sfx", "type": "sfx", "items": []},
            {"id": "graphics", "type": "graphics", "items": graphics},
        ],
        "sourceAssetIds": manifest.get("sourceAssetIds") or [],
        "attribution": attribution,
    }
    _reflow(doc)
    return EditorDocument(**doc).model_dump(mode="json")


def _sync_audio_from(clip: dict, old_start: float, new_start: float,
                     new_end: float) -> None:
    """Phase 3 audio-under b-roll: a clip carrying `audioFrom` plays the
    DONOR clip's audio under its own picture. When the picture range moves
    (trim/split), the donor window must move in lockstep or the export's
    speech desyncs from what the customer saw. Donor time advances at
    donor.speed per PICTURE-timeline second; picture timeline seconds are
    picture-source seconds / clip speed."""
    donor = clip.get("audioFrom")
    if not isinstance(donor, dict):
        return
    clip_speed = float(clip.get("speed") or 1.0) or 1.0
    d_speed = float(donor.get("speed") or 1.0)
    rate = d_speed / clip_speed
    base = float(donor.get("sourceStart") or 0.0)
    donor["sourceStart"] = round(base + (new_start - old_start) * rate, 3)
    donor["sourceEnd"] = round(base + (new_end - old_start) * rate, 3)


def renderer_timeline(document: dict) -> dict:
    picture = copy.deepcopy(_track(document, "picture")["items"])
    text = []
    for caption in _track(document, "captions")["items"]:
        text.append({"id": caption["id"], "role": "caption",
                     "text": caption.get("text") or caption.get("displayText", ""),
                     "timelineStart": caption.get("startSeconds", caption.get("timelineStart", 0)),
                     "timelineEnd": caption.get("endSeconds", caption.get("timelineEnd", 1)),
                     "fontSize": 48, "position": "bottom"})
    return {"version": 1, "width": document["width"], "height": document["height"],
            "fps": document["fps"], "duration": document["duration"],
            "tracks": [{"id": "video-1", "type": "video", "clips": picture},
                       {"id": "text-1", "type": "text", "clips": text}]}


def translate_revision(prompt: str, document: dict, base_version: int,
                       actor: Actor = "ai", proposal_id: UUID | None = None) -> list[dict]:
    """Deterministic, mockable Phase-1 translator; no provider call is made."""
    text = prompt.strip()
    lower = text.lower()
    if not text or len(text) > 500:
        raise EditorError("revision prompt must contain 1-500 characters")
    pictures = _track(document, "picture")["items"]
    captions = _track(document, "captions")["items"]
    if lower in {"remove first clip", "delete first clip"} and len(pictures) > 1:
        op: EditorOperation = DeleteClip(actor=actor, targetId=pictures[0]["id"],
                                         proposalId=proposal_id,
                                         baseVersion=base_version, type="delete_clip")
    elif lower.startswith("caption: ") and captions:
        op = UpdateCaption(actor=actor, targetId=captions[0]["id"], proposalId=proposal_id,
                           baseVersion=base_version, type="update_caption",
                           text=text.split(":", 1)[1].strip())
    elif lower in {"mute music", "turn music off"}:
        op = SetMusicGain(actor=actor, targetId="music-main", baseVersion=base_version,
                          proposalId=proposal_id,
                          type="set_music_gain", gainDb=-60)
    else:
        raise EditorError("request is not supported in Phase 1")
    return [op.model_dump(mode="json")]
