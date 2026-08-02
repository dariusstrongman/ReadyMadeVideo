"""Human-ceiling evaluation primitives.

This module does not select clips, plan stories, critique previews, or render.
It only describes immutable autonomous baselines, human timeline ancestry,
manual correction events, scorecards, and deterministic comparison reports.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from typing import Any


AUTONOMOUS_INITIAL = "autonomous_initial"
AUTONOMOUS_REVISED = "autonomous_revised"
HUMAN_DRAFT = "human_draft"
HUMAN_APPROVED = "human_approved"

REQUIRED_COMPARISON_LINEAGES = (AUTONOMOUS_INITIAL, HUMAN_APPROVED)

SCORE_DIMENSIONS = (
    "hook",
    "story_clarity",
    "shot_selection",
    "shot_variety",
    "pacing",
    "continuity",
    "action_visibility",
    "emotional_intensity",
    "natural_audio",
    "audio_mix",
    "captions_titles",
    "color_consistency",
    "ending_payoff",
)

_CORRECTION_TYPES = {
    "replace_clip": "replacement",
    "trim_clip": "trim",
    "move_clip": "reorder",
    "change_volume": "audio",
    "duck_music": "audio",
    "add_title": "title",
    "insert_clip": "insert",
    "delete_clip": "delete",
    "change_speed": "speed",
    "add_caption": "caption",
}


class HumanCeilingError(ValueError):
    """Raised when comparison evidence is incomplete or inconsistent."""


def correction_type(operation: dict[str, Any]) -> str:
    """Return the stable learning-data category for one constrained op."""
    op = operation.get("op")
    if op not in _CORRECTION_TYPES:
        raise HumanCeilingError(f"unsupported manual operation: {op!r}")
    return _CORRECTION_TYPES[op]


def split_elapsed_seconds(
    total_seconds: float | None, operation_count: int,
) -> list[float | None]:
    """Allocate a client diagnostic hint without inflating its total."""
    if total_seconds is None:
        return [None] * max(operation_count, 0)
    if total_seconds < 0:
        raise HumanCeilingError("elapsed_seconds cannot be negative")
    if operation_count < 1:
        return []
    each = total_seconds / operation_count
    values = [round(each, 3) for _ in range(operation_count)]
    values[-1] = round(total_seconds - sum(values[:-1]), 3)
    return values


def _timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if not value:
        raise HumanCeilingError("timing event is missing occurred_at")
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise HumanCeilingError(f"invalid timing timestamp: {value!r}") from exc


def measure_server_time(events: list[dict[str, Any]], idle_gap_cap_seconds: float) -> dict[str, Any]:
    """Derive authoritative active time from persisted server-stamped events.

    Each running interval is capped independently, so an abandoned browser tab
    cannot add unbounded benchmark time. Explicitly paused intervals add zero.
    """
    if idle_gap_cap_seconds <= 0:
        raise HumanCeilingError("idle gap cap must be positive")
    ordered = sorted(events, key=lambda e: (_timestamp(e.get("occurred_at")), str(e.get("id") or "")))
    state = "not_started"
    anchor: datetime | None = None
    total = 0.0
    operation_seconds: dict[int, float] = {}
    last_at: datetime | None = None
    terminal: str | None = None

    def close_running(at: datetime) -> float:
        nonlocal anchor, total
        if anchor is None:
            raise HumanCeilingError("running timing state has no anchor")
        gap = max(0.0, (at - anchor).total_seconds())
        measured = min(gap, idle_gap_cap_seconds)
        total += measured
        anchor = at
        return measured

    for event in ordered:
        kind = event.get("event_type")
        at = _timestamp(event.get("occurred_at"))
        if last_at and at < last_at:
            raise HumanCeilingError("timing events are out of order")
        if state == "closed":
            raise HumanCeilingError("timing event exists after session closure")
        if kind == "start":
            if state != "not_started":
                raise HumanCeilingError("duplicate or misplaced start event")
            state, anchor = "running", at
        elif kind == "resume":
            if state != "paused":
                raise HumanCeilingError("resume requires a paused session")
            state, anchor = "running", at
        elif kind == "pause":
            if state != "running":
                raise HumanCeilingError("pause requires a running session")
            close_running(at)
            state, anchor = "paused", None
        elif kind == "operation":
            if state != "running":
                raise HumanCeilingError("operations require a running timing state")
            measured = close_running(at)
            index = event.get("operation_index")
            if index is None:
                raise HumanCeilingError("operation timing event is missing operation_index")
            operation_seconds[int(index)] = round(measured, 6)
        elif kind in ("approve", "abandon"):
            if state not in ("running", "paused"):
                raise HumanCeilingError(f"{kind} requires an active timing state")
            if state == "running":
                close_running(at)
            state, anchor, terminal = "closed", None, kind
        else:
            raise HumanCeilingError(f"unsupported timing event: {kind!r}")
        last_at = at

    if state == "not_started":
        raise HumanCeilingError("session has no start timing event")
    return {
        "server_measured_seconds": round(total, 6),
        "timing_state": state,
        "terminal_event": terminal,
        "last_activity_at": last_at.isoformat() if last_at else None,
        "operation_seconds": operation_seconds,
        "event_count": len(ordered),
    }


def validate_scores(scores: dict[str, Any]) -> dict[str, int | None]:
    """Validate named scorecard dimensions; omitted/N/A dimensions stay null."""
    unknown = sorted(set(scores) - set(SCORE_DIMENSIONS))
    if unknown:
        raise HumanCeilingError(f"unknown score dimensions: {unknown}")
    clean: dict[str, int | None] = {}
    for key in SCORE_DIMENSIONS:
        value = scores.get(key)
        if value is None or value == "":
            clean[key] = None
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise HumanCeilingError(f"{key} must be a number from 1 to 10 or null")
        numeric = int(value)
        if numeric != value or not 1 <= numeric <= 10:
            raise HumanCeilingError(f"{key} must be an integer from 1 to 10 or null")
        clean[key] = numeric
    return clean


def _timeline_json(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get("timeline_json") or {}
    return json.loads(value) if isinstance(value, str) else value


def timeline_metrics(row: dict[str, Any]) -> dict[str, Any]:
    """Summarize comparable, deterministic timeline properties."""
    timeline = _timeline_json(row)
    video = [
        clip
        for track in timeline.get("tracks", [])
        if track.get("type") == "video"
        for clip in track.get("clips", [])
    ]
    text = [
        clip
        for track in timeline.get("tracks", [])
        if track.get("type") == "text"
        for clip in track.get("clips", [])
    ]
    return {
        "timeline_id": row.get("id"),
        "version": row.get("version"),
        "lineage": row.get("lineage"),
        "immutable": bool(row.get("is_immutable")),
        "duration_seconds": round(float(timeline.get("duration") or 0), 3),
        "video_clip_count": len(video),
        "source_asset_count": len({c.get("assetId") for c in video if c.get("assetId")}),
        "title_count": len([c for c in text if c.get("role") == "title_card"]),
        "caption_count": len([c for c in text if c.get("role") == "caption"]),
        "muted_clip_count": len([c for c in video if float(c.get("volume", 1)) == 0]),
        "volume_adjusted_clip_count": len([c for c in video if float(c.get("volume", 1)) != 1]),
        "speed_adjusted_clip_count": len([c for c in video if float(c.get("speed", 1)) != 1]),
        "music_present": bool(timeline.get("music")),
    }


def _scorecard_for(timeline_id: str, scorecards: list[dict[str, Any]]) -> dict[str, Any] | None:
    matching = [s for s in scorecards if s.get("timeline_id") == timeline_id]
    if not matching:
        return None
    row = sorted(matching, key=lambda s: str(s.get("updated_at") or s.get("created_at") or ""))[-1]
    scores = row.get("scores") or {}
    if isinstance(scores, str):
        scores = json.loads(scores)
    return {
        "overall_rating": row.get("overall_rating"),
        "publishable": row.get("publishable"),
        "scores": scores,
        "notes": row.get("notes"),
        "evaluator_role": row.get("evaluator_role"),
    }


def _first_operation(correction: dict[str, Any]) -> dict[str, Any] | None:
    operations = correction.get("applied_operations") or []
    if isinstance(operations, str):
        operations = json.loads(operations)
    return operations[0] if operations else None


def build_comparison_report(
    session: dict[str, Any],
    timeline_rows: list[dict[str, Any]],
    scorecards: list[dict[str, Any]],
    corrections: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the canonical initial vs revised vs human-approved report."""
    ids = {
        AUTONOMOUS_INITIAL: session.get("autonomous_initial_timeline_id"),
        HUMAN_APPROVED: session.get("approved_timeline_id"),
    }
    if session.get("autonomous_revised_timeline_id"):
        ids[AUTONOMOUS_REVISED] = session["autonomous_revised_timeline_id"]
    missing_ids = [lineage for lineage, timeline_id in ids.items() if not timeline_id]
    if missing_ids:
        raise HumanCeilingError(f"comparison is missing timeline IDs: {missing_ids}")

    by_id = {row.get("id"): row for row in timeline_rows}
    versions: dict[str, Any] = {}
    lineages = [AUTONOMOUS_INITIAL]
    if AUTONOMOUS_REVISED in ids:
        lineages.append(AUTONOMOUS_REVISED)
    lineages.append(HUMAN_APPROVED)
    for lineage in lineages:
        timeline_id = ids[lineage]
        row = by_id.get(timeline_id)
        if not row:
            raise HumanCeilingError(f"comparison timeline not found: {lineage}")
        if lineage.startswith("autonomous_") and not row.get("is_immutable"):
            raise HumanCeilingError(f"{lineage} baseline is not immutable")
        metrics = timeline_metrics(row)
        metrics["scorecard"] = _scorecard_for(timeline_id, scorecards)
        versions[lineage] = metrics

    counts = Counter(c.get("correction_type") for c in corrections)
    ordered = sorted(corrections, key=lambda c: int(c.get("operation_index") or 0))
    initial = versions[AUTONOMOUS_INITIAL]
    revised = versions.get(AUTONOMOUS_REVISED)
    human = versions[HUMAN_APPROVED]

    def rating(version: dict[str, Any]):
        card = version.get("scorecard") or {}
        return card.get("overall_rating")

    report = {
        "schema_version": 2,
        "project_id": session.get("project_id"),
        "human_edit_session_id": session.get("id"),
        "status": session.get("status"),
        "versions": versions,
        "human_work": {
            "server_measured_seconds": round(float(session.get("server_measured_seconds") or 0), 3),
            "server_measured_minutes": round(float(session.get("server_measured_seconds") or 0) / 60, 2),
            "client_reported_seconds": session.get("client_reported_seconds"),
            "operation_count": len(ordered),
            "counts_by_type": dict(sorted((k, v) for k, v in counts.items() if k)),
            "operations": [
                {
                    "index": c.get("operation_index"),
                    "type": c.get("correction_type"),
                    "operation": _first_operation(c),
                    "server_measured_seconds": c.get("server_measured_seconds"),
                    "client_reported_seconds": c.get("client_reported_seconds"),
                    "base_timeline_id": c.get("base_timeline_id"),
                    "result_timeline_id": c.get("result_timeline_id"),
                }
                for c in ordered
            ],
        },
        "deltas": {
            "revised_vs_initial_rating": None if revised is None
            or rating(revised) is None or rating(initial) is None
            else rating(revised) - rating(initial),
            "human_vs_initial_rating": None if rating(human) is None or rating(initial) is None
            else rating(human) - rating(initial),
            "human_vs_revised_rating": None if revised is None
            or rating(human) is None or rating(revised) is None
            else rating(human) - rating(revised),
            "human_vs_initial_duration_seconds": round(
                human["duration_seconds"] - initial["duration_seconds"], 3),
            "human_vs_revised_duration_seconds": None if revised is None else round(
                human["duration_seconds"] - revised["duration_seconds"], 3),
        },
    }
    report["markdown"] = comparison_markdown(report)
    return report


