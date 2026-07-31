"""Build a timeline dict from a StoryBlueprint + SelectionReport (Milestone 10).

Output uses the same timeline JSON contract the app and renderer2 consume.
Every clip carries its beat + selection rationale in metadata (auditable).
"""
from __future__ import annotations

from .planner import StoryBlueprint
from .selector import SelectionReport


def build_timeline(blueprint: StoryBlueprint, selection: SelectionReport,
                   title_text: str | None = None,
                   width: int | None = None, height: int | None = None,
                   fps: int = 30) -> dict:
    portrait = blueprint.platform == "vertical"
    W = width or (1080 if portrait else 1920)
    H = height or (1920 if portrait else 1080)

    clips = []
    t = 0.0
    title_dur = 2.0 if title_text else 0.0
    t = title_dur
    for i, b in enumerate(selection.beats):
        if b.unfilled or not b.chosen:
            continue
        dur = b.sourceEnd - b.sourceStart
        clips.append({
            "id": f"clip-{b.beatKey}",
            "assetId": next(  # segmentId encodes asset in catalog; caller passes map via metadata
                (c for c in [b.chosen]), b.chosen),
            "segmentId": b.chosen,
            "sourceStart": b.sourceStart, "sourceEnd": b.sourceEnd,
            "timelineStart": round(t, 3), "timelineEnd": round(t + dur, 3),
            "volume": 1.0, "speed": 1.0,
            "meta": {"beat": b.beatKey, "reason": b.reason, "actor": "editor_agent"},
        })
        t += dur

    tracks = [{"id": "video-1", "type": "video", "clips": clips}]
    if title_text:
        tracks.append({"id": "text-1", "type": "text", "clips": [{
            "id": "title-1", "role": "title_card", "text": title_text,
            "timelineStart": 0, "timelineEnd": title_dur,
            "fontSize": 72, "position": "center"}]})

    return {"version": 1, "width": W, "height": H, "fps": fps,
            "duration": round(t, 3), "tracks": tracks,
            "meta": {"templateId": blueprint.templateId, "brief": blueprint.brief,
                     "builtBy": "editor_agent"}}


def resolve_asset_ids(timeline: dict, segments) -> dict:
    """Clip assetId fields initially hold segmentIds; swap to real asset ids."""
    seg_by_id = {s.segmentId: s for s in segments}
    for track in timeline["tracks"]:
        if track["type"] != "video":
            continue
        for c in track["clips"]:
            sid = c.get("segmentId") or c["assetId"]
            if sid in seg_by_id:
                c["assetId"] = seg_by_id[sid].assetId
    return timeline
