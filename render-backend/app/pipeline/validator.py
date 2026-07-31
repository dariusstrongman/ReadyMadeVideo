"""Milestone 11: mechanical validator — deterministic checks on a timeline
(and optionally its rendered preview). Returns a structured report; no LLMs.
"""
from __future__ import annotations

from pydantic import BaseModel

from ..renderer import probe
from .schemas import Segment


class ValidationIssue(BaseModel):
    severity: str            # "error" | "warning"
    code: str
    message: str
    timelineRange: list[float] | None = None


class ValidationReport(BaseModel):
    ok: bool
    issues: list[ValidationIssue]
    duration: float
    clipCount: int


def validate_timeline(timeline: dict,
                      segments: list[Segment],
                      target_duration: float | None = None,
                      asset_durations: dict[str, float] | None = None,
                      preview_path: str | None = None) -> ValidationReport:
    issues: list[ValidationIssue] = []
    seg_by_id = {s.segmentId: s for s in segments}
    problems_by_asset: dict[str, list[Segment]] = {}
    for s in segments:
        problems_by_asset.setdefault(s.assetId, []).append(s)

    vclips = [c for t in timeline.get("tracks", []) if t.get("type") == "video"
              for c in t.get("clips", [])]
    if not vclips:
        issues.append(ValidationIssue(severity="error", code="no_clips",
                                      message="timeline has no video clips"))
        return ValidationReport(ok=False, issues=issues, duration=0, clipCount=0)

    duration = float(timeline.get("duration", 0))

    seen_ranges: set[tuple] = set()
    for c in vclips:
        rng = [c.get("timelineStart", 0), c.get("timelineEnd", 0)]
        if c["sourceEnd"] <= c["sourceStart"]:
            issues.append(ValidationIssue(severity="error", code="broken_range",
                                          message=f"clip {c['id']}: sourceEnd <= sourceStart",
                                          timelineRange=rng))
        if asset_durations and c["assetId"] in asset_durations:
            if c["sourceEnd"] > asset_durations[c["assetId"]] + 0.05:
                issues.append(ValidationIssue(
                    severity="error", code="beyond_source",
                    message=f"clip {c['id']}: trim exceeds source duration",
                    timelineRange=rng))
        if asset_durations is not None and c["assetId"] not in asset_durations:
            issues.append(ValidationIssue(severity="error", code="missing_media",
                                          message=f"clip {c['id']}: asset {c['assetId']} missing",
                                          timelineRange=rng))
        key = (c["assetId"], round(c["sourceStart"], 1), round(c["sourceEnd"], 1))
        if key in seen_ranges:
            issues.append(ValidationIssue(severity="warning", code="duplicate_segment",
                                          message=f"clip {c['id']}: identical source range reused",
                                          timelineRange=rng))
        seen_ranges.add(key)

        # overlap with catalog problem segments (black/frozen)
        for s in problems_by_asset.get(c["assetId"], []):
            if s.problems and c["sourceStart"] < s.sourceEnd and c["sourceEnd"] > s.sourceStart:
                bad = {"mostly_black", "mostly_frozen"} & set(s.problems)
                if bad:
                    issues.append(ValidationIssue(
                        severity="error", code="unusable_footage",
                        message=f"clip {c['id']} overlaps {sorted(bad)} segment "
                                f"{s.segmentId}", timelineRange=rng))

        dur = c["sourceEnd"] - c["sourceStart"]
        if dur < 0.6:
            issues.append(ValidationIssue(severity="warning", code="very_short_clip",
                                          message=f"clip {c['id']} is {dur:.2f}s",
                                          timelineRange=rng))

    if target_duration:
        if duration < target_duration * 0.6:
            issues.append(ValidationIssue(
                severity="warning", code="too_short",
                message=f"timeline {duration:.1f}s vs target {target_duration:.0f}s"))
        if duration > target_duration * 1.5:
            issues.append(ValidationIssue(
                severity="warning", code="too_long",
                message=f"timeline {duration:.1f}s vs target {target_duration:.0f}s"))

    # caption safe zones
    for t in timeline.get("tracks", []):
        if t.get("type") != "text":
            continue
        for c in t.get("clips", []):
            if c.get("timelineEnd", 0) > duration + 0.05:
                issues.append(ValidationIssue(
                    severity="warning", code="caption_overrun",
                    message=f"text '{c.get('text', '')[:30]}' ends after the video"))

    # empty ending: last clip finishing before declared duration
    last_end = max(c.get("timelineEnd", 0) for c in vclips)
    if duration - last_end > 0.25:
        issues.append(ValidationIssue(severity="error", code="empty_ending",
                                      message=f"{duration - last_end:.2f}s of nothing at the end"))

    # rendered preview sanity
    if preview_path:
        try:
            info = probe(preview_path)
            if abs(info.duration - duration) > max(1.0, duration * 0.1):
                issues.append(ValidationIssue(
                    severity="warning", code="render_duration_drift",
                    message=f"preview {info.duration:.1f}s vs timeline {duration:.1f}s"))
        except Exception as e:
            issues.append(ValidationIssue(severity="error", code="invalid_output",
                                          message=f"preview unreadable: {e}"))

    ok = not any(i.severity == "error" for i in issues)
    return ValidationReport(ok=ok, issues=issues, duration=duration,
                            clipCount=len(vclips))
