"""Picture Edit Engine V2 — turns an APPROVED EditorialPlan into the real
picture timeline.

Pipeline position:
    analysis -> Editorial Planner -> approved EditorialPlan
             -> Picture Edit Engine V2 (this module)
             -> timeline_json -> existing bridge -> Product Editor -> export

The engine is PURE and DETERMINISTIC: same (project, plan version, source
catalog, engine version) always produces the same PictureEditV2 result and
deterministic hash.

Contract note — canonical vs compatibility fields: `actualDuration` and
`requestedDuration` {min,max} are the CANONICAL duration fields;
`actualDurationSeconds` / `requestedDurationMin` / `requestedDurationMax` are
flat COMPATIBILITY ALIASES carrying the same values for scalar-preferring
consumers. New consumers should read the canonical fields. It never invents footage, never reorders the approved
beats, never falls back to the old selector, and fails loudly with the exact
validation reason when the plan is missing, unapproved, ungrounded against the
CURRENT catalog, or impossible to execute.

Execution honesty:
  * constant per-clip playback speed, trims, order, hook and duration are
    EXECUTED into the timeline
  * hard_cut / dissolve / dip_to_black / dip_to_white transitions and
    static / pan-interpolated crops are executable by the V2 preview renderer
  * variable-speed ramps, advanced transitions (whip/push/slide/zoom/
    match_cut/masked_reveal/audio_led_cut) and zoom-interpolated crops are
    preserved as STRUCTURED pending instructions — surfaced, never silently
    replaced or claimed as applied
"""
from __future__ import annotations

import hashlib
import json

from .editorial_planner import APPROVAL_THRESHOLD, EditorialPlan, TIME_EPSILON
from .schemas import Segment

SCHEMA_VERSION = 1
# ENGINE_VERSION participates in the deterministic hash and the idempotency
# identity: ANY change to the output payload or its semantics MUST bump this,
# or previously persisted results would collide/miss across engine revisions.
# 2.1.0: added actualDuration/requestedDuration canonical fields and measured
#        actualEnergy/energyDeviation pacing metrics (payload change).
ENGINE_VERSION = "2.3.0"   # 2.3.0: clips carry segmentId; transition
# instructions carry planBoundaryIndex + splice-remapped boundaryIndex
# (2.2.0: b-roll execution, audio-under clips)
DURATION_TOLERANCE = 0.1
RAMP_CONSISTENCY_TOLERANCE = 0.25
# a beat's actual duration may deviate this much from the approved pacing
# target before the timeline is rejected as violating the plan
PACING_HARD_TOLERANCE = lambda target: max(2.0, 0.35 * target)  # noqa: E731
EXECUTABLE_TRANSITIONS = {"hard_cut", "dissolve", "dip_to_black", "dip_to_white"}


class PictureEditRejected(RuntimeError):
    """The engine refuses to build a timeline. `reasons` is the exact list."""

    def __init__(self, reasons: list[str]):
        self.reasons = reasons
        super().__init__("picture edit rejected: " + "; ".join(reasons[:6]))


def catalog_hash(segments: list[Segment]) -> str:
    """Stable hash of the source catalog the plan is executed against."""
    canonical = sorted(
        ([s.segmentId, s.assetId, round(s.sourceStart, 3), round(s.sourceEnd, 3)]
         for s in segments), key=lambda r: r[0])
    return hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()


def _hash(payload) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def _load_plan(plan_row: dict) -> tuple[EditorialPlan, list[str]]:
    reasons: list[str] = []
    if not plan_row or not isinstance(plan_row, dict):
        return None, ["no persisted editorial plan — run the Editorial Planner "
                      "first"]
    status = plan_row.get("status")
    if status == "insufficient_footage":
        plan_json = plan_row.get("plan") or {}
        missing = plan_json.get("missingFootage") or []
        shots = "; ".join(f"{m.get('beat')}: {m.get('shotType')} "
                          f"({m.get('recommendedDurationSeconds')}s) — {m.get('why')}"
                          for m in missing)
        return None, ["the approved plan honestly reports insufficient footage "
                      f"(achievable {plan_json.get('achievableDurationSeconds')}s) "
                      "— no timeline can honor the request until the missing "
                      f"shots are filmed: {shots or 'unspecified'}"]
    if status != "approved":
        return None, [f"editorial plan status is {status!r}, not 'approved'"]
    gate = ((plan_row.get("validation") or {}).get("deterministicGate") or {})
    if not gate.get("passed"):
        return None, ["the plan did not pass the deterministic quality gate "
                      f"(score {gate.get('score')}, threshold {APPROVAL_THRESHOLD}, "
                      f"hard failures {gate.get('hardFailures')})"]
    try:
        plan = EditorialPlan(**(plan_row.get("plan") or {}))
    except Exception as exc:  # noqa: BLE001 — surfaced as the exact reason
        return None, [f"persisted plan does not validate against the "
                      f"EditorialPlan schema: {str(exc)[:300]}"]
    return plan, reasons


