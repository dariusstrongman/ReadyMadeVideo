"""Output Intelligence — what is worth making, before how to make it.

Sits between analysis (the segment catalog) and the Editorial Planner. Reads
the EXISTING catalog — no model calls, no re-analysis, no invented signals —
and answers three questions deterministically:

  1. What does this footage actually contain? (usable inventory)
  2. What finished videos could honestly be made from it? (opportunities)
  3. Of those, which combination is strongest? (ranked packages)

A separate deterministic feasibility engine judges any requested output —
recommended or customer-customized — against the inventory. The semantic
model may propose; feasibility decides. Nothing here fabricates: quantity is
never inflated to match a request, raw duration is never used where usable
duration is meant, and every rejection carries a machine-readable reason and
the nearest honest alternative.

Everything in this module is a pure function of the Segment list plus
explicit request data, so the whole surface is unit-testable without a
database, a provider, or a network.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field

from .schemas import Segment

# Bumped whenever discovery/ranking logic changes meaning. Persisted with
# every recommendation so a stored row can be recognized as produced by an
# older engine and superseded instead of silently trusted.
ENGINE_VERSION = 1

# ---- usability -------------------------------------------------------------
# A segment is excluded from the usable inventory only for problems the
# analyzer actually reports. Substring match, lowercase: the analyzer's
# problem strings are free-form prose from a bounded prompt.
HARD_PROBLEM_TOKENS = ("black", "corrupt", "unreadable", "no video",
                       "no_video", "unusable", "severely underexposed",
                       "severely overexposed")

# ---- story clustering ------------------------------------------------------
STORY_GAP_S = 30.0          # same-asset gap that starts a new story block
MIN_STORY_S = 20.0          # blocks shorter than this are texture, not story

# ---- shorts ----------------------------------------------------------------
SHORT_MIN_S = 8.0
SHORT_MAX_S = 75.0
SHORT_TARGET_S = 45.0
SHORT_SCORE_FLOOR = 0.45    # candidates below this are not offered at all
SHORT_MAX_OFFERED = 8
SHORT_OVERLAP_FRAC = 0.5    # source-range overlap that makes two shorts "the same moment"

# ---- long form -------------------------------------------------------------
LONG_MIN_USABLE_S = 120.0   # under two usable minutes there is no long-form
DOMINANT_STORY_FRAC = 0.6   # one story must carry this share for ONE long-form
# The planner enforces raw/15 as the shortest defensible full story
# (MAX_OPEN_COMPRESSION). Feasibility reuses the same constant so this stage
# never green-lights a duration the planner would reject.
PLANNER_MAX_COMPRESSION = 15.0
SPEECH_DOMINANT_FRAC = 0.65


HOOK_USES = {"hook", "peak"}
PAYOFF_USES = {"completion", "reflection", "peak"}


@dataclass
class UsableInventory:
    """What the footage honestly contains, from stored analysis only."""
    raw_seconds: float
    usable_seconds: float           # unique: duplicates collapsed, hard problems out
    usable_segment_ids: list[str]
    excluded: list[dict]            # [{segmentId, reason}]
    duplicate_groups: int
    speech_seconds: float
    speech_fraction: float          # of usable
    hook_segment_ids: list[str]
    payoff_segment_ids: list[str]
    stories: list[dict]             # [{storyId, segmentIds, seconds, location, subjects}]
    asset_ids: list[str]
    catalog_hash: str


@dataclass
class Opportunity:
    opportunityId: str
    format: str                     # long_form | short_form
    purpose: str                    # story | interview | highlight | condensed | excerpt
    recommendedDurationS: float
    feasibleDurationS: tuple[float, float]
    recommendedAspect: str
    recommendedPlatform: str
    supportingSegmentIds: list[str]
    hookSegmentIds: list[str]
    payoffSegmentIds: list[str]
    storyId: str | None
    sourceRange: dict               # {assetId: [start, end]} outer bounds
    standaloneScore: float
    coherenceScore: float
    qualityScore: float
    confidence: float
    limitations: list[str]
    reason: str


@dataclass
class Package:
    packageKey: str                 # stable within a recommendation
    title: str
    deliverables: list[Opportunity]
    score: float
    reason: str


@dataclass
class Recommendation:
    engineVersion: int
    catalogHash: str
    inventory: UsableInventory
    packages: list[Package]         # ranked, [0] is the recommendation
    recommendedKey: str | None

    def to_json(self) -> dict:
        d = asdict(self)
        return d


# ---------------------------------------------------------------- inventory
def _dur(s: Segment) -> float:
    return max(0.0, float(s.sourceEnd) - float(s.sourceStart))


def _is_hard_unusable(s: Segment) -> str | None:
    for p in s.problems or []:
        low = str(p).lower()
        for tok in HARD_PROBLEM_TOKENS:
            if tok in low:
                return str(p)
    return None


def _speech_overlap_s(s: Segment) -> float:
    total = 0.0
    for span in s.speechSpans or []:
        a = max(float(span.start), float(s.sourceStart))
        b = min(float(span.end), float(s.sourceEnd))
        total += max(0.0, b - a)
    return min(total, _dur(s))


def catalog_hash(segments: list[Segment]) -> str:
    """Same identity the Picture Edit engine binds to (kept algorithm-equal
    with picture_edit_v2.catalog_hash so one project has ONE catalog identity;
    asserted equal by test, not imported, to keep this module dependency-free).
    """
    canonical = sorted(
        ([s.segmentId, s.assetId, round(s.sourceStart, 3), round(s.sourceEnd, 3)]
         for s in segments), key=lambda r: r[0])
    return hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()


def build_inventory(segments: list[Segment]) -> UsableInventory:
    raw = sum(_dur(s) for s in segments)
    excluded: list[dict] = []
    usable: list[Segment] = []

    # 1) hard problems out — the analyzer said the picture is not usable
    for s in segments:
        why = _is_hard_unusable(s)
        if why:
            excluded.append({"segmentId": s.segmentId, "reason": f"problem: {why}"})
        else:
            usable.append(s)

    # 2) duplicates counted ONCE: the longest member represents its group.
    # Duplicate material must not inflate usable minutes or short quantity.
    groups: dict[str, list[Segment]] = {}
    solo: list[Segment] = []
    for s in usable:
        if s.duplicateGroupId:
            groups.setdefault(s.duplicateGroupId, []).append(s)
        else:
            solo.append(s)
    kept = list(solo)
    for _gid, members in sorted(groups.items()):
        members.sort(key=lambda m: (-_dur(m), m.segmentId))
        kept.append(members[0])
        for m in members[1:]:
            excluded.append({"segmentId": m.segmentId,
                             "reason": f"duplicate of {members[0].segmentId}"})

    kept.sort(key=lambda s: (s.assetId, s.sourceStart))
    usable_s = sum(_dur(s) for s in kept)
    speech_s = sum(_speech_overlap_s(s) for s in kept)

    # 3) story blocks: same asset, small gap, same location => same block.
    # Blocks merge across assets when location and a subject coincide.
    blocks: list[dict] = []
    for s in kept:
        b = blocks[-1] if blocks else None
        same = (b is not None and b["assetId"] == s.assetId
                and float(s.sourceStart) - b["end"] <= STORY_GAP_S
                and (not s.location or not b["location"]
                     or s.location == b["location"]))
        if same:
            b["end"] = max(b["end"], float(s.sourceEnd))
            b["segmentIds"].append(s.segmentId)
            b["seconds"] += _dur(s)
            b["subjects"] |= set(s.subjects or [])
            b["location"] = b["location"] or s.location
        else:
            blocks.append({"assetId": s.assetId, "start": float(s.sourceStart),
                           "end": float(s.sourceEnd),
                           "segmentIds": [s.segmentId], "seconds": _dur(s),
                           "subjects": set(s.subjects or []),
                           "location": s.location or ""})
    merged: list[dict] = []
    for b in blocks:
        home = next((m for m in merged
                     if m["location"] and m["location"] == b["location"]
                     and (m["subjects"] & b["subjects"])), None)
        if home is not None:
            home["segmentIds"] += b["segmentIds"]
            home["seconds"] += b["seconds"]
            home["subjects"] |= b["subjects"]
        else:
            merged.append(dict(b))
    stories = [{"storyId": f"story-{i + 1}", "segmentIds": m["segmentIds"],
                "seconds": round(m["seconds"], 3), "location": m["location"],
                "subjects": sorted(m["subjects"])}
               for i, m in enumerate(merged) if m["seconds"] >= MIN_STORY_S]

    return UsableInventory(
        raw_seconds=round(raw, 3),
        usable_seconds=round(usable_s, 3),
        usable_segment_ids=[s.segmentId for s in kept],
        excluded=excluded,
        duplicate_groups=len(groups),
        speech_seconds=round(speech_s, 3),
        speech_fraction=round(speech_s / usable_s, 4) if usable_s else 0.0,
        hook_segment_ids=[s.segmentId for s in kept
                          if HOOK_USES & set(s.storyUses or [])],
        payoff_segment_ids=[s.segmentId for s in kept
                            if PAYOFF_USES & set(s.storyUses or [])],
        stories=stories,
        asset_ids=sorted({s.assetId for s in kept}),
        catalog_hash=catalog_hash(segments),
    )


# ---------------------------------------------------------------- shorts
def _quality(s: Segment) -> float:
    """Mean of the quality scores the analyzer actually measured (>0).
    Unmeasured (all zero, e.g. older schema rows) reads as neutral 0.5 —
    absence of measurement is not evidence of badness."""
    vals = [v for v in (s.focusScore, s.exposureScore, s.stabilityScore,
                        s.audioScore) if v and v > 0]
    return round(sum(vals) / len(vals), 4) if vals else 0.5


def _overlap_frac(a: dict, b: dict) -> float:
    if a["assetId"] != b["assetId"]:
        return 0.0
    inter = min(a["end"], b["end"]) - max(a["start"], b["start"])
    if inter <= 0:
        return 0.0
    return inter / max(1e-6, min(a["end"] - a["start"], b["end"] - b["start"]))


def discover_shorts(segments: list[Segment],
                    inventory: UsableInventory) -> list[Opportunity]:
    """Semantic standalone moments — never equal-length chunks.

    A candidate grows from a hook-evidenced segment (analyzer storyUses, not
    guesswork) through contiguous same-asset neighbours until it reaches a
    payoff or the short ceiling. Ranked by hook strength, coherence and
    measured quality; diversity is enforced on source overlap, duplicate
    ancestry and repeated (location, action) identity.
    """
    by_id = {s.segmentId: s for s in segments}
    usable = [by_id[sid] for sid in inventory.usable_segment_ids if sid in by_id]
    usable.sort(key=lambda s: (s.assetId, s.sourceStart))

    candidates: list[dict] = []
    for i, seed in enumerate(usable):
        uses = set(seed.storyUses or [])
        hooky = bool(HOOK_USES & uses) or bool(seed.motionPeaks)
        if not hooky:
            continue
        window = [seed]
        end_i = i
        # grow while contiguous (same asset, tiny gap) and under the ceiling
        while end_i + 1 < len(usable):
            nxt = usable[end_i + 1]
            cur_end = float(window[-1].sourceEnd)
            if (nxt.assetId != seed.assetId
                    or float(nxt.sourceStart) - cur_end > 2.0):
                break
            if sum(_dur(w) for w in window) + _dur(nxt) > SHORT_MAX_S:
                break
            window.append(nxt)
            end_i += 1
            if PAYOFF_USES & set(nxt.storyUses or []):
                break                      # a payoff is a natural endpoint
        total = sum(_dur(w) for w in window)
        if total < SHORT_MIN_S:
            continue
        w_uses = set().union(*(set(w.storyUses or []) for w in window))
        has_payoff = bool(PAYOFF_USES & w_uses)
        # dialogue-safe endpoints: the window must not start or end inside a
        # sentence the analyzer measured (word-level truth lives downstream
        # in the planner's dialogue rules; this is the coarse standalone test)
        def _cuts_speech(edge_t: float, _window=window) -> bool:
            for w in _window:
                for span in w.speechSpans or []:
                    if float(span.start) + 0.25 < edge_t < float(span.end) - 0.25:
                        return True
            return False
        speech_cut = (_cuts_speech(float(window[0].sourceStart))
                      or _cuts_speech(float(window[-1].sourceEnd)))
        hook_strength = 1.0 if HOOK_USES & set(seed.storyUses or []) else 0.6
        coherence = (0.5 + (0.35 if has_payoff else 0.0)
                     + (0.15 if not speech_cut else -0.2))
        quality = sum(_quality(w) for w in window) / len(window)
        score = round(0.4 * hook_strength + 0.35 * max(0.0, coherence)
                      + 0.25 * quality, 4)
        limitations = []
        if not has_payoff:
            limitations.append("no analyzer-marked payoff inside the moment")
        if speech_cut:
            limitations.append("edges may cut mid-sentence; planner must re-time")
        candidates.append({
            "seed": seed, "window": window, "score": score,
            "assetId": seed.assetId, "start": float(window[0].sourceStart),
            "end": float(window[-1].sourceEnd), "quality": quality,
            "coherence": max(0.0, min(1.0, coherence)),
            "hook": hook_strength, "limitations": limitations,
        })

    candidates.sort(key=lambda c: (-c["score"], c["assetId"], c["start"]))
    accepted: list[dict] = []
    seen_identity: dict[tuple, int] = {}
    for c in candidates:
        if c["score"] < SHORT_SCORE_FLOOR:
            continue
        if any(_overlap_frac(c, a) > SHORT_OVERLAP_FRAC for a in accepted):
            continue                       # materially the same moment
        dup_ids = {w.duplicateGroupId for w in c["window"] if w.duplicateGroupId}
        if any(dup_ids & {w.duplicateGroupId for w in a["window"]
                          if w.duplicateGroupId} for a in accepted):
            continue                       # same underlying material
        ident = (c["seed"].location, c["seed"].action[:40])
        if seen_identity.get(ident, 0) >= 2:
            continue                       # a third take of the same beat
        seen_identity[ident] = seen_identity.get(ident, 0) + 1
        accepted.append(c)
        if len(accepted) >= SHORT_MAX_OFFERED:
            break

    out: list[Opportunity] = []
    for n, c in enumerate(accepted, start=1):
        dur = c["end"] - c["start"]
        rec = min(SHORT_TARGET_S, max(SHORT_MIN_S, dur * 0.8))
        out.append(Opportunity(
            opportunityId=f"short-{n}",
            format="short_form",
            purpose="highlight" if c["seed"].motionPeaks else "excerpt",
            recommendedDurationS=round(rec, 1),
            feasibleDurationS=(SHORT_MIN_S, round(min(SHORT_MAX_S, dur), 1)),
            recommendedAspect="9:16",
            recommendedPlatform="shorts",
            supportingSegmentIds=[w.segmentId for w in c["window"]],
            hookSegmentIds=[c["seed"].segmentId],
            payoffSegmentIds=[w.segmentId for w in c["window"]
                              if PAYOFF_USES & set(w.storyUses or [])],
            storyId=None,
            sourceRange={c["assetId"]: [round(c["start"], 3), round(c["end"], 3)]},
            standaloneScore=round(c["coherence"], 4),
            coherenceScore=round(c["coherence"], 4),
            qualityScore=round(c["quality"], 4),
            confidence=c["score"],
            limitations=c["limitations"],
            reason=(f"{c['seed'].action[:80]}" if c["seed"].action
                    else "analyzer-marked hook moment"),
        ))
    return out


# ---------------------------------------------------------------- long form
def assess_long_form(segments: list[Segment],
                     inventory: UsableInventory) -> list[Opportunity]:
    """Long-form only where continuity actually exists.

    One dominant story => one long-form. Several independent stories, each
    substantial => several separate videos, never one forced container.
    """
    if inventory.usable_seconds < LONG_MIN_USABLE_S:
        return []
    stories = inventory.stories
    if not stories:
        return []
    total_story_s = sum(s["seconds"] for s in stories) or 1.0
    dominant = max(stories, key=lambda s: s["seconds"])
    speechy = inventory.speech_fraction >= SPEECH_DOMINANT_FRAC

    def _story_opportunity(story: dict, idx: int, solo: bool) -> Opportunity:
        story_ids = set(story["segmentIds"])
        hooks = [h for h in inventory.hook_segment_ids if h in story_ids]
        payoffs = [p for p in inventory.payoff_segment_ids if p in story_ids]
        base = story["seconds"]
        # Speech-led material survives light compression; action footage is
        # distilled harder. Bounds stay inside what the planner itself will
        # accept (raw/15 floor).
        target = base * (0.6 if speechy else 0.35)
        lo = max(30.0, base / PLANNER_MAX_COMPRESSION)
        hi = base * 0.85
        target = max(lo, min(target, hi))
        coherence = 0.4
        coherence += 0.25 if hooks else 0.0
        coherence += 0.25 if payoffs else 0.0
        coherence += 0.1 if solo else 0.0
        limitations = []
        if not hooks:
            limitations.append("no analyzer-marked hook — opening will be weaker")
        if not payoffs:
            limitations.append("no analyzer-marked completion/payoff")
        purpose = "interview" if speechy else "story"
        return Opportunity(
            opportunityId=f"long-{idx}",
            format="long_form",
            purpose=purpose,
            recommendedDurationS=round(target, 1),
            feasibleDurationS=(round(lo, 1), round(hi, 1)),
            recommendedAspect="16:9",
            recommendedPlatform="youtube",
            supportingSegmentIds=story["segmentIds"],
            hookSegmentIds=hooks[:3],
            payoffSegmentIds=payoffs[:3],
            storyId=story["storyId"],
            sourceRange={},
            standaloneScore=1.0,
            coherenceScore=round(min(1.0, coherence), 4),
            qualityScore=0.5,
            confidence=round(min(1.0, coherence), 4),
            limitations=limitations,
            reason=(f"one coherent {'conversation' if speechy else 'story'} "
                    f"at {story['location'] or 'a single setting'}"
                    if solo else
                    f"independent story at {story['location'] or 'its own setting'}"),
        )

    if dominant["seconds"] / total_story_s >= DOMINANT_STORY_FRAC:
        return [_story_opportunity(dominant, 1, solo=True)]
    # several independent stories: offer each that stands on its own
    outs = []
    for i, st in enumerate(sorted(stories, key=lambda s: -s["seconds"]), 1):
        if st["seconds"] >= LONG_MIN_USABLE_S:
            outs.append(_story_opportunity(st, i, solo=False))
    return outs


# ---------------------------------------------------------------- packages
def build_packages(longs: list[Opportunity],
                   shorts: list[Opportunity],
                   inventory: UsableInventory) -> list[Package]:
    """Rank honest combinations. Strongest useful outputs, not most outputs."""
    packages: list[Package] = []

    def _score(items: list[Opportunity]) -> float:
        if not items:
            return 0.0
        strength = sum(o.confidence for o in items)
        covered = set()
        for o in items:
            covered.update(o.supportingSegmentIds)
        coverage = len(covered) / max(1, len(inventory.usable_segment_ids))
        return round(strength * (0.7 + 0.3 * coverage), 4)

    top_shorts = shorts[:5]
    if longs and top_shorts:
        combo = longs + top_shorts
        packages.append(Package(
            packageKey="combo",
            title=(f"{len(longs)} full video{'s' if len(longs) > 1 else ''} + "
                   f"{len(top_shorts)} short{'s' if len(top_shorts) > 1 else ''}"),
            deliverables=combo, score=_score(combo),
            reason="the footage holds a complete story AND moments that stand alone"))
    if longs:
        packages.append(Package(
            packageKey="long_only",
            title=f"{len(longs)} full video{'s' if len(longs) > 1 else ''}",
            deliverables=list(longs), score=_score(longs) * 0.92,
            reason="one polished edit of the full story, nothing split out"))
    if shorts:
        packages.append(Package(
            packageKey="shorts_only",
            title=f"{len(shorts)} short{'s' if len(shorts) > 1 else ''}",
            deliverables=list(shorts), score=_score(shorts) * 0.9,
            reason="every strong standalone moment as its own vertical clip"))
    packages.sort(key=lambda p: -p.score)
    return packages


def recommend(segments: list[Segment]) -> Recommendation:
    inventory = build_inventory(segments)
    shorts = discover_shorts(segments, inventory)
    longs = assess_long_form(segments, inventory)
    packages = build_packages(longs, shorts, inventory)
    return Recommendation(
        engineVersion=ENGINE_VERSION,
        catalogHash=inventory.catalog_hash,
        inventory=inventory,
        packages=packages,
        recommendedKey=packages[0].packageKey if packages else None,
    )


# ---------------------------------------------------------------- feasibility
SUPPORTED = "SUPPORTED"
SUPPORTED_WITH_CONSTRAINTS = "SUPPORTED_WITH_CONSTRAINTS"
NOT_RECOMMENDED = "NOT_RECOMMENDED"
IMPOSSIBLE = "IMPOSSIBLE"


@dataclass
class FeasibilityResult:
    verdict: str
    reasons: list[dict] = field(default_factory=list)   # {code, message}
    alternative: dict | None = None

    def to_json(self) -> dict:
        return asdict(self)


def check_feasibility(request: dict, segments: list[Segment],
                      inventory: UsableInventory | None = None,
                      shorts: list[Opportunity] | None = None,
                      longs: list[Opportunity] | None = None) -> FeasibilityResult:
    """Deterministic judgement of a requested output against the inventory.

    request = {
      "kind": "long_form" | "short_form",
      "quantity": int (short_form),
      "durationTargetS": float | None,
      "aspect": str | None,
    }
    Hard physics is IMPOSSIBLE; honest-but-unwise is NOT_RECOMMENDED; edge
    pressure is SUPPORTED_WITH_CONSTRAINTS. Every non-SUPPORTED verdict names
    its reason and, where one exists, the nearest viable alternative.
    """
    inv = inventory or build_inventory(segments)
    reasons: list[dict] = []
    alternative: dict | None = None

    if not inv.usable_segment_ids:
        return FeasibilityResult(IMPOSSIBLE, [{
            "code": "no_usable_footage",
            "message": "analysis found no usable footage in this upload"}])

    kind = request.get("kind")
    dur = request.get("durationTargetS")
    qty = request.get("quantity")
    # Garbage in the request is rejected, never coerced: a quantity of 0 or -3
    # silently becoming 1 is exactly the "silently alter it" the contract bans.
    if qty is not None and (not isinstance(qty, int) or qty < 1):
        return FeasibilityResult(IMPOSSIBLE, [{
            "code": "invalid_quantity",
            "message": f"quantity must be a positive whole number, got {qty!r}"}])
    if dur is not None and (not isinstance(dur, (int, float)) or dur <= 0):
        return FeasibilityResult(IMPOSSIBLE, [{
            "code": "invalid_duration",
            "message": f"durationTargetS must be a positive number, got {dur!r}"}])

    if kind == "long_form":
        if dur is not None:
            if dur > inv.usable_seconds:
                return FeasibilityResult(IMPOSSIBLE, [{
                    "code": "duration_exceeds_usable",
                    "message": (f"you asked for {round(dur)}s but the footage "
                                f"holds {round(inv.usable_seconds)}s of usable "
                                "material — a video cannot contain more than "
                                "was filmed")}],
                    {"kind": "long_form",
                     "durationTargetS": round(inv.usable_seconds * 0.6)})
            if dur > inv.usable_seconds * 0.85:
                reasons.append({
                    "code": "near_total_usable",
                    "message": ("this length uses nearly every usable second — "
                                "there is no room to drop weak material")})
        lf = longs if longs is not None else assess_long_form(segments, inv)
        if not lf:
            if inv.usable_seconds < LONG_MIN_USABLE_S:
                return FeasibilityResult(NOT_RECOMMENDED, [{
                    "code": "insufficient_usable_for_long_form",
                    "message": (f"only {round(inv.usable_seconds)}s of usable "
                                "footage — that is a short, not a long-form "
                                "video")}],
                    {"kind": "short_form", "quantity": 1})
            return FeasibilityResult(NOT_RECOMMENDED, [{
                "code": "no_coherent_story",
                "message": ("the footage contains no single coherent story to "
                            "carry a long-form edit")}],
                {"kind": "short_form",
                 "quantity": max(1, len(shorts or []))})
        if dur is not None and lf:
            lo, hi = lf[0].feasibleDurationS
            if dur < lo or dur > hi:
                reasons.append({
                    "code": "duration_outside_recommended_range",
                    "message": (f"the honest range for this footage is "
                                f"{round(lo)}–{round(hi)}s")})
        verdict = SUPPORTED_WITH_CONSTRAINTS if reasons else SUPPORTED
        return FeasibilityResult(verdict, reasons, alternative)

    if kind == "short_form":
        found = shorts if shorts is not None else discover_shorts(segments, inv)
        n = int(request.get("quantity") or 1)
        if not found:
            return FeasibilityResult(IMPOSSIBLE, [{
                "code": "no_standalone_moments",
                "message": ("no moment in this footage stands alone with a "
                            "hook — there is nothing honest to cut a short "
                            "from")}])
        if n > len(found):
            return FeasibilityResult(NOT_RECOMMENDED, [{
                "code": "quantity_exceeds_moments",
                "message": (f"you asked for {n} shorts but the footage holds "
                            f"{len(found)} strong standalone moment"
                            f"{'s' if len(found) != 1 else ''} — repeating "
                            "material would make them worse, not more")}],
                {"kind": "short_form", "quantity": len(found)})
        if dur is not None and not (SHORT_MIN_S <= dur <= SHORT_MAX_S):
            reasons.append({
                "code": "short_duration_out_of_band",
                "message": (f"shorts run {int(SHORT_MIN_S)}–{int(SHORT_MAX_S)}s; "
                            f"{round(dur)}s is outside that")})
            alternative = {"kind": "short_form", "quantity": n,
                           "durationTargetS": SHORT_TARGET_S}
        verdict = SUPPORTED_WITH_CONSTRAINTS if reasons else SUPPORTED
        return FeasibilityResult(verdict, reasons, alternative)

    return FeasibilityResult(IMPOSSIBLE, [{
        "code": "unknown_kind",
        "message": f"unknown output kind {kind!r}"}])


def check_selection(selection: list[dict],
                    segments: list[Segment]) -> list[FeasibilityResult]:
    """Feasibility for a whole customer selection (one result per item).

    Quantity is checked ACROSS the selection: five separate one-short items
    are five shorts, and they compete for the same standalone moments.
    """
    inv = build_inventory(segments)
    shorts = discover_shorts(segments, inv)
    longs = assess_long_form(segments, inv)
    results = []

    def _valid_qty(item) -> bool:
        q = item.get("quantity")
        return q is None or (isinstance(q, int) and q >= 1)

    # Aggregate ONLY over well-formed quantities; a malformed item keeps its
    # raw value so check_feasibility rejects it instead of a sum masking it.
    shorts_requested = sum((item.get("quantity") if item.get("quantity")
                            is not None else 1)
                           for item in selection
                           if item.get("kind") == "short_form"
                           and _valid_qty(item))
    longs_requested = sum(1 for item in selection
                          if item.get("kind") == "long_form")
    for item in selection:
        req = dict(item)
        if item.get("kind") == "short_form" and _valid_qty(item):
            req["quantity"] = shorts_requested
        r = check_feasibility(req, segments, inv, shorts, longs)
        # Two long-form requests against one coherent story would both map to
        # the SAME material — duplicate videos, quantity inflation by another
        # door. Each long-form needs its own independent story.
        if (item.get("kind") == "long_form" and r.verdict in
                (SUPPORTED, SUPPORTED_WITH_CONSTRAINTS)
                and longs_requested > len(longs or [])):
            r = FeasibilityResult(NOT_RECOMMENDED, [{
                "code": "long_form_count_exceeds_stories",
                "message": (f"you asked for {longs_requested} full videos but "
                            f"the footage holds {len(longs or [])} independent "
                            "stor" + ("y" if len(longs or []) == 1 else "ies")
                            + " — the same story cut twice is one video, twice")}],
                {"kind": "long_form"} if longs else None)
        results.append(r)
    return results
