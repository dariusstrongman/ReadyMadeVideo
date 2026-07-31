"""Milestone 9: candidate selector.

For every story beat: apply HARD constraints first, then weighted ranking.
Every candidate's scores and the selection reason are stored -> auditable.
Deterministic; user-preference weights plug in from preferences.py.
"""
from __future__ import annotations

from pydantic import BaseModel

from .planner import BeatPlan, StoryBlueprint
from .schemas import Segment

DEFAULT_WEIGHTS = {
    "semantic": 0.30,     # storyUses match + provider relevance
    "technical": 0.20,    # focus/exposure/stability
    "motion_fit": 0.20,   # matches the beat's wanted intensity
    "emotion": 0.05,
    "variety": 0.10,      # penalize same shot type / same asset as previous pick
    "audio": 0.05,
    "uniqueness": 0.10,   # penalize duplicate groups / reused ranges
}

UNUSABLE_PROBLEMS = {"mostly_black", "mostly_frozen"}


class Candidate(BaseModel):
    segmentId: str
    scores: dict[str, float]
    total: float
    excluded: bool = False
    excludeReason: str = ""


class BeatSelection(BaseModel):
    beatKey: str
    chosen: str | None            # segmentId
    sourceStart: float | None = None
    sourceEnd: float | None = None
    reason: str = ""
    candidates: list[Candidate] = []
    unfilled: bool = False


class SelectionReport(BaseModel):
    schemaVersion: int = 1
    weights: dict[str, float]
    beats: list[BeatSelection]


def _hard_filter(seg: Segment, beat: BeatPlan, used_ranges: dict[str, list],
                 orientation: str) -> str:
    """Return exclusion reason or '' if usable."""
    if seg.sourceEnd <= seg.sourceStart:
        return "invalid source range"
    if UNUSABLE_PROBLEMS & set(seg.problems):
        return f"unusable: {sorted(UNUSABLE_PROBLEMS & set(seg.problems))}"
    dur = seg.sourceEnd - seg.sourceStart
    if dur < 0.8:
        return "too short"
    # range reuse: same asset overlapping an already used range
    for (s, e) in used_ranges.get(seg.assetId, []):
        if seg.sourceStart < e and seg.sourceEnd > s:
            return "source range already used"
    req_subjects = beat.wants.get("subjects")
    if req_subjects and not set(map(str.lower, req_subjects)) & set(map(str.lower, seg.subjects)):
        return "required subject missing"
    return ""


def _score(seg: Segment, beat: BeatPlan, prev_pick: Segment | None,
           dup_counts: dict[str, int], weights: dict[str, float]) -> dict[str, float]:
    w = beat.wants
    # semantic: storyUses intersection is the main signal
    uses = set(seg.storyUses)
    wanted = set(w.get("storyUses", []))
    semantic = (0.75 if uses & wanted else 0.15) + 0.25 * seg.semanticRelevance
    semantic = min(1.0, semantic)

    technical = (seg.focusScore + seg.exposureScore + seg.stabilityScore) / 3

    mi = seg.motionIntensity
    lo, hi = w.get("minMotion", 0.0), w.get("maxMotion", 1.0)
    prefer = w.get("prefer", "")
    if lo <= mi <= hi:
        if prefer in ("highest_motion", "highest_impact", "rising"):
            motion_fit = 0.5 + 0.5 * mi          # more motion = better
        elif prefer in ("establishing", "emotional"):
            motion_fit = 0.5 + 0.5 * (1.0 - mi)  # calmer = better
        else:
            centered = 1.0 - (abs(mi - (lo + hi) / 2) / max(0.01, (hi - lo)))
            motion_fit = 0.5 + 0.5 * max(0.0, centered)
    else:
        motion_fit = max(0.0, 0.5 - min(abs(mi - lo), abs(mi - hi)))

    emotion = seg.semanticRelevance if seg.emotion else 0.3

    variety = 1.0
    if prev_pick is not None:
        if seg.assetId == prev_pick.assetId:
            variety -= 0.4
        if seg.shotType and seg.shotType == prev_pick.shotType:
            variety -= 0.3
    variety = max(0.0, variety)

    audio = seg.audioScore

    uniqueness = 1.0
    if seg.duplicateGroupId:
        uniqueness -= 0.4 * dup_counts.get(seg.duplicateGroupId, 0)
    uniqueness = max(0.0, uniqueness)

    return {"semantic": round(semantic, 3), "technical": round(technical, 3),
            "motion_fit": round(motion_fit, 3), "emotion": round(emotion, 3),
            "variety": round(variety, 3), "audio": round(audio, 3),
            "uniqueness": round(uniqueness, 3)}


def select_segments(blueprint: StoryBlueprint, segments: list[Segment],
                    weights: dict[str, float] | None = None) -> SelectionReport:
    weights = {**DEFAULT_WEIGHTS, **(weights or {})}
    used_ranges: dict[str, list] = {}
    dup_counts: dict[str, int] = {}
    prev_pick: Segment | None = None
    seg_by_id = {s.segmentId: s for s in segments}
    beats_out: list[BeatSelection] = []

    for beat in blueprint.beats:
        cands: list[Candidate] = []
        for seg in segments:
            reason = _hard_filter(seg, beat, used_ranges, blueprint.platform)
            if reason:
                cands.append(Candidate(segmentId=seg.segmentId, scores={},
                                       total=-1, excluded=True, excludeReason=reason))
                continue
            scores = _score(seg, beat, prev_pick, dup_counts, weights)
            total = sum(scores[k] * weights[k] for k in weights)
            cands.append(Candidate(segmentId=seg.segmentId,
                                   scores=scores, total=round(total, 4)))

        ranked = sorted([c for c in cands if not c.excluded],
                        key=lambda c: c.total, reverse=True)
        if not ranked:
            beats_out.append(BeatSelection(
                beatKey=beat.key, chosen=None, unfilled=True,
                reason="no candidate passed hard constraints", candidates=cands))
            continue

        top = ranked[0]
        seg = seg_by_id[top.segmentId]
        # trim the pick to the beat's clip length around the strongest region
        dur = min(beat.clipSeconds, seg.sourceEnd - seg.sourceStart)
        start = seg.sourceStart
        end = round(start + dur, 3)

        used_ranges.setdefault(seg.assetId, []).append((start, end))
        if seg.duplicateGroupId:
            dup_counts[seg.duplicateGroupId] = dup_counts.get(seg.duplicateGroupId, 0) + 1
        prev_pick = seg

        top_scores = ", ".join(f"{k}={v}" for k, v in top.scores.items())
        beats_out.append(BeatSelection(
            beatKey=beat.key, chosen=seg.segmentId,
            sourceStart=start, sourceEnd=end,
            reason=f"top of {len(ranked)} candidates (total={top.total}; {top_scores})",
            candidates=cands))

    return SelectionReport(weights=weights, beats=beats_out)
