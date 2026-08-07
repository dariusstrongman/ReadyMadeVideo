"""Phase 2 editorial intelligence — deterministic, flag-gated, additive.

Design contract (docs/PHASE2_EDITORIAL_INTELLIGENCE.md):
  * plans compete on INTEREST subject to TRUTH, never the reverse — nothing
    here can weaken, reorder or override the grounding validator or the
    deterministic truth gate
  * every check is computed from the plan + the segment catalog; no model
    call, no randomness, no wall clock
  * all flags default OFF; with every flag off this module contributes
    nothing and planner behavior is byte-identical to Phase 1
  * the interest gate is a FLOOR to clear, never a score to maximize
    (Murch: emotion+story are 74% of a cut and are what machines measure
    worst — computable metrics are veto/penalty checks only)

Flags (env, "1"/"true"/"yes"):
  PHASE2_DIALOGUE       cut points must not sever speech (snap, then reject)
  PHASE2_HOOK           hook chosen from a ranked shortlist + curiosity loops
  PHASE2_COHERENCE      captions must add information; word-timed; <=17 cps
  PHASE2_TENSION        energy must escalate; payoff peaks; no late longueur
  PHASE2_INTEREST_GATE  scored floor across all of the above
"""
from __future__ import annotations

import os
import re

from .schemas import Segment

# ---------------------------------------------------------------- flags
_TRUTHY = ("1", "true", "yes")

DIALOGUE_FLAG = "PHASE2_DIALOGUE"
HOOK_FLAG = "PHASE2_HOOK"
COHERENCE_FLAG = "PHASE2_COHERENCE"
TENSION_FLAG = "PHASE2_TENSION"
INTEREST_FLAG = "PHASE2_INTEREST_GATE"

# formant guard: a cut may not land closer than this to a word's interior
WORD_EPS = 0.03
SNAP_TOLERANCE_S = 1.0        # measured: snapping fixes 7/11 real cuts at <=2s;
                              # 1s keeps only the cheap tier — the rest REVISE
CAPTION_MAX_CPS = 17.0        # BBC 160-180wpm ~= 15-17cps; Netflix adult 20
CAPTION_SYNC_TOLERANCE_S = 0.75
MAX_LOOPS = 3
PRIMARY_LOOP_CLOSE_FRACTION = 0.6   # "the final 40%" — stated in the prompt
UTILIZATION_MIN_CATALOG = 20        # floor only judged on catalogs this size+
# ONE source of truth for the minimum usable clip: the planner's clamp
# constant (audit A8 — a drifted local copy claimed to "mirror" it at 0.75
# while the planner used 0.8).
from .editorial_planner import _CLAMP_MIN_DURATION_S as _MIN_CLIP_S  # noqa: E402


def enabled(flag: str) -> bool:
    return os.environ.get(flag, "").lower() in _TRUTHY


def any_enabled() -> bool:
    return any(enabled(f) for f in (DIALOGUE_FLAG, HOOK_FLAG, COHERENCE_FLAG,
                                    TENSION_FLAG, INTEREST_FLAG))


# ---------------------------------------------------- dialogue integrity (S2)
def word_severed_at(seg: Segment, t: float):
    """The Word a cut at absolute time ``t`` would sever, else None.

    A cut strictly inside (start, end) truncates the word — including a cut
    just after the start, which clips the initial formant. Cuts AT a word
    boundary are safe in both directions (in-cut at w.start includes the
    whole word; out-cut at w.start excludes it whole)."""
    for w in seg.wordTimings:
        if w.start + WORD_EPS < t < w.end - WORD_EPS:
            return w
    return None


def _safe_boundaries(seg: Segment) -> list[tuple[float, bool]]:
    """(time, is_sentence_boundary) cut points inside the segment's range."""
    out: list[tuple[float, bool]] = []
    for sp in seg.speechSpans:
        out.append((sp.start, True))
        out.append((sp.end, True))
    for w in seg.wordTimings:
        out.append((w.start, False))
        out.append((w.end, False))
    lo, hi = seg.sourceStart, seg.sourceEnd
    return [(t, s) for t, s in out if lo - WORD_EPS <= t <= hi + WORD_EPS]