def build_picture_edit(plan_row: dict, segments: list[Segment], *,
                       now: str) -> dict:
    """Build the PictureEditV2 result from the approved plan + CURRENT catalog.

    Raises PictureEditRejected with the exact reasons on any violation.
    ``now`` is injected (excluded from the deterministic hash).
    """
    plan, reasons = _load_plan(plan_row)
    if plan is None:
        raise PictureEditRejected(reasons)
    by_id = {s.segmentId: s for s in segments}
    request = plan_row.get("request") or {}
    warnings: list[str] = list(w.text for w in plan.technicalWarnings)
    trim_adjustments: list[dict] = []

    # ---- timeline grounding against the CURRENT catalog (it may have changed
    # since planning) — order, trims and speeds come from the plan verbatim
    clips: list[dict] = []
    mappings: list[dict] = []
    seen_ranges: set[tuple[str, float, float]] = set()
    planned_cuts: dict[str, list[tuple[float, float, float]]] = {}
    cursor = 0.0
    for i, seg in enumerate(plan.timeline):
        src = by_id.get(seg.segmentId)
        if src is None:
            reasons.append(f"timeline[{i}] references segment {seg.segmentId!r} "
                           "which is absent from the current source catalog")
            continue
        if seg.assetId != src.assetId:
            reasons.append(f"timeline[{i}] asset ancestry mismatch: plan says "
                           f"{seg.assetId}, catalog says {src.assetId}")
        s_in, s_out = seg.sourceIn, seg.sourceOut
        # safe refinement ONLY: clamp epsilon-rounding back inside the real
        # source bounds; anything larger is an impossible mapping
        if s_in < src.sourceStart:
            if src.sourceStart - s_in <= TIME_EPSILON:
                trim_adjustments.append(
                    {"segmentId": seg.segmentId, "field": "sourceIn",
                     "from": s_in, "to": src.sourceStart,
                     "reason": "clamped inside real source start (epsilon)"})
                s_in = src.sourceStart
            else:
                reasons.append(f"timeline[{i}] trims {seg.segmentId} before its "
                               f"real source start ({s_in} < {src.sourceStart})")
        if s_out > src.sourceEnd:
            if s_out - src.sourceEnd <= TIME_EPSILON:
                trim_adjustments.append(
                    {"segmentId": seg.segmentId, "field": "sourceOut",
                     "from": s_out, "to": src.sourceEnd,
                     "reason": "clamped inside real source end (epsilon)"})
                s_out = src.sourceEnd
            else:
                reasons.append(f"timeline[{i}] trims {seg.segmentId} past its "
                               f"real source end ({s_out} > {src.sourceEnd})")
        if s_in >= s_out:
            reasons.append(f"timeline[{i}] has an empty or inverted source range")
            continue
        key = (seg.segmentId, round(s_in, 2), round(s_out, 2))
        if key in seen_ranges:
            reasons.append(f"timeline[{i}] repeats an identical source range of "
                           f"{seg.segmentId} — duplicated footage is rejected")
        seen_ranges.add(key)
        if abs(seg.timelineIn - cursor) > TIME_EPSILON:
            reasons.append(f"timeline[{i}] breaks continuity: starts at "
                           f"{seg.timelineIn}s, expected {round(cursor, 3)}s")
        expected = (s_out - s_in) / seg.playbackSpeed
        if abs((seg.timelineOut - seg.timelineIn) - expected) > 2 * TIME_EPSILON:
            reasons.append(f"timeline[{i}] speed-adjusted duration is impossible: "
                           f"({s_out}-{s_in})/{seg.playbackSpeed} != "
                           f"{seg.timelineOut}-{seg.timelineIn}")
        planned_cuts.setdefault(seg.segmentId, []).append(
            (s_in, s_out, seg.playbackSpeed))
        clips.append({"id": f"pe2-{i:03d}-{seg.segmentId}",
                      "segmentId": seg.segmentId,
                      "assetId": seg.assetId,
                      "sourceStart": round(s_in, 3), "sourceEnd": round(s_out, 3),
                      "timelineStart": round(seg.timelineIn, 3),
                      "timelineEnd": round(seg.timelineOut, 3),
                      "speed": seg.playbackSpeed, "volume": 1.0})
        mappings.append({"order": i, "segmentId": seg.segmentId,
                         "assetId": seg.assetId, "beat": seg.beat,
                         "sourceIn": round(s_in, 3), "sourceOut": round(s_out, 3),
                         "timelineIn": round(seg.timelineIn, 3),
                         "timelineOut": round(seg.timelineOut, 3),
                         "playbackSpeed": seg.playbackSpeed,
                         "reason": seg.reason})
        cursor = seg.timelineOut

    actual_duration = round(cursor, 3)

    # ---- Phase 3 b-roll execution (plan-driven, no env check: presence of
    # system-authored brollInsertions in the APPROVED plan decides). The
    # b-roll COVERS the host picture while the host clip's speech continues
    # underneath (audioFrom) — total duration is unchanged; the covered
    # stretch of host picture is simply never shown. Payload change ->
    # ENGINE_VERSION bumped per the bump-on-change rule.
    broll_applied: list[dict] = []
    # descending target order: splicing 1 clip into 3 shifts later indices,
    # so higher indices are consumed first and every targetIndex stays valid
    for b in sorted(getattr(plan, "brollInsertions", []) or [],
                    key=lambda x: -x.targetIndex):
        if not 0 <= b.targetIndex < len(clips):
            reasons.append(f"broll {b.id}: target entry {b.targetIndex} "
                           "does not exist in the built timeline")
            continue
        host = clips[b.targetIndex]
        src = by_id.get(b.brollSegmentId)
        if src is None:
            reasons.append(f"broll {b.id}: segment {b.brollSegmentId!r} is "
                           "absent from the current source catalog")
            continue
        if not (src.sourceStart - TIME_EPSILON <= b.sourceStart
                and b.sourceEnd <= src.sourceEnd + TIME_EPSILON):
            reasons.append(f"broll {b.id}: trims outside its segment's real "
                           "range in the current catalog")
            continue
        sp = float(host.get("speed") or 1.0)
        host_dur = host["timelineEnd"] - host["timelineStart"]
        o, d = b.offsetSeconds, b.durationSeconds
        if o <= 0 or o + d >= host_dur:
            reasons.append(f"broll {b.id}: does not fit inside its host clip")
            continue
        t0 = host["timelineStart"]
        a1 = dict(host,
                  sourceEnd=round(host["sourceStart"] + o * sp, 3),
                  timelineEnd=round(t0 + o, 3))
        bclip = {"id": f"pe2b-{b.targetIndex:03d}-{b.brollSegmentId}",
                 "segmentId": b.brollSegmentId,
                 "assetId": b.assetId,
                 "sourceStart": round(b.sourceStart, 3),
                 "sourceEnd": round(b.sourceStart + d, 3),
                 "timelineStart": round(t0 + o, 3),
                 "timelineEnd": round(t0 + o + d, 3),
                 "speed": 1.0, "volume": 0.0,
                 # the host's voice continues under the b-roll picture
                 "audioFrom": {"assetId": host["assetId"],
                               "sourceStart": round(
                                   host["sourceStart"] + o * sp, 3),
                               "sourceEnd": round(
                                   host["sourceStart"] + (o + d) * sp, 3),
                               "speed": sp}}
        a2 = dict(host,
                  id=host["id"] + "-post",
                  sourceStart=round(host["sourceStart"] + (o + d) * sp, 3),
                  timelineStart=round(t0 + o + d, 3))
        clips[b.targetIndex:b.targetIndex + 1] = [a1, bclip, a2]
        broll_applied.append({
            "id": b.id, "hostClipId": host["id"],
            "brollSegmentId": b.brollSegmentId, "assetId": b.assetId,
            "timelineStart": bclip["timelineStart"],
            "timelineEnd": bclip["timelineEnd"],
            "motivation": b.motivation, "confidence": b.confidence,
            "origin": b.origin, "matchedTokens": b.matchedTokens,
            "audioUnder": True})

    # ---- hook execution: timeline zero IS the planned hook
    if plan.timeline:
        first = plan.timeline[0]
        if first.timelineIn > TIME_EPSILON:
            reasons.append("the timeline does not start at 0 — the hook must be "
                           "the first frame")
        if plan.hook.segmentId != first.segmentId \
                or abs(plan.hook.sourceIn - first.sourceIn) > TIME_EPSILON:
            reasons.append("timeline[0] is not the planned hook (segment "
                           f"{plan.hook.segmentId} @ {plan.hook.sourceIn}s) — "
                           "chronological footage may not replace the hook")
        if plan.hook.durationSeconds > 5 + TIME_EPSILON:
            reasons.append("hook exceeds the 5s hook duration limit")

    # ---- duration is binding: computed truth vs plan vs ORIGINAL request
    if abs(actual_duration - plan.plannedDurationSeconds) > DURATION_TOLERANCE:
        reasons.append(f"computed timeline duration {actual_duration}s does not "
                       f"match the approved plan ({plan.plannedDurationSeconds}s)")
    lo, hi = request.get("durationMin"), request.get("durationMax")
    if lo is not None and actual_duration < float(lo) - DURATION_TOLERANCE:
        reasons.append(f"timeline ({actual_duration}s) is materially shorter "
                       f"than the requested minimum ({lo}s)")
    if hi is not None and actual_duration > float(hi) + DURATION_TOLERANCE:
        reasons.append(f"timeline ({actual_duration}s) is materially longer "
                       f"than the requested maximum ({hi}s)")

    # ---- pacing engine: beat-level targets from the approved plan.
    # Actual delivered energy is measured deterministically from the footage
    # itself: duration-weighted motion intensity of the used segments blended
    # with cut density (2 cuts/second saturates the cutting contribution).
    pacing_metrics: list[dict] = []
    by_beat: dict[str, list[dict]] = {}
    for m in mappings:
        by_beat.setdefault(m["beat"], []).append(m)
    for pb in plan.pacing:
        beat_maps = by_beat.get(pb.beat, [])
        actual = sum(m["timelineOut"] - m["timelineIn"] for m in beat_maps)
        shots = len(beat_maps)
        deviation = round(actual - pb.targetDurationSeconds, 3)
        density = round(shots / actual, 3) if actual else 0.0
        if actual > 0:
            motion = sum((m["timelineOut"] - m["timelineIn"])
                         * (by_id[m["segmentId"]].motionIntensity
                            if m["segmentId"] in by_id else 0.0)
                         for m in beat_maps) / actual
        else:
            motion = 0.0
        actual_energy = round(min(1.0, 0.6 * motion
                                  + 0.4 * min(1.0, density / 2.0)), 3)
        pacing_metrics.append({
            "beat": pb.beat, "targetDurationSeconds": pb.targetDurationSeconds,
            "actualDurationSeconds": round(actual, 3),
            "energyTarget": pb.energy,
            "actualEnergy": actual_energy,
            "energyDeviation": round(actual_energy - pb.energy, 3),
            "shotCount": shots, "shotDensity": density,
            "deviationSeconds": deviation})
        if abs(deviation) > PACING_HARD_TOLERANCE(pb.targetDurationSeconds):
            reasons.append(f"pacing materially violates the approved plan: beat "
                           f"{pb.beat!r} runs {round(actual, 2)}s against a "
                           f"{pb.targetDurationSeconds}s target")

    # ---- continuity findings (advisory, deterministic)
    continuity: list[str] = []
    use_counts: dict[str, int] = {}
    last_shot, run = None, 0
    prev_by_asset: dict[str, float] = {}
    for m in mappings:
        use_counts[m["segmentId"]] = use_counts.get(m["segmentId"], 0) + 1
        src = by_id.get(m["segmentId"])
        if src is None:
            continue
        if src.shotType:
            if src.shotType == last_shot:
                run += 1
                if run == 3:
                    continuity.append(f"three consecutive {src.shotType!r} shots "
                                      f"ending at {m['segmentId']} — consider "
                                      "wide/medium/close variation")
            else:
                last_shot, run = src.shotType, 1
        prior = prev_by_asset.get(m["assetId"])
        if prior is not None and m["sourceIn"] < prior - TIME_EPSILON:
            continuity.append(f"{m['segmentId']} jumps backwards in source time "
                              f"within asset {m['assetId']} — verify the "
                              "non-chronological order is intentional")
        prev_by_asset[m["assetId"]] = m["sourceOut"]
    for sid, n in sorted(use_counts.items()):
        if n > 1:
            continuity.append(f"segment {sid} is used {n} times — verify the "
                              "repetition adds information")

    # ---- transitions: executable now vs preserved pending instructions
    transition_instructions: list[dict] = []
    unsupported: list[dict] = []
    tl_ids = [m["segmentId"] for m in mappings]
    boundaries = set()
    for i, tr in enumerate(plan.transitions):
        key = (tr.fromSegmentId, tr.toSegmentId)
        if key in boundaries:
            reasons.append(f"transitions[{i}] duplicates the boundary "
                           f"{key[0]}->{key[1]}")
        boundaries.add(key)
        join = next((j for j in range(len(tl_ids) - 1)
                     if tl_ids[j] == tr.fromSegmentId
                     and tl_ids[j + 1] == tr.toSegmentId), None)
        if join is None:
            reasons.append(f"transitions[{i}] {tr.fromSegmentId}->"
                           f"{tr.toSegmentId} does not join adjacent segments")
            continue
        # boundaryIndex is the FINAL-clip-list boundary the renderer keys
        # xfade on. B-roll splices expand one plan entry into three clips,
        # so the plan-entry index (`join`) and the final index diverge —
        # remap to the LAST final clip that originates from plan entry
        # `join` (clip ids embed the plan index: pe2-### / pe2b-###).
        # Audit blocker repro: a dissolve at plan boundary 1 rendered at
        # final boundary 1 (host->b-roll) instead of 3 (host->next).
        final_boundary = join
        for f_idx, c in enumerate(clips):
            try:
                if int(str(c["id"]).split("-")[1]) == join:
                    final_boundary = f_idx
            except (IndexError, ValueError):
                continue
        inst = {"fromSegmentId": tr.fromSegmentId, "toSegmentId": tr.toSegmentId,
                "type": tr.type, "durationSeconds": tr.durationSeconds,
                "purpose": tr.purpose, "boundaryIndex": final_boundary,
                "planBoundaryIndex": join}
        if tr.type in EXECUTABLE_TRANSITIONS:
            if tr.type != "hard_cut":
                out_m, in_m = mappings[join], mappings[join + 1]
                out_src, in_src = by_id.get(out_m["segmentId"]), by_id.get(in_m["segmentId"])
                if out_src and in_src:
                    handle = min(out_src.sourceEnd - out_m["sourceOut"],
                                 in_m["sourceIn"] - in_src.sourceStart)
                    if tr.durationSeconds > handle + TIME_EPSILON:
                        reasons.append(f"transitions[{i}] ({tr.type}) needs "
                                       f"{tr.durationSeconds}s of source handle "
                                       f"but only {round(handle, 2)}s exists")
            inst["status"] = "executable"
        else:
            inst["status"] = "pending_renderer_support"
            inst["note"] = (f"{tr.type} is a valid planned transition the "
                            "renderer cannot execute yet — preserved, NOT "
                            "replaced")
            unsupported.append(inst)
        transition_instructions.append(inst)

    # ---- speed ramps: validated structured instructions (variable-speed
    # rendering is pending renderer support — never claimed as applied)
    speed_instructions: list[dict] = []
    for i, ramp in enumerate(plan.speedRamps):
        cuts = planned_cuts.get(ramp.segmentId, [])
        host = next(((a, b, spd) for a, b, spd in cuts
                     if ramp.sourceStart >= a - TIME_EPSILON
                     and ramp.sourceEnd <= b + TIME_EPSILON), None)
        if host is None:
            reasons.append(f"speedRamps[{i}] range is outside every planned cut "
                           f"of {ramp.segmentId}")
            continue
        span = ramp.sourceEnd - ramp.sourceStart
        avg_speed = (ramp.entrySpeed + 2 * ramp.peakSpeed + ramp.exitSpeed) / 4
        effective = span / avg_speed
        constant = span / host[2]
        if abs(effective - constant) > RAMP_CONSISTENCY_TOLERANCE:
            reasons.append(
                f"speedRamps[{i}] would change the segment's duration "
                f"({round(effective, 2)}s ramped vs {round(constant, 2)}s at the "
                f"clip's constant speed) — the plan's timeline math must "
                "account for the ramp")
        inst = {"segmentId": ramp.segmentId, "sourceStart": ramp.sourceStart,
                "sourceEnd": ramp.sourceEnd, "entrySpeed": ramp.entrySpeed,
                "peakSpeed": ramp.peakSpeed, "exitSpeed": ramp.exitSpeed,
                "easing": ramp.easing, "narrativePurpose": ramp.narrativePurpose,
                "effectiveDurationSeconds": round(effective, 3),
                "status": "pending_renderer_support",
                "note": "variable-speed rendering pending; constant clip speed "
                        "is applied on the timeline"}
        if min(ramp.entrySpeed, ramp.peakSpeed, ramp.exitSpeed) < 1.0:
            inst["warning"] = ("slow motion requested but source frame rate "
                               "metadata is unavailable — smoothness unverified")
            warnings.append(f"speed ramp on {ramp.segmentId}: " + inst["warning"])
        speed_instructions.append(inst)
        unsupported.append(inst)

    # ---- reframes: static + pan-interpolated crops are executable; zoom
    # interpolation is preserved as a pending instruction
    reframe_instructions: list[dict] = []
    for i, rf in enumerate(plan.reframes):
        if rf.segmentId not in planned_cuts:
            reasons.append(f"reframes[{i}] references unplanned segment "
                           f"{rf.segmentId!r}")
            continue
        sc, ec = rf.startCrop, rf.endCrop
        for name, crop in (("startCrop", sc), ("endCrop", ec)):
            if crop.x + crop.width > 1 + 1e-6 or crop.y + crop.height > 1 + 1e-6:
                reasons.append(f"reframes[{i}].{name} exceeds the frame — "
                               "out-of-frame geometry is rejected")
        static = (abs(sc.x - ec.x) < 1e-6 and abs(sc.y - ec.y) < 1e-6
                  and abs(sc.width - ec.width) < 1e-6
                  and abs(sc.height - ec.height) < 1e-6)
        pan = (abs(sc.width - ec.width) < 1e-6
               and abs(sc.height - ec.height) < 1e-6 and not static)
        mode = "static" if static else "pan_interpolated" if pan \
            else "zoom_interpolated"
        inst = {"segmentId": rf.segmentId, "mode": mode,
                "outputAspectRatio": rf.outputAspectRatio,
                "subjectTarget": rf.subjectTarget,
                "startCrop": sc.model_dump(), "endCrop": ec.model_dump(),
                "trackingMode": rf.trackingMode, "safeArea": rf.safeArea,
                "status": "executable" if mode != "zoom_interpolated"
                          else "pending_renderer_support",
                "warning": "source resolution metadata unavailable — upscale "
                           "risk unverified"}
        warnings.append(f"reframe on {rf.segmentId}: source resolution unknown; "
                        "upscale risk unverified")
        if mode == "zoom_interpolated":
            inst["note"] = ("zoom interpolation pending renderer support — "
                            "preserved, NOT replaced")
            unsupported.append(inst)
        reframe_instructions.append(inst)

    if reasons:
        raise PictureEditRejected(reasons)

    timeline = {"version": 1, "width": plan.render.width,
                "height": plan.render.height, "fps": plan.render.fps,
                "duration": actual_duration,
                "tracks": [{"id": "v", "type": "video", "clips": clips}]}

    src_hash = catalog_hash(segments)
    core = {"schemaVersion": SCHEMA_VERSION, "engineVersion": ENGINE_VERSION,
            "projectId": plan_row.get("project_id"),
            "editorialPlanId": plan_row.get("id"),
            "editorialPlanVersion": plan_row.get("version"),
            "sourceCatalogHash": src_hash, "timeline": timeline,
            "segmentMappings": mappings,
            "speedInstructions": speed_instructions,
            "transitionInstructions": transition_instructions,
            "reframeInstructions": reframe_instructions,
            # canonical duration fields of the PictureEditV2 contract
            "actualDuration": actual_duration,
            "requestedDuration": {"min": request.get("durationMin"),
                                  "max": request.get("durationMax")},
            # COMPATIBILITY ALIASES (not canonical): flat duplicates of the two
            # canonical fields above, kept for consumers that prefer scalars.
            "actualDurationSeconds": actual_duration,
            "requestedDurationMin": request.get("durationMin"),
            "requestedDurationMax": request.get("durationMax"),
            "pacingMetrics": pacing_metrics,
            "continuityFindings": continuity,
            "technicalWarnings": warnings,
            "unsupportedExecution": unsupported,
            "trimAdjustments": trim_adjustments,
            "brollApplied": broll_applied}
    return {**core, "deterministicHash": _hash(core), "createdAt": now}
