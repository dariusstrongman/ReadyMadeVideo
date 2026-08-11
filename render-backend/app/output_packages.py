"""Output packages — executing an accepted recommendation as real deliverables.

The engine (pipeline/output_intelligence.py) decides WHAT could be made; this
module owns the lifecycle of what the customer chose: persistence, sequential
execution through the EXISTING pipeline, honest derived status, idempotent
retry, and staleness protection.

Execution model, deliberately minimal: one deliverable at a time per project.
Each deliverable is one `editorial_plan` job carrying `deliverable_id`; the
existing customer-journey chain (plan approval -> autoedit -> timeline +
bridged candidate) runs unchanged, and the job hooks below record the exact
plan/timeline identities onto the deliverable as they are produced. The
pipeline_jobs_active_uniq index (one active job per project+kind) makes the
sequencing race-free without any new locking.

Flag-gated: OUTPUT_INTELLIGENCE_ENABLED, default OFF. Off means no endpoint
responds, no recommendation is written, and analysis chains exactly as today.
"""
from __future__ import annotations

import hashlib
import json
import os

from . import supa
from .pipeline import output_intelligence as oi
from .pipeline.schemas import Segment

FLAG = "OUTPUT_INTELLIGENCE_ENABLED"


def _insert(table: str, body: dict):
    """Service-role insert via the jobs helper (deferred import: jobs.py
    imports this module's hooks, so a top-level import would be circular)."""
    from .jobs import _insert as jobs_insert
    return jobs_insert(table, body)

# Deliverable statuses (DB check constraint mirrors this list).
QUEUED, PLANNING, EDITING = "queued", "planning", "editing"
READY, FAILED, CANCELLED, BUDGET_BLOCKED = ("ready", "failed", "cancelled",
                                            "budget_blocked")
ACTIVE_CHILD = (PLANNING, EDITING)
TERMINAL = (READY, FAILED, CANCELLED, BUDGET_BLOCKED)


def enabled() -> bool:
    return os.environ.get(FLAG, "").lower() in ("1", "true", "yes", "on")


class SelectionRejected(Exception):
    """The requested outputs are not honestly supported by the footage."""

    def __init__(self, results: list[oi.FeasibilityResult]):
        self.results = results
        super().__init__("selection rejected by feasibility")


class StaleRecommendation(Exception):
    """The catalog changed since this recommendation was computed."""


def _load_segments(project_id: str) -> list[Segment]:
    rows = supa.db_select("segments", f"project_id=eq.{project_id}"
                                      "&select=data&order=segment_key.asc")
    return [Segment(**r["data"]) for r in rows]


# ---------------------------------------------------------------- recommendation
def generate_recommendation(project: dict) -> dict:
    """Compute (or return the existing) recommendation for the CURRENT catalog.

    Idempotent per (project, catalog_hash, engine_version) — recomputing over
    unchanged footage returns the stored row, so accepting is always racing
    against a stable identity, and nothing is paid twice (the engine is free,
    but the identity discipline is what staleness detection hangs off).
    """
    segments = _load_segments(project["id"])
    if not segments:
        raise RuntimeError("no segment catalog — run analysis first")
    rec = oi.recommend(segments)
    existing = supa.db_select(
        "output_recommendations",
        f"project_id=eq.{project['id']}&catalog_hash=eq.{rec.catalogHash}"
        f"&engine_version=eq.{oi.ENGINE_VERSION}&limit=1")
    if existing:
        return existing[0]
    # Anything computed from an older catalog is superseded the moment a
    # fresh catalog produces a recommendation.
    supa.db_update("output_recommendations",
                   f"project_id=eq.{project['id']}&status=eq.active",
                   {"status": "superseded"})
    body = rec.to_json()
    r = _insert("output_recommendations", {
        "project_id": project["id"], "user_id": project["user_id"],
        "engine_version": oi.ENGINE_VERSION, "catalog_hash": rec.catalogHash,
        "status": "active", "inventory": body["inventory"],
        "packages": body["packages"], "recommended_key": body["recommendedKey"],
    })
    if r.status_code == 409:      # concurrent identical compute: return theirs
        again = supa.db_select(
            "output_recommendations",
            f"project_id=eq.{project['id']}&catalog_hash=eq.{rec.catalogHash}"
            f"&engine_version=eq.{oi.ENGINE_VERSION}&limit=1")
        if again:
            return again[0]
    r.raise_for_status()
    return r.json()[0]