def nearest_safe_cut(seg: Segment, t: float, lo: float, hi: float,
                     tolerance: float = SNAP_TOLERANCE_S) -> float | None:
    """Nearest legal cut point to ``t`` within [lo, hi] and ``tolerance``.

    Sentence boundaries get a 0.35s preference bonus (finish the sentence
    rather than merely the word) — deterministic: ties resolve to the
    earlier time."""
    best, best_cost = None, None
    for c, is_sentence in sorted(_safe_boundaries(seg)):
        if not (lo - WORD_EPS <= c <= hi + WORD_EPS):
            continue
        dist = abs(c - t)
        if dist > tolerance:
            continue
        cost = dist - (0.35 if is_sentence else 0.0)
        if best_cost is None or cost < best_cost:
            best, best_cost = min(max(c, lo), hi), cost
    return best


def _reserved_handles(raw: dict) -> dict:
    """Per-timeline-index (head_reserve, tail_reserve) seconds that the plan's
    own soft transitions require as UNUSED source. The snapper must never
    consume them (audit A2: a snap that widens sourceOut into a dissolve's
    handle turns a valid plan into an impossible one, then loops)."""
    reserves: dict[int, list[float]] = {}
    timeline = raw.get("timeline") or []
    order = [e.get("segmentId") for e in timeline if isinstance(e, dict)]
    for tr in raw.get("transitions") or []:
        if not isinstance(tr, dict) or tr.get("type") == "hard_cut":
            continue
        try:
            dur = float(tr.get("durationSeconds") or 0)
        except (TypeError, ValueError):
            continue
        if dur <= 0:
            continue
        for j in range(len(order) - 1):
            if order[j] == tr.get("fromSegmentId") \
                    and order[j + 1] == tr.get("toSegmentId"):
                reserves.setdefault(j, [0.0, 0.0])[1] = max(
                    reserves.get(j, [0.0, 0.0])[1], dur)      # outgoing tail
                reserves.setdefault(j + 1, [0.0, 0.0])[0] = max(
                    reserves.get(j + 1, [0.0, 0.0])[0], dur)  # incoming head
                break
    return reserves


def snap_timeline_speech(raw: dict, segments: list[Segment]) -> None:
    """Deterministic repair: snap severing cuts to the nearest safe boundary
    within tolerance. Runs INSIDE the planner's normalize step, before
    arithmetic rebuild, so timeline bookkeeping is recomputed afterwards.

    Bounds honored, in order: the segment's real range, the plan's own
    reserved transition handles (never repair speech by breaking geometry),
    and a minimum usable clip. Cuts with no safe boundary inside those
    bounds are LEFT ALONE — validation reports them as structured
    violations and the revise loop teaches the model the safe boundaries.
    Malformed entries are SKIPPED (never abort: earlier snaps must stay
    accounted for in the ledger — audit A3)."""
    if not (isinstance(raw, dict) and isinstance(raw.get("timeline"), list)):
        return
    by_id = {s.segmentId: s for s in segments}
    reserves = _reserved_handles(raw)
    adjustments = []
    for i, entry in enumerate(raw["timeline"]):
        if not isinstance(entry, dict):
            continue                    # malformed — validator's job
        seg = by_id.get(entry.get("segmentId"))
        if seg is None or not seg.wordTimings:
            continue
        try:
            s_in = float(entry.get("sourceIn"))
            s_out = float(entry.get("sourceOut"))
        except (TypeError, ValueError):
            continue                    # malformed — validator's job
        head_res, tail_res = reserves.get(i, (0.0, 0.0))
        w = word_severed_at(seg, s_in)
        if w is not None:
            lo = seg.sourceStart + head_res
            hi = s_out - _MIN_CLIP_S
            snapped = nearest_safe_cut(seg, s_in, lo, hi) if lo <= hi else None
            if snapped is not None:
                adjustments.append({"index": i, "field": "sourceIn",
                                    "from": round(s_in, 3),
                                    "to": round(snapped, 3),
                                    "reason": f"cut severed '{w.word}'"})
                s_in = snapped
        w = word_severed_at(seg, s_out)
        if w is not None:
            lo = s_in + _MIN_CLIP_S
            hi = seg.sourceEnd - tail_res
            snapped = nearest_safe_cut(seg, s_out, lo, hi) if lo <= hi else None
            if snapped is not None:
                adjustments.append({"index": i, "field": "sourceOut",
                                    "from": round(s_out, 3),
                                    "to": round(snapped, 3),
                                    "reason": f"cut severed '{w.word}'"})
                s_out = snapped
        entry["sourceIn"], entry["sourceOut"] = round(s_in, 3), round(s_out, 3)
    if adjustments:
        raw["dialogueAdjustments"] = adjustments


