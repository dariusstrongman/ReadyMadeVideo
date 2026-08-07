"""P4: persistent job worker — hardened.

Jobs live in pipeline_jobs (DB) and survive API restarts. A background worker
claims queued jobs optimistically, heartbeats, recovers stale jobs, and runs
handlers with explicit cancellation checkpoints.

CANCELLATION MODEL (documented):
- states: queued -> processing -> completed | failed | cancelled
          queued/processing -> cancel_requested -> cancelled
- a queued job cancels immediately; a processing job gets cancel_requested and
  the worker honors it: before each new stage, between assets, and during FFmpeg
  renders (subprocess is terminated). In-flight Gemini/Whisper HTTP requests are
  NOT interruptible — cancellation lands at the next checkpoint after they
  return. Partial outputs are deleted (temp dir removal) and never uploaded.
- who/when is recorded (cancel_requested_by/at) and the action is audited.

TELEMETRY MODEL: every handler counts expected vs recorded stage metrics; the
result is stored in artifacts.telemetry_status (visible incompleteness) and
failed writes are retried via telemetry.reconcile_pending() at job end.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import threading
import time
import traceback
from datetime import datetime, timezone

import httpx

from . import supa
from .logging_util import log_event
from .pipeline import telemetry
from .pipeline.schemas import Segment
from .renderer2 import RenderCancelled

EXPORT_STORAGE_PROVIDER = os.environ.get("EXPORT_STORAGE_PROVIDER", "supabase")

WORKER_CONCURRENCY = int(os.environ.get("WORKER_CONCURRENCY", "1"))
STALE_AFTER_S = int(os.environ.get("JOB_STALE_AFTER_S", "900"))
# Whole-plan retries after a PlanRejected in the customer journey. Each job
# already makes MAX_ATTEMPTS internal repair passes, so this is the outer
# bound on total planning effort before the customer is told honestly.
MAX_PLAN_RETRIES = int(os.environ.get("MAX_PLAN_RETRIES", "3"))
POLL_INTERVAL_S = float(os.environ.get("JOB_POLL_INTERVAL_S", "3"))
MAX_ACTIVE_JOBS_PER_USER = int(os.environ.get("MAX_ACTIVE_JOBS_PER_USER", "4"))

ACTIVE_STATES = ("queued", "processing", "cancel_requested")

_service_headers = {
    "apikey": supa.SERVICE_KEY,
    "Authorization": f"Bearer {supa.SERVICE_KEY}",
    "Content-Type": "application/json",
}


class JobCancelled(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _patch(table, filters, body, prefer="return=representation"):
    r = httpx.patch(f"{supa.SUPABASE_URL}/rest/v1/{table}?{filters}",
                    headers={**_service_headers, "Prefer": prefer},
                    json=body, timeout=30)
    r.raise_for_status()
    return r.json() if prefer != "return=minimal" else None


def _insert(table, body, prefer="return=representation"):
    return httpx.post(f"{supa.SUPABASE_URL}/rest/v1/{table}",
                      headers={**_service_headers, "Prefer": prefer},
                      json=body, timeout=30)


def set_project_status(project_id: str, status: str, reason: str) -> None:
    _patch("projects", f"id=eq.{project_id}",
           {"status": status, "status_reason": reason[:400]},
           prefer="return=minimal")


def update_job(job_id: str, body: dict) -> None:
    body["heartbeat_at"] = _now()
    _patch("pipeline_jobs", f"id=eq.{job_id}", body, prefer="return=minimal")


class ConcurrencyLimit(Exception):
    pass


def enqueue_job(project_id: str, user_id: str, kind: str,
                params: dict | None = None) -> dict:
    """Idempotent per (project, kind); global per-user active-job cap."""
    active = supa.db_select(
        "pipeline_jobs",
        f"user_id=eq.{user_id}&status=in.({','.join(ACTIVE_STATES)})")
    dup = sorted((a for a in active
                  if a["project_id"] == project_id and a["kind"] == kind),
                 key=lambda a: a.get("created_at") or "")
    if dup:
        # B5: ACTIVE_STATES includes cancel_requested, but the DB partial
        # unique index covers only (queued, processing) — relying on the 409
        # here would insert a SECOND active job of the same kind while the
        # first one drains its cancellation. Return the duplicate directly.
        return dup[-1]
    if len(active) >= MAX_ACTIVE_JOBS_PER_USER:
        raise ConcurrencyLimit(
            f"user already has {len(active)} active jobs "
            f"(max {MAX_ACTIVE_JOBS_PER_USER})")
    r = _insert("pipeline_jobs", {"project_id": project_id, "user_id": user_id,
                                  "kind": kind, "params": params or {}})
    if r.status_code == 201:
        return r.json()[0]
    if r.status_code == 409:  # active duplicate — return it (idempotency)
        rows = supa.db_select(
            "pipeline_jobs",
            f"project_id=eq.{project_id}&kind=eq.{kind}"
            f"&status=in.({','.join(ACTIVE_STATES)})&order=created_at.desc&limit=1")
        if rows:
            return rows[0]
    r.raise_for_status()
    return {}


def request_cancel(job: dict, requested_by: str) -> dict:
    """queued -> cancelled immediately; processing -> cancel_requested."""
    if job["status"] == "queued":
        _patch("pipeline_jobs", f"id=eq.{job['id']}&status=eq.queued",
               {"status": "cancelled", "cancel_requested_by": requested_by,
                "cancel_requested_at": _now(), "completed_at": _now()},
               prefer="return=minimal")
        return {"status": "cancelled"}
    if job["status"] in ("processing", "cancel_requested"):
        _patch("pipeline_jobs", f"id=eq.{job['id']}",
               {"status": "cancel_requested", "cancel_requested_by": requested_by,
                "cancel_requested_at": _now()}, prefer="return=minimal")
        return {"status": "cancel_requested",
                "note": "worker will stop at the next checkpoint; in-flight "
                        "provider requests cannot be interrupted"}
    raise ValueError(f"job is {job['status']}")


def _claim_next() -> dict | None:
    rows = supa.db_select("pipeline_jobs",
                          "status=eq.queued&order=created_at.asc&limit=1")
    if not rows:
        return None
    job = rows[0]
    claimed = _patch(
        "pipeline_jobs",
        f"id=eq.{job['id']}&status=eq.queued",   # optimistic: still queued
        {"status": "processing", "started_at": _now(), "heartbeat_at": _now(),
         "attempt_count": job["attempt_count"] + 1})
    return claimed[0] if claimed else None


def recover_stale() -> int:
    rows = supa.db_select(
        "pipeline_jobs", "status=in.(processing,cancel_requested)")
    n = 0
    for job in rows:
        hb = job.get("heartbeat_at") or job.get("started_at") or job["created_at"]
        age = time.time() - datetime.fromisoformat(
            hb.replace("Z", "+00:00")).timestamp()
        if age > STALE_AFTER_S:
            n += 1
            if job["status"] == "cancel_requested" \
                    or job["attempt_count"] >= job["max_attempts"]:
                update_job(job["id"], {
                    "status": "cancelled" if job["status"] == "cancel_requested"
                    else "failed",
                    "error_message": "stale: worker heartbeat lost"
                    + ("" if job["status"] == "cancel_requested"
                       else " and max attempts exhausted"),
                    "completed_at": _now()})
            else:
                update_job(job["id"], {"status": "queued",
                                       "error_message":
                                       "recovered after stale heartbeat"})
            log_event("JOB-STALE-RECOVERED", job_id=job["id"],
                      kind=job["kind"], prior_status=job["status"])
    return n


class JobContext:
    """Cancellation checkpoints + telemetry accounting for one job run."""

    HEARTBEAT_EVERY_S = 25.0

    def __init__(self, job: dict):
        self.job = job
        self.expected = 0
        self.recorded = 0
        self._last_beat = time.monotonic()

    def beat(self, force: bool = False) -> None:
        """B8: liveness-only heartbeat (no fake progress). Throttled so the
        0.5s render cancel-poll doesn't turn into a PATCH storm; `force`
        is for coarse events (planner attempts, provider-call boundaries)."""
        now = time.monotonic()
        if not force and now - self._last_beat < self.HEARTBEAT_EVERY_S:
            return
        self._last_beat = now
        try:
            update_job(self.job["id"], {})       # update_job stamps heartbeat_at
        except Exception:  # noqa: BLE001 — liveness must never break the job
            pass

    def cancelled(self) -> bool:
        # every cancel poll doubles as a (throttled) liveness signal: long
        # renders and per-asset loops heartbeat for free, so a healthy job
        # can no longer be stale-recovered mid-encode (B8)
        self.beat()
        rows = supa.db_select("pipeline_jobs", f"id=eq.{self.job['id']}", "status")
        return bool(rows) and rows[0]["status"] == "cancel_requested"

    def checkpoint(self, stage: str) -> None:
        """Raise JobCancelled if cancellation was requested."""
        if self.cancelled():
            log_event("JOB-CANCEL-CHECKPOINT", job_id=self.job["id"], stage=stage)
            raise JobCancelled(stage)

    def rec(self, stage: str, duration_seconds=None, bytes_=None, units=None):
        self.expected += 1
        ok = telemetry.record(stage, self.job["project_id"], self.job["id"],
                              duration_seconds, bytes_, units)
        if ok:
            self.recorded += 1

    def telemetry_status(self) -> dict:
        rec = telemetry.reconcile_pending()
        # reconciliation may have recovered rows queued from this job
        recovered_here = min(rec.get("recovered", 0),
                             self.expected - self.recorded)
        recorded = self.recorded + recovered_here
        return {"expected_stages": self.expected, "recorded_stages": recorded,
                "complete": recorded >= self.expected,
                "pricing_version": telemetry.pricing_version(),
                "note": "all costs are ESTIMATES",
                **({"reconciliation": rec} if rec.get("retried") else {})}


# ---------------- handlers ----------------
def owned_raw_storage_path(path: str, user_id: str, project_id: str) -> bool:
    """Defense-in-depth ancestry check for a raw-footage object key.

    media_assets is client-writable (RLS only checks user_id = auth.uid(), not the
    storage_path), so the service-role worker must never blindly trust a stored
    path. A legitimate object always lives under the project owner's prefix:
        users/{user_id}/projects/{project_id}/...
    Rejects foreign-owner paths and any path traversal. This does not change how
    existing valid uploads (which already use this prefix) are handled.
    """
    prefix = f"users/{user_id}/projects/{project_id}/"
    return bool(path) and path.startswith(prefix) and ".." not in path


def _download_sources(project: dict, tmp: str, ctx: JobContext | None = None):
    from . import media_store
    assets = supa.db_select("media_assets", f"project_id=eq.{project['id']}")
    sources = {}
    for a in assets:
        if ctx:
            ctx.checkpoint("download_sources")
        path = a["storage_path"]
        if not owned_raw_storage_path(path, project["user_id"], project["id"]):
            raise RuntimeError(
                f"media asset {a['id']} storage path failed ownership check")
        dst = os.path.join(tmp, a["id"] + os.path.splitext(a["filename"])[1])
        # Single ownership-validating, provider-aware choke point.
        # Handles legacy Supabase assets and S3 assets alike; the explicit
        # owned_raw_storage_path() check above stays as defense-in-depth.
        media_store.download_media_asset(a, project, dst)
        sources[a["id"]] = dst
    return sources, assets


def _load_segments(project_id: str) -> list[Segment]:
    """Catalog load with deterministic latest-schema selection — the rule
    lives in segment_store (B2: ONE selection rule for every consumer)."""
    from . import segment_store
    return segment_store.load_segments(project_id)


def _upload_export(project: dict, rel: str, local: str) -> str:
    path = f"users/{project['user_id']}/projects/{project['id']}/{rel}"
    if EXPORT_STORAGE_PROVIDER == "s3":
        from . import s3store
        s3store.upload_file(path, local, "video/mp4")
    else:
        supa.storage_upload("exports", path, local)
    return path


# Human-readable labels for the per-clip analysis stage callback. Module-level so
# the stage_cb closure binds a stable reference (not a loop-local — fixes ruff B023);
# the mapping is identical every iteration.
_STAGE_HUMAN = {
    "probe":      "Validating clip format",
    "proxy":      "Building proxy video",
    "scenes":     "Detecting scene cuts",
    "mechanical": "Analyzing camera motion",
    "audio":      "Measuring audio levels",
    "transcript": "Transcribing speech",
    "semantic":   "Running AI scene analysis",
    "motion":     "Scoring motion quality",
    "catalog":    "Building segment catalog",
}


def _maybe_enqueue_customer_autoedit(project: dict) -> None:
    """Idempotent analysis -> autoedit hand-off for the customer journey.

    Skips if a bridged candidate already exists or an autoedit job is already
    active, so repeated analysis completion never spawns duplicate edits.
    """
    existing = supa.db_select(
        "candidate_runs",
        f"project_id=eq.{project['id']}&generation_kind=eq.bridged&limit=1")
    if existing:
        return
    active = supa.db_select(
        "pipeline_jobs",
        f"project_id=eq.{project['id']}&kind=eq.autoedit"
        f"&status=in.({','.join(ACTIVE_STATES)})")
    if active:
        return
    if picture_edit_v2_enabled():
        # V2 journey: analysis -> editorial_plan -> (on approval) -> autoedit.
        # The plan job carries source=customer_journey so its completion knows
        # to chain onward and its failure is surfaced on the project (an
        # operator-requested plan stays status-silent as before).
        _maybe_enqueue_customer_editorial_plan(project)
        return
    try:
        enqueue_job(project["id"], project["user_id"], "autoedit",
                    {"source": "customer_journey"})
    except ConcurrencyLimit:
        pass  # at the active-job cap; analysis still completed cleanly


def _plan_constraints_for(project: dict) -> dict:
    """Planning constraints derived from what the customer already told us."""
    out = {}
    if project.get("name"):
        out["brief"] = project["name"]
    if project.get("aspect_ratio"):
        out["aspectRatio"] = project["aspect_ratio"]
    if project.get("target_platform"):
        out["platform"] = project["target_platform"]
    # Requested length is a RANGE the planner must honor (recommending the
    # ideal duration within it from the footage) — or honestly report
    # insufficient_footage. Falls back to 0026's point target (±15%) for rows
    # written before the range columns existed.
    lo, hi = project.get("duration_min_seconds"), project.get("duration_max_seconds")
    if lo and hi:
        out["durationMin"], out["durationMax"] = int(lo), int(hi)
    elif project.get("target_duration_seconds"):
        target = project["target_duration_seconds"]
        out["durationMin"] = max(10, round(target * 0.85))
        out["durationMax"] = round(target * 1.15)
    return out


def _maybe_enqueue_customer_editorial_plan(project: dict,
                                           source: str = "customer_journey",
                                           extra: dict | None = None):
    """Idempotent analysis -> editorial_plan hand-off (V2 journey only).

    If an APPROVED plan already exists but no candidate does (a mid-journey
    failure), skip straight to autoedit with that exact plan so a retry repairs
    the journey instead of re-planning.
    """
    active = supa.db_select(
        "pipeline_jobs",
        f"project_id=eq.{project['id']}&kind=eq.editorial_plan"
        f"&status=in.({','.join(ACTIVE_STATES)})")
    if active:
        return active[0]
    approved = supa.db_select(
        "editorial_plans",
        f"project_id=eq.{project['id']}&status=eq.approved"
        "&order=version.desc&limit=1")
    try:
        if approved:
            return enqueue_job(
                project["id"], project["user_id"], "autoedit",
                {"source": source,
                 "editorial_plan_id": approved[0]["id"],
                 "editorial_plan_version": approved[0]["version"],
                 **({"aspect_ratio": (extra or {}).get("aspectRatio")}
                    if (extra or {}).get("aspectRatio") else {})})
        params = {**_plan_constraints_for(project), **(extra or {}),
                  "source": source}
        return enqueue_job(project["id"], project["user_id"],
                           "editorial_plan", params)
    except ConcurrencyLimit:
        return None  # at the active-job cap; analysis still completed cleanly


def handle_analysis(job: dict, project: dict, tmp: str, ctx: JobContext) -> dict:
    from .pipeline.runner import CloudStore, run_pipeline
    set_project_status(project["id"], "analyzing", f"analysis job {job['id'][:8]}")
    sources, assets = _download_sources(project, tmp, ctx)
    if not assets:
        raise RuntimeError("project has no uploaded footage")
    done = 0
    for a in assets:
        ctx.checkpoint("between_assets")          # cancellation between assets
        t0 = time.time()
        store = CloudStore(a)
        wd = os.path.join(tmp, "work-" + a["id"][:8])

        def upload_cb(files, a=a, store=store):
            paths = {"proxy": store.upload_file(files["proxy"], "proxy.mp4", "video/mp4"),
                     "wav": store.upload_file(files["wav"], "audio.wav", "audio/wav")}
            if files["thumbs"]:
                paths["thumb_0"] = store.upload_file(files["thumbs"][0],
                                                     "thumb_0.jpg", "image/jpeg")
            return paths

        clip_num = done + 1
        clip_total = len(assets)
        clip_label = f"clip {clip_num}/{clip_total}"
        def stage_cb(stage_name, status, _clip=clip_label, _jid=job["id"]):
            human = _STAGE_HUMAN.get(stage_name, stage_name)
            if status == "start":
                msg = f"{_clip} \u2014 {human}..."
            elif status == "skip":
                msg = f"{_clip} \u2014 {human} (skipped)"
            else:
                msg = f"{_clip} \u2014 {human} \u2713"
            update_job(_jid, {"current_stage": msg})
        run_pipeline(sources[a["id"]], store, asset_id=a["id"], workdir=wd,
                     upload_cb=upload_cb,
                     context=(job.get("params") or {}).get("context", ""),
                     stage_cb=stage_cb)
        dur = a.get("duration_seconds") or 0
        ctx.rec("analysis_asset", round(time.time() - t0, 2),
                a.get("size_bytes"),
                units={"gemini_requests": 1, "gemini_video_seconds": dur,
                       "whisper_minutes": dur / 60,
                       "cpu_hours": (time.time() - t0) / 3600})
        done += 1
        update_job(job["id"], {"progress": int(done / len(assets) * 100),
                               "current_stage": f"analyzed {done}/{len(assets)}"})
    set_project_status(project["id"], "ready", "analysis completed")
    _maybe_enqueue_customer_autoedit(project)
    return {"assets_analyzed": done}


def picture_edit_v2_enabled() -> bool:
    """Feature flag (default OFF). With the flag off, production behavior is
    the unchanged legacy autoedit path; with it on, an APPROVED EditorialPlan
    is REQUIRED and the old selector is never silently used."""
    return os.environ.get("PICTURE_EDIT_ENGINE_V2_ENABLED", "").lower() \
        in ("1", "true", "yes")


def handle_autoedit_v2(job: dict, project: dict, tmp: str, ctx: JobContext) -> dict:
    """Picture Edit Engine V2: approved EditorialPlan -> real timeline.

    No silent fallback: a missing/unapproved/ungrounded plan fails this job
    with the engine's exact validation reasons. Idempotent per (project, plan
    version, engine version, catalog hash) via the deterministic hash persisted
    on the edit run."""
    from . import autoedit_bridge
    from .pipeline import picture_edit_v2, picture_render_v2

    segments = _load_segments(project["id"])
    if not segments:
        raise RuntimeError("no segment catalog — run analysis first")
    params = job.get("params") or {}
    want_id = params.get("editorial_plan_id")
    if want_id:
        # Customer-journey chain: consume the EXACT plan the planner job
        # persisted — never "whatever is newest", which could silently swap in
        # a different operator-made plan between enqueue and execution.
        plans = supa.db_select("editorial_plans", f"id=eq.{want_id}&limit=1")
        plan_row = plans[0] if plans else None
        if not plan_row:
            raise RuntimeError(f"editorial plan {want_id} not found")
        if str(plan_row["project_id"]) != str(project["id"]):
            raise RuntimeError("editorial plan does not belong to this project")
        want_ver = params.get("editorial_plan_version")
        if want_ver is not None and int(plan_row["version"]) != int(want_ver):
            raise RuntimeError(
                f"editorial plan version changed: expected v{want_ver}, "
                f"found v{plan_row['version']} — refusing a stale hand-off")
    else:
        plans = supa.db_select(
            "editorial_plans",
            f"project_id=eq.{project['id']}&order=version.desc&limit=1")
        plan_row = plans[0] if plans else None
    update_job(job["id"], {"current_stage": "picture edit v2", "progress": 10})
    t0 = time.time()
    # raises PictureEditRejected with the exact reasons — never falls back
    result = picture_edit_v2.build_picture_edit(plan_row, segments, now=_now())

    # ---- idempotency: same project + plan version + engine version + catalog
    # hash returns the EXISTING persisted result (no duplicate timelines/runs).
    # A retry after a mid-journey failure REPAIRS what is missing (e.g. the
    # bridge failed after the timeline persisted) instead of reporting success
    # while the Product Editor has no candidate to open.
    for run in supa.db_select("edit_runs",
                              f"project_id=eq.{project['id']}"
                              "&order=created_at.desc&limit=25"):
        bp = run.get("blueprint") or {}
        if bp.get("deterministicHash") == result["deterministicHash"] \
                and run.get("timeline_v2_id"):
            artifacts = {"engine": "picture_edit_v2",
                         "engineVersion": result["engineVersion"],
                         "reused": True, "editRunId": run["id"],
                         "timelineId": run["timeline_v2_id"],
                         "deterministicHash": result["deterministicHash"],
                         "editorialPlanId": result["editorialPlanId"],
                         "editorialPlanVersion": result["editorialPlanVersion"],
                         "actualDurationSeconds": result["actualDurationSeconds"]}
            # ancestry-bound: only a candidate descending from THIS timeline
            # counts — a different (older-engine) timeline's candidate never
            # satisfies the reuse path
            bridged_row = autoedit_bridge.find_bridged_candidate(
                supa.db_select, project["id"], str(run["timeline_v2_id"]))
            if bridged_row:
                artifacts["bridgedCandidateRunId"] = bridged_row["id"]
                log_event("PICTURE-EDIT-V2-REUSED", job_id=job["id"],
                          edit_run_id=run["id"],
                          hash=result["deterministicHash"])
                # B6: reuse was the ONLY success path that left project
                # status untouched — a project sitting at render_failed
                # whose re-run reused a draft kept showing the failure while
                # a valid candidate existed. Every success path states the
                # truth: a draft is ready.
                set_project_status(project["id"], "draft_ready",
                                   "existing draft reused (identical plan, "
                                   "catalog and engine)")
                return artifacts
            # bridge repair: re-render the preview and finish the journey
            log_event("PICTURE-EDIT-V2-BRIDGE-REPAIR", job_id=job["id"],
                      edit_run_id=run["id"])
            sources, _ = _download_sources(project, tmp, ctx)
            ctx.checkpoint("before_v2_bridge_repair")
            preview_path = os.path.join(tmp, "picture-edit-v2-preview.mp4")
            picture_render_v2.render_picture_edit(
                result, sources, preview_path,
                cancel_check=lambda: ctx.cancelled())
            tl_rows = supa.db_select("timelines",
                                     f"id=eq.{run['timeline_v2_id']}&limit=1")
            if not tl_rows:
                raise RuntimeError("persisted V2 timeline vanished — cannot "
                                   "repair the bridge")
            bridged = autoedit_bridge.bridge_from_autoedit(
                project, tl_rows[0], preview_path,
                insert=_insert, db_select=supa.db_select,
                upload_export=_upload_export, now=_now,
                remove=supa.storage_remove, update=supa.db_update,
                export_provider=EXPORT_STORAGE_PROVIDER)
            if not bridged:
                raise RuntimeError("bridge repair produced no candidate")
            artifacts["bridgedCandidateRunId"] = bridged["id"]
            artifacts["bridgeRepaired"] = True
            set_project_status(project["id"], "draft_ready",
                               "picture edit v2 bridge repaired on retry")
            return artifacts

    sources, _ = _download_sources(project, tmp, ctx)
    ctx.checkpoint("before_v2_render")
    preview_path = os.path.join(tmp, "picture-edit-v2-preview.mp4")
    picture_render_v2.render_picture_edit(
        result, sources, preview_path, cancel_check=lambda: ctx.cancelled())

    ctx.checkpoint("before_v2_persist")
    er = _insert("edit_runs", {
        "project_id": project["id"], "user_id": project["user_id"],
        "status": "completed",
        "brief": (job.get("params") or {}).get("brief"),
        "blueprint": result,                       # full PictureEditV2 contract
        "selection": {"engine": "picture_edit_v2",
                      "engineVersion": result["engineVersion"]}})
    er.raise_for_status()
    edit_run_id = er.json()[0]["id"]
    existing = supa.db_select("timelines",
                              f"project_id=eq.{project['id']}"
                              "&order=version.desc&limit=1")
    next_ver = (existing[0]["version"] + 1) if existing else 1
    tl = _insert("timelines", {
        "project_id": project["id"], "user_id": project["user_id"],
        "version": next_ver, "timeline_json": result["timeline"],
        "lineage": "autonomous_revised", "is_immutable": True,
        "edit_run_id": edit_run_id})
    tl.raise_for_status()
    tl_row = tl.json()[0]
    _patch("edit_runs", f"id=eq.{edit_run_id}",
           {"timeline_v2_id": tl_row["id"]}, prefer="return=minimal")

    artifacts: dict = {"engine": "picture_edit_v2",
                       "engineVersion": result["engineVersion"], "reused": False,
                       "editRunId": edit_run_id, "timelineId": tl_row["id"],
                       "deterministicHash": result["deterministicHash"],
                       "editorialPlanId": result["editorialPlanId"],
                       "editorialPlanVersion": result["editorialPlanVersion"],
                       "actualDurationSeconds": result["actualDurationSeconds"],
                       "pendingExecution": len(result["unsupportedExecution"]),
                       "continuityFindings": result["continuityFindings"],
                       "technicalWarnings": result["technicalWarnings"]}
    bridged = autoedit_bridge.bridge_from_autoedit(
        project, tl_row, preview_path,
        insert=_insert, db_select=supa.db_select,
        upload_export=_upload_export, now=_now, remove=supa.storage_remove,
        update=supa.db_update, export_provider=EXPORT_STORAGE_PROVIDER)
    if bridged:
        artifacts["bridgedCandidateRunId"] = bridged["id"]
    dur = time.time() - t0
    ctx.rec("picture_edit_v2", round(dur, 2), units={"cpu_hours": dur / 3600})
    set_project_status(project["id"], "draft_ready",
                       f"picture edit v2 built timeline v{next_ver} from plan "
                       f"v{result['editorialPlanVersion']}")
    return artifacts


def handle_autoedit(job: dict, project: dict, tmp: str, ctx: JobContext) -> dict:
    import json as _json

    from .pipeline.autoedit import autoedit
    if picture_edit_v2_enabled():
        # Flag ON: Picture Edit Engine V2 only. The legacy selector is never a
        # silent fallback — engine failures fail this job with exact reasons.
        return handle_autoedit_v2(job, project, tmp, ctx)
    params = job.get("params") or {}
    segments = _load_segments(project["id"])
    if not segments:
        raise RuntimeError("no segment catalog — run analysis first")
    sources, _ = _download_sources(project, tmp, ctx)
    run_dir = os.path.join(tmp, "run")
    update_job(job["id"], {"current_stage": "autoedit", "progress": 10})
    t0 = time.time()
    report = autoedit(
        segments, sources,
        brief=params.get("brief") or project.get("name", "fitness edit"),
        out_dir=run_dir,
        target_duration=params.get("target_duration"),
        # The project's own shape drives the render. An explicit job param still
        # wins (operator overrides); otherwise the customer's choice from the
        # New Project wizard is what gets built.
        platform=params.get("platform", "horizontal"),
        aspect_ratio=params.get("aspect_ratio") or project.get("aspect_ratio"),
        title_text=params.get("title"),
        use_critic=params.get("use_critic", True),
        render_final=False,
        cancel_check=lambda: ctx.cancelled())
    if report.get("status") == "cancelled":
        raise JobCancelled("autoedit stage")
    if report.get("status") != "completed":
        raise RuntimeError(f"autoedit failed: {report.get('error')}")

    ctx.checkpoint("before_artifact_upload")      # never upload after cancel
    artifacts: dict = {"previews": [], "run_report": report}
    for fname in ("blueprint.json", "selection.json", "validator_v1.json",
                  "critic_pass1.json", "revision_ops_pass1.json"):
        p = os.path.join(run_dir, fname)
        if os.path.exists(p):
            artifacts[fname.replace(".json", "")] = _json.load(
                open(p, encoding="utf-8"))
    # Create the run first so every autonomous timeline is associated at insert
    # time rather than being backfilled only when a human session begins.
    er = _insert("edit_runs", {
        "project_id": project["id"], "user_id": project["user_id"],
        "status": "running", "brief": params.get("brief"),
        "blueprint": artifacts.get("blueprint"),
        "selection": artifacts.get("selection"),
        "validator_report": artifacts.get("validator_v1"),
        "critic_verdict": artifacts.get("critic_pass1"),
        "revision_ops": artifacts.get("revision_ops_pass1")})
    edit_run_id = er.json()[0]["id"] if er.status_code == 201 else None
    if not edit_run_id:
        raise RuntimeError("could not create edit run before timeline persistence")
    existing = supa.db_select("timelines",
                              f"project_id=eq.{project['id']}"
                              f"&order=version.desc&limit=1")
    next_ver = (existing[0]["version"] + 1) if existing else 1
    tl_ids = []
    best_tl_row = None
    timeline_files = sorted(f for f in os.listdir(run_dir)
                            if f.startswith("timeline_v") and f.endswith(".json"))
    for index, fname in enumerate(timeline_files):
        tl = _json.load(open(os.path.join(run_dir, fname), encoding="utf-8"))
        if index == 0:
            lineage = "autonomous_initial"
        elif index == len(timeline_files) - 1:
            lineage = "autonomous_revised"
        else:
            lineage = "autonomous_intermediate"
        row = _insert("timelines", {"project_id": project["id"],
                                    "user_id": project["user_id"],
                                    "version": next_ver, "timeline_json": tl,
                                    "lineage": lineage,
                                    "edit_run_id": edit_run_id,
                                    "parent_timeline_id": tl_ids[-1] if tl_ids else None,
                                    "is_immutable": lineage in
                                    ("autonomous_initial", "autonomous_revised")}).json()[0]
        tl_ids.append(row["id"])
        best_tl_row = row   # last inserted = autonomous_revised (best draft)
        next_ver += 1
    preview_files = sorted(f for f in os.listdir(run_dir) if f.endswith(".mp4"))
    for fname in preview_files:
        artifacts["previews"].append(_upload_export(
            project, f"drafts/{job['id']}/{fname}", os.path.join(run_dir, fname)))
    _patch("edit_runs", f"id=eq.{edit_run_id}", {
        "status": "completed",
        "timeline_v1_id": tl_ids[0] if tl_ids else None,
        "timeline_v2_id": tl_ids[-1] if len(tl_ids) > 1 else None,
        "preview_paths": artifacts["previews"], "completed_at": _now()},
        prefer="return=minimal")

    sel = artifacts.get("selection") or {}
    beats = sel.get("beats", [])
    _insert("draft_evaluations", {
        "project_id": project["id"], "user_id": project["user_id"],
        "edit_run_id": edit_run_id,
        "raw_footage_seconds": sum(s.sourceEnd - s.sourceStart for s in segments),
        "source_asset_count": len(sources),
        "scene_count": len(segments), "segment_count": len(segments),
        "usable_segment_count": len([s for s in segments if not s.problems]),
        "beats_requested": len(beats),
        "beats_filled": len([b for b in beats if b.get("chosen")]),
        "first_draft_seconds": next((s.get("duration") for s in report["steps"]
                                     if s.get("step") == "preview_v1"), None),
        "final_seconds": next((s.get("duration") for s in reversed(report["steps"])
                               if str(s.get("step", "")).startswith("preview")), None),
        "duplicate_use_count": 0,
        "validation_issue_count": next((s.get("issues") for s in report["steps"]
                                        if s.get("step") == "validate_v1"), 0),
        "critic_request_count": next((s.get("requests") for s in report["steps"]
                                      if s.get("step") == "critic_pass1"), 0),
        "revision_passes": report.get("revisionPasses", 0)},
        prefer="return=minimal")

    # Strategy-B bridge: expose this basic autoedit as an editable Product Editor
    # candidate (idempotent — at most one bridged candidate per project).
    if best_tl_row and preview_files:
        from . import autoedit_bridge
        bridged = autoedit_bridge.bridge_from_autoedit(
            project, best_tl_row, os.path.join(run_dir, preview_files[-1]),
            insert=_insert, db_select=supa.db_select,
            upload_export=_upload_export, now=_now, remove=supa.storage_remove,
            update=supa.db_update, export_provider=EXPORT_STORAGE_PROVIDER)
        if bridged:
            artifacts["bridgedCandidateRunId"] = bridged["id"]

    dur = time.time() - t0
    ctx.rec("autoedit", round(dur, 2),
            units={"gemini_requests": 1 + report.get("revisionPasses", 0),
                   "cpu_hours": dur / 3600})
    set_project_status(project["id"], "draft_ready",
                       f"autoedit job {job['id'][:8]} produced a draft")
    return artifacts


def handle_revision(job: dict, project: dict, tmp: str, ctx: JobContext) -> dict:
    return handle_autoedit(job, project, tmp, ctx)


def handle_editorial_plan(job: dict, project: dict, tmp: str, ctx: JobContext) -> dict:
    """Editorial Planner v1 — separate structured planning stage.

    Sits between analysis (segment catalog) and timeline generation. Emits a
    grounded EditorialPlan JSON into editorial_plans for downstream picture-
    edit / graphics / audio / color / render consumption. Does NOT touch the
    existing autoedit pipeline, timelines or project status."""
    from .pipeline import editorial_planner

    params = job.get("params") or {}
    segments = _load_segments(project["id"])
    if not segments:
        raise RuntimeError("no segment catalog — run analysis first")
    # Licensed music must actually EXIST for the plan to reference music at all.
    music_available = bool(supa.db_select(
        "licensed_music_assets", f"project_id=eq.{project['id']}&limit=1"))
    update_job(job["id"], {"current_stage": "editorial planning", "progress": 10})
    t0 = time.time()

    # ---- B9/B10: real usage accounting + spend ceilings around every paid
    # call. Costs are ESTIMATES from provider-reported tokens (actual billing
    # is unknowable at request time — kept as a separate null field).
    from .pipeline import ai_budget
    prior = supa.db_select(
        "stage_metrics",
        f"project_id=eq.{project['id']}&stage=eq.editorial_plan_ai")
    budget = ai_budget.AiBudget(project_spent_usd=sum(
        float(r.get("estimated_cost_usd") or 0) for r in prior))
    usage: list[dict] = []
    model = os.environ.get("EDITORIAL_PLANNER_MODEL", "gemini-2.5-pro")

    def _generate(parts, schema):
        ctx.beat(force=True)                       # B8: liveness per attempt
        budget.precheck(ai_budget.estimate_call_cost(parts, model))
        before = len(usage)
        gen = editorial_planner.gemini_generate
        try:
            import inspect
            takes_usage = "usage" in inspect.signature(gen).parameters
        except (TypeError, ValueError):
            takes_usage = False
        if takes_usage:
            out = gen(parts, schema,
                      heartbeat=lambda: ctx.beat(force=True), usage=usage)
        else:
            out = gen(parts, schema)               # tests inject 2-arg stubs
        budget.add(usage[before:])
        return out

    def _record_ai_usage():
        if not usage:
            return None
        units = ai_budget.units_for(usage)
        units["gemini_requests"] = 0               # tokens supersede flat rate
        summary = {
            "provider": "gemini", "model": model,
            "inputTokens": sum(r["inputTokens"] for r in usage),
            "outputTokens": sum(r["outputTokens"] for r in usage),
            "cachedTokens": sum(r["cachedTokens"] for r in usage),
            "requests": len(usage),
            "retries": max(0, len(usage) // 2 - 1),
            "estimatedCostUsd": ai_budget.cost_of(usage),
            "actualCostUsd": None,                 # billing-level actual unknown
            "pricingVersion": telemetry.pricing_version(), "at": _now()}
        ctx.rec("editorial_plan_ai", units=units)
        return summary

    try:
        result = editorial_planner.plan_editorial(
            segments,
            constraints={k: params.get(k) for k in (
                "brief", "platform", "aspectRatio", "tone", "style",
                "toneAdvisoryOnly", "durationMin", "durationMax",
                "mustInclude", "mustExclude")},
            music_available=music_available,
            generate=_generate)
    except Exception:
        # failure paths (PlanRejected, BudgetExceeded, provider errors) must
        # still account for what was actually spent — the most expensive
        # customers were previously invisible in the unit economics
        _record_ai_usage()
        raise
    ai_usage = _record_ai_usage()
    ctx.checkpoint("before_plan_persist")
    existing = supa.db_select(
        "editorial_plans",
        f"project_id=eq.{project['id']}&order=version.desc&limit=1")
    version = (int(existing[0]["version"]) + 1) if existing else 1
    row = _insert("editorial_plans", {
        "project_id": project["id"], "user_id": project["user_id"],
        "version": version, "status": result["status"],
        # quality_score is the DETERMINISTIC gate score; the model's own
        # self-assessment is stored inside the plan as advisory metadata only.
        "quality_score": result["qualityScore"], "attempts": result["attempts"],
        "request": params, "plan": result["plan"],
        "validation": {"violationsHistory": result["violationsHistory"],
                       "deterministicGate": result["deterministicGate"],
                       # Phase 2 (flag-gated): present only when the interest
                       # gate ran; additive key, absent = Phase 1 behavior
                       **({"interestGate": result["interestGate"]}
                          if result.get("interestGate") else {})},
    })
    row.raise_for_status()
    plan_id = row.json()[0]["id"]
    dur = time.time() - t0
    # attempts only — the real token spend is in the editorial_plan_ai row
    # (B9); the legacy flat per-request rate is deliberately NOT applied here
    ctx.rec("editorial_plan", round(dur, 2), units={})
    # ── Customer-journey chain: an approved plan hands off to Picture Edit V2
    # with the EXACT plan id/version just persisted. Operator/manual plan jobs
    # (no `source` param) keep their original stop-here semantics.
    source = params.get("source")
    if source in ("customer_journey", "recut"):
        if result["status"] == "approved":
            chain = {"source": source, "editorial_plan_id": plan_id,
                     "editorial_plan_version": version}
            if params.get("aspectRatio"):
                chain["aspect_ratio"] = params["aspectRatio"]
            try:
                enqueue_job(project["id"], project["user_id"],
                            "autoedit", chain)
            except ConcurrencyLimit:
                # Plan is persisted and approved; the journey resumes via the
                # retry path rather than dying silently mid-chain.
                set_project_status(
                    project["id"], "analysis_failed",
                    "your edit is planned but could not start (too many "
                    "active jobs) — retry to continue")
        else:
            # Honest planner outcome (insufficient_footage): no autoedit job
            # is enqueued and no legacy fallback happens. Surface the exact
            # reason so the customer can act on it.
            plan_json = result["plan"] or {}
            achievable = plan_json.get("achievableDurationSeconds")
            missing = plan_json.get("missingFootage") or []
            shots = "; ".join(str(m.get("shotType") or m.get("beat") or "shot")
                              for m in missing[:4])
            reason = ("the footage cannot fill the requested edit"
                      + (f" (achievable {achievable}s)" if achievable else "")
                      + (f" — missing: {shots}" if shots else ""))
            set_project_status(project["id"], "analysis_failed",
                               f"editorial plan v{version}: {reason}")
    return {"editorialPlanId": plan_id, "planVersion": version,
            "status": result["status"], "qualityScore": result["qualityScore"],
            "attempts": result["attempts"],
            "plannedDurationSeconds":
                result["plan"].get("plannedDurationSeconds"),
            **({"aiUsage": ai_usage} if ai_usage else {})}


def handle_final_render(job: dict, project: dict, tmp: str, ctx: JobContext) -> dict:
    import json as _json

    if (job.get("params") or {}).get("editor_document_id"):
        return handle_product_editor_render(job, project, tmp, ctx)

    from .renderer2 import render_timeline
    params = job.get("params") or {}
    tl_id = params.get("timeline_id")
    if tl_id:
        rows = supa.db_select("timelines", f"id=eq.{tl_id}")
    else:
        rows = supa.db_select("timelines", f"project_id=eq.{project['id']}"
                                           f"&order=version.desc&limit=1")
    if not rows or rows[0]["project_id"] != project["id"]:
        raise RuntimeError("no approved timeline for this project")
    tl = rows[0]["timeline_json"]
    if isinstance(tl, str):
        tl = _json.loads(tl)
    assets = {a["id"] for a in supa.db_select("media_assets",
                                              f"project_id=eq.{project['id']}")}
    for t in tl.get("tracks", []):
        if t.get("type") == "video":
            for c in t.get("clips", []):
                if c["assetId"] not in assets:
                    raise RuntimeError(f"timeline references foreign asset "
                                       f"{c['assetId']}")
                # Phase 3 audio-under b-roll: the donor audio asset gets the
                # SAME ownership check as the picture asset
                donor = (c.get("audioFrom") or {}).get("assetId")
                if donor and donor not in assets:
                    raise RuntimeError("timeline audioFrom references "
                                       f"foreign asset {donor}")
    set_project_status(project["id"], "rendering",
                       f"final render job {job['id'][:8]}")
    sources, _ = _download_sources(project, tmp, ctx)
    out = os.path.join(tmp, "final.mp4")
    update_job(job["id"], {"current_stage": "rendering", "progress": 30})
    ctx.checkpoint("before_render")
    t0 = time.time()
    result = render_timeline(tl, sources, out, profile="final",
                             cancel_check=lambda: ctx.cancelled(),
        tick=lambda secs: update_job(job["id"], {"current_stage":
            f"rendering — {int(secs // 60)}m {int(secs % 60):02d}s elapsed"}))
    ctx.checkpoint("before_upload")               # never upload a partial export
    path = _upload_export(project, f"renders/{job['id']}.mp4", out)
    dur = time.time() - t0
    ctx.rec("final_render", round(dur, 2), result["size_bytes"],
            units={"cpu_hours": dur / 3600})
    set_project_status(project["id"], "completed",
                       f"final render {job['id'][:8]} completed")
    return {"output": path, "export_provider": EXPORT_STORAGE_PROVIDER,
            **{k: result[k] for k in ("duration", "width", "height", "size_bytes")}}


def _render_bridged_editor(job: dict, project: dict, tmp: str, ctx: JobContext,
                           doc_row: dict, document: dict) -> dict:
    """Export a bridged (music-less) editor document: render the exact saved picture
    timeline with the clips' ORIGINAL audio. No licensed-music mixing, no fabricated
    audio records. Exact-version binding was already checked by the caller."""
    from .product_editor import renderer_timeline
    from .renderer2 import render_timeline

    picture_items = next(track["items"] for track in document["tracks"]
                         if track["type"] == "picture")
    sources, assets = _download_sources(project, tmp, ctx)
    allowed = {item["id"] for item in assets}
    referenced = {str(clip["assetId"]) for clip in picture_items} | {
        str((clip.get("audioFrom") or {}).get("assetId"))
        for clip in picture_items if clip.get("audioFrom")}
    if referenced - allowed:
        raise RuntimeError("editor picture references a foreign source asset")
    set_project_status(project["id"], "rendering",
                       f"Product Editor revision {doc_row['version']} render (bridged)")
    update_job(job["id"], {"current_stage": "rendering picture + original audio",
                           "progress": 40})
    ctx.checkpoint("before_editor_render")
    out = os.path.join(tmp, "product-editor-bridged.mp4")
    result = render_timeline(
        renderer_timeline(document), sources, out, profile="final",
        cancel_check=lambda: ctx.cancelled(),
        tick=lambda secs: update_job(job["id"], {"current_stage":
            f"rendering — {int(secs // 60)}m {int(secs % 60):02d}s elapsed"}))
    ctx.checkpoint("before_editor_upload")
    path = _upload_export(
        project, f"renders/{job['id']}-editor-v{doc_row['version']}.mp4", out)
    size = os.path.getsize(out)
    ctx.rec("product_editor_render", bytes_=size, units={"cpu_hours": 0})
    caption_items = next(track["items"] for track in document["tracks"]
                         if track["type"] == "captions")
    set_project_status(project["id"], "completed",
                       f"Product Editor revision {doc_row['version']} export completed")
    return {"output": path, "export_provider": EXPORT_STORAGE_PROVIDER,
            "editor_document_id": doc_row["id"],
            "editor_document_version": doc_row["version"],
            "duration": result["duration"], "width": result["width"],
            "height": result["height"], "size_bytes": size,
            "graphics_events": 0, "caption_groups": len(caption_items),
            "music_gain_db": None}


def handle_product_editor_render(job: dict, project: dict, tmp: str,
                                 ctx: JobContext) -> dict:
    """Render the exact saved Product Editor document through M4/M5 contracts."""
    import json as _json

    from .pipeline.audio_rendering import CompletedAudioMix, render_completed_mix
    from .pipeline.editorial_intelligence import CompleteCandidateManifest
    from .pipeline.music_supervisor import MusicPlan
    from .pipeline.picture_editor import PictureCandidateSummary
    from .pipeline.visual_finishing import (CaptionPackage, ColorPackage,
                                             GraphicsPackage,
                                             render_finishing_preview)

    params = job.get("params") or {}
    documents = supa.db_select(
        "editor_documents", f"id=eq.{params['editor_document_id']}&limit=1",
    )
    if not documents:
        raise RuntimeError("saved editor document is missing")
    row = documents[0]
    if (row["project_id"] != project["id"] or row["user_id"] != project["user_id"]
            or row["version"] != params.get("editor_document_version")):
        raise RuntimeError("editor render version ancestry is inconsistent")
    document = row["document"]
    if isinstance(document, str):
        document = _json.loads(document)
    if any(item.get("required") and not item.get("rendered")
           for item in document.get("attribution", [])):
        raise RuntimeError("required attribution has not been rendered")
    candidate_rows = supa.db_select(
        "candidate_runs", f"id=eq.{row['candidate_run_id']}&limit=1",
    )
    if not candidate_rows or candidate_rows[0]["project_id"] != project["id"]:
        raise RuntimeError("editor candidate ancestry is invalid")
    candidate_row = candidate_rows[0]
    # Bridged candidates have no music/audio ancestry — render picture + original
    # audio instead of the M4/M5 licensed-music mix.
    if candidate_row.get("audio_mix_run_id") is None:
        return _render_bridged_editor(job, project, tmp, ctx, row, document)
    raw_manifest = candidate_row["manifest"]
    if isinstance(raw_manifest, str):
        raw_manifest = _json.loads(raw_manifest)
    manifest = CompleteCandidateManifest(**raw_manifest)

    picture_items = next(track["items"] for track in document["tracks"]
                         if track["type"] == "picture")
    picture_timeline = {
        "version": 1, "width": document["width"], "height": document["height"],
        "fps": document["fps"], "duration": document["duration"],
        "tracks": [{"id": "video-1", "type": "video", "clips": picture_items}],
    }
    caption_items = next(track["items"] for track in document["tracks"]
                         if track["type"] == "captions")
    graphic_items = [item for item in next(
        track["items"] for track in document["tracks"] if track["type"] == "graphics"
    ) if item.get("enabled", True)]
    caption_payload = manifest.captions.model_dump(mode="json")
    caption_payload["groups"] = caption_items
    graphics_payload = manifest.graphics.model_dump(mode="json")
    graphics_payload["events"] = graphic_items
    captions = CaptionPackage(**caption_payload)
    graphics = GraphicsPackage(**graphics_payload)
    color = ColorPackage(**manifest.color.model_dump(mode="json"))

    completed_rows = supa.db_select(
        "audio_mix_runs", f"id=eq.{candidate_row['audio_mix_run_id']}&limit=1",
    )
    music_rows = supa.db_select(
        "music_sound_runs", f"id=eq.{candidate_row['music_sound_run_id']}&limit=1",
    )
    licensed_rows = supa.db_select(
        "licensed_music_assets",
        f"music_sound_run_id=eq.{candidate_row['music_sound_run_id']}"
        "&order=version.desc&limit=1",
    )
    if not completed_rows or not music_rows or not licensed_rows:
        raise RuntimeError("completed audio ancestry is missing")
    completed_payload = completed_rows[0]["mix_instructions"]
    plan_payload = music_rows[0]["music_plan"]
    if isinstance(completed_payload, str):
        completed_payload = _json.loads(completed_payload)
    if isinstance(plan_payload, str):
        plan_payload = _json.loads(plan_payload)
    completed = CompletedAudioMix(**completed_payload)
    plan = MusicPlan(**plan_payload)

    sources, assets = _download_sources(project, tmp, ctx)
    allowed_assets = {item["id"] for item in assets}
    referenced = {str(clip["assetId"]) for clip in picture_items} | {
        str((clip.get("audioFrom") or {}).get("assetId"))
        for clip in picture_items if clip.get("audioFrom")}
    if referenced - allowed_assets:
        raise RuntimeError("editor picture references a foreign source asset")
    music_path = os.path.join(tmp, "licensed-music" + os.path.splitext(
        licensed_rows[0]["filename"])[1])
    supa.storage_download(licensed_rows[0]["storage_bucket"],
                          licensed_rows[0]["storage_path"], music_path)
    duration = float(document["duration"])
    summary = PictureCandidateSummary(
        candidateId=f"editor-{row['id']}", label="Product Editor",
        storyVariantId="editor_revision", valid=True, durationSeconds=duration,
        targetDurationSeconds=max(15, min(60, duration)), coverageRatio=1,
        editorialScore=1, structuralSignature=">".join(
            str(clip["id"]) for clip in picture_items),
        clipCount=len(picture_items), timeline=picture_timeline,
    )
    audio_path = os.path.join(tmp, "editor-audio.mp4")
    gain = float(next(track["items"][0].get("gainDb", -8) for track in document["tracks"]
                      if track["type"] == "music" and track["items"]))
    set_project_status(project["id"], "rendering",
                       f"Product Editor revision {row['version']} render")
    update_job(job["id"], {"current_stage": "mixing saved editor audio", "progress": 25})
    render_completed_mix(summary, sources, music_path, plan, completed.targetVsActual,
                         audio_path, tmp, music_gain_db=gain)
    ctx.checkpoint("before_editor_finishing")
    output = os.path.join(tmp, "product-editor-final.mp4")
    update_job(job["id"], {"current_stage": "rendering captions and graphics", "progress": 70})
    qc = render_finishing_preview(audio_path, output, graphics, captions, color)
    ctx.checkpoint("before_editor_upload")
    path = _upload_export(
        project, f"renders/{job['id']}-editor-v{row['version']}.mp4", output,
    )
    size = os.path.getsize(output)
    ctx.rec("product_editor_render", bytes_=size,
            units={"cpu_hours": 0})
    set_project_status(project["id"], "completed",
                       f"Product Editor revision {row['version']} export completed")
    return {"output": path, "export_provider": EXPORT_STORAGE_PROVIDER,
            "editor_document_id": row["id"],
            "editor_document_version": row["version"],
            "duration": qc["durationSeconds"], "width": qc["width"],
            "height": qc["height"], "size_bytes": size,
            "graphics_events": qc["graphicsEvents"],
            "caption_groups": qc["captionGroups"], "music_gain_db": gain}


HANDLERS = {"analysis": handle_analysis, "autoedit": handle_autoedit,
            "revision": handle_revision, "final_render": handle_final_render,
            "editorial_plan": handle_editorial_plan}
# editorial_plan is deliberately absent: a failed OPTIONAL planning stage must
# never move the project's state machine (the guard below skips unknown kinds).
FAIL_STATUS = {"analysis": "analysis_failed", "autoedit": "analysis_failed",
               "revision": "analysis_failed", "final_render": "render_failed"}


def _run_job(job: dict) -> None:
    t0 = time.time()
    tmp = tempfile.mkdtemp(prefix=f"stromation-job-{job['id'][:8]}-")
    ctx = JobContext(job)
    log_event("JOB-START", job_id=job["id"], kind=job["kind"],
              project_id=job["project_id"], attempt=job["attempt_count"])
    try:
        projects = supa.db_select("projects", f"id=eq.{job['project_id']}")
        if not projects:
            raise RuntimeError("project vanished")
        project = projects[0]
        artifacts = HANDLERS[job["kind"]](job, project, tmp, ctx)
        artifacts["telemetry_status"] = ctx.telemetry_status()
        update_job(job["id"], {"status": "completed", "progress": 100,
                               "artifacts": artifacts,
                               "processing_seconds": round(time.time() - t0, 2),
                               "completed_at": _now()})
        log_event("JOB-DONE", job_id=job["id"], kind=job["kind"],
                  seconds=round(time.time() - t0, 1),
                  telemetry=artifacts["telemetry_status"]["complete"])
    except (JobCancelled, RenderCancelled) as e:
        # B4: RenderCancelled (ffmpeg terminated mid-render by a cancel
        # request) is a CANCELLATION, not a failure — previously it fell
        # through to the generic handler and told the customer their edit
        # "failed" right after they pressed Cancel.
        update_job(job["id"], {"status": "cancelled",
                               "error_message": f"cancelled at checkpoint: {e}",
                               "artifacts": {"telemetry_status":
                                             ctx.telemetry_status()},
                               "processing_seconds": round(time.time() - t0, 2),
                               "completed_at": _now()})
        if job["kind"] in FAIL_STATUS:   # optional stages (editorial_plan) must
            try:                          # preserve the exact prior status
                set_project_status(job["project_id"], "ready",
                                   f"job {job['id'][:8]} cancelled")
            except Exception:
                pass
        log_event("JOB-CANCELLED", job_id=job["id"], kind=job["kind"],
                  checkpoint=str(e))
    except Exception as e:  # noqa: BLE001 — a job must never kill the worker
        err = f"{type(e).__name__}: {e}"
        ctx.rec("failed_job", round(time.time() - t0, 2),
                units={"cpu_hours": (time.time() - t0) / 3600})
        update_job(job["id"], {"status": "failed",
                               "error_message": err[:900],
                               "artifacts": {"telemetry_status":
                                             ctx.telemetry_status()},
                               "processing_seconds": round(time.time() - t0, 2),
                               "completed_at": _now()})
        fail_status = FAIL_STATUS.get(job["kind"])
        # An operator-requested editorial_plan stays status-silent (optional
        # stage), but in the V2 customer journey the plan is REQUIRED: a
        # planner crash must surface as a failed edit, never as an eternal
        # "building your edit" spinner and never as a silent legacy fallback.
        if job["kind"] == "editorial_plan"                 and (job.get("params") or {}).get("source") in (
                    "customer_journey", "recut"):
            fail_status = "analysis_failed"
        # AUTO-RESUME the customer journey. A rejected plan is a generative
        # miss, not a broken system: the repair loop converges but any one of
        # ~30 constraints can still slip. Rather than dead-ending on a human
        # clicking Try again, re-enqueue the planning stage with fresh
        # randomness, bounded so it can never loop or burn budget forever.
        # Everything else (crashes, quota, missing catalog) fails through
        # normally on the first try.
        params = job.get("params") or {}
        if (job["kind"] == "editorial_plan"
                and params.get("source") in ("customer_journey", "recut")
                and err.startswith("PlanRejected")
                and int(params.get("plan_retry", 0)) < MAX_PLAN_RETRIES):
            nxt = int(params.get("plan_retry", 0)) + 1
            try:
                enqueue_job(job["project_id"], job["user_id"],
                            "editorial_plan", {**params, "plan_retry": nxt})
                set_project_status(
                    job["project_id"], "analyzing",
                    f"refining your edit (pass {nxt + 1})")
                log_event("PLAN-AUTO-RETRY", job_id=job["id"], attempt=nxt)
                return
            except ConcurrencyLimit:
                pass          # at the cap — fall through and surface honestly

        if fail_status:
            try:
                set_project_status(job["project_id"], fail_status,
                                   f"job {job['id'][:8]} failed: {err[:200]}")
            except Exception:
                pass
        log_event("JOB-FAILED", job_id=job["id"], kind=job["kind"],
                  error=err[:300], trace=traceback.format_exc()[-400:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)   # partial outputs always deleted


_stop = threading.Event()


def worker_loop():
    log_event("WORKER-START", concurrency=WORKER_CONCURRENCY,
              stale_after_s=STALE_AFTER_S)
    recover_stale()
    while not _stop.is_set():
        try:
            job = _claim_next()
            if job:
                _run_job(job)
                continue
            recover_stale()
        except Exception as e:  # noqa: BLE001
            log_event("WORKER-LOOP-ERROR", error=str(e)[:300])
        _stop.wait(POLL_INTERVAL_S)


def start_worker() -> threading.Thread:
    t = threading.Thread(target=worker_loop, daemon=True, name="pipeline-worker")
    t.start()
    return t


def stop_worker():
    _stop.set()