def recommendation_staleness(project_id: str, recommendation: dict) -> bool:
    """True when the stored recommendation no longer matches the live catalog."""
    segments = _load_segments(project_id)
    return oi.catalog_hash(segments) != recommendation["catalog_hash"]


# ---------------------------------------------------------------- creation
def _request_key(recommendation_id: str, selection: list[dict]) -> str:
    canonical = json.dumps({"rec": recommendation_id, "sel": selection},
                           sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _specs_from_selection(selection: list[dict],
                          segments: list[Segment]) -> list[dict]:
    """Expand the customer's selection into per-deliverable intent contracts.

    Shorts map onto the top-ranked discovered moments (recomputed
    deterministically — identical to the recommendation while the catalog is
    unchanged, which staleness checking guarantees). Every spec carries the
    fields the planner genuinely consumes today plus the opportunity evidence
    for audit."""
    inv = oi.build_inventory(segments)
    shorts = oi.discover_shorts(segments, inv)
    longs = oi.assess_long_form(segments, inv)
    specs: list[dict] = []
    short_cursor = 0
    long_used: set[str] = set()
    for item in selection:
        kind = item.get("kind")
        if kind == "long_form":
            pool = [o for o in longs if o.opportunityId not in long_used]
            opp = next((o for o in pool
                        if o.opportunityId == item.get("opportunityId")),
                       pool[0] if pool else None)
            if opp is None:
                continue                    # feasibility already rejected this
            long_used.add(opp.opportunityId)
            lo, hi = opp.feasibleDurationS
            target = float(item.get("durationTargetS")
                           or opp.recommendedDurationS)
            target = max(lo, min(hi, target))
            specs.append({
                "kind": "long_form", "opportunityId": opp.opportunityId,
                "purpose": opp.purpose,
                "aspectRatio": item.get("aspect") or opp.recommendedAspect,
                "platform": item.get("platform") or opp.recommendedPlatform,
                "durationMin": int(max(10, target * 0.85)),
                "durationMax": int(target * 1.15),
                "supportingSegmentIds": opp.supportingSegmentIds,
                "reason": opp.reason,
            })
        elif kind == "short_form":
            n = int(item.get("quantity") or 1)
            for _ in range(n):
                if short_cursor >= len(shorts):
                    break                   # feasibility already bounded this
                opp = shorts[short_cursor]
                short_cursor += 1
                lo, hi = opp.feasibleDurationS
                target = float(item.get("durationTargetS")
                               or opp.recommendedDurationS)
                target = max(lo, min(hi, target))
                seed = next((s for s in segments
                             if s.segmentId == opp.hookSegmentIds[0]), None)
                # Binding scope: the planner's mustInclude matches this text
                # against selected segments, so the plan MUST contain the
                # moment this short exists for (or honestly fail).
                must = ([seed.action[:60]]
                        if seed and seed.action and " " in seed.action else [])
                specs.append({
                    "kind": "short_form", "opportunityId": opp.opportunityId,
                    "purpose": opp.purpose,
                    "aspectRatio": item.get("aspect") or opp.recommendedAspect,
                    "platform": item.get("platform") or opp.recommendedPlatform,
                    "durationMin": int(max(5, min(lo, target * 0.85))),
                    "durationMax": int(max(target * 1.15, lo + 5)),
                    "mustInclude": must,
                    "supportingSegmentIds": opp.supportingSegmentIds,
                    "sourceRange": opp.sourceRange,
                    "reason": opp.reason,
                })
    return specs


def create_package(project: dict, recommendation_id: str,
                   selection: list[dict]) -> dict:
    """Accept/customize: feasibility-checked, stale-proof, idempotent.

    Returns {package, deliverables, created:bool}. Raises SelectionRejected
    (with per-item verdicts) or StaleRecommendation.
    """
    recs = supa.db_select("output_recommendations",
                          f"id=eq.{recommendation_id}"
                          f"&project_id=eq.{project['id']}&limit=1")
    if not recs:
        raise LookupError("recommendation not found for this project")
    rec = recs[0]
    segments = _load_segments(project["id"])
    live_hash = oi.catalog_hash(segments)
    if live_hash != rec["catalog_hash"]:
        raise StaleRecommendation(
            "the footage changed after this recommendation was made — "
            "request a fresh recommendation")

    results = oi.check_selection(selection, segments)
    if any(r.verdict in (oi.IMPOSSIBLE, oi.NOT_RECOMMENDED) for r in results):
        raise SelectionRejected(results)
    feasibility = [r.to_json() for r in results
                   if r.verdict == oi.SUPPORTED_WITH_CONSTRAINTS]

    key = _request_key(recommendation_id, selection)
    existing = supa.db_select("output_packages",
                              f"project_id=eq.{project['id']}"
                              f"&request_key=eq.{key}&limit=1")
    if existing:
        return {"package": existing[0],
                "deliverables": list_deliverables(existing[0]["id"]),
                "created": False}

    specs = _specs_from_selection(selection, segments)
    if not specs:
        raise SelectionRejected([oi.FeasibilityResult(
            oi.IMPOSSIBLE, [{"code": "empty_selection",
                             "message": "the selection resolves to nothing"}])])
    r = _insert("output_packages", {
        "project_id": project["id"], "user_id": project["user_id"],
        "recommendation_id": recommendation_id, "catalog_hash": live_hash,
        "request_key": key, "selection": selection, "status": "active"})
    if r.status_code == 409:      # double-click / concurrent accept: theirs won
        rows = supa.db_select("output_packages",
                              f"project_id=eq.{project['id']}"
                              f"&request_key=eq.{key}&limit=1")
        return {"package": rows[0],
                "deliverables": list_deliverables(rows[0]["id"]),
                "created": False}
    r.raise_for_status()
    package = r.json()[0]
    children = []
    for pos, spec in enumerate(specs):
        c = _insert("output_deliverables", {
            "package_id": package["id"], "project_id": project["id"],
            "user_id": project["user_id"], "position": pos,
            "spec": spec, "status": QUEUED})
        c.raise_for_status()
        children.append(c.json()[0])
    advance_package(package["id"])
    return {"package": package,
            "deliverables": list_deliverables(package["id"]),
            "created": True, "feasibility": feasibility}


# ---------------------------------------------------------------- execution
def list_deliverables(package_id: str) -> list[dict]:
    return supa.db_select("output_deliverables",
                          f"package_id=eq.{package_id}&order=position.asc")


def package_status(package: dict, children: list[dict]) -> str:
    """Derived, never stored — a package must not claim what its children
    do not collectively deliver."""
    if package.get("status") == "cancelled":
        return "cancelled"
    if not children:
        return "empty"
    states = [c["status"] for c in children]
    if all(s == READY for s in states):
        return "complete"
    if any(s in (QUEUED,) + tuple(ACTIVE_CHILD) for s in states):
        return "processing"
    if any(s == READY for s in states):
        return "partial"
    return "failed"


def _set_child(child_id: str, patch: dict) -> None:
    from .jobs import _now
    supa.db_update("output_deliverables", f"id=eq.{child_id}",
                   {**patch, "updated_at": _now()})


def advance_package(package_id: str) -> dict | None:
    """Start the next queued deliverable if nothing in this package is active.

    Sequential on purpose: one active job per (project, kind) is a DB
    invariant, and honest budget accounting is simpler when children spend
    one at a time. enqueue_job's own idempotency makes double-advance safe.
    """
    from .jobs import ConcurrencyLimit, enqueue_job
    pkgs = supa.db_select("output_packages", f"id=eq.{package_id}&limit=1")
    if not pkgs or pkgs[0]["status"] == "cancelled":
        return None
    package = pkgs[0]
    projects = supa.db_select("projects", f"id=eq.{package['project_id']}&limit=1")
    if not projects or projects[0].get("deleted_at"):
        # project vanished mid-package: children can never run again
        for c in list_deliverables(package_id):
            if c["status"] in (QUEUED,) + tuple(ACTIVE_CHILD):
                _set_child(c["id"], {"status": CANCELLED,
                                     "error_message": "project deleted"})
        return None
    children = list_deliverables(package_id)
    if any(c["status"] in ACTIVE_CHILD for c in children):
        return None
    nxt = next((c for c in children if c["status"] == QUEUED), None)
    if nxt is None:
        return None
    # The package was accepted against ONE catalog identity. Footage uploaded
    # or removed mid-package would make later children plan against material
    # the customer never saw offered — cancel them honestly instead.
    live_hash = oi.catalog_hash(_load_segments(package["project_id"]))
    if live_hash != package["catalog_hash"]:
        for c in children:
            if c["status"] == QUEUED:
                _set_child(c["id"], {"status": CANCELLED,
                                     "error_message":
                                         "footage changed after this package "
                                         "was created — request a fresh "
                                         "recommendation for the new footage"})
        return None
    spec = nxt["spec"]
    params = {"source": "customer_journey", "deliverable_id": nxt["id"],
              **{k: spec[k] for k in ("aspectRatio", "platform",
                                      "durationMin", "durationMax",
                                      "mustInclude")
                 if spec.get(k) is not None},
              "brief": spec.get("reason") or projects[0].get("name") or ""}
    try:
        job = enqueue_job(package["project_id"], package["user_id"],
                          "editorial_plan", params)
    except ConcurrencyLimit:
        return None                        # user at the cap; retried on next poll
    if (job.get("params") or {}).get("deliverable_id") != nxt["id"]:
        # enqueue_job's idempotency returned a FOREIGN active plan job
        # (operator/classic journey). Claiming it would strand this child in
        # "planning" against a job that will never report back to it. Leave
        # the child queued; the self-heal advance retries after that job ends.
        return None
    _set_child(nxt["id"], {"status": PLANNING})
    return job


def reconcile_package(package: dict, children: list[dict]) -> list[dict]:
    """Repair liveness holes the worker hooks cannot see.

    A child in planning/editing whose job no longer exists in any active
    state was orphaned — stale-recovery fails jobs without firing the
    deliverable hook, and a crash between chain steps leaves no job at all.
    An orphaned child becomes failed (retryable); nothing running is touched.
    """
    active = [c for c in children if c["status"] in ACTIVE_CHILD]
    if not active:
        return children
    jobs = supa.db_select(
        "pipeline_jobs",
        f"project_id=eq.{package['project_id']}"
        "&status=in.(queued,processing,cancel_requested)")
    live_children = {(j.get("params") or {}).get("deliverable_id")
                     for j in jobs}
    changed = False
    for c in active:
        if c["id"] not in live_children:
            _set_child(c["id"], {"status": FAILED,
                                 "error_message":
                                     "its job was lost (worker restart) — "
                                     "retry to continue"})
            changed = True
    return list_deliverables(package["id"]) if changed else children


def cancel_package(package_id: str) -> None:
    from .jobs import request_cancel
    supa.db_update("output_packages", f"id=eq.{package_id}",
                   {"status": "cancelled"})
    for c in list_deliverables(package_id):
        if c["status"] == QUEUED:
            _set_child(c["id"], {"status": CANCELLED})
        elif c["status"] in ACTIVE_CHILD:
            # the running child's job is cancelled through the existing path;
            # on_job_finished records the terminal state
            jobs = supa.db_select(
                "pipeline_jobs",
                f"project_id=eq.{c['project_id']}"
                f"&status=in.(queued,processing)&order=created_at.desc")
            for j in jobs:
                if (j.get("params") or {}).get("deliverable_id") == c["id"]:
                    request_cancel(j, requested_by="package_cancel")
            _set_child(c["id"], {"status": CANCELLED,
                                 "error_message": "package cancelled"})


def retry_deliverable(child: dict) -> dict | None:
    """Idempotent single-child retry: only a failed/budget-blocked child
    resets, siblings are untouched, and a double retry is a no-op because
    the second call finds the child no longer failed."""
    if child["status"] not in (FAILED, BUDGET_BLOCKED):
        return None
    _set_child(child["id"], {"status": QUEUED, "error_message": None})
    return advance_package(child["package_id"])


# ---------------------------------------------------------------- job hooks
def on_job_finished(job: dict) -> None:
    """Called by the worker for every terminal job that carries deliverable_id.

    Harvests the exact identities the job produced (plan id/version, timeline
    id) onto the deliverable — ancestry recorded at the moment of creation,
    never inferred later — then advances the package.
    """
    params = job.get("params") or {}
    child_id = params.get("deliverable_id")
    if not child_id:
        return
    rows = supa.db_select("output_deliverables", f"id=eq.{child_id}&limit=1")
    if not rows:
        return
    child = rows[0]
    if child["status"] in (CANCELLED,):
        return
    art = job.get("artifacts") or {}
    status, kind = job.get("status"), job.get("kind")

    if kind == "editorial_plan":
        if status == "completed":
            patch = {"editorial_plan_id": art.get("editorialPlanId"),
                     "editorial_plan_version": art.get("planVersion")}
            if art.get("status") == "approved":
                # chain to autoedit was enqueued by the plan handler; the
                # child is now editing
                _set_child(child_id, {**patch, "status": EDITING})
                return                     # do NOT advance: this child is active
            _set_child(child_id, {**patch, "status": FAILED,
                                  "error_message":
                                      "insufficient footage for this "
                                      "deliverable (honest planner outcome)"})
        elif status == "failed":
            err = (job.get("error_message") or "")[:400]
            if "BudgetExceeded" in err:
                _set_child(child_id, {"status": FAILED, "error_message": err})
                _budget_block_remaining(child)
            else:
                _set_child(child_id, {"status": FAILED, "error_message": err})
        else:                              # cancelled
            _set_child(child_id, {"status": CANCELLED,
                                  "error_message": "cancelled"})
    elif kind == "autoedit":
        if status == "completed":
            _set_child(child_id, {
                "status": READY,
                "timeline_id": art.get("timelineId"),
                "editorial_plan_id": art.get("editorialPlanId")
                or child.get("editorial_plan_id"),
                "editorial_plan_version": art.get("editorialPlanVersion")
                or child.get("editorial_plan_version")})
        elif status == "failed":
            _set_child(child_id, {"status": FAILED,
                                  "error_message":
                                      (job.get("error_message") or "")[:400]})
        else:
            _set_child(child_id, {"status": CANCELLED,
                                  "error_message": "cancelled"})
    advance_package(child["package_id"])


def _budget_block_remaining(child: dict) -> None:
    """Budget death is a package-level fact: the siblings still queued would
    fail the same precheck, so they are blocked honestly instead of being
    marched one by one into the same wall. Completed siblings are untouched,
    and retry after a budget change re-queues blocked children legitimately."""
    for c in list_deliverables(child["package_id"]):
        if c["status"] == QUEUED:
            _set_child(c["id"], {"status": BUDGET_BLOCKED,
                                 "error_message":
                                     "project AI budget exhausted before this "
                                     "deliverable started"})