def snap_hook_out(raw: dict, segments: list[Segment]) -> None:
    """A6: the hook's out-point is re-derived AFTER timeline snapping (the
    planner caps it at min(span, cap, 5.0)), so the cap itself can land
    inside a word. Shrink it to the nearest safe boundary — never grow it
    (growing could exceed the executor's 5.0s hook ceiling)."""
    hook = raw.get("hook") if isinstance(raw, dict) else None
    timeline = raw.get("timeline") if isinstance(raw, dict) else None
    if not (isinstance(hook, dict) and isinstance(timeline, list) and timeline):
        return
    first = timeline[0]
    if not isinstance(first, dict):
        return
    seg = next((s for s in segments
                if s.segmentId == hook.get("segmentId")), None)
    if seg is None or not seg.wordTimings:
        return
    try:
        h_in = float(hook.get("sourceIn"))
        h_out = float(hook.get("sourceOut"))
    except (TypeError, ValueError):
        return
    if word_severed_at(seg, h_out) is None:
        return
    lo, hi = h_in + 0.5, h_out          # shrink only
    # search the WHOLE shrink window: a hook that must shrink from 5.0s to
    # 2.3s to finish a word is a valid hook; a severed word is not
    snapped = nearest_safe_cut(seg, h_out, lo, hi,
                               tolerance=hi - lo) if lo <= hi else None
    if snapped is not None and snapped < h_out:
        hook["sourceOut"] = round(snapped, 3)
        hook["durationSeconds"] = round(snapped - h_in, 3)


def dialogue_violations(plan, segments: list[Segment]) -> list[str]:
    """Cuts that still sever speech after snapping. Joining the violations
    list makes these hard by construction (a plan with violations revises)."""
    by_id = {s.segmentId: s for s in segments}
    out: list[str] = []
    for i, entry in enumerate(plan.timeline):
        seg = by_id.get(entry.segmentId)
        if seg is None or not seg.wordTimings:
            continue
        for field, t in (("sourceIn", entry.sourceIn),
                         ("sourceOut", entry.sourceOut)):
            w = word_severed_at(seg, t)
            if w is None:
                continue
            near = nearest_safe_cut(seg, t, seg.sourceStart, seg.sourceEnd,
                                    tolerance=10.0)
            hint = f" (nearest safe boundary {round(near, 2)}s)" \
                if near is not None else ""
            out.append(f"dialogue: timeline[{i}].{field} at {round(t, 2)}s "
                       f"severs the word {w.word!r} mid-speech{hint} — cut at "
                       "a sentence boundary or word gap")
    return out


# --------------------------------------------------------- hook engine (S3)
_DISCOURSE_OPENERS = {"so", "hi", "hey", "welcome", "today", "alright",
                      "okay", "ok", "all", "well", "um", "uh"}
_ANAPHORA_OPENERS = {"it", "that", "this", "he", "she", "they", "and", "but",
                     "then"}
_BELIEF = re.compile(r"\b(everyone|everybody|people|most|they|you)\b"
                     r".{0,60}\b(think|thinks|say|says|believe|believes|"
                     r"told|assume)\b", re.S)
_ADVERSATIVE = re.compile(r"\b(but|actually|in fact|however|wrong|myth)\b")
_QUESTION = re.compile(r"^\s*(why|how|what|when|who|where|is|are|do|does|"
                       r"can|could|should|would)\b", re.I)
_WARNING = re.compile(r"^\s*(don'?t|never|stop|avoid)\b|"
                      r"\b(mistake|ruin|dangerous|scam|warning)\b", re.I)
_NUMBER = re.compile(r"\b\d[\d,.]*\b|\b(hundred|thousand|million|billion)\b",
                     re.I)
_CONSEQUENCE = re.compile(r"\b(cost|costs|lost|lose|save|saved|fail|failed|"
                          r"damage|damaged|deadline|worth|paid|broke)\b", re.I)


def _opening_text(seg: Segment) -> str:
    if seg.speechSpans:
        return seg.speechSpans[0].text or ""
    return (seg.transcript or "").split(".")[0]


