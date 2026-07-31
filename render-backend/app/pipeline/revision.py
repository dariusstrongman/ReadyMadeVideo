"""Milestone 13: revision agent — converts critic revision requests into
CONSTRAINED timeline operations, touching only the ranges the critic named.
Deterministic; alternatives come from the auditable SelectionReport ranking.
"""
from __future__ import annotations

from .critic import CriticVerdict
from .schemas import Segment
from .selector import SelectionReport


def plan_revision_ops(verdict: CriticVerdict,
                      timeline: dict,
                      selection: SelectionReport,
                      segments: list[Segment],
                      max_changes: int = 4) -> list[dict]:
    """Return raw op dicts (validated later by timeline_ops.parse_operations)."""
    seg_by_id = {s.segmentId: s for s in segments}
    used_segment_ids = {c.get("segmentId") for t in timeline["tracks"]
                        if t["type"] == "video" for c in t["clips"]}
    beat_alternatives: dict[str, list[str]] = {}
    for b in selection.beats:
        ranked = sorted([c for c in b.candidates if not c.excluded],
                        key=lambda c: c.total, reverse=True)
        beat_alternatives[b.beatKey] = [c.segmentId for c in ranked
                                        if c.segmentId not in used_segment_ids]

    vclips = [c for t in timeline["tracks"] if t["type"] == "video"
              for c in t["clips"]]
    ops: list[dict] = []
    touched: set[str] = set()

    requests = sorted(verdict.revisionRequests,
                      key=lambda r: 0 if r.severity == "major" else 1)
    for req in requests:
        if len(ops) >= max_changes:
            break
        # locate the clip overlapping the requested timeline range
        target = next((c for c in vclips
                       if c["timelineStart"] < req.timelineEnd
                       and c["timelineEnd"] > req.timelineStart
                       and c["id"] not in touched), None)
        if target is None:
            continue
        beat = target.get("meta", {}).get("beat", req.beatKey)
        issue = req.issue.lower() + " " + req.suggestion.lower()

        if any(k in issue for k in ("replace", "repeat", "repetitive", "same shot",
                                    "different", "variety", "higher-motion",
                                    "stronger", "weak")):
            alts = beat_alternatives.get(beat, [])
            if alts:
                alt = seg_by_id[alts.pop(0)]
                dur = min(target["sourceEnd"] - target["sourceStart"],
                          alt.sourceEnd - alt.sourceStart)
                ops.append({"op": "replace_clip", "clipId": target["id"],
                            "assetId": alt.assetId,
                            "sourceStart": alt.sourceStart,
                            "sourceEnd": round(alt.sourceStart + dur, 3),
                            "comment": f"critic: {req.issue} -> swapped to "
                                       f"{alt.segmentId}"})
                touched.add(target["id"])
                continue
        if any(k in issue for k in ("trim", "shorter", "too long", "slow start",
                                    "faster", "cut the")):
            dur = target["sourceEnd"] - target["sourceStart"]
            if dur > 1.6:
                ops.append({"op": "trim_clip", "clipId": target["id"],
                            "sourceStart": round(target["sourceStart"] + min(1.0, dur * 0.25), 3),
                            "comment": f"critic: {req.issue} -> tightened head"})
                touched.add(target["id"])
                continue
        if "delete" in issue or "remove" in issue:
            if len(vclips) > 2:
                ops.append({"op": "delete_clip", "clipId": target["id"],
                            "comment": f"critic: {req.issue}"})
                touched.add(target["id"])
                continue
        # fallback: gentle tail trim whenever the clip can spare it
        dur = target["sourceEnd"] - target["sourceStart"]
        if dur > 1.0:
            ops.append({"op": "trim_clip", "clipId": target["id"],
                        "sourceEnd": round(target["sourceEnd"] - min(0.5, dur * 0.2), 3),
                        "comment": f"critic (fallback tighten): {req.issue}"})
            touched.add(target["id"])
    return ops