def comparison_markdown(report: dict[str, Any]) -> str:
    """Render a portable report suitable for Project One evidence files."""
    versions = report["versions"]
    labels = {
        AUTONOMOUS_INITIAL: "Autonomous initial",
        AUTONOMOUS_REVISED: "Autonomous revised",
        HUMAN_APPROVED: "Human approved",
    }
    lines = [
        "# Human-ceiling comparison",
        "",
        "| Version | Duration | Clips | Sources | Overall | Publishable |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for lineage in (AUTONOMOUS_INITIAL, AUTONOMOUS_REVISED, HUMAN_APPROVED):
        if lineage not in versions:
            continue
        version = versions[lineage]
        card = version.get("scorecard") or {}
        rating = card.get("overall_rating")
        publishable = card.get("publishable")
        lines.append(
            f"| {labels[lineage]} | {version['duration_seconds']:.1f}s | "
            f"{version['video_clip_count']} | {version['source_asset_count']} | "
            f"{rating if rating is not None else 'not scored'} | "
            f"{publishable if publishable is not None else 'not scored'} |"
        )
    work = report["human_work"]
    lines.extend([
        "",
        "## Human work",
        "",
        f"- Server-measured correction time: {work['server_measured_minutes']:.2f} minutes",
        f"- Client-reported timer hint: "
        f"{work['client_reported_seconds'] if work['client_reported_seconds'] is not None else 'not supplied'} seconds",
        f"- Recorded operations: {work['operation_count']}",
        f"- Operations by type: {json.dumps(work['counts_by_type'], sort_keys=True)}",
        "",
        "## Rating deltas",
        "",
    ])
    for key, value in report["deltas"].items():
        lines.append(f"- {key}: {value if value is not None else 'not scored'}")
    lines.append("")
    return "\n".join(lines)