def classify_hook_archetype(seg: Segment) -> tuple[str | None, str]:
    """(archetype, reason) from transcript signals alone — the five
    archetypes buildable without face detection or frame embeddings."""
    text = _opening_text(seg).strip()
    low = text.lower()
    first = (low.split() or [""])[0].strip(",.!?'\"")
    if not text:
        return None, "no speech"
    if _BELIEF.search(low) and _ADVERSATIVE.search(low):
        return "contradiction", f"belief-negation: {text[:60]!r}"
    if _QUESTION.match(low) and "?" in text and len(low.split()) >= 4:
        return "question", f"opens on a question: {text[:60]!r}"
    if _WARNING.search(low) and re.search(r"\byou\b|\byour\b", low):
        return "warning", f"prohibition aimed at the viewer: {text[:60]!r}"
    if _NUMBER.search(low) and _CONSEQUENCE.search(low):
        return "stakes", f"number bound to a consequence: {text[:60]!r}"
    if first in _DISCOURSE_OPENERS:
        return None, f"discourse opener {first!r} vetoes in-media-res"
    if first in _ANAPHORA_OPENERS:
        return "in_media_res", f"unresolved opening reference {first!r}"
    return None, "no archetype signal"


def rank_hook_candidates(segments: list[Segment], top_n: int = 6) -> list[dict]:
    """Deterministically ranked hook shortlist. A hook is an UNRESOLVED
    state, not an exciting one — archetype signals dominate; quality and
    motion are tie-breakers. Motion is ranked against THIS video's own
    distribution (the raw score is normalized per-video and near-flat)."""
    if not segments:
        return []
    motions = sorted(s.motionIntensity for s in segments)

    def motion_pct(v: float) -> float:
        below = sum(1 for m in motions if m < v)
        return below / max(1, len(motions) - 1) if len(motions) > 1 else 0.5

    out = []
    for s in segments:
        archetype, reason = classify_hook_archetype(s)
        # A12: only segments with a REAL hook signal (an archetype or an
        # explicit storyUses hook tag) are candidates. Quality-only entries
        # produced degenerate all-tied shortlists whose "ranking" was
        # lexicographic segment-id order presented as editorial judgment.
        if not archetype and "hook" not in s.storyUses:
            continue
        score = 0.0
        if archetype:
            score += 3.0
        if "hook" in s.storyUses:
            score += 2.0
        if "peak" in s.storyUses:
            score += 1.0
        if motion_pct(s.motionIntensity) >= 0.75:
            score += 1.0
        if (s.focusScore + s.audioScore) / 2 >= 0.5:
            score += 1.0
        if s.problems:
            score -= 2.0
        if score > 0:
            out.append({"segmentId": s.segmentId,
                        "archetype": archetype or "story_hook",
                        "score": round(score, 2), "reason": reason,
                        "_motion": motion_pct(s.motionIntensity),
                        "_quality": (s.focusScore + s.audioScore) / 2})
    # deterministic, signal-led tie-breaks — id order is the LAST resort
    out.sort(key=lambda c: (-c["score"], -c["_motion"], -c["_quality"],
                            c["segmentId"]))
    for c in out:
        c.pop("_motion", None)
        c.pop("_quality", None)
    return out[:top_n]


def hook_violations(plan, segments: list[Segment]) -> list[str]:
    """A12: enforce the shortlist only when it offers a REAL choice (>= 2
    candidates). Zero candidates never blocks; a single candidate is
    advisory (shown in the prompt) — forcing it would fake a ranked choice
    the ranking never made."""
    shortlist = rank_hook_candidates(segments)
    if len(shortlist) < 2:
        return []
    ids = {c["segmentId"] for c in shortlist}
    out: list[str] = []
    if plan.hook.segmentId not in ids:
        options = ", ".join(f"{c['segmentId']} ({c['archetype']})"
                            for c in shortlist)
        out.append("hook: the opening segment is not among the ranked hook "
                   f"candidates — open on one of: {options}")
    return out


def primary_loop_opened_by_hook(plan) -> bool:
    """THE definition of 'the hook opens the primary loop' — identity AND
    timing (audit A9: two competing definitions lived in this module; the
    validator and the interest gate now share this one predicate). All loop
    times are TIMELINE seconds."""
    loops = getattr(plan, "loops", []) or []
    if not loops:
        return False
    return (loops[0].openedBySegmentId == plan.hook.segmentId
            and loops[0].openedAtSeconds <= plan.hook.durationSeconds + 0.1)


def loop_violations(plan) -> list[str]:
    """Curiosity-loop ledger (S4). Ovsiankina: loops are a COMPLETION
    mechanic — an unclosed loop is worse than none, so closure is hard.
    All times are TIMELINE seconds, judged AFTER the planner's arithmetic
    normalization (the same rewritten duration the prompt's feedback shows)."""
    out: list[str] = []
    loops = getattr(plan, "loops", []) or []
    duration = plan.plannedDurationSeconds
    planned = {e.segmentId for e in plan.timeline}
    if not loops:
        return ["loops: declare 1-3 curiosity loops — the hook must open a "
                "question the edit later answers"]
    if len(loops) > MAX_LOOPS:
        out.append(f"loops: {len(loops)} concurrent loops exceed the "
                   f"cognitive-load cap of {MAX_LOOPS}")
    for i, lp in enumerate(loops):
        if lp.closedAtSeconds <= lp.openedAtSeconds:
            out.append(f"loops[{i}] closes before it opens")
        if lp.closedAtSeconds > duration + 0.1:
            out.append(f"loops[{i}] never closes inside the video "
                       f"({round(lp.closedAtSeconds, 2)}s > "
                       f"{round(duration, 2)}s of TIMELINE time) — an "
                       "unclosed loop is a broken promise")
        for field, sid in (("openedBySegmentId", lp.openedBySegmentId),
                           ("closedBySegmentId", lp.closedBySegmentId)):
            if sid not in planned:
                out.append(f"loops[{i}].{field} references a segment the "
                           "timeline does not use")
    if not primary_loop_opened_by_hook(plan):
        out.append("loops[0] (the primary loop) must be opened by the hook "
                   "itself: openedBySegmentId is the hook's segment and "
                   "openedAtSeconds falls inside the hook")
    if loops[0].closedAtSeconds < PRIMARY_LOOP_CLOSE_FRACTION * duration:
        out.append("loops[0] (the primary loop) closes too early — it must "
                   f"close in the final "
                   f"{round((1 - PRIMARY_LOOP_CLOSE_FRACTION) * 100):d}% "
                   "of the video")
    return out


# ------------------------------------------------- caption coherence (S7a)
def _quote_start_on_timeline(plan, seg: Segment, quote: str) -> float | None:
    """Source time of a quote's first word, mapped to timeline time.

    A14: quotes that span a sentence boundary live across TWO speechSpans —
    single-span lookup silently no-oped on them, so adjacent pairs are
    searched too (the earlier span's start is the honest anchor)."""
    span = next((sp for sp in seg.speechSpans
                 if quote in (sp.text or "").lower()), None)
    if span is None:
        for a, b in zip(seg.speechSpans, seg.speechSpans[1:], strict=False):
            joined = f"{(a.text or '').lower()} {(b.text or '').lower()}"
            if quote in joined:
                span = a
                break
    if span is None:
        return None
    for e in plan.timeline:
        if e.segmentId == seg.segmentId and \
                e.sourceIn - 0.1 <= span.start < e.sourceOut:
            return e.timelineIn + max(0.0, span.start - e.sourceIn) \
                / (e.playbackSpeed or 1.0)
    return None


def coherence_violations(plan, segments: list[Segment],
                         constraints: dict | None = None) -> list[str]:
    """Mayer: coherence d=0.86 (delete extraneous), temporal contiguity
    d=1.31 (text lands WITH its words). The rule is not 'fewer captions' —
    it is 'text that adds what the audio/picture does not'.

    A13: a fact caption needs at least one VERIFIED non-visual evidence — a
    transcript quote actually present in its cited segment, or the user's
    own words. Merely ATTACHING a transcript entry no longer bypasses the
    narration rule."""
    by_id = {s.segmentId: s for s in segments}
    user_text = " ".join(str((constraints or {}).get(k) or "")
                         for k in ("brief", "platform", "tone",
                                   "style")).lower()
    out: list[str] = []
    for i, cap in enumerate(plan.captions):
        where = f"captions[{i}]"
        dur = cap.timelineEnd - cap.timelineStart
        if dur > 0 and len(cap.text) / dur > CAPTION_MAX_CPS:
            out.append(f"coherence: {where} reads at "
                       f"{round(len(cap.text) / dur, 1)} chars/sec (max "
                       f"{CAPTION_MAX_CPS:g}) — shorten the text or extend "
                       "its time")
        if cap.claimType != "fact" or not cap.evidence:
            continue

        def _verified_nonvisual(ev) -> bool:
            quote = ev.quoteOrValue.strip().lower()
            if not quote:
                return False
            if ev.sourceType == "user_input":
                return quote in user_text
            if ev.sourceType != "transcript" or not ev.segmentId:
                return False
            seg = by_id.get(ev.segmentId)
            return seg is not None and quote in (seg.transcript or "").lower()

        if not any(_verified_nonvisual(ev) for ev in cap.evidence):
            out.append(f"coherence: {where} narrates the visible picture "
                       "(no VERIFIED transcript or user quote in its "
                       "evidence) — a caption must add information the "
                       "viewer cannot already see: quote the transcript "
                       "verbatim or drop it")
            continue
        for ev in cap.evidence:
            if ev.sourceType != "transcript" or not ev.segmentId:
                continue
            seg = by_id.get(ev.segmentId)
            if seg is None:
                continue
            expected = _quote_start_on_timeline(
                plan, seg, ev.quoteOrValue.strip().lower())
            if expected is None:
                continue                 # try the next transcript evidence
            if abs(cap.timelineStart - expected) > CAPTION_SYNC_TOLERANCE_S:
                out.append(f"coherence: {where} starts at "
                           f"{round(cap.timelineStart, 2)}s but its quoted "
                           f"words are spoken at {round(expected, 2)}s — "
                           "bind the caption to the words' timing")
            break                        # first RESOLVABLE evidence decides
    return out


# ------------------------------------------------------ tension shape (S5)
_PAYOFF_TOKENS = frozenset(("payoff", "result", "results", "reveal",
                            "closing", "outcome", "after", "finale",
                            "ending"))
_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")


def _is_payoff_beat(name: str) -> bool:
    """A10: WHOLE-token match. Substring matching declared 'rafter_install'
    a payoff beat (contains 'after'), silently passing the tension rules on
    the wrong beat."""
    return any(t in _PAYOFF_TOKENS
               for t in _TOKEN_SPLIT.split((name or "").lower()) if t)


def tension_violations(plan) -> list[str]:
    """Pearlman: pace is DIFFERENTIAL — a flat curve has no fast or slow.
    These rules force shape, not a template: variance floor, payoff above
    the mean, no accidental longueur at the retention cliff."""
    out: list[str] = []
    energies = [(pb.beat, pb.energy) for pb in plan.pacing]
    if len(energies) >= 3:
        vals = [e for _, e in energies]
        if max(vals) - min(vals) < 0.2:
            out.append("tension: pacing energy is flat "
                       f"(range {round(max(vals) - min(vals), 2)} < 0.2) — "
                       "shape an escalation: quieter setup, peaked payoff")
        payoff = [e for b, e in energies if _is_payoff_beat(b)]
        others = [e for b, e in energies if not _is_payoff_beat(b)]
        if payoff and others and max(payoff) < (sum(others) / len(others)) \
                + 0.05:
            out.append("tension: the payoff beat's energy does not rise "
                       "above the rest of the edit — the ending must peak, "
                       "not coast")
    if plan.timeline:
        duration = plan.plannedDurationSeconds
        longest = max(plan.timeline,
                      key=lambda e: e.timelineOut - e.timelineIn)
        if duration > 0 and longest.timelineIn >= 0.75 * duration \
                and not _is_payoff_beat(longest.beat):
            out.append("tension: the longest shot "
                       f"({round(longest.timelineOut - longest.timelineIn, 1)}s)"
                       " sits in the final quarter without being the payoff — "
                       "late longueurs are where viewers leave")
    return out


# --------------------------------------------------- interest gate (S9)
# FLAG DEPENDENCY MODEL (audit A1). Each rule names the subsystem flag that
# provides its INPUTS (the loops schema+prompt, the dialogue repair+data, the
# tension/coherence instructions). A rule whose feeding flag is OFF is
# EXCLUDED and the score is normalized to the applicable weight total — so
# every enabled combination can genuinely reach 100 and the flags are
# independently safe. Rules with feeder None are computable from the plan
# alone and always apply.
_RULE_FEEDER = {
    "hook_from_shortlist": HOOK_FLAG,
    "hook_opens_loop": HOOK_FLAG,
    "loops_closed": HOOK_FLAG,
    "dialogue_clean": DIALOGUE_FLAG,
    "captions_add_info": COHERENCE_FLAG,
    "energy_variance": TENSION_FLAG,
    "payoff_peak": TENSION_FLAG,
    "no_late_longueur": TENSION_FLAG,
    "catalog_utilization": None,
    "shot_length_variety": None,
}


def _interest_threshold() -> int:
    """A5: a malformed env value must degrade to the default, never crash
    the job outside the PlanRejected retry path."""
    raw = os.environ.get("PHASE2_INTEREST_THRESHOLD", "60")
    try:
        return max(0, min(100, int(float(raw))))
    except (TypeError, ValueError):
        return 60


def interest_gate(plan, segments: list[Segment],
                  constraints: dict | None = None) -> dict:
    """Scored FLOOR (threshold env PHASE2_INTEREST_THRESHOLD, default 60),
    normalized over the rules whose feeding flags are enabled.

    Rules are measurable proxies only. Story/emotion judgment deliberately
    stays with the model + rendered critique: scoring what we can compute
    and maximizing it would invert Murch (the measurable 26% dominating the
    unmeasurable 74%)."""
    rules: list[dict] = []

    def rule(name: str, weight: int, passed: bool, detail: str):
        feeder = _RULE_FEEDER[name]
        rules.append({"rule": name, "weight": weight, "passed": passed,
                      "detail": detail, "feeder": feeder,
                      "applicable": feeder is None or enabled(feeder)})

    shortlist = rank_hook_candidates(segments)
    ids = {c["segmentId"] for c in shortlist}
    rule("hook_from_shortlist", 15,
         len(shortlist) < 2 or plan.hook.segmentId in ids,
         "the opening is a ranked hook candidate, not chronology")
    loops = getattr(plan, "loops", []) or []
    rule("hook_opens_loop", 10, primary_loop_opened_by_hook(plan),
         "the hook itself opens the primary curiosity loop")
    rule("loops_closed", 15, bool(loops) and not loop_violations(plan),
         "every opened loop is answered inside the video")
    rule("dialogue_clean", 15, not dialogue_violations(plan, segments),
         "no cut severs a spoken word")
    energies = [pb.energy for pb in plan.pacing]
    rule("energy_variance", 10,
         len(energies) >= 3 and max(energies) - min(energies) >= 0.2,
         "the energy curve has shape (range >= 0.2)")
    payoff = [pb.energy for pb in plan.pacing if _is_payoff_beat(pb.beat)]
    others = [pb.energy for pb in plan.pacing
              if not _is_payoff_beat(pb.beat)]
    rule("payoff_peak", 10,
         bool(payoff) and bool(others)
         and max(payoff) >= (sum(others) / len(others)) + 0.05,
         "the payoff beat peaks above the mean of the rest")
    dur = plan.plannedDurationSeconds
    longest = max(plan.timeline, key=lambda e: e.timelineOut - e.timelineIn) \
        if plan.timeline else None
    rule("no_late_longueur", 5,
         longest is None or dur <= 0
         or longest.timelineIn < 0.75 * dur or _is_payoff_beat(longest.beat),
         "the longest shot is not parked at the retention cliff")
    rule("captions_add_info", 10,
         not [v for v in coherence_violations(plan, segments, constraints)
              if "narrates the visible" in v],
         "no caption narrates what the picture already shows")
    used = {e.segmentId for e in plan.timeline}
    # A11: only judge utilization on catalogs big enough for the ratio to
    # mean anything — a legitimate 3-cut edit of a 3-segment catalog is not
    # "under-utilization", and dropping bad footage is correct editing.
    if len(segments) >= UTILIZATION_MIN_CATALOG:
        floor = len(segments) // 10
        rule("catalog_utilization", 5, len(used) >= floor,
             f"at least {floor} distinct segments considered worth using")
    else:
        rule("catalog_utilization", 5, True,
             f"catalog under {UTILIZATION_MIN_CATALOG} segments — "
             "utilization not judged")
    durs = [e.timelineOut - e.timelineIn for e in plan.timeline]
    if len(durs) >= 4:
        mean = sum(durs) / len(durs)
        var = sum((d - mean) ** 2 for d in durs) / len(durs)
        cv = (var ** 0.5) / mean if mean > 0 else 0.0
        variety = cv >= 0.3
    else:
        variety = True
    rule("shot_length_variety", 5, variety,
         "shot lengths vary (cv >= 0.3), not metronomic")

    applicable = [r for r in rules if r["applicable"]]
    total = sum(r["weight"] for r in applicable)
    earned = sum(r["weight"] for r in applicable if r["passed"])
    score = round(earned * 100 / total) if total else 100
    threshold = _interest_threshold()
    return {"rules": rules, "score": score, "threshold": threshold,
            "applicableWeight": total, "passed": score >= threshold}


# ------------------------------------------------- planner integration
def phase2_violations(plan, segments: list[Segment],
                      constraints: dict | None = None) -> list[str]:
    """All flag-gated hard checks, appended to the planner's violations list
    (any violation forces a revise pass — same loop as Phase 1 grounding)."""
    out: list[str] = []
    if enabled(DIALOGUE_FLAG):
        out += dialogue_violations(plan, segments)
    if enabled(HOOK_FLAG):
        out += hook_violations(plan, segments)
        out += loop_violations(plan)
    if enabled(COHERENCE_FLAG):
        out += coherence_violations(plan, segments, constraints)
    if enabled(TENSION_FLAG):
        out += tension_violations(plan)
    return out


def prompt_parts(segments: list[Segment]) -> list[dict]:
    """Extra prompt sections, only for the flags that are ON. Every number
    and unit here MUST match the validator exactly (audit A15: 'final act'
    was secretly 0.6x; the dialogue rule was graded on word timings the
    model never saw). No promise is enforced only by prose."""
    parts: list[dict] = []
    if enabled(HOOK_FLAG):
        shortlist = rank_hook_candidates(segments)
        if shortlist:
            import json
            forced = (
                "the opening timeline cut MUST use one of these segments"
                if len(shortlist) >= 2 else
                "advisory — a single candidate is shown, not enforced")
            parts.append({"text": (
                f"HOOK CANDIDATES (ranked; {forced}):\n"
                + json.dumps(shortlist)
                + "\nAlso declare `loops`: 1-3 curiosity loops "
                  "{question, openedAtSeconds, openedBySegmentId, "
                  "closedAtSeconds, closedBySegmentId}. ALL loop times are "
                  "TIMELINE seconds (0 = the start of the finished video). "
                  "loops[0] is opened BY THE HOOK: openedBySegmentId is the "
                  "hook's segment and openedAtSeconds falls inside the "
                  "hook's duration. Every loop is ANSWERED by a later "
                  "timeline moment before the video ends; loops[0] closes "
                  f"in the final "
                  f"{round((1 - PRIMARY_LOOP_CLOSE_FRACTION) * 100):d}% of "
                  "the video. The viewerPromise must promise an unresolved "
                  "outcome, never describe the video's contents.")})
    if enabled(DIALOGUE_FLAG):
        parts.append({"text": (
            "DIALOGUE INTEGRITY (validated): no sourceIn/sourceOut may land "
            "inside a spoken word. The per-segment `speech` spans list "
            "sentence start/end times and `speechFree` lists silent gaps — "
            "cutting exactly at a span edge or anywhere inside a speechFree "
            "gap is always safe. Let people finish their sentences — a "
            "clipped word is charged to the speaker's credibility.")})
    if enabled(COHERENCE_FLAG):
        parts.append({"text": (
            "CAPTIONS (validated): a fact caption needs at least one "
            "VERIFIED quote — a short verbatim transcript excerpt actually "
            "present in the cited segment (or the user's own words). NEVER "
            "describe what is visible on screen. Start each caption within "
            f"{CAPTION_SYNC_TOLERANCE_S}s of when its quoted words are "
            f"spoken. Max {CAPTION_MAX_CPS:g} characters per second of "
            "display time.")})
    if enabled(TENSION_FLAG):
        parts.append({"text": (
            "TENSION (validated): pacing energies must have shape — "
            "max-min >= 0.2 across beats, the payoff beat's energy at "
            "least 0.05 above the MEAN of the other beats, and the longest "
            "shot must not START in the final 25% of the timeline unless "
            "its beat IS the payoff. Put a quiet beat immediately before "
            "the payoff: contrast is what makes it land.")})
    return parts


def catalog_extras(seg: Segment) -> dict:
    """Per-segment additions to the planner's catalog projection, flag-gated
    (prompt size is a real constraint — only ship what a flag will use)."""
    extra: dict = {}
    if enabled(DIALOGUE_FLAG):
        extra["speech"] = [[round(sp.start, 2), round(sp.end, 2)]
                           for sp in seg.speechSpans]
        extra["speechFree"] = [[round(sp.start, 2), round(sp.end, 2)]
                               for sp in seg.speechFreeRanges]
    if enabled(TENSION_FLAG) and seg.motionPeaks:
        extra["motionPeaks"] = seg.motionPeaks[:5]
    return extra


def loops_schema_fragment() -> dict:
    """Gemini structured-output subtree for `loops` (core call, small)."""
    def s(t, **kw):
        return {"type": t, **kw}
    return s("ARRAY", items=s("OBJECT", properties={
        "question": s("STRING"),
        "openedAtSeconds": s("NUMBER"),
        "openedBySegmentId": s("STRING"),
        "closedAtSeconds": s("NUMBER"),
        "closedBySegmentId": s("STRING")},
        required=["question", "openedAtSeconds", "openedBySegmentId",
                  "closedAtSeconds", "closedBySegmentId"]))
