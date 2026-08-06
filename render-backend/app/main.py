"""Stromation render backend — FastAPI.

POST /render {"job_id": "..."}  (Authorization: Bearer <user JWT>)
  1. Verify the JWT against GoTrue.
  2. Load the render_jobs row; require job.user_id == caller.
  3. Verify the ownership chain: project, timeline, media asset all belong
     to the caller and to each other.
  4. Validate the stored timeline JSON and derive the v1 render plan.
  5. Background task: download private source -> ffmpeg -> upload private
     export -> update job row (status/progress/output metadata). Temp files
     are always deleted.

GET /healthz — liveness (no auth).

Job status is NOT served here: the frontend polls the render_jobs row
directly via PostgREST under RLS (select-own policy).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from typing import Literal
from uuid import UUID, uuid4

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, ValidationError

from . import config

config.validate()          # fail fast with clear errors before anything imports supa

from . import raw_uploads, s3store, supa
from .renderer import RenderError, render

# Exports stay on Supabase by default; set to "s3" to route completed exports to
# the S3 bucket too (raw footage always uses S3 once AWS_S3_BUCKET is set).
EXPORT_STORAGE_PROVIDER = os.environ.get("EXPORT_STORAGE_PROVIDER", "supabase")

app = FastAPI(title="Stromation Render Backend", version="0.1.0")

ALLOWED_ORIGINS = os.environ.get(
    "ALLOWED_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


class RenderRequest(BaseModel):
    job_id: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _service_insert(table: str, body: dict) -> dict:
    """Insert one service-role row and require the representation back."""
    import httpx as _hx
    r = _hx.post(f"{supa.SUPABASE_URL}/rest/v1/{table}",
                 headers={"apikey": supa.SERVICE_KEY,
                          "Authorization": f"Bearer {supa.SERVICE_KEY}",
                          "Content-Type": "application/json",
                          "Prefer": "return=representation"},
                 json=body, timeout=30)
    r.raise_for_status()
    return r.json()[0]


def _service_patch(table: str, filters: str, body: dict) -> list[dict]:
    """Patch service-role rows and require the representations back."""
    import httpx as _hx
    r = _hx.patch(f"{supa.SUPABASE_URL}/rest/v1/{table}?{filters}",
                  headers={"apikey": supa.SERVICE_KEY,
                           "Authorization": f"Bearer {supa.SERVICE_KEY}",
                           "Content-Type": "application/json",
                           "Prefer": "return=representation"},
                  json=body, timeout=30)
    r.raise_for_status()
    return r.json()


HUMAN_EDIT_IDLE_GAP_CAP_SECONDS = max(
    1.0, min(3600.0, float(os.environ.get("HUMAN_EDIT_IDLE_GAP_CAP_SECONDS", "300"))))
HUMAN_OPERATION_INDEX_RETRIES = max(
    1, min(10, int(os.environ.get("HUMAN_OPERATION_INDEX_RETRIES", "3"))))


def _timing_events(session_id: str) -> list[dict]:
    return supa.db_select(
        "human_edit_timing_events",
        f"human_edit_session_id=eq.{session_id}&order=occurred_at.asc",
    )


def _timing_snapshot(session: dict, extra_event: dict | None = None) -> dict:
    from .human_ceiling import measure_server_time
    events = _timing_events(session["id"])
    if extra_event:
        events.append(extra_event)
    return measure_server_time(
        events, float(session.get("idle_gap_cap_seconds")
                      or HUMAN_EDIT_IDLE_GAP_CAP_SECONDS))


def _record_timing_event(session: dict, event_type: str, operator_id: str,
                         client_reported_seconds: float | None = None,
                         operation_index: int | None = None,
                         details: dict | None = None) -> tuple[dict, dict]:
    event = _service_insert("human_edit_timing_events", {
        "project_id": session["project_id"],
        "user_id": session["user_id"],
        "human_edit_session_id": session["id"],
        "event_type": event_type,
        "operation_index": operation_index,
        "operator_user_id": operator_id,
        "client_reported_seconds": client_reported_seconds,
        "details": details or {},
    })
    snapshot = _timing_snapshot(session)
    update = {
        "server_measured_seconds": snapshot["server_measured_seconds"],
        # Backward-compatible field is explicitly server-derived now.
        "human_correction_seconds": snapshot["server_measured_seconds"],
        "timing_state": snapshot["timing_state"],
        "last_activity_at": snapshot["last_activity_at"],
    }
    if client_reported_seconds is not None:
        update["client_reported_seconds"] = client_reported_seconds
    _service_patch("human_edit_sessions", f"id=eq.{session['id']}", update)
    if operation_index is not None:
        seconds = snapshot["operation_seconds"].get(operation_index, 0)
        _service_patch(
            "user_corrections",
            f"human_edit_session_id=eq.{session['id']}&operation_index=eq.{operation_index}",
            {"server_measured_seconds": seconds},
        )
    return event, snapshot


def _insert_correction_with_retry(session_id: str, payload: dict) -> dict:
    """Assign operation_index with bounded retry on the DB unique constraint."""
    import httpx as _hx
    for _attempt in range(HUMAN_OPERATION_INDEX_RETRIES):
        existing = supa.db_select(
            "user_corrections",
            f"human_edit_session_id=eq.{session_id}"
            "&order=operation_index.desc&limit=1",
        )
        operation_index = ((existing[0].get("operation_index") or 0) + 1
                           if existing else 1)
        body = {**payload, "operation_index": operation_index}
        response = _hx.post(
            f"{supa.SUPABASE_URL}/rest/v1/user_corrections",
            headers={"apikey": supa.SERVICE_KEY,
                     "Authorization": f"Bearer {supa.SERVICE_KEY}",
                     "Content-Type": "application/json",
                     "Prefer": "return=representation"},
            json=body, timeout=30)
        if response.status_code == 201:
            return response.json()[0]
        if response.status_code != 409:
            response.raise_for_status()
    raise HTTPException(409, "operation index contention; retry the edit")


def _fail_job(job_id: str, message: str) -> None:
    supa.db_update("render_jobs", f"id=eq.{job_id}",
                   {"status": "failed", "error_message": message[:1000],
                    "completed_at": _now()})


def _run_render_job(job_id: str, plan_dict: dict, asset: dict, project: dict) -> None:
    """Background worker: download -> render -> upload -> record."""
    from . import media_store
    from .timeline import RenderPlan
    plan = RenderPlan(**plan_dict)
    tmp = tempfile.mkdtemp(prefix=f"stromation-render-{job_id[:8]}-")
    try:
        supa.db_update("render_jobs", f"id=eq.{job_id}",
                       {"status": "processing", "progress": 10, "started_at": _now()})
        src = os.path.join(tmp, "source" + os.path.splitext(asset.get("filename")
                                                            or asset["storage_path"])[1])
        media_store.download_media_asset(asset, project, src)
        supa.db_update("render_jobs", f"id=eq.{job_id}", {"progress": 30})

        dst = os.path.join(tmp, "output.mp4")
        result = render(plan, src, dst)
        supa.db_update("render_jobs", f"id=eq.{job_id}", {"progress": 80})

        job = supa.db_select("render_jobs", f"id=eq.{job_id}",
                             "user_id,project_id")[0]
        out_path = (f"users/{job['user_id']}/projects/{job['project_id']}"
                    f"/exports/{job_id}.mp4")
        supa.storage_upload("exports", out_path, dst)

        supa.db_update("render_jobs", f"id=eq.{job_id}", {
            "status": "completed", "progress": 100,
            "output_storage_path": out_path,
            "output_size_bytes": result.size_bytes,
            "output_duration_seconds": result.duration_seconds,
            "output_width": result.width,
            "output_height": result.height,
            "completed_at": _now(),
        })
    except RenderError as e:
        _fail_job(job_id, f"render failed: {e}")
    except Exception as e:  # noqa: BLE001 — job must never crash the service
        _fail_job(job_id, f"internal error: {type(e).__name__}: {e}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@app.get("/healthz")
def healthz():
    """Liveness only — process is up."""
    return {"ok": True}


@app.get("/readyz")
def readyz():
    """Readiness: config valid + database reachable + storage reachable.
    Returns 503 with the failing component (no secrets/paths leaked)."""
    import httpx as _hx
    problems = config.validate(exit_on_error=False)
    try:
        r = _hx.get(f"{supa.SUPABASE_URL}/rest/v1/projects?select=id&limit=1",
                    headers={"apikey": supa.SERVICE_KEY,
                             "Authorization": f"Bearer {supa.SERVICE_KEY}"},
                    timeout=10)
        if r.status_code != 200:
            problems.append("database not reachable")
    except Exception:
        problems.append("database not reachable")
    try:
        r = _hx.get(f"{supa.SUPABASE_URL}/storage/v1/bucket/raw-footage",
                    headers={"apikey": supa.SERVICE_KEY,
                             "Authorization": f"Bearer {supa.SERVICE_KEY}"},
                    timeout=10)
        if r.status_code != 200:
            problems.append("storage bucket not reachable")
    except Exception:
        problems.append("storage bucket not reachable")
    if problems:
        raise HTTPException(503, {"ready": False, "problems": problems})
    return {"ready": True}


@app.get("/readyz/s3")
def readyz_s3():
    """Shallow public S3 probe: {enabled, reachable} only — no bucket name,
    region, keys, paths, or error class. Informational; does not gate /readyz."""
    status = s3store.check_connectivity()
    return {"enabled": bool(status.get("enabled")),
            "reachable": bool(status.get("reachable"))}


@app.get("/readyz/s3/canary")
def readyz_s3_canary(authorization: str = Header(default="")):
    """Operator-only deep S3 probe: multipart create/abort + object put/get/delete
    under users/_readiness/, cleaned up in finally. Verifies the upload flow's IAM
    permissions (which HeadBucket cannot)."""
    _require_operator(authorization)
    return s3store.check_canary()


# ==================== operator API (P3/P4) ====================
# Operator access is enforced SERVER-SIDE (operators table lookup with the
# service role) — never by a hidden frontend route. Every sensitive action is
# audited with a CONFIRMED write (see _audit docstring for the policy).

from contextlib import asynccontextmanager  # noqa: E402


@asynccontextmanager
async def _lifespan(app_):
    if os.environ.get("WORKER_ENABLED", "1") == "1":
        from . import jobs
        jobs.start_worker()
    yield


app.router.lifespan_context = _lifespan


# ---- request-body size limit (operator/JSON endpoints only need small bodies)
MAX_BODY_BYTES = int(os.environ.get("MAX_BODY_BYTES", str(1024 * 1024)))


@app.middleware("http")
async def _body_size_limit(request, call_next):
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > MAX_BODY_BYTES:
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": "request body too large"}, status_code=413)
    return await call_next(request)


# ---- sanitized errors: unexpected exceptions never leak paths/traces
@app.exception_handler(Exception)
async def _unhandled(request, exc):
    from fastapi.responses import JSONResponse
    from .logging_util import log_event
    log_event("API-UNHANDLED-ERROR", path=str(request.url.path),
              error=f"{type(exc).__name__}: {exc}"[:300])
    return JSONResponse({"detail": "internal error"}, status_code=500)


# ---- simple in-memory rate limiter for expensive operator actions
_rate: dict[str, list[float]] = {}
RATE_LIMIT_PER_MIN = int(os.environ.get("OPERATOR_RATE_LIMIT_PER_MIN", "10"))
# Per-bucket overrides. The 10/min operator default STRANGLED customer uploads:
# a multipart upload signs a URL per 16 MB part, so anything over ~150 MB on a
# normal connection hit 429 mid-transfer. Signing is cheap (auth + ownership +
# a presign), so it gets headroom for a full 2 GB upload (128 parts).
_RATE_OVERRIDES = {"raw_upload_sign": 240, "raw_upload": 30,
                   "raw_finalize": 20, "editor_write": 60}


def _rate_check(user_id: str, bucket: str = "enqueue") -> None:
    import time as _t
    key = f"{user_id}:{bucket}"
    now = _t.time()
    window = [t for t in _rate.get(key, []) if now - t < 60]
    if len(window) >= _RATE_OVERRIDES.get(bucket, RATE_LIMIT_PER_MIN):
        raise HTTPException(429, "rate limit exceeded, retry in a minute")
    window.append(now)
    _rate[key] = window


def _auth_user(authorization: str) -> dict:
    token = authorization.removeprefix("Bearer ").strip()
    try:
        return supa.verify_user(token)
    except supa.AuthError as e:
        raise HTTPException(401, str(e))


def _require_operator(authorization: str) -> dict:
    user = _auth_user(authorization)
    rows = supa.db_select("operators", f"user_id=eq.{user['id']}")
    if not rows:
        raise HTTPException(403, "operator access required")
    return user


class AuditFailure(Exception):
    pass


def _audit(operator: dict, action: str, project_id=None, details=None) -> str:
    """CONFIRMED audit write — AUDIT-BEFORE-ACTION policy.

    Policy (documented): the audit record is inserted and CONFIRMED (with one
    retry) BEFORE the sensitive action runs. If the audit store is unavailable
    the action is aborted with 503 — we never perform an unaudited sensitive
    action. If the action later fails, the audit row remains as a record of the
    attempt (the endpoint's error response makes the outcome unambiguous).
    True DB-level atomicity is not possible across PostgREST + external
    side-effects; this ordering guarantees no unaudited action instead.
    Failures raise AuditFailure (mapped to 503) and emit an operational alert.
    """
    import httpx as _hx
    from .logging_util import log_event
    payload = {"operator_user_id": operator["id"], "action": action,
               "project_id": project_id, "details": details or {}}
    last_err = None
    for _attempt in range(2):
        try:
            r = _hx.post(f"{supa.SUPABASE_URL}/rest/v1/operator_audit",
                         headers={"apikey": supa.SERVICE_KEY,
                                  "Authorization": f"Bearer {supa.SERVICE_KEY}",
                                  "Content-Type": "application/json",
                                  "Prefer": "return=representation"},
                         json=payload, timeout=15)
            if r.status_code == 201:
                return r.json()[0]["id"]
            last_err = f"HTTP {r.status_code}"
        except Exception as e:
            last_err = f"{type(e).__name__}"
    log_event("AUDIT-FAILURE-ALERT", action=action, project_id=project_id,
              operator=operator["id"], error=last_err)
    raise AuditFailure(f"audit store unavailable ({last_err})")


@app.exception_handler(AuditFailure)
async def _audit_failure(request, exc):
    from fastapi.responses import JSONResponse
    return JSONResponse(
        {"detail": "action aborted: audit record could not be stored"},
        status_code=503)


def _get_project(project_id: str) -> dict:
    rows = supa.db_select("projects", f"id=eq.{project_id}")
    if not rows:
        raise HTTPException(404, "project not found")
    return rows[0]


class JobParams(BaseModel):
    params: dict = {}


def _enqueue(kind: str, project_id: str, body: JobParams, authorization: str):
    from . import jobs
    op = _require_operator(authorization)
    _rate_check(op["id"], "enqueue")
    project = _get_project(project_id)
    # audit-before-action: intent is recorded and confirmed first
    _audit(op, f"enqueue_{kind}", project_id, {"params": body.params})
    try:
        job = jobs.enqueue_job(project_id, project["user_id"], kind, body.params)
    except jobs.ConcurrencyLimit as e:
        raise HTTPException(429, str(e))
    return job


@app.post("/projects/{project_id}/analyze")
def op_analyze(project_id: str, body: JobParams = JobParams(),
               authorization: str = Header(default="")):
    return _enqueue("analysis", project_id, body, authorization)



@app.post("/projects/{project_id}/request-analysis")
def op_request_analysis(project_id: str,
                        authorization: str = Header(default="")):
    """User-accessible endpoint to trigger analysis after footage upload.

    Unlike /analyze (operator-only), this endpoint:
    - Accepts any authenticated user
    - Verifies the user owns the project
    - Is idempotent: returns the existing active job if one already exists
    - Prevents duplicate active analysis jobs for the same project

    Called by the frontend immediately after upload completes.
    """
    from . import jobs
    # _owned_project also rejects soft-deleted projects (404) — a deleted project
    # can never restart analysis.
    user, _ = _owned_project(project_id, authorization)
    # Idempotency: return existing active analysis job if present
    active = supa.db_select(
        "pipeline_jobs",
        f"project_id=eq.{project_id}&kind=eq.analysis"
        f"&status=in.(queued,processing)&order=created_at.desc&limit=1")
    if active:
        return active[0]
    # Enqueue — enqueue_job handles global per-user concurrency cap
    try:
        job = jobs.enqueue_job(project_id, user["id"], "analysis", {})
    except jobs.ConcurrencyLimit as e:
        raise HTTPException(429, str(e))
    return job


@app.post("/projects/{project_id}/request-edit")
def customer_request_edit(project_id: str,
                          authorization: str = Header(default="")):
    """Resume the analysis -> edit journey from wherever it stopped.

    The explicit retry path for a failed editorial_plan or autoedit job:
    - V2 flag ON: re-enqueues editorial_plan (chain source preserved), or —
      when an APPROVED plan already exists — skips straight to autoedit with
      that exact plan id/version. Never re-runs analysis, never falls back to
      the legacy selector.
    - V2 flag OFF: enqueues the legacy customer autoedit, exactly what
      analysis completion would have done.
    Ownership-checked, rate-limited, idempotent (active jobs are returned).
    """
    from . import jobs
    user, project = _owned_project(project_id, authorization)
    _rate_check(user["id"], "request_edit")
    if not supa.db_select("segments", f"project_id=eq.{project_id}&limit=1"):
        raise HTTPException(409, "this video has not been analysed yet")
    active = supa.db_select(
        "pipeline_jobs",
        f"project_id=eq.{project_id}&kind=in.(autoedit,editorial_plan)"
        "&status=in.(queued,processing)&order=created_at.desc&limit=1")
    if active:
        return active[0]
    try:
        if jobs.picture_edit_v2_enabled():
            job = jobs._maybe_enqueue_customer_editorial_plan(project)
            if not job:
                raise HTTPException(429, "too many active jobs — try again "
                                         "in a moment")
        else:
            job = jobs.enqueue_job(project_id, user["id"], "autoedit",
                                   {"source": "customer_journey"})
    except jobs.ConcurrencyLimit as e:
        raise HTTPException(429, str(e))
    jobs.set_project_status(project_id, "analyzing", "edit restarted")
    return job


class RecutRequest(BaseModel):
    """Optional new output shape for a re-cut."""
    aspectRatio: str | None = Field(default=None, max_length=8)


@app.post("/projects/{project_id}/recut")
def customer_recut(project_id: str, body: RecutRequest = RecutRequest(),
                   authorization: str = Header(default="")):
    """Re-cut an already-analysed project, optionally at a new frame shape.

    Distinct from /request-analysis: the segment catalog is expensive and does
    not depend on the output shape, so a re-cut re-runs only the autoedit stage
    and reuses the existing analysis. Customer-owned (unlike /generate-draft,
    which is operator-only) because changing the shape of your own video is not
    an operator action.

    Ownership-checked, rate-limited, and idempotent: an autoedit already in
    flight is returned rather than queued twice.
    """
    from . import jobs
    user, project = _owned_project(project_id, authorization)
    _rate_check(user["id"], "recut")

    aspect = body.aspectRatio
    if aspect is not None and aspect not in ("16:9", "9:16", "1:1"):
        raise HTTPException(422, "aspectRatio must be one of 16:9, 9:16, 1:1")

    # A re-cut reuses the catalog; without one there is nothing to cut from.
    if not supa.db_select("segments", f"project_id=eq.{project_id}&limit=1"):
        raise HTTPException(409, "this video has not been analysed yet")

    active = supa.db_select(
        "pipeline_jobs",
        f"project_id=eq.{project_id}&kind=eq.autoedit"
        f"&status=in.(queued,processing)&order=created_at.desc&limit=1")
    if active:
        return active[0]

    if aspect and aspect != project.get("aspect_ratio"):
        # Service-role write: aspect_ratio is customer-editable via RLS, but the
        # job must read the same value this request decided on, so commit it here
        # rather than trusting a separate client update to have landed first.
        supa.db_update("projects", f"id=eq.{project_id}", {"aspect_ratio": aspect})
        _editor_audit(user["id"], project_id, "recut_aspect_change",
                      {"from": project.get("aspect_ratio"), "to": aspect})

    effective_aspect = aspect or project.get("aspect_ratio")
    try:
        if jobs.picture_edit_v2_enabled():
            # V2 journey: a re-cut re-PLANS (the plan is shape-aware), then the
            # plan's completion chains into V2 autoedit. Never enqueue a bare
            # autoedit here — with the flag on it would fail for lack of a
            # plan bound to the new shape.
            active_plan = supa.db_select(
                "pipeline_jobs",
                f"project_id=eq.{project_id}&kind=eq.editorial_plan"
                f"&status=in.(queued,processing)&order=created_at.desc&limit=1")
            if active_plan:
                return active_plan[0]
            # Same constraint derivation as the analysis chain (brief,
            # platform, target duration), with the recut's shape override.
            plan_params = {**jobs._plan_constraints_for(project),
                           "source": "recut"}
            if effective_aspect:
                plan_params["aspectRatio"] = effective_aspect
            job = jobs.enqueue_job(project_id, user["id"],
                                   "editorial_plan", plan_params)
        else:
            job = jobs.enqueue_job(project_id, user["id"], "autoedit",
                                   {"source": "recut",
                                    "aspect_ratio": effective_aspect})
    except jobs.ConcurrencyLimit as e:
        raise HTTPException(429, str(e))
    jobs.set_project_status(project_id, "analyzing", "re-cutting at a new shape")
    return job


class EditorialPlanRequest(BaseModel):
    """Binding creative constraints for the Editorial Planner (all optional)."""
    brief: str | None = Field(default=None, max_length=2000)
    platform: str | None = Field(default=None, max_length=40)
    aspectRatio: str | None = Field(default=None, max_length=10)
    tone: str | None = Field(default=None, max_length=200)
    style: str | None = Field(default=None, max_length=200)
    # When true, tone/style words that cannot be parsed into enforceable
    # policy become warned advisories instead of hard rejections.
    toneAdvisoryOnly: bool = False
    durationMin: float | None = Field(default=None, gt=0, le=3600)
    durationMax: float | None = Field(default=None, gt=0, le=3600)
    mustInclude: list[str] = Field(default_factory=list, max_length=20)
    mustExclude: list[str] = Field(default_factory=list, max_length=20)


@app.post("/projects/{project_id}/editorial-plan")
def customer_request_editorial_plan(project_id: str,
                                    body: EditorialPlanRequest = EditorialPlanRequest(),
                                    authorization: str = Header(default="")):
    """Run the Editorial Planner (separate structured planning stage).

    Requires a completed analysis (segment catalog). Owner-only, deleted-aware,
    idempotent per (project, kind): an active planning job is returned as-is.
    The plan lands in editorial_plans; poll GET /projects/{id}/editorial-plan.
    """
    from . import jobs as job_service
    user, _ = _owned_project(project_id, authorization)
    _rate_check(user["id"], "editorial_plan")
    if body.durationMin and body.durationMax and body.durationMin > body.durationMax:
        raise HTTPException(422, "durationMin exceeds durationMax")
    if not supa.db_select("segments", f"project_id=eq.{project_id}&limit=1"):
        raise HTTPException(409, "no segment catalog yet — run analysis first")
    active = supa.db_select(
        "pipeline_jobs",
        f"project_id=eq.{project_id}&kind=eq.editorial_plan"
        "&status=in.(queued,processing)&order=created_at.desc&limit=1")
    if active:
        return active[0]
    try:
        return job_service.enqueue_job(project_id, user["id"], "editorial_plan",
                                       body.model_dump(exclude_none=True))
    except job_service.ConcurrencyLimit as exc:
        raise HTTPException(429, str(exc))


@app.get("/projects/{project_id}/editorial-plan")
def customer_get_editorial_plan(project_id: str,
                                authorization: str = Header(default="")):
    """Latest editorial plan for the project (owner-only, deleted-aware)."""
    _owned_project(project_id, authorization)
    rows = supa.db_select(
        "editorial_plans",
        f"project_id=eq.{project_id}&order=version.desc&limit=1")
    if not rows:
        raise HTTPException(404, "no editorial plan yet")
    return rows[0]


@app.post("/projects/{project_id}/generate-draft")
def op_generate_draft(project_id: str, body: JobParams = JobParams(),
                      authorization: str = Header(default="")):
    return _enqueue("autoedit", project_id, body, authorization)


@app.post("/projects/{project_id}/revise")
def op_revise(project_id: str, body: JobParams = JobParams(),
              authorization: str = Header(default="")):
    return _enqueue("revision", project_id, body, authorization)


@app.post("/projects/{project_id}/render-final")
def op_render_final(project_id: str, body: JobParams = JobParams(),
                    authorization: str = Header(default="")):
    return _enqueue("final_render", project_id, body, authorization)


@app.get("/jobs/{job_id}")
def op_get_job(job_id: str, authorization: str = Header(default="")):
    user = _auth_user(authorization)
    rows = supa.db_select("pipeline_jobs", f"id=eq.{job_id}")
    if not rows:
        raise HTTPException(404, "job not found")
    job = rows[0]
    is_operator = bool(supa.db_select("operators", f"user_id=eq.{user['id']}"))
    if job["user_id"] != user["id"]:
        if not is_operator:                # owners or operators only
            raise HTTPException(403, "operator access required")
        return job
    # Owner path: a job whose parent project has been soft-deleted is hidden, matching
    # the child-table RLS (operators retain access for support/forensics).
    parent = supa.db_select("projects", f"id=eq.{job['project_id']}&limit=1")
    if parent and parent[0].get("deleted_at") and not is_operator:
        raise HTTPException(404, "job not found")
    return job


@app.post("/jobs/{job_id}/retry")
def op_retry_job(job_id: str, authorization: str = Header(default="")):
    from . import jobs
    op = _require_operator(authorization)
    rows = supa.db_select("pipeline_jobs", f"id=eq.{job_id}")
    if not rows:
        raise HTTPException(404, "job not found")
    job = rows[0]
    if job["status"] != "failed":
        raise HTTPException(409, f"job is {job['status']}, only failed jobs retry")
    if job["attempt_count"] >= job["max_attempts"]:
        raise HTTPException(409, "max attempts exhausted")
    _audit(op, "retry_job", job["project_id"], {"job_id": job_id})
    jobs.update_job(job_id, {"status": "queued", "error_message": None})
    return {"job_id": job_id, "status": "queued"}


@app.post("/jobs/{job_id}/cancel")
def op_cancel_job(job_id: str, authorization: str = Header(default="")):
    """Explicit cancellation states: queued -> cancelled immediately;
    processing -> cancel_requested (worker honors it at the next checkpoint;
    in-flight provider requests cannot be interrupted — documented)."""
    from . import jobs
    op = _require_operator(authorization)
    rows = supa.db_select("pipeline_jobs", f"id=eq.{job_id}")
    if not rows:
        raise HTTPException(404, "job not found")
    job = rows[0]
    if job["status"] not in ("queued", "processing", "cancel_requested"):
        raise HTTPException(409, f"job is {job['status']}")
    _audit(op, "cancel_job", job["project_id"],
           {"job_id": job_id, "prior_status": job["status"]})
    try:
        result = jobs.request_cancel(job, requested_by=op["id"])
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {"job_id": job_id, **result}


class TimelineOpsBody(BaseModel):
    base_timeline_id: str
    operations: list[dict]
    protected_ranges: list[list[float]] = Field(default_factory=list)
    human_edit_session_id: str | None = None
    client_reported_seconds: float | None = Field(default=None, ge=0, le=12 * 60 * 60)
    # Deprecated request alias retained for older clients; never authoritative.
    elapsed_seconds: float | None = Field(default=None, ge=0, le=12 * 60 * 60)
    note: str = Field(default="", max_length=1000)


@app.post("/projects/{project_id}/timeline-ops")
def op_timeline_ops(project_id: str, body: TimelineOpsBody,
                    authorization: str = Header(default="")):
    """Operator edits the timeline through CONSTRAINED operations only."""
    from .human_ceiling import (HUMAN_DRAFT, HumanCeilingError,
                                correction_type, split_elapsed_seconds)
    from .timeline_ops import OpError, apply_operations, parse_operations
    op = _require_operator(authorization)
    project = _get_project(project_id)
    rows = supa.db_select("timelines", f"id=eq.{body.base_timeline_id}")
    if not rows or rows[0]["project_id"] != project_id:
        raise HTTPException(404, "timeline not found in this project")
    tl = rows[0]["timeline_json"]
    if isinstance(tl, str):
        tl = json.loads(tl)

    session = None
    if body.human_edit_session_id:
        sessions = supa.db_select(
            "human_edit_sessions", f"id=eq.{body.human_edit_session_id}")
        if not sessions or sessions[0]["project_id"] != project_id:
            raise HTTPException(404, "human edit session not found in this project")
        session = sessions[0]
        if session["status"] != "active":
            raise HTTPException(409, f"human edit session is {session['status']}")
        try:
            timing = _timing_snapshot(session)
        except HumanCeilingError as e:
            raise HTTPException(409, f"inconsistent session timing: {e}")
        if timing["timing_state"] != "running":
            raise HTTPException(409, "human edit session must be resumed before editing")
        if session.get("current_timeline_id") != body.base_timeline_id:
            raise HTTPException(409, "base timeline is not the session's current human draft")
    elif rows[0].get("is_immutable"):
        raise HTTPException(
            409, "immutable timeline cannot be edited; start a human edit session")

    _audit(op, "timeline_ops", project_id,
           {"base": body.base_timeline_id, "operations": body.operations,
            "protected_ranges": body.protected_ranges,
            "human_edit_session_id": body.human_edit_session_id,
            "client_reported_seconds": body.client_reported_seconds,
            "legacy_elapsed_seconds": body.elapsed_seconds})
    try:
        ops = parse_operations(body.operations)
        result = apply_operations(tl, ops, actor="user",
                                  protected=[tuple(r) for r in
                                             body.protected_ranges if len(r) == 2])
    except OpError as e:
        raise HTTPException(422, str(e))
    if session and not result.applied:
        raise HTTPException(422, "session-bound edit must apply at least one operation")

    latest = supa.db_select("timelines",
                            f"project_id=eq.{project_id}&order=version.desc&limit=1")
    next_version = (latest[0]["version"] + 1) if latest else rows[0]["version"] + 1
    new_tl = _service_insert("timelines", {
        "project_id": project_id,
        "user_id": project["user_id"],
        "version": next_version,
        "timeline_json": result.timeline,
        "lineage": HUMAN_DRAFT if session else rows[0].get("lineage", "legacy"),
        "parent_timeline_id": rows[0]["id"],
        "edit_run_id": session.get("edit_run_id") if session else rows[0].get("edit_run_id"),
        "is_immutable": False,
    })

    if session:
        client_hint = (body.client_reported_seconds
                       if body.client_reported_seconds is not None
                       else body.elapsed_seconds)
        client_hints = split_elapsed_seconds(client_hint, len(result.applied))
        for applied, operation_client_hint in zip(
            result.applied, client_hints, strict=True,
        ):
            kind = correction_type(applied)
            correction = _insert_correction_with_retry(session["id"], {
                "project_id": project_id,
                "user_id": project["user_id"],
                "original_timeline_version": rows[0]["version"],
                "requested_change": body.note or applied.get("comment") or f"human {kind}",
                "applied_operations": [applied],
                "accepted": True,
                "final_timeline_version": new_tl["version"],
                "project_style": "human_ceiling",
                "human_edit_session_id": session["id"],
                "base_timeline_id": rows[0]["id"],
                "result_timeline_id": new_tl["id"],
                "correction_type": kind,
                "elapsed_seconds": operation_client_hint,
                "client_reported_seconds": operation_client_hint,
                "operator_user_id": op["id"],
            })
            _, timing = _record_timing_event(
                session, "operation", op["id"],
                client_reported_seconds=operation_client_hint,
                operation_index=correction["operation_index"],
                details={"correction_type": kind, "timeline_id": new_tl["id"]},
            )
        _service_patch("human_edit_sessions", f"id=eq.{session['id']}", {
            "current_timeline_id": new_tl["id"],
        })
    return {"timeline_id": new_tl["id"], "version": new_tl["version"],
            "lineage": new_tl.get("lineage"),
            "applied": result.applied, "rejected": result.rejected,
            **({"timing": {
                "authoritative_source": "server_timestamps",
                "server_measured_seconds": timing["server_measured_seconds"],
                "client_reported_seconds": client_hint,
                "idle_gap_cap_seconds": session.get("idle_gap_cap_seconds")
                or HUMAN_EDIT_IDLE_GAP_CAP_SECONDS,
            }} if session else {})}


class HumanCeilingStartBody(BaseModel):
    autonomous_initial_timeline_id: str
    autonomous_revised_timeline_id: str | None = None
    edit_run_id: str | None = None


@app.post("/projects/{project_id}/human-ceiling/start")
def op_start_human_ceiling(project_id: str, body: HumanCeilingStartBody,
                           authorization: str = Header(default="")):
    """Freeze available autonomous evidence and branch a human draft."""
    from .human_ceiling import (AUTONOMOUS_INITIAL, AUTONOMOUS_REVISED,
                                HUMAN_DRAFT)
    op = _require_operator(authorization)
    project = _get_project(project_id)
    active = supa.db_select("human_edit_sessions",
                            f"project_id=eq.{project_id}&status=eq.active&limit=1")
    if active:
        raise HTTPException(409, "an active human edit session already exists")

    timeline_ids = [body.autonomous_initial_timeline_id]
    if body.autonomous_revised_timeline_id:
        timeline_ids.append(body.autonomous_revised_timeline_id)
    timelines = []
    for timeline_id in timeline_ids:
        rows = supa.db_select("timelines", f"id=eq.{timeline_id}")
        if not rows or rows[0]["project_id"] != project_id:
            raise HTTPException(404, "autonomous baseline not found in this project")
        timelines.append(rows[0])
    if len(timeline_ids) == 2 and timeline_ids[0] == timeline_ids[1]:
        raise HTTPException(
            422, "omit autonomous_revised_timeline_id when no distinct revision exists")

    edit_run_id = body.edit_run_id
    if edit_run_id:
        runs = supa.db_select("edit_runs", f"id=eq.{edit_run_id}")
        if not runs or runs[0]["project_id"] != project_id:
            raise HTTPException(404, "edit run not found in this project")
        run = runs[0]
        if run.get("timeline_v1_id") != timeline_ids[0]:
            raise HTTPException(422, "baseline timelines do not match the edit run")
        if len(timeline_ids) == 2 and run.get("timeline_v2_id") != timeline_ids[1]:
            raise HTTPException(422, "revised baseline does not match the edit run")
        if len(timeline_ids) == 1 and run.get("timeline_v2_id") is not None:
            raise HTTPException(422, "edit run has a revised timeline; select it explicitly")
    else:
        runs = supa.db_select("edit_runs",
                              f"project_id=eq.{project_id}&order=created_at.desc")
        run = next((r for r in runs
                    if r.get("timeline_v1_id") == timeline_ids[0]
                    and ((len(timeline_ids) == 2
                          and r.get("timeline_v2_id") == timeline_ids[1])
                         or (len(timeline_ids) == 1
                             and r.get("timeline_v2_id") is None))), None)
        edit_run_id = run.get("id") if run else None
    if not edit_run_id:
        raise HTTPException(422, "baselines must belong to the same recorded edit run")

    _audit(op, "start_human_ceiling", project_id, {
        "autonomous_initial_timeline_id": timeline_ids[0],
        "autonomous_revised_timeline_id": (
            timeline_ids[1] if len(timeline_ids) == 2 else None),
        "edit_run_id": edit_run_id,
    })
    lineages = ([AUTONOMOUS_INITIAL, AUTONOMOUS_REVISED]
                if len(timelines) == 2 else [AUTONOMOUS_INITIAL])
    for timeline, lineage in zip(timelines, lineages, strict=True):
        if timeline.get("is_immutable"):
            if timeline.get("lineage") != lineage:
                raise HTTPException(
                    409, f"immutable baseline has unexpected lineage: {timeline.get('lineage')}")
            continue
        _service_patch("timelines", f"id=eq.{timeline['id']}", {
            "lineage": lineage, "is_immutable": True,
            "edit_run_id": edit_run_id,
        })

    latest = supa.db_select("timelines",
                            f"project_id=eq.{project_id}&order=version.desc&limit=1")
    human_timeline = _service_insert("timelines", {
        "project_id": project_id,
        "user_id": project["user_id"],
        "version": (latest[0]["version"] + 1) if latest else 1,
        "timeline_json": timelines[-1]["timeline_json"],
        "lineage": HUMAN_DRAFT,
        "parent_timeline_id": timeline_ids[-1],
        "edit_run_id": edit_run_id,
        "is_immutable": False,
    })
    session = _service_insert("human_edit_sessions", {
        "project_id": project_id,
        "user_id": project["user_id"],
        "edit_run_id": edit_run_id,
        "operator_user_id": op["id"],
        "autonomous_initial_timeline_id": timeline_ids[0],
        "autonomous_revised_timeline_id": (
            timeline_ids[1] if len(timeline_ids) == 2 else None),
        "current_timeline_id": human_timeline["id"],
        "status": "active",
        "timing_state": "running",
        "server_measured_seconds": 0,
        "client_reported_seconds": None,
        "idle_gap_cap_seconds": HUMAN_EDIT_IDLE_GAP_CAP_SECONDS,
        "human_correction_seconds": 0,
    })
    _, timing = _record_timing_event(session, "start", op["id"])
    session = _service_patch("human_edit_sessions", f"id=eq.{session['id']}", {
        "timing_state": timing["timing_state"],
        "last_activity_at": timing["last_activity_at"],
    })[0]
    return {"session": session, "human_timeline": human_timeline,
            "comparison_mode": ("three_way" if len(timeline_ids) == 2
                                else "initial_vs_human"),
            "timing": {"authoritative_source": "server_timestamps",
                       "server_measured_seconds": 0,
                       "client_reported_seconds": None,
                       "idle_gap_cap_seconds": HUMAN_EDIT_IDLE_GAP_CAP_SECONDS}}


class HumanCeilingTimingBody(BaseModel):
    session_id: str
    client_reported_seconds: float | None = Field(default=None, ge=0, le=24 * 60 * 60)


def _active_human_session(project_id: str, session_id: str) -> dict:
    sessions = supa.db_select("human_edit_sessions", f"id=eq.{session_id}")
    if not sessions or sessions[0]["project_id"] != project_id:
        raise HTTPException(404, "human edit session not found in this project")
    if sessions[0]["status"] != "active":
        raise HTTPException(409, f"human edit session is {sessions[0]['status']}")
    return sessions[0]


@app.post("/projects/{project_id}/human-ceiling/pause")
def op_pause_human_ceiling(project_id: str, body: HumanCeilingTimingBody,
                           authorization: str = Header(default="")):
    from .human_ceiling import HumanCeilingError
    op = _require_operator(authorization)
    _get_project(project_id)
    session = _active_human_session(project_id, body.session_id)
    try:
        current = _timing_snapshot(session)
    except HumanCeilingError as e:
        raise HTTPException(409, f"inconsistent session timing: {e}")
    if current["timing_state"] != "running":
        raise HTTPException(409, "only a running session can be paused")
    _audit(op, "pause_human_ceiling", project_id, {"session_id": session["id"]})
    event, timing = _record_timing_event(
        session, "pause", op["id"], body.client_reported_seconds)
    _service_patch("human_edit_sessions", f"id=eq.{session['id']}", {
        "paused_at": event["occurred_at"],
    })
    return {"session_id": session["id"], "status": "active",
            "timing_state": "paused", "authoritative_source": "server_timestamps",
            "server_measured_seconds": timing["server_measured_seconds"],
            "client_reported_seconds": body.client_reported_seconds,
            "idle_gap_cap_seconds": session["idle_gap_cap_seconds"]}


@app.post("/projects/{project_id}/human-ceiling/resume")
def op_resume_human_ceiling(project_id: str, body: HumanCeilingTimingBody,
                            authorization: str = Header(default="")):
    from .human_ceiling import HumanCeilingError
    op = _require_operator(authorization)
    _get_project(project_id)
    session = _active_human_session(project_id, body.session_id)
    try:
        current = _timing_snapshot(session)
    except HumanCeilingError as e:
        raise HTTPException(409, f"inconsistent session timing: {e}")
    if current["timing_state"] != "paused":
        raise HTTPException(409, "only a paused session can be resumed")
    _audit(op, "resume_human_ceiling", project_id, {"session_id": session["id"]})
    event, timing = _record_timing_event(
        session, "resume", op["id"], body.client_reported_seconds)
    _service_patch("human_edit_sessions", f"id=eq.{session['id']}", {
        "resumed_at": event["occurred_at"], "paused_at": None,
    })
    return {"session_id": session["id"], "status": "active",
            "timing_state": "running", "authoritative_source": "server_timestamps",
            "server_measured_seconds": timing["server_measured_seconds"],
            "client_reported_seconds": body.client_reported_seconds,
            "idle_gap_cap_seconds": session["idle_gap_cap_seconds"]}


class HumanCeilingApproveBody(BaseModel):
    session_id: str
    client_reported_seconds: float | None = Field(default=None, ge=0, le=24 * 60 * 60)
    # Deprecated request alias retained as a diagnostic hint only.
    total_human_seconds: float | None = Field(default=None, ge=0, le=24 * 60 * 60)


@app.post("/projects/{project_id}/human-ceiling/approve")
def op_approve_human_ceiling(project_id: str, body: HumanCeilingApproveBody,
                             authorization: str = Header(default="")):
    """Approve and freeze the separate human timeline lineage."""
    from collections import Counter
    from .human_ceiling import (HUMAN_APPROVED, HUMAN_DRAFT,
                                HumanCeilingError)
    op = _require_operator(authorization)
    _get_project(project_id)
    session = _active_human_session(project_id, body.session_id)
    timelines = supa.db_select("timelines", f"id=eq.{session['current_timeline_id']}")
    if not timelines or timelines[0].get("lineage") != HUMAN_DRAFT:
        raise HTTPException(409, "session has no mutable human draft to approve")

    corrections = supa.db_select(
        "user_corrections", f"human_edit_session_id=eq.{session['id']}")
    client_hint = (body.client_reported_seconds
                   if body.client_reported_seconds is not None
                   else body.total_human_seconds)
    try:
        current = _timing_snapshot(session)
        if current["timing_state"] not in ("running", "paused"):
            raise HumanCeilingError("approval requires a running or paused session")
        preview = _timing_snapshot(session, {
            "id": "approval-preview", "event_type": "approve", "occurred_at": _now(),
        })
    except HumanCeilingError as e:
        raise HTTPException(409, f"inconsistent session timing: {e}")
    if corrections and preview["server_measured_seconds"] <= 0:
        raise HTTPException(422, "operations exist but server-measured correction time is zero")
    _audit(op, "approve_human_ceiling", project_id, {
        "session_id": session["id"],
        "timeline_id": session["current_timeline_id"],
        "client_reported_seconds": client_hint,
    })
    approval_event, timing = _record_timing_event(
        session, "approve", op["id"], client_hint)
    approved = _service_patch("timelines", f"id=eq.{session['current_timeline_id']}", {
        "lineage": HUMAN_APPROVED,
        "is_immutable": True,
        "approved_by": op["id"],
        "approved_at": approval_event["occurred_at"],
    })[0]
    _service_patch("human_edit_sessions", f"id=eq.{session['id']}", {
        "status": "approved",
        "timing_state": "closed",
        "approved_timeline_id": approved["id"],
        "server_measured_seconds": timing["server_measured_seconds"],
        "client_reported_seconds": client_hint,
        "human_correction_seconds": timing["server_measured_seconds"],
        "approved_at": approval_event["occurred_at"],
    })

    counts = Counter(c.get("correction_type") for c in corrections)
    evaluations = supa.db_select(
        "draft_evaluations", f"project_id=eq.{project_id}&order=created_at.desc&limit=1")
    if evaluations:
        _service_patch("draft_evaluations", f"id=eq.{evaluations[0]['id']}", {
            "clips_manually_replaced": counts["replacement"],
            "clips_manually_trimmed": counts["trim"],
            "clips_manually_reordered": counts["reorder"],
            "audio_changes": counts["audio"],
            "title_changes": counts["title"],
            "captions_manually_changed": counts["caption"],
            "human_correction_minutes": round(timing["server_measured_seconds"] / 60, 3),
        })
    return {"session_id": session["id"], "approved_timeline_id": approved["id"],
            "lineage": approved["lineage"],
            "timing": {"authoritative_source": "server_timestamps",
                       "server_measured_seconds": timing["server_measured_seconds"],
                       "client_reported_seconds": client_hint,
                       "idle_gap_cap_seconds": session["idle_gap_cap_seconds"]},
            "correction_counts": dict(counts)}


class HumanCeilingAbandonBody(BaseModel):
    session_id: str
    reason: str = Field(min_length=3, max_length=2000)
    client_reported_seconds: float | None = Field(default=None, ge=0, le=24 * 60 * 60)


@app.post("/projects/{project_id}/human-ceiling/abandon")
def op_abandon_human_ceiling(project_id: str, body: HumanCeilingAbandonBody,
                             authorization: str = Header(default="")):
    from .human_ceiling import HumanCeilingError, HUMAN_DRAFT
    op = _require_operator(authorization)
    _get_project(project_id)
    session = _active_human_session(project_id, body.session_id)
    timelines = supa.db_select("timelines", f"id=eq.{session['current_timeline_id']}")
    if not timelines or timelines[0].get("lineage") != HUMAN_DRAFT:
        raise HTTPException(409, "session has no human draft to preserve")
    try:
        current = _timing_snapshot(session)
        if current["timing_state"] not in ("running", "paused"):
            raise HumanCeilingError("abandon requires a running or paused session")
    except HumanCeilingError as e:
        raise HTTPException(409, f"inconsistent session timing: {e}")
    _audit(op, "abandon_human_ceiling", project_id, {
        "session_id": session["id"], "timeline_id": session["current_timeline_id"],
        "reason": body.reason,
    })
    event, timing = _record_timing_event(
        session, "abandon", op["id"], body.client_reported_seconds,
        details={"reason": body.reason})
    # Freeze the draft as non-approved evidence; never mutate either baseline.
    _service_patch("timelines", f"id=eq.{session['current_timeline_id']}", {
        "is_immutable": True,
    })
    _service_patch("human_edit_sessions", f"id=eq.{session['id']}", {
        "status": "abandoned", "timing_state": "closed",
        "abandoned_at": event["occurred_at"], "abandoned_by": op["id"],
        "abandonment_reason": body.reason,
        "abandoned_timeline_id": session["current_timeline_id"],
        "server_measured_seconds": timing["server_measured_seconds"],
        "client_reported_seconds": body.client_reported_seconds,
        "human_correction_seconds": timing["server_measured_seconds"],
    })
    return {"session_id": session["id"], "status": "abandoned",
            "abandoned_timeline_id": session["current_timeline_id"],
            "human_timeline_lineage": HUMAN_DRAFT,
            "human_timeline_immutable": True,
            "reason": body.reason,
            "timing": {"authoritative_source": "server_timestamps",
                       "server_measured_seconds": timing["server_measured_seconds"],
                       "client_reported_seconds": body.client_reported_seconds,
                       "idle_gap_cap_seconds": session["idle_gap_cap_seconds"]}}


class TimelineScorecardBody(BaseModel):
    session_id: str
    timeline_id: str
    scores: dict = Field(default_factory=dict)
    overall_rating: int = Field(ge=1, le=10)
    publishable: bool | None = None
    evaluator_role: str = "operator"
    notes: str = Field(default="", max_length=10000)


@app.post("/projects/{project_id}/human-ceiling/scorecard")
def op_human_ceiling_scorecard(project_id: str, body: TimelineScorecardBody,
                               authorization: str = Header(default="")):
    """Record a version-specific scorecard for the three-way comparison."""
    from .human_ceiling import HumanCeilingError, validate_scores
    op = _require_operator(authorization)
    project = _get_project(project_id)
    sessions = supa.db_select("human_edit_sessions", f"id=eq.{body.session_id}")
    if not sessions or sessions[0]["project_id"] != project_id:
        raise HTTPException(404, "human edit session not found in this project")
    session = sessions[0]
    if session.get("status") != "approved":
        raise HTTPException(409, "scorecards require an approved human edit session")
    allowed_ids = {session.get("autonomous_initial_timeline_id"),
                   session.get("autonomous_revised_timeline_id"),
                   session.get("approved_timeline_id")}
    allowed_ids.discard(None)
    if body.timeline_id not in allowed_ids:
        raise HTTPException(422, "scorecard timeline is not a comparison version")
    if body.evaluator_role not in {"operator", "founder", "customer", "system"}:
        raise HTTPException(422, "invalid evaluator_role")
    try:
        scores = validate_scores(body.scores)
    except HumanCeilingError as e:
        raise HTTPException(422, str(e))
    _audit(op, "record_human_ceiling_scorecard", project_id, {
        "session_id": session["id"], "timeline_id": body.timeline_id,
        "overall_rating": body.overall_rating,
        "evaluator_role": body.evaluator_role,
    })
    existing = supa.db_select(
        "timeline_scorecards",
        f"timeline_id=eq.{body.timeline_id}&evaluator_user_id=eq.{op['id']}"
        f"&evaluator_role=eq.{body.evaluator_role}&limit=1")
    payload = {
        "scores": scores, "overall_rating": body.overall_rating,
        "publishable": body.publishable, "notes": body.notes,
        "server_measured_seconds": float(session.get("server_measured_seconds") or 0),
        "client_reported_seconds": session.get("client_reported_seconds"),
    }
    if existing:
        row = _service_patch("timeline_scorecards", f"id=eq.{existing[0]['id']}", payload)[0]
    else:
        row = _service_insert("timeline_scorecards", {
            "project_id": project_id, "user_id": project["user_id"],
            "timeline_id": body.timeline_id,
            "human_edit_session_id": session["id"],
            "evaluator_user_id": op["id"],
            "evaluator_role": body.evaluator_role,
            **payload,
        })
    return {**row, "timing": {
        "authoritative_source": "server_timestamps",
        "server_measured_seconds": float(session.get("server_measured_seconds") or 0),
        "client_reported_seconds": session.get("client_reported_seconds")}}


@app.get("/projects/{project_id}/human-ceiling/report")
def op_human_ceiling_report(project_id: str, session_id: str | None = None,
                            authorization: str = Header(default="")):
    """Generate the side-by-side initial/revised/human-approved report."""
    from .human_ceiling import HumanCeilingError, build_comparison_report
    _require_operator(authorization)
    _get_project(project_id)
    filters = f"project_id=eq.{project_id}&status=eq.approved&order=created_at.desc&limit=1"
    if session_id:
        filters = f"id=eq.{session_id}"
    sessions = supa.db_select("human_edit_sessions", filters)
    if not sessions or sessions[0]["project_id"] != project_id:
        raise HTTPException(404, "approved human edit session not found")
    session = sessions[0]
    timelines = supa.db_select("timelines", f"project_id=eq.{project_id}")
    scorecards = supa.db_select(
        "timeline_scorecards", f"human_edit_session_id=eq.{session['id']}")
    corrections = supa.db_select(
        "user_corrections", f"human_edit_session_id=eq.{session['id']}")
    try:
        return build_comparison_report(session, timelines, scorecards, corrections)
    except HumanCeilingError as e:
        raise HTTPException(409, str(e))


class SegmentFlagBody(BaseModel):
    unusable: bool = True
    reason: str = ""


@app.post("/segments/{segment_id}/flag")
def op_flag_segment(segment_id: str, body: SegmentFlagBody,
                    authorization: str = Header(default="")):
    import httpx as _hx
    op = _require_operator(authorization)
    rows = supa.db_select("segments", f"id=eq.{segment_id}")
    if not rows:
        raise HTTPException(404, "segment not found")
    seg = rows[0]
    data = seg["data"]
    problems = set(data.get("problems", []))
    if body.unusable:
        problems.add("operator_unusable")
    else:
        problems.discard("operator_unusable")
    data["problems"] = sorted(problems)
    _audit(op, "flag_segment", seg["project_id"],
           {"segment": segment_id, "unusable": body.unusable,
            "reason": body.reason})
    _hx.patch(f"{supa.SUPABASE_URL}/rest/v1/segments?id=eq.{segment_id}",
              headers={"apikey": supa.SERVICE_KEY,
                       "Authorization": f"Bearer {supa.SERVICE_KEY}",
                       "Content-Type": "application/json",
                       "Prefer": "return=minimal"},
              json={"data": data}, timeout=30).raise_for_status()
    return {"segment_id": segment_id, "problems": data["problems"]}


@app.get("/projects/{project_id}/coverage")
def op_coverage(project_id: str, authorization: str = Header(default="")):
    from .pipeline.coverage import validate_coverage
    from .pipeline.schemas import Segment as Seg
    _require_operator(authorization)
    _get_project(project_id)
    rows = supa.db_select("segments", f"project_id=eq.{project_id}")
    segs = [Seg(**r["data"]) for r in rows]
    return validate_coverage(segs).model_dump()


class PreproductionBody(BaseModel):
    purpose: str = "cinematic fitness recap"
    audience: str = "social fitness audience"
    targetDurationSeconds: float | None = None
    targetPlatform: Literal["vertical"] = "vertical"
    referenceStyle: str | None = None
    tone: list[str] = Field(default_factory=lambda: [
        "intense", "authentic", "motivational",
    ])
    preferredVariant: str | None = None
    graphicsPreference: Literal["none", "low", "medium"] = "low"
    colorPreference: str = "high contrast natural warmth"


@app.post("/projects/{project_id}/preproduction")
def op_preproduction(project_id: str, body: PreproductionBody,
                     authorization: str = Header(default="")):
    """Create the Milestone 1 planning contract without selecting or rendering."""
    import httpx as _hx

    from .pipeline.creative_director import CreativeBrief
    from .pipeline.preproduction import build_preproduction_package
    from .pipeline.schemas import Segment as Seg

    op = _require_operator(authorization)
    _rate_check(op["id"], "preproduction")
    project = _get_project(project_id)
    rows = supa.db_select("segments", f"project_id=eq.{project_id}")
    if not rows:
        raise HTTPException(409, "segment catalog required - run analysis first")
    try:
        brief = CreativeBrief(
            purpose=body.purpose,
            audience=body.audience,
            targetDurationSeconds=body.targetDurationSeconds,
            referenceStyle=body.referenceStyle,
            tone=body.tone,
            preferredVariant=body.preferredVariant,
            graphicsPreference=body.graphicsPreference,
            colorPreference=body.colorPreference,
        )
        package = build_preproduction_package(
            brief,
            [Seg(**row["data"]) for row in rows],
        )
    except ValidationError as exc:
        raise HTTPException(422, exc.errors(include_url=False))

    existing = supa.db_select(
        "preproduction_runs",
        f"project_id=eq.{project_id}&order=version.desc&limit=1",
    )
    version = (existing[0]["version"] + 1) if existing else 1
    request = body.model_dump()
    _audit(op, "create_preproduction", project_id, {
        "version": version,
        "target_duration": package.creativeTreatment.targetDurationSeconds,
        "status": package.status,
    })
    payload = {
        "project_id": project_id,
        "user_id": project["user_id"],
        "version": version,
        "status": package.status,
        "request": request,
        "creative_treatment": package.creativeTreatment.model_dump(),
        "capture_quality_report": package.captureQualityReport.model_dump(),
        "composition_by_segment": {
            key: value.model_dump() for key, value in package.compositionBySegment.items()
        },
        "story_variants": package.storyVariants.model_dump(),
        "warnings": package.warnings,
    }
    response = _hx.post(
        f"{supa.SUPABASE_URL}/rest/v1/preproduction_runs",
        headers={
            "apikey": supa.SERVICE_KEY,
            "Authorization": f"Bearer {supa.SERVICE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        },
        json=payload,
        timeout=30,
    )
    if response.status_code == 409:
        raise HTTPException(409, "preproduction version conflict; retry")
    response.raise_for_status()
    saved = response.json()[0]
    return {"id": saved["id"], "version": version, **package.model_dump()}


class PictureEditBody(BaseModel):
    preproductionRunId: UUID | None = None


@app.post("/projects/{project_id}/picture-edit")
def op_picture_edit(project_id: str, body: PictureEditBody,
                    authorization: str = Header(default="")):
    """Build three Milestone 2 picture candidates without rendering or finishing."""
    import httpx as _hx

    from .pipeline.capture_quality import CaptureQualityReport
    from .pipeline.composition import CompositionMetrics
    from .pipeline.creative_director import CreativeTreatment
    from .pipeline.picture_editor import (
        PictureEditorError,
        build_picture_edit_package,
    )
    from .pipeline.schemas import Segment as Seg
    from .pipeline.story_editor import StoryVariantSet

    op = _require_operator(authorization)
    _rate_check(op["id"], "picture_edit")
    project = _get_project(project_id)
    segment_rows = supa.db_select("segments", f"project_id=eq.{project_id}")
    if not segment_rows:
        raise HTTPException(409, "segment catalog required - run analysis first")

    if body.preproductionRunId:
        preproduction_rows = supa.db_select(
            "preproduction_runs", f"id=eq.{body.preproductionRunId}&limit=1",
        )
    else:
        preproduction_rows = supa.db_select(
            "preproduction_runs",
            f"project_id=eq.{project_id}&order=version.desc&limit=1",
        )
    if not preproduction_rows or preproduction_rows[0]["project_id"] != project_id:
        raise HTTPException(409, "Milestone 1 preproduction run required")
    preproduction = preproduction_rows[0]

    def _json_value(key: str):
        value = preproduction[key]
        return json.loads(value) if isinstance(value, str) else value

    try:
        treatment = CreativeTreatment(**_json_value("creative_treatment"))
        capture = CaptureQualityReport(**_json_value("capture_quality_report"))
        composition = {
            key: CompositionMetrics(**value)
            for key, value in _json_value("composition_by_segment").items()
        }
        variants = StoryVariantSet(**_json_value("story_variants"))
        package = build_picture_edit_package(
            preproduction["id"], treatment, capture, composition, variants,
            [Seg(**row["data"]) for row in segment_rows],
        )
    except (ValidationError, PictureEditorError, ValueError) as exc:
        detail = exc.errors(include_url=False) if isinstance(exc, ValidationError) else str(exc)
        raise HTTPException(422, detail)

    existing = supa.db_select(
        "picture_edit_runs",
        f"project_id=eq.{project_id}&order=version.desc&limit=1",
    )
    version = (existing[0]["version"] + 1) if existing else 1
    _audit(op, "create_picture_edit", project_id, {
        "version": version,
        "preproduction_run_id": preproduction["id"],
        "status": package.status,
        "selected_candidate_id": package.selectedCandidateId,
    })
    payload = {
        "project_id": project_id,
        "user_id": project["user_id"],
        "preproduction_run_id": preproduction["id"],
        "version": version,
        "status": package.status,
        "request": body.model_dump(mode="json"),
        "visual_rhythm_plans": {
            key: value.model_dump() for key, value in package.visualRhythmPlans.items()
        },
        "candidates": [candidate.model_dump() for candidate in package.candidates],
        "selected_candidate_id": package.selectedCandidateId,
        "warnings": package.warnings,
    }
    response = _hx.post(
        f"{supa.SUPABASE_URL}/rest/v1/picture_edit_runs",
        headers={
            "apikey": supa.SERVICE_KEY,
            "Authorization": f"Bearer {supa.SERVICE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        },
        json=payload,
        timeout=30,
    )
    if response.status_code == 409:
        raise HTTPException(409, "picture-edit version conflict; retry")
    response.raise_for_status()
    saved = response.json()[0]
    return {"id": saved["id"], "version": version, **package.model_dump()}


class MusicSoundBody(BaseModel):
    pictureEditRunId: UUID | None = None


@app.post("/projects/{project_id}/music-sound")
def op_music_sound(project_id: str, body: MusicSoundBody,
                   authorization: str = Header(default="")):
    """Create the immutable Milestone 3 Music Plan for selected picture."""
    import httpx as _hx

    from .pipeline.creative_director import CreativeTreatment
    from .pipeline.music_supervisor import MusicSupervisorError, build_music_plan
    from .pipeline.picture_editor import PictureCandidateSummary
    from .pipeline.schemas import Segment as Seg

    op = _require_operator(authorization)
    _rate_check(op["id"], "music_sound")
    project = _get_project(project_id)
    if body.pictureEditRunId:
        picture_rows = supa.db_select(
            "picture_edit_runs", f"id=eq.{body.pictureEditRunId}&limit=1",
        )
    else:
        picture_rows = supa.db_select(
            "picture_edit_runs", f"project_id=eq.{project_id}&order=version.desc&limit=1",
        )
    if not picture_rows or picture_rows[0]["project_id"] != project_id:
        raise HTTPException(409, "Milestone 2 picture-edit run required")
    picture_run = picture_rows[0]
    selected_id = picture_run.get("selected_candidate_id")
    if not selected_id:
        raise HTTPException(409, "Milestone 2 has no supported selected picture candidate")

    preproduction_rows = supa.db_select(
        "preproduction_runs", f"id=eq.{picture_run['preproduction_run_id']}&limit=1",
    )
    if not preproduction_rows or preproduction_rows[0]["project_id"] != project_id:
        raise HTTPException(409, "Milestone 1 treatment ancestry is invalid")
    preproduction = preproduction_rows[0]
    segment_rows = supa.db_select("segments", f"project_id=eq.{project_id}")
    if not segment_rows:
        raise HTTPException(409, "segment catalog required - run analysis first")

    def _json_value(row: dict, key: str):
        value = row[key]
        return json.loads(value) if isinstance(value, str) else value

    try:
        treatment = CreativeTreatment(**_json_value(preproduction, "creative_treatment"))
        candidate_data = next(
            candidate for candidate in _json_value(picture_run, "candidates")
            if candidate.get("candidateId") == selected_id
        )
        candidate = PictureCandidateSummary(**candidate_data)
        plan = build_music_plan(
            preproduction["id"], picture_run["id"], treatment, candidate,
            [Seg(**row["data"]) for row in segment_rows],
        )
    except StopIteration:
        raise HTTPException(409, "selected picture candidate is missing")
    except (ValidationError, MusicSupervisorError, ValueError) as exc:
        detail = exc.errors(include_url=False) if isinstance(exc, ValidationError) else str(exc)
        raise HTTPException(422, detail)

    existing = supa.db_select(
        "music_sound_runs", f"project_id=eq.{project_id}&order=version.desc&limit=1",
    )
    version = (existing[0]["version"] + 1) if existing else 1
    _audit(op, "create_music_sound_plan", project_id, {
        "version": version,
        "preproduction_run_id": preproduction["id"],
        "picture_edit_run_id": picture_run["id"],
        "selected_candidate_id": selected_id,
    })
    payload = {
        "project_id": project_id,
        "user_id": project["user_id"],
        "preproduction_run_id": preproduction["id"],
        "picture_edit_run_id": picture_run["id"],
        "selected_candidate_id": selected_id,
        "version": version,
        "status": "ready",
        "request": body.model_dump(mode="json"),
        "music_plan": plan.model_dump(),
    }
    response = _hx.post(
        f"{supa.SUPABASE_URL}/rest/v1/music_sound_runs",
        headers={
            "apikey": supa.SERVICE_KEY,
            "Authorization": f"Bearer {supa.SERVICE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        },
        json=payload,
        timeout=30,
    )
    if response.status_code == 409:
        raise HTTPException(409, "music-sound version conflict; retry")
    response.raise_for_status()
    saved = response.json()[0]
    return {"id": saved["id"], "version": version, "status": "ready",
            "musicPlan": plan.model_dump()}


class LicensedMusicMetadataBody(BaseModel):
    provider: str = Field(min_length=2, max_length=120)
    licenseType: str = Field(min_length=2, max_length=120)
    licenseReference: str = Field(min_length=3, max_length=500)
    confirmedByOperator: Literal[True]


class AudioRenderBody(BaseModel):
    musicSoundRunId: UUID | None = None
    storagePath: str = Field(min_length=10, max_length=1000)
    filename: str = Field(min_length=1, max_length=255)
    contentType: str = Field(min_length=3, max_length=100)
    sizeBytes: int = Field(gt=0, le=50 * 1024 * 1024)
    licenseMetadata: LicensedMusicMetadataBody


def _licensed_music_path(project: dict, project_id: str, path: str) -> bool:
    parts = PurePosixPath(path).parts
    return (
        len(parts) >= 7
        and parts[0] == "users"
        and parts[1] == project["user_id"]
        and parts[2] == "projects"
        and parts[3] == project_id
        and parts[4] == "licensed-music"
        and ".." not in parts
    )


@app.post("/projects/{project_id}/licensed-music/upload")
async def op_upload_licensed_music(
    project_id: str, request: Request, filename: str, content_type: str,
    authorization: str = Header(default=""),
):
    """Receive one bounded operator upload into the project owner's private path."""
    from .pipeline.audio_rendering import ALLOWED_CONTENT_TYPES, ALLOWED_EXTENSIONS

    op = _require_operator(authorization)
    _rate_check(op["id"], "licensed_music_upload")
    project = _get_project(project_id)
    safe_name = PurePosixPath(filename.replace("\\", "/")).name.replace("\x00", "")
    if not safe_name or os.path.splitext(safe_name.lower())[1] not in ALLOWED_EXTENSIONS:
        raise HTTPException(422, "licensed music filename must be WAV, MP3, M4A, AAC, or FLAC")
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(422, "unsupported licensed music content type")
    content = await request.body()
    if not content or len(content) > 50 * 1024 * 1024:
        raise HTTPException(413, "licensed music must be between 1 byte and 50 MB")
    path = (f"users/{project['user_id']}/projects/{project_id}/licensed-music/"
            f"{uuid4()}/{safe_name}")
    _audit(op, "upload_licensed_music", project_id, {
        "path": path, "filename": safe_name, "content_type": content_type,
        "size_bytes": len(content),
    })
    with tempfile.TemporaryDirectory(prefix="stromation-music-upload-") as tmp:
        local = os.path.join(tmp, safe_name)
        with open(local, "wb") as handle:
            handle.write(content)
        supa.storage_upload("raw-footage", path, local, content_type=content_type)
    return {"storagePath": path, "filename": safe_name,
            "contentType": content_type, "sizeBytes": len(content)}


@app.post("/projects/{project_id}/audio-render")
def op_audio_render(project_id: str, body: AudioRenderBody,
                    authorization: str = Header(default="")):
    """Analyze an actual licensed waveform and render the completed audio preview."""
    import httpx as _hx

    from .pipeline.audio_rendering import (
        AudioRenderingError,
        CompletedAudioMix,
        LicenseMetadata,
        analyze_actual_waveform,
        analyze_audio_qc,
        match_picture_to_actual_track,
        probe_music_file,
        render_completed_mix,
    )
    from .pipeline.music_supervisor import MusicPlan
    from .pipeline.picture_editor import PictureCandidateSummary

    op = _require_operator(authorization)
    _rate_check(op["id"], "audio_render")
    project = _get_project(project_id)
    if not _licensed_music_path(project, project_id, body.storagePath):
        raise HTTPException(403, "licensed music path does not belong to this project")
    safe_filename = PurePosixPath(body.filename.replace("\\", "/")).name
    if safe_filename != body.filename or not safe_filename:
        raise HTTPException(422, "licensed music filename must not contain a path")
    if body.musicSoundRunId:
        music_rows = supa.db_select(
            "music_sound_runs", f"id=eq.{body.musicSoundRunId}&limit=1",
        )
    else:
        music_rows = supa.db_select(
            "music_sound_runs", f"project_id=eq.{project_id}&order=version.desc&limit=1",
        )
    if not music_rows or music_rows[0]["project_id"] != project_id:
        raise HTTPException(409, "Milestone 3 music/sound run required")
    music_run = music_rows[0]
    picture_rows = supa.db_select(
        "picture_edit_runs", f"id=eq.{music_run['picture_edit_run_id']}&limit=1",
    )
    if not picture_rows or picture_rows[0]["project_id"] != project_id:
        raise HTTPException(409, "Milestone 2 picture ancestry is invalid")
    picture_run = picture_rows[0]

    def _json_value(row: dict, key: str):
        value = row[key]
        return json.loads(value) if isinstance(value, str) else value

    try:
        plan = MusicPlan(**_json_value(music_run, "music_plan"))
        candidate_data = next(
            item for item in _json_value(picture_run, "candidates")
            if item.get("candidateId") == music_run["selected_candidate_id"]
        )
        candidate = PictureCandidateSummary(**candidate_data)
        license_metadata = LicenseMetadata(**body.licenseMetadata.model_dump())
    except StopIteration:
        raise HTTPException(409, "selected picture candidate is missing")
    except ValidationError as exc:
        raise HTTPException(422, exc.errors(include_url=False))

    assets = supa.db_select("media_assets", f"project_id=eq.{project_id}")
    asset_by_id = {row["id"]: row for row in assets
                   if row.get("user_id") == project["user_id"]}
    required_ids = {clip["assetId"] for track in candidate.timeline["tracks"]
                    if track["type"] == "video" for clip in track["clips"]}
    if not required_ids.issubset(asset_by_id):
        raise HTTPException(409, "selected picture references missing or foreign media assets")

    with tempfile.TemporaryDirectory(prefix="stromation-audio-render-") as tmp:
        music_path = os.path.join(tmp, safe_filename)
        supa.storage_download("raw-footage", body.storagePath, music_path)
        actual_size = os.path.getsize(music_path)
        if actual_size != body.sizeBytes or actual_size > 50 * 1024 * 1024:
            raise HTTPException(422, "licensed music size metadata does not match stored object")
        try:
            media_info = probe_music_file(
                music_path, filename=body.filename, content_type=body.contentType,
                picture_duration=candidate.durationSeconds,
            )
            analysis = analyze_actual_waveform(music_path)
            match = match_picture_to_actual_track(
                candidate, plan, analysis, media_info.durationSeconds,
            )
            from . import media_store
            sources = {}
            for asset_id in required_ids:
                row = asset_by_id[asset_id]
                local = os.path.join(tmp, f"source-{asset_id}.mp4")
                media_store.download_media_asset(row, project, local)
                sources[asset_id] = local
            preview = os.path.join(tmp, "completed-audio-preview.mp4")
            measurement, ducking = render_completed_mix(
                candidate, sources, music_path, plan, match, preview, tmp,
            )
            qc = analyze_audio_qc(preview)
        except (AudioRenderingError, RenderError, subprocess.CalledProcessError) as exc:
            raise HTTPException(422, str(exc))

        existing = supa.db_select(
            "audio_mix_runs", f"project_id=eq.{project_id}&order=version.desc&limit=1",
        )
        version = (existing[0]["version"] + 1) if existing else 1
        licensed_existing = supa.db_select(
            "licensed_music_assets",
            f"music_sound_run_id=eq.{music_run['id']}&order=version.desc&limit=1",
        )
        licensed_version = (licensed_existing[0]["version"] + 1) if licensed_existing else 1
        output_path = (f"users/{project['user_id']}/projects/{project_id}/"
                       f"audio-previews/v{version}-{uuid4()}.mp4")
        _audit(op, "create_completed_audio_mix", project_id, {
            "version": version, "music_sound_run_id": music_run["id"],
            "picture_edit_run_id": picture_run["id"], "storage_path": body.storagePath,
            "preview_storage_path": output_path, "qc_passed": qc.passed,
        })
        licensed = _service_insert("licensed_music_assets", {
            "project_id": project_id, "user_id": project["user_id"],
            "music_sound_run_id": music_run["id"],
            "picture_edit_run_id": picture_run["id"],
            "selected_candidate_id": music_run["selected_candidate_id"],
            "version": licensed_version, "storage_bucket": "raw-footage",
            "storage_path": body.storagePath, "filename": body.filename,
            "content_type": body.contentType, "size_bytes": actual_size,
            "license_metadata": license_metadata.model_dump(),
            "media_info": media_info.model_dump(),
            "waveform_analysis": analysis.model_dump(), "attached_by": op["id"],
        })
        supa.storage_upload("exports", output_path, preview, content_type="video/mp4")
        completed = CompletedAudioMix(
            analysis=analysis, targetVsActual=match,
            mergedDuckingEnvelopes=ducking,
            sourceAudioInstructions=[item.model_dump() for item in plan.sourceAudioInstructions],
            loudnessMeasurementPass=measurement, qc=qc,
            previewStoragePath=output_path, pictureTimingChanged=False,
            excludedDepartments=[
                "motion_graphics", "captions", "color_grading",
                "specialized_critics", "tournament_selection",
            ],
        )
        payload = {
            "project_id": project_id, "user_id": project["user_id"],
            "preproduction_run_id": music_run["preproduction_run_id"],
            "picture_edit_run_id": picture_run["id"],
            "music_sound_run_id": music_run["id"],
            "licensed_music_asset_id": licensed["id"],
            "selected_candidate_id": music_run["selected_candidate_id"],
            "version": version, "status": "qc_passed" if qc.passed else "qc_failed",
            "target_vs_actual": match.model_dump(),
            "mix_instructions": completed.model_dump(), "audio_qc": qc.model_dump(),
            "preview_storage_bucket": "exports", "preview_storage_path": output_path,
            "picture_timing_changed": False,
        }
        response = _hx.post(
            f"{supa.SUPABASE_URL}/rest/v1/audio_mix_runs",
            headers={"apikey": supa.SERVICE_KEY,
                     "Authorization": f"Bearer {supa.SERVICE_KEY}",
                     "Content-Type": "application/json",
                     "Prefer": "return=representation"},
            json=payload, timeout=30,
        )
        if response.status_code == 409:
            raise HTTPException(409, "audio-mix version conflict; retry")
        response.raise_for_status()
        saved = response.json()[0]
    return {"id": saved["id"], "version": version, "status": payload["status"],
            "licensedMusicAssetId": licensed["id"],
            "completedAudioMix": completed.model_dump()}


class BrandTemplateBody(BaseModel):
    templateId: str = Field(default="stromation-social-v1", min_length=2, max_length=80)
    name: str = Field(default="Stromation Social", min_length=2, max_length=120)
    fontFamily: Literal["DejaVu Sans", "DejaVu Sans Condensed"] = "DejaVu Sans"
    primary: str = "#FFFFFF"
    secondary: str = "#101820"
    accent: str = "#00E5FF"
    captionStyle: Literal["clean", "boxed", "kinetic"] = "kinetic"
    titleCase: Literal["upper", "sentence"] = "upper"


class VisualFinishingBody(BaseModel):
    audioMixRunId: UUID | None = None
    aspect: Literal["9:16", "1:1", "16:9"] = "9:16"
    lutPreset: Literal["none", "clean_warm", "cool_contrast", "neutral_social"] = "none"
    brandTemplate: BrandTemplateBody = Field(default_factory=BrandTemplateBody)


@app.post("/projects/{project_id}/visual-finishing")
def op_visual_finishing(project_id: str, body: VisualFinishingBody,
                         authorization: str = Header(default="")):
    """Build and render immutable Milestone 5 graphics/caption/color evidence."""
    import httpx as _hx

    from .pipeline.audio_rendering import CompletedAudioMix
    from .pipeline.composition import CompositionMetrics
    from .pipeline.creative_director import CreativeTreatment
    from .pipeline.music_supervisor import MusicPlan
    from .pipeline.picture_editor import PictureCandidateSummary
    from .pipeline.schemas import Segment as Seg, TranscriptArtifact
    from .pipeline.visual_finishing import (
        BrandTemplate,
        VisualFinishingError,
        build_caption_package,
        build_color_package,
        build_graphics_package,
        render_finishing_preview,
    )

    op = _require_operator(authorization)
    _rate_check(op["id"], "visual_finishing")
    project = _get_project(project_id)
    if body.audioMixRunId:
        audio_rows = supa.db_select("audio_mix_runs", f"id=eq.{body.audioMixRunId}&limit=1")
    else:
        audio_rows = supa.db_select(
            "audio_mix_runs", f"project_id=eq.{project_id}&order=version.desc&limit=1",
        )
    if not audio_rows or audio_rows[0]["project_id"] != project_id:
        raise HTTPException(409, "Milestone 4 completed audio mix required")
    audio_run = audio_rows[0]
    if audio_run.get("status") != "qc_passed":
        raise HTTPException(409, "Milestone 4 audio mix must pass QC before visual finishing")

    picture_rows = supa.db_select(
        "picture_edit_runs", f"id=eq.{audio_run['picture_edit_run_id']}&limit=1",
    )
    music_rows = supa.db_select(
        "music_sound_runs", f"id=eq.{audio_run['music_sound_run_id']}&limit=1",
    )
    preproduction_rows = supa.db_select(
        "preproduction_runs", f"id=eq.{audio_run['preproduction_run_id']}&limit=1",
    )
    lineage = [picture_rows, music_rows, preproduction_rows]
    if any(not rows or rows[0]["project_id"] != project_id for rows in lineage):
        raise HTTPException(409, "Milestone 1-4 ancestry is invalid")
    picture_run, music_run, preproduction = (
        picture_rows[0], music_rows[0], preproduction_rows[0],
    )
    if (music_run.get("picture_edit_run_id") != picture_run["id"]
            or music_run.get("preproduction_run_id") != preproduction["id"]
            or audio_run.get("selected_candidate_id") != picture_run.get("selected_candidate_id")):
        raise HTTPException(409, "Milestone 1-4 selected-picture ancestry is inconsistent")

    def _json_value(row: dict, key: str):
        value = row[key]
        return json.loads(value) if isinstance(value, str) else value

    segment_rows = supa.db_select("segments", f"project_id=eq.{project_id}")
    transcript_rows = supa.db_select(
        "asset_analysis", f"project_id=eq.{project_id}&kind=eq.transcript&status=eq.completed",
    )
    try:
        candidate_data = next(
            item for item in _json_value(picture_run, "candidates")
            if item.get("candidateId") == audio_run["selected_candidate_id"]
        )
        candidate = PictureCandidateSummary(**candidate_data)
        treatment = CreativeTreatment(**_json_value(preproduction, "creative_treatment"))
        completed = CompletedAudioMix(**_json_value(audio_run, "mix_instructions"))
        music_plan = MusicPlan(**_json_value(music_run, "music_plan"))
        composition = {
            key: CompositionMetrics(**value)
            for key, value in _json_value(preproduction, "composition_by_segment").items()
        }
        segments = [Seg(**row["data"]) for row in segment_rows]
        transcripts = {}
        for row in transcript_rows:
            data = row.get("data")
            if isinstance(data, str):
                data = json.loads(data)
            if data:
                transcripts[row["asset_id"]] = TranscriptArtifact(**data)
        template = BrandTemplate(**body.brandTemplate.model_dump())
        graphics = build_graphics_package(
            treatment, candidate, completed, composition,
            segments,
            aspect=body.aspect, template=template,
        )
        captions = build_caption_package(
            candidate, segments, graphics, transcripts, music_plan.naturalAudioEvents,
        )
        color = build_color_package(candidate, segments, lut=body.lutPreset)
    except StopIteration:
        raise HTTPException(409, "selected picture candidate is missing")
    except (ValidationError, VisualFinishingError, ValueError) as exc:
        detail = exc.errors(include_url=False) if isinstance(exc, ValidationError) else str(exc)
        raise HTTPException(422, detail)

    latest_versions = []
    for table in ("graphics_runs", "caption_runs", "color_runs"):
        rows = supa.db_select(
            table, f"project_id=eq.{project_id}&order=version.desc&limit=1",
        )
        if rows:
            latest_versions.append(rows[0]["version"])
    # One shared lineage version plus DB uniqueness means concurrent requests
    # collide on graphics before either can create dependent caption/color rows.
    version = max(latest_versions, default=0) + 1
    output_path = (f"users/{project['user_id']}/projects/{project_id}/"
                   f"visual-finishing/v{version}-{uuid4()}.mp4")
    with tempfile.TemporaryDirectory(prefix="stromation-visual-finishing-") as tmp:
        source = os.path.join(tmp, "completed-audio-preview.mp4")
        output = os.path.join(tmp, "visual-finishing-preview.mp4")
        supa.storage_download("exports", audio_run["preview_storage_path"], source)
        try:
            render_qc = render_finishing_preview(source, output, graphics, captions, color)
        except VisualFinishingError as exc:
            raise HTTPException(422, str(exc))
        if not render_qc["videoStreamPresent"] or not render_qc["audioStreamPresent"]:
            raise HTTPException(422, "visual-finishing preview is missing a required stream")
        _audit(op, "create_visual_finishing", project_id, {
            "version": version, "audio_mix_run_id": audio_run["id"],
            "picture_edit_run_id": picture_run["id"], "aspect": body.aspect,
            "graphics_events": len(graphics.events), "caption_groups": len(captions.groups),
            "preview_storage_path": output_path,
        })
        supa.storage_upload("exports", output_path, output, content_type="video/mp4")

    headers = {"apikey": supa.SERVICE_KEY,
               "Authorization": f"Bearer {supa.SERVICE_KEY}",
               "Content-Type": "application/json", "Prefer": "return=representation"}

    def _insert(table: str, payload: dict):
        response = _hx.post(f"{supa.SUPABASE_URL}/rest/v1/{table}", headers=headers,
                            json=payload, timeout=30)
        if response.status_code == 409:
            raise HTTPException(409, f"{table} version conflict; retry")
        response.raise_for_status()
        return response.json()[0]

    ancestry = {
        "project_id": project_id, "user_id": project["user_id"],
        "preproduction_run_id": preproduction["id"],
        "picture_edit_run_id": picture_run["id"],
        "music_sound_run_id": music_run["id"], "audio_mix_run_id": audio_run["id"],
        "selected_candidate_id": audio_run["selected_candidate_id"],
        "version": version, "created_by": op["id"],
    }
    graphics_row = _insert("graphics_runs", {
        **ancestry, "status": "ready", "request": body.model_dump(mode="json"),
        "platform_preset": graphics.platform.model_dump(),
        "brand_template": graphics.brandTemplate.model_dump(),
        "graphics_timeline": graphics.model_dump(),
        "picture_timing_changed": False, "audio_changed": False,
    })
    caption_row = _insert("caption_runs", {
        **ancestry, "graphics_run_id": graphics_row["id"],
        "status": "ready" if captions.groups else "no_speech",
        "caption_timeline": captions.model_dump(),
        "timing_provenance": captions.timingProvenance,
        "overlaps_detected": captions.overlapsDetected, "picture_timing_changed": False,
    })
    color_row = _insert("color_runs", {
        **ancestry, "graphics_run_id": graphics_row["id"],
        "caption_run_id": caption_row["id"], "status": "qc_passed",
        "color_instructions": color.model_dump(), "render_qc": render_qc,
        "preview_storage_bucket": "exports", "preview_storage_path": output_path,
        "non_destructive": True, "picture_timing_changed": False, "audio_changed": False,
    })
    return {
        "version": version, "status": "qc_passed",
        "graphicsRunId": graphics_row["id"], "captionRunId": caption_row["id"],
        "colorRunId": color_row["id"], "previewStoragePath": output_path,
        "graphics": graphics.model_dump(), "captions": captions.model_dump(),
        "color": color.model_dump(), "renderQc": render_qc,
    }


class EditorialIntelligenceBody(BaseModel):
    colorRunId: UUID | None = None
    includeBoundedRevision: bool = True


@app.post("/projects/{project_id}/editorial-intelligence")
def op_editorial_intelligence(
    project_id: str, body: EditorialIntelligenceBody,
    authorization: str = Header(default=""),
):
    """Render, critique, compare, and persist one immutable Milestone 6 batch."""
    from .human_ceiling import HumanCeilingError, build_comparison_report
    from .pipeline.audio_rendering import CompletedAudioMix
    from .pipeline.composition import CompositionMetrics
    from .pipeline.creative_director import CreativeTreatment
    from .pipeline.editorial_intelligence import (
        EditorialIntelligenceError,
        apply_bounded_revision,
        build_four_way_comparison,
        build_publishability_report,
        generate_initial_candidates,
        render_complete_candidate,
        run_specialized_critics,
        run_tournament,
    )
    from .pipeline.music_supervisor import MusicPlan
    from .pipeline.picture_editor import PictureCandidateSummary
    from .pipeline.schemas import Segment as Seg, TranscriptArtifact

    op = _require_operator(authorization)
    _rate_check(op["id"], "editorial_intelligence")
    project = _get_project(project_id)

    def json_value(row: dict, key: str):
        value = row[key]
        return json.loads(value) if isinstance(value, str) else value

    color_filter = (f"id=eq.{body.colorRunId}&limit=1" if body.colorRunId else
                    f"project_id=eq.{project_id}&order=version.desc&limit=1")
    color_rows = supa.db_select("color_runs", color_filter)
    if not color_rows or color_rows[0]["project_id"] != project_id:
        raise HTTPException(409, "Milestone 5 QC-passed color run required")
    color_run = color_rows[0]
    if color_run.get("status") != "qc_passed":
        raise HTTPException(409, "Milestone 5 color run must pass QC")
    refs = {
        "caption_run": ("caption_runs", color_run["caption_run_id"]),
        "graphics_run": ("graphics_runs", color_run["graphics_run_id"]),
        "audio_run": ("audio_mix_runs", color_run["audio_mix_run_id"]),
        "music_run": ("music_sound_runs", color_run["music_sound_run_id"]),
        "picture_run": ("picture_edit_runs", color_run["picture_edit_run_id"]),
        "preproduction": ("preproduction_runs", color_run["preproduction_run_id"]),
    }
    lineage = {}
    for name, (table, row_id) in refs.items():
        rows = supa.db_select(table, f"id=eq.{row_id}&limit=1")
        if not rows or rows[0].get("project_id") != project_id:
            raise HTTPException(409, f"Milestone 1-5 {name} ancestry is invalid")
        lineage[name] = rows[0]
    if (lineage["caption_run"].get("graphics_run_id") != color_run["graphics_run_id"]
            or lineage["audio_run"].get("status") != "qc_passed"):
        raise HTTPException(409, "Milestone 1-5 ancestry is inconsistent")

    segment_rows = supa.db_select("segments", f"project_id=eq.{project_id}")
    transcript_rows = supa.db_select(
        "asset_analysis", f"project_id=eq.{project_id}&kind=eq.transcript&status=eq.completed",
    )
    try:
        treatment = CreativeTreatment(**json_value(lineage["preproduction"], "creative_treatment"))
        composition = {key: CompositionMetrics(**value) for key, value in
                       json_value(lineage["preproduction"], "composition_by_segment").items()}
        pictures = [PictureCandidateSummary(**item) for item in
                    json_value(lineage["picture_run"], "candidates")]
        music_plan = MusicPlan(**json_value(lineage["music_run"], "music_plan"))
        completed = CompletedAudioMix(**json_value(lineage["audio_run"], "mix_instructions"))
        segments = [Seg(**row["data"]) for row in segment_rows]
        transcripts = {}
        for row in transcript_rows:
            data = row.get("data")
            data = json.loads(data) if isinstance(data, str) else data
            if data:
                transcripts[row["asset_id"]] = TranscriptArtifact(**data)
        candidates = generate_initial_candidates(
            pictures, treatment, completed, music_plan, segments, composition, transcripts,
        )
    except (ValidationError, EditorialIntelligenceError, ValueError) as exc:
        detail = exc.errors(include_url=False) if isinstance(exc, ValidationError) else str(exc)
        raise HTTPException(422, detail)

    asset_rows = supa.db_select("media_assets", f"project_id=eq.{project_id}")
    asset_by_id = {row["id"]: row for row in asset_rows
                   if row.get("user_id") == project["user_id"]}
    required_ids = {asset_id for candidate in candidates for asset_id in candidate.sourceAssetIds}
    if not required_ids.issubset(asset_by_id):
        raise HTTPException(409, "editorial candidates reference missing or foreign media")

    batch_id = str(uuid4())
    existing = supa.db_select(
        "tournament_runs", f"project_id=eq.{project_id}&order=version.desc&limit=1",
    )
    version = (int(existing[0]["version"]) + 1) if existing else 1
    reports_by_key = {}
    publishability_by_key = {}
    output_files = {}
    with tempfile.TemporaryDirectory(prefix="stromation-editorial-intelligence-") as tmp:
        completed_preview = os.path.join(tmp, "completed-audio-preview.mp4")
        supa.storage_download(
            "exports", lineage["audio_run"]["preview_storage_path"], completed_preview,
        )
        from . import media_store
        source_paths = {}
        for asset_id in required_ids:
            local = os.path.join(tmp, f"source-{asset_id}.mp4")
            media_store.download_media_asset(asset_by_id[asset_id], project, local)
            source_paths[asset_id] = local

        def evaluate(candidate):
            output = os.path.join(tmp, f"{candidate.candidateKey}.mp4")
            candidate.renderQc = render_complete_candidate(
                candidate, source_paths, completed_preview, output, tmp,
            )
            candidate.previewStoragePath = (
                f"users/{project['user_id']}/projects/{project_id}/"
                f"editorial-intelligence/v{version}/{candidate.candidateKey}-{uuid4()}.mp4"
            )
            critics = run_specialized_critics(candidate, segments, composition, completed)
            report = build_publishability_report(candidate, critics)
            reports_by_key[candidate.candidateKey] = critics
            publishability_by_key[candidate.candidateKey] = report
            output_files[candidate.candidateKey] = output

        try:
            for candidate in candidates:
                evaluate(candidate)
            if body.includeBoundedRevision:
                revisable = sorted(candidates, key=lambda item: (
                    bool([request for critic in reports_by_key[item.candidateKey]
                          for request in critic.revisionRequests]),
                    publishability_by_key[item.candidateKey].overallPublishabilityScore,
                ), reverse=True)[0]
                requests = [request for critic in reports_by_key[revisable.candidateKey]
                            for request in critic.revisionRequests]
                if requests:
                    revised = apply_bounded_revision(revisable, requests, segments)
                    candidates.append(revised)
                    evaluate(revised)
        except (EditorialIntelligenceError, RenderError, subprocess.CalledProcessError) as exc:
            raise HTTPException(422, str(exc))

        try:
            tournament = run_tournament(list(publishability_by_key.values()))
        except EditorialIntelligenceError as exc:
            raise HTTPException(422, str(exc))
        winner = next(item for item in candidates
                      if item.candidateKey == tournament.winnerCandidateKey)
        human_report = None
        sessions = supa.db_select(
            "human_edit_sessions",
            f"project_id=eq.{project_id}&status=eq.approved&order=created_at.desc&limit=1",
        )
        if sessions:
            session = sessions[0]
            try:
                human_report = build_comparison_report(
                    session, supa.db_select("timelines", f"project_id=eq.{project_id}"),
                    supa.db_select("timeline_scorecards",
                                   f"human_edit_session_id=eq.{session['id']}"),
                    supa.db_select("user_corrections",
                                   f"human_edit_session_id=eq.{session['id']}"),
                )
            except HumanCeilingError:
                human_report = None
        comparison = build_four_way_comparison(
            human_report, winner, publishability_by_key[winner.candidateKey],
        )
        _audit(op, "create_editorial_intelligence", project_id, {
            "batch_id": batch_id, "version": version,
            "candidate_count": len(candidates), "winner": winner.candidateKey,
            "pairwise_count": len(tournament.pairwiseComparisons),
        })
        for candidate in candidates:
            supa.storage_upload("exports", candidate.previewStoragePath,
                                output_files[candidate.candidateKey], content_type="video/mp4")

    ancestry = {
        "batch_id": batch_id, "project_id": project_id, "user_id": project["user_id"],
        "preproduction_run_id": color_run["preproduction_run_id"],
        "picture_edit_run_id": color_run["picture_edit_run_id"],
        "music_sound_run_id": color_run["music_sound_run_id"],
        "audio_mix_run_id": color_run["audio_mix_run_id"],
        "graphics_run_id": color_run["graphics_run_id"],
        "caption_run_id": color_run["caption_run_id"], "color_run_id": color_run["id"],
    }
    candidate_rows = {}
    for index, candidate in enumerate(candidates, 1):
        parent_id = (candidate_rows[candidate.parentCandidateKey]["id"]
                     if candidate.parentCandidateKey else None)
        row = _service_insert("candidate_runs", {
            **ancestry, "parent_candidate_run_id": parent_id,
            "candidate_key": candidate.candidateKey, "candidate_index": index,
            "generation_kind": candidate.generationKind,
            "source_picture_candidate_id": candidate.sourcePictureCandidateId,
            "variant_config": candidate.variant.model_dump(mode="json"),
            "manifest": candidate.model_dump(mode="json"), "render_qc": candidate.renderQc,
            "preview_storage_bucket": "exports",
            "preview_storage_path": candidate.previewStoragePath,
            "fabricated_footage": False, "created_by": op["id"],
        })
        candidate_rows[candidate.candidateKey] = row
        for critic in reports_by_key[candidate.candidateKey]:
            _service_insert("critic_runs", {
                "batch_id": batch_id, "project_id": project_id,
                "user_id": project["user_id"], "candidate_run_id": row["id"],
                "critic_kind": critic.criticKind, "version": 1, "score": critic.score,
                "passed": critic.passed,
                "evidence": [item.model_dump(mode="json") for item in critic.evidence],
                "issues": critic.issues,
                "revision_requests": [item.model_dump(mode="json")
                                      for item in critic.revisionRequests],
                "consistency_hash": critic.consistencyHash, "created_by": op["id"],
            })
        publishability = publishability_by_key[candidate.candidateKey]
        _service_insert("publishability_reports", {
            "batch_id": batch_id, "project_id": project_id,
            "user_id": project["user_id"], "candidate_run_id": row["id"], "version": 1,
            "dimensions": {key: value.model_dump(mode="json")
                           for key, value in publishability.dimensions.items()},
            "overall_publishability_score": publishability.overallPublishabilityScore,
            "publishable": publishability.publishable,
            "blocking_issues": publishability.blockingIssues,
            "technical_qc_passed": publishability.technicalQcPassed,
            "rendered_media_qc_passed": publishability.renderedMediaQcPassed,
            "tournament_eligible": publishability.tournamentEligible,
            "rendered_media_qc": publishability.renderedMediaQc,
            "created_by": op["id"],
        })
    tournament_row = _service_insert("tournament_runs", {
        **ancestry, "version": version,
        "candidate_run_ids": [candidate_rows[key]["id"] for key in tournament.candidateKeys],
        "pairwise_comparisons": [item.model_dump(mode="json")
                                 for item in tournament.pairwiseComparisons],
        "bracket": [item.model_dump(mode="json") for item in tournament.bracket],
        "winner_candidate_run_id": candidate_rows[tournament.winnerCandidateKey]["id"],
        "winner_reasoning": tournament.winnerReasoning,
        "human_ceiling_comparison": comparison, "created_by": op["id"],
    })
    return {
        "batchId": batch_id, "version": version, "tournamentRunId": tournament_row["id"],
        "winnerCandidateRunId": tournament_row["winner_candidate_run_id"],
        "winnerCandidateKey": tournament.winnerCandidateKey,
        "candidates": [{"id": candidate_rows[item.candidateKey]["id"],
                        **item.model_dump(mode="json")} for item in candidates],
        "publishabilityReports": {key: value.model_dump(mode="json")
                                  for key, value in publishability_by_key.items()},
        "tournament": tournament.model_dump(mode="json"),
        "humanCeilingComparison": comparison,
    }


# ==================== customer Product Editor API ====================


def _owned_project(project_id: str, authorization: str,
                   allow_deleted: bool = False) -> tuple[dict, dict]:
    user = _auth_user(authorization)
    project = _get_project(project_id)
    if project["user_id"] != user["id"]:
        raise HTTPException(403, "project ownership check failed")
    # Soft-deleted projects are rejected from every customer API (only the delete
    # endpoint opts in via allow_deleted to finish/retry cleanup).
    if not allow_deleted and project.get("deleted_at"):
        raise HTTPException(404, "project not found")
    return user, project


def _editor_audit(user_id: str, project_id: str, action: str,
                  details: dict | None = None) -> None:
    try:
        _service_insert("editor_audit_events", {
            "user_id": user_id, "project_id": project_id,
            "action": action, "details": details or {},
        })
    except Exception as exc:
        raise HTTPException(503, "action aborted: editor audit unavailable") from exc


def _editor_document(document_id: str, project_id: str) -> dict:
    rows = supa.db_select("editor_documents", f"id=eq.{document_id}&limit=1")
    if not rows or rows[0]["project_id"] != project_id:
        raise HTTPException(404, "editor document not found")
    return rows[0]


def _editor_existing_document_filter(candidate_run_id: str, project_id: str) -> str:
    return (f"candidate_run_id=eq.{candidate_run_id}&project_id=eq.{project_id}"
            "&order=version.desc&limit=1")


class EditorStartBody(BaseModel):
    candidateRunId: UUID


class EditorRevisionBody(BaseModel):
    documentId: UUID
    expectedVersion: int = Field(ge=1)
    prompt: str = Field(min_length=1, max_length=500)


class EditorRenderBody(BaseModel):
    documentId: UUID


def _public_candidate(row: dict) -> dict:
    """Drop raw storage internals from a customer-facing candidate. The preview is fetched
    via /candidates/{id}/preview-url (server-signed), so the raw path/bucket are never
    needed client-side and are not exposed."""
    return {k: v for k, v in row.items()
            if k not in ("preview_storage_path", "preview_storage_bucket")}


def _public_render_job(row: dict) -> dict:
    """Drop the raw export storage path from a customer-facing render job. Downloads go
    through /editor/renders/{job}/sign (server-signed); artifacts keep only the display
    metadata (dimensions/size/telemetry), never the raw object key."""
    art = row.get("artifacts")
    if isinstance(art, dict) and "output" in art:
        return {**row, "artifacts": {k: v for k, v in art.items() if k != "output"}}
    return row


@app.get("/projects/{project_id}/workspace")
def customer_workspace(project_id: str, authorization: str = Header(default="")):
    user, project = _owned_project(project_id, authorization)
    candidates = supa.db_select(
        "candidate_runs", f"project_id=eq.{project_id}&order=created_at.desc",
    )
    reports = supa.db_select(
        "publishability_reports", f"project_id=eq.{project_id}&order=created_at.desc",
    )
    report_by_candidate = {row["candidate_run_id"]: row for row in reports}
    documents = supa.db_select(
        "editor_documents", f"project_id=eq.{project_id}&order=version.desc",
    )
    jobs = supa.db_select(
        "pipeline_jobs", f"project_id=eq.{project_id}&kind=eq.final_render"
        "&order=created_at.desc&limit=10",
    )
    return {
        "project": project,
        "candidates": [{**_public_candidate(row),
                        "publishability": report_by_candidate.get(row["id"])}
                       for row in candidates],
        "editorDocuments": documents,
        "renderJobs": [_public_render_job(j) for j in jobs],
        "viewerUserId": user["id"],
    }


def _cleanup_project_storage(user_id: str, project_id: str) -> list[str]:
    """Remove EVERY object under the project's storage prefixes (raw footage +
    proxies/wav/thumbs + licensed music + exports + autoedit drafts + finishing
    previews). Failures are NOT swallowed — returns the list of buckets that failed
    so the caller can record retryable state."""
    prefix = f"users/{user_id}/projects/{project_id}/"
    failures = []
    for bucket in ("raw-footage", "exports"):
        try:
            supa.storage_remove_prefix(bucket, prefix)
        except Exception as exc:  # noqa: BLE001 — surfaced, not swallowed
            failures.append(f"{bucket} ({type(exc).__name__})")
    return failures


@app.delete("/projects/{project_id}")
def customer_delete_project(project_id: str, authorization: str = Header(default="")):
    """Safe, server-authorized project deletion (soft-delete).

    Immutable evidence (candidate_runs/editor_documents/timelines/audio ancestry) is
    preserved by design — protect_*_evidence triggers make a hard delete impossible.
    The project is marked deleted (hidden from the customer) FIRST, then all project
    storage prefixes are cleaned. Cleanup failures are recorded (deleted_cleanup_done
    = false) and surfaced; a repeated DELETE RETRIES the cleanup rather than no-opping.
    Idempotent + retry-safe."""
    user, project = _owned_project(project_id, authorization, allow_deleted=True)
    if project.get("deleted_at") and project.get("deleted_cleanup_done"):
        return {"status": "deleted", "cleanup": "complete", "projectId": project_id}
    if not project.get("deleted_at"):
        _editor_audit(user["id"], project_id, "delete_project", {})
        supa.db_update("projects", f"id=eq.{project_id}",
                       {"deleted_at": _now(), "deleted_cleanup_done": False,
                        "status_reason": "deleted by owner"})
    failures = _cleanup_project_storage(user["id"], project_id)
    if failures:
        supa.db_update("projects", f"id=eq.{project_id}",
                       {"deleted_cleanup_done": False,
                        "status_reason": f"deleted; storage cleanup pending: "
                                         f"{', '.join(failures)}"[:300]})
        raise HTTPException(503, {"status": "deleted", "cleanup": "pending",
                                  "message": "project deleted; storage cleanup "
                                             "incomplete — retry to finish cleanup"})
    supa.db_update("projects", f"id=eq.{project_id}", {"deleted_cleanup_done": True})
    return {"status": "deleted", "cleanup": "complete", "projectId": project_id}


@app.post("/projects/{project_id}/candidates/{candidate_id}/preview-url")
def customer_candidate_preview_url(project_id: str, candidate_id: UUID,
                                   authorization: str = Header(default="")):
    """Short-lived signed URL for a candidate's preview, minted server-side.

    Verifies JWT + project ownership + candidate/project/owner ancestry, and that the
    preview lives in the owner's exports prefix. The browser never sees the storage
    path or the service-role key."""
    import httpx as _hx
    user, _ = _owned_project(project_id, authorization)
    rows = supa.db_select("candidate_runs", f"id=eq.{candidate_id}&limit=1")
    if (not rows or rows[0]["project_id"] != project_id
            or rows[0]["user_id"] != user["id"]):
        raise HTTPException(404, "candidate not found")
    candidate = rows[0]
    bucket = candidate.get("preview_storage_bucket") or "exports"
    path = candidate.get("preview_storage_path")
    expected = f"users/{user['id']}/projects/{project_id}/"
    if bucket != "exports" or not path or not path.startswith(expected):
        raise HTTPException(409, "candidate preview is not available")
    _editor_audit(user["id"], project_id, "sign_candidate_preview",
                  {"candidate": str(candidate_id)})

    def _sign_supabase() -> str | None:
        response = _hx.post(
            f"{supa.SUPABASE_URL}/storage/v1/object/sign/{bucket}/{path}",
            headers={"apikey": supa.SERVICE_KEY,
                     "Authorization": f"Bearer {supa.SERVICE_KEY}",
                     "Content-Type": "application/json"},
            json={"expiresIn": 3600}, timeout=30,
        )
        if response.status_code != 200:
            return None
        return f"{supa.SUPABASE_URL}/storage/v1{response.json()['signedURL']}"

    def _sign_s3() -> str | None:
        if not s3store.enabled():
            return None
        try:
            s3store.head_object(path)          # never sign a key that is not there
            return s3store.presign_get(path, expires=3600)
        except Exception:                      # noqa: BLE001 — absent/unreachable
            return None

    # The preview is written by upload_export, which routes to S3 or to Supabase
    # storage depending on EXPORT_STORAGE_PROVIDER, so the reader must sign against
    # the store that actually holds the object (the export-download path already
    # does this via artifacts.export_provider). Candidates written before that
    # provenance was recorded carry no provider: for those we VERIFY both stores
    # rather than guessing from the deployment's current setting, which would
    # mis-sign whichever era of rows the env does not match.
    provider = (candidate.get("variant_config") or {}).get("previewStorageProvider")
    if provider == "s3":
        url = _sign_s3()
    elif provider == "supabase":
        url = _sign_supabase()
    else:
        url = _sign_supabase() or _sign_s3()
    if not url:
        raise HTTPException(404, "candidate preview could not be signed")
    return {"url": url, "expiresIn": 3600}


@app.post("/projects/{project_id}/editor/start")
def customer_editor_start(project_id: str, body: EditorStartBody,
                          authorization: str = Header(default="")):
    from .product_editor import EditorDocument, document_from_candidate, renderer_timeline

    user, project = _owned_project(project_id, authorization)
    _rate_check(user["id"], "editor_write")
    rows = supa.db_select("candidate_runs", f"id=eq.{body.candidateRunId}&limit=1")
    if not rows or rows[0]["project_id"] != project_id or rows[0]["user_id"] != user["id"]:
        raise HTTPException(409, "candidate ancestry does not belong to this project")
    candidate = rows[0]
    existing = supa.db_select(
        "editor_documents", _editor_existing_document_filter(candidate["id"], project_id),
    )
    if existing:
        return existing[0]
    assets = supa.db_select("media_assets", f"project_id=eq.{project_id}")
    durations = {row["id"]: float(row.get("duration_seconds") or 0) for row in assets}
    manifest = candidate.get("manifest") or {}
    source_ids = {str(value) for value in manifest.get("sourceAssetIds", [])}
    if (manifest.get("fabricatedFootage") is not False or not source_ids
            or not source_ids.issubset(durations)):
        raise HTTPException(409, "candidate source assets are missing, foreign, or fabricated")
    try:
        document = document_from_candidate(project_id, candidate, durations)
        EditorDocument(**document)
    except (ValueError, ValidationError) as exc:
        raise HTTPException(422, str(exc))
    _editor_audit(user["id"], project_id, "start_editor", {"candidate": candidate["id"]})
    timeline_existing = supa.db_select(
        "timelines", f"project_id=eq.{project_id}&order=version.desc&limit=1",
    )
    timeline_version = int(timeline_existing[0]["version"] + 1) if timeline_existing else 1
    timeline = _service_insert("timelines", {
        "project_id": project_id, "user_id": user["id"], "version": timeline_version,
        "timeline_json": renderer_timeline(document), "lineage": "product_editor",
        "is_immutable": True,
    })
    row = _service_insert("editor_documents", {
        "project_id": project_id, "user_id": user["id"],
        "candidate_run_id": candidate["id"], "timeline_id": timeline["id"],
        "version": 1, "document": document, "created_by": user["id"],
    })
    return row


@app.get("/projects/{project_id}/editor/{document_id}")
def customer_editor_get(project_id: str, document_id: UUID,
                        authorization: str = Header(default="")):
    user, _ = _owned_project(project_id, authorization)
    row = _editor_document(str(document_id), project_id)
    if row["user_id"] != user["id"]:
        raise HTTPException(403, "editor document ownership check failed")
    return row


@app.post("/projects/{project_id}/editor/{document_id}/operations")
def customer_editor_operations(project_id: str, document_id: UUID, body: dict,
                               authorization: str = Header(default="")):
    from .product_editor import (EditorError, OperationBatch, apply_batch,
                                 renderer_timeline)

    user, _ = _owned_project(project_id, authorization)
    _rate_check(user["id"], "editor_write")
    current = _editor_document(str(document_id), project_id)
    if current["user_id"] != user["id"]:
        raise HTTPException(403, "editor document ownership check failed")
    try:
        batch = OperationBatch(**body)
    except ValidationError as exc:
        raise HTTPException(422, exc.errors(include_url=False))
    ai_operations = [operation for operation in batch.operations if operation.actor == "ai"]
    if ai_operations:
        proposal_ids = {str(operation.proposalId) for operation in ai_operations
                        if operation.proposalId}
        if len(proposal_ids) != 1 or len(ai_operations) != len(batch.operations):
            raise HTTPException(422, "AI operations require one persisted revision proposal")
        proposals = supa.db_select(
            "editor_revision_proposals", f"id=eq.{next(iter(proposal_ids))}&limit=1",
        )
        submitted = [operation.model_dump(mode="json") for operation in batch.operations]
        if (not proposals or proposals[0]["project_id"] != project_id
                or proposals[0]["user_id"] != user["id"]
                or proposals[0]["base_document_id"] != current["id"]
                or proposals[0]["operations"] != submitted):
            raise HTTPException(422, "AI operation proposal evidence is invalid")
    elif any(operation.proposalId for operation in batch.operations):
        raise HTTPException(422, "user operations cannot claim AI proposal evidence")
    if batch.expectedVersion != current["version"] or any(
            op.baseVersion != current["version"] for op in batch.operations):
        raise HTTPException(409, {"message": "editor version conflict",
                                  "latestDocumentId": current["id"],
                                  "latestVersion": current["version"]})
    latest = supa.db_select(
        "editor_documents", f"candidate_run_id=eq.{current['candidate_run_id']}"
        "&order=version.desc&limit=1",
    )
    if not latest or latest[0]["id"] != current["id"]:
        raise HTTPException(409, {"message": "editor version conflict",
                                  "latestDocumentId": latest[0]["id"],
                                  "latestVersion": latest[0]["version"]})
    try:
        document = apply_batch(current["document"], batch.operations)
    except EditorError as exc:
        raise HTTPException(422, str(exc))
    _editor_audit(user["id"], project_id, "apply_editor_operations", {
        "base_document_id": current["id"], "operation_count": len(batch.operations),
    })
    timelines = supa.db_select(
        "timelines", f"project_id=eq.{project_id}&order=version.desc&limit=1",
    )
    timeline = _service_insert("timelines", {
        "project_id": project_id, "user_id": user["id"],
        "version": int(timelines[0]["version"] + 1) if timelines else 1,
        "timeline_json": renderer_timeline(document), "lineage": "product_editor",
        "parent_timeline_id": current["timeline_id"], "is_immutable": True,
    })
    try:
        created = _service_insert("editor_documents", {
            "project_id": project_id, "user_id": user["id"],
            "candidate_run_id": current["candidate_run_id"],
            "parent_document_id": current["id"], "timeline_id": timeline["id"],
            "version": current["version"] + 1, "document": document,
            "created_by": user["id"],
        })
    except Exception as exc:
        raise HTTPException(409, "editor version conflict; reload the latest revision") from exc
    for index, operation in enumerate(batch.operations, 1):
        _service_insert("editor_operations", {
            "project_id": project_id, "user_id": user["id"],
            "candidate_run_id": current["candidate_run_id"],
            "base_document_id": current["id"], "result_document_id": created["id"],
            "operation_id": str(operation.operationId), "operation_index": index,
            "operation_type": operation.type, "target_id": operation.targetId,
            "actor": operation.actor, "operation": operation.model_dump(mode="json"),
            "client_timestamp": operation.timestamp.isoformat(),
        })
    return created


@app.post("/projects/{project_id}/editor/revisions/propose")
def customer_editor_propose(project_id: str, body: EditorRevisionBody,
                            authorization: str = Header(default="")):
    from .product_editor import EditorError, translate_revision

    user, _ = _owned_project(project_id, authorization)
    _rate_check(user["id"], "editor_revision")
    document = _editor_document(str(body.documentId), project_id)
    if document["user_id"] != user["id"] or document["version"] != body.expectedVersion:
        raise HTTPException(409, "editor version conflict")
    try:
        proposal_id = uuid4()
        operations = translate_revision(body.prompt, document["document"],
                                        body.expectedVersion, proposal_id=proposal_id)
    except EditorError as exc:
        raise HTTPException(422, str(exc))
    _editor_audit(user["id"], project_id, "propose_editor_revision", {
        "document_id": document["id"], "operation_count": len(operations),
    })
    _service_insert("editor_revision_proposals", {
        "id": str(proposal_id), "project_id": project_id, "user_id": user["id"],
        "candidate_run_id": document["candidate_run_id"],
        "base_document_id": document["id"], "prompt": body.prompt,
        "operations": operations,
    })
    return {"proposalId": str(proposal_id), "documentId": document["id"],
            "baseVersion": document["version"], "operations": operations,
            "providerCalled": False}


@app.post("/projects/{project_id}/editor/render")
def customer_editor_render(project_id: str, body: EditorRenderBody,
                           authorization: str = Header(default="")):
    from . import jobs as job_service

    user, _ = _owned_project(project_id, authorization)
    _rate_check(user["id"], "editor_render")
    document = _editor_document(str(body.documentId), project_id)
    if document["user_id"] != user["id"]:
        raise HTTPException(403, "editor document ownership check failed")
    blockers = [item for item in document["document"].get("attribution", [])
                if item.get("required") and not item.get("rendered")]
    if blockers:
        raise HTTPException(409, "export blocked: required attribution has not been rendered")
    _editor_audit(user["id"], project_id, "render_editor_document", {
        "document_id": document["id"], "version": document["version"],
    })
    # Version-bound idempotency: enqueue_job dedupes only on (project, kind), so an
    # active render for a DIFFERENT editor revision must never be reused for this one.
    active_renders = supa.db_select(
        "pipeline_jobs",
        f"project_id=eq.{project_id}&kind=eq.final_render"
        "&status=in.(queued,processing,cancel_requested)&order=created_at.desc")
    for existing in active_renders:
        params = existing.get("params") or {}
        if (params.get("editor_document_id") == document["id"]
                and params.get("editor_document_version") == document["version"]):
            return existing   # duplicate export of the SAME revision -> same job
        raise HTTPException(409, "another export is already in progress for this "
                                 "project; wait for it to finish before exporting again")
    try:
        job = job_service.enqueue_job(
            project_id, user["id"], "final_render",
            {"timeline_id": document["timeline_id"], "editor_document_id": document["id"],
             "editor_document_version": document["version"]},
        )
    except job_service.ConcurrencyLimit as exc:
        raise HTTPException(429, str(exc))
    # Revalidate the returned job. enqueue_job dedupes only on (project, kind): under a
    # concurrent race two different-revision requests can both pass the pre-check above,
    # and the loser's enqueue_job returns the WINNER's active job (a different revision).
    # If the job it handed back is not for THIS exact revision, reject rather than return
    # a render that would export the wrong revision.
    job_params = job.get("params") or {}
    if (job_params.get("editor_document_id") != document["id"]
            or job_params.get("editor_document_version") != document["version"]):
        raise HTTPException(409, "another export is already in progress for a different "
                                 "revision of this project; wait for it to finish")
    # Idempotent: enqueue_job dedupes to the existing active job on a duplicate
    # click, so a render-request row for this job may already exist — that's fine.
    try:
        _service_insert("editor_render_requests", {
            "project_id": project_id, "user_id": user["id"],
            "editor_document_id": document["id"],
            "editor_document_version": document["version"],
            "pipeline_job_id": job["id"],
        })
    except Exception:  # noqa: BLE001 — tracking row only; the job is the source of truth
        pass
    return _public_render_job(job)


@app.post("/projects/{project_id}/editor/renders/{job_id}/retry")
def customer_editor_render_retry(project_id: str, job_id: UUID,
                                 authorization: str = Header(default="")):
    user, _ = _owned_project(project_id, authorization)
    rows = supa.db_select("pipeline_jobs", f"id=eq.{job_id}&limit=1")
    if not rows or rows[0]["project_id"] != project_id or rows[0]["user_id"] != user["id"]:
        raise HTTPException(404, "editor render job not found")
    job = rows[0]
    if job["kind"] != "final_render" or not (job.get("params") or {}).get("editor_document_id"):
        raise HTTPException(409, "job is not a Product Editor export")
    if job["status"] != "failed":
        raise HTTPException(409, f"job is {job['status']}, not retryable")
    if int(job.get("attempt_count") or 0) >= int(job.get("max_attempts") or 3):
        raise HTTPException(409, "job has exhausted its retry limit")
    _editor_audit(user["id"], project_id, "retry_editor_render", {"job_id": job["id"]})
    supa.db_update("pipeline_jobs", f"id=eq.{job['id']}&status=eq.failed", {
        "status": "queued", "progress": 0, "error_message": None,
        "current_stage": "retry queued", "completed_at": None,
    })
    return _public_render_job(supa.db_select("pipeline_jobs", f"id=eq.{job['id']}")[0])


@app.post("/projects/{project_id}/editor/renders/{job_id}/sign")
def customer_editor_render_sign(project_id: str, job_id: UUID,
                                authorization: str = Header(default="")):
    import httpx as _hx
    user, _ = _owned_project(project_id, authorization)
    rows = supa.db_select("pipeline_jobs", f"id=eq.{job_id}&limit=1")
    if not rows or rows[0]["project_id"] != project_id or rows[0]["user_id"] != user["id"]:
        raise HTTPException(404, "editor render job not found")
    job = rows[0]
    path = (job.get("artifacts") or {}).get("output")
    expected = f"users/{user['id']}/projects/{project_id}/renders/"
    if job["status"] != "completed" or not path or not path.startswith(expected):
        raise HTTPException(409, "completed export is not available")
    _editor_audit(user["id"], project_id, "sign_editor_export", {"job_id": job["id"]})
    # Use the provider recorded on the job (self-describing). Older jobs that never
    # recorded one predate S3 exports and MUST default to Supabase — never to the
    # current deployment default, or switching the env would mis-sign old objects.
    provider = (job.get("artifacts") or {}).get("export_provider") or "supabase"
    if provider == "s3":
        url = s3store.presign_get(path, expires=3600,
                                  download_name=f"stromation-{str(job_id)[:8]}.mp4")
        return {"url": url, "expiresIn": 3600}
    response = _hx.post(
        f"{supa.SUPABASE_URL}/storage/v1/object/sign/exports/{path}",
        headers={"apikey": supa.SERVICE_KEY,
                 "Authorization": f"Bearer {supa.SERVICE_KEY}",
                 "Content-Type": "application/json"},
        json={"expiresIn": 3600}, timeout=30,
    )
    if response.status_code != 200:
        raise HTTPException(404, "export could not be signed")
    return {"url": f"{supa.SUPABASE_URL}/storage/v1{response.json()['signedURL']}",
            "expiresIn": 3600}


# ==================== S3 multipart raw-footage uploads ====================
# The video body goes browser -> S3 directly via presigned URLs; only small JSON
# control messages reach Railway. Every endpoint verifies the Supabase JWT and
# project ownership, and re-derives the object key server-side.

class RawUploadInitiateBody(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    contentType: str = Field(min_length=1, max_length=100)
    size: int = Field(ge=1)


class RawUploadSignPartsBody(BaseModel):
    partNumbers: list[int] = Field(min_length=1, max_length=1000)


class RawUploadCompletedPart(BaseModel):
    partNumber: int = Field(ge=1, le=raw_uploads.S3_MAX_PARTS)
    etag: str = Field(min_length=1, max_length=256)


class RawUploadCompleteBody(BaseModel):
    parts: list[RawUploadCompletedPart] = Field(min_length=1)


RAW_UPLOAD_TTL_S = int(os.environ.get("RAW_UPLOAD_TTL_S", str(24 * 3600)))


def _require_s3() -> None:
    if not s3store.enabled():
        raise HTTPException(503, "raw-footage S3 uploads are not configured")


def _raw_session(session_id: str, user: dict, project_id: str) -> dict:
    rows = supa.db_select("raw_upload_sessions", f"id=eq.{session_id}&limit=1")
    if (not rows or rows[0]["user_id"] != user["id"]
            or rows[0]["project_id"] != project_id):
        raise HTTPException(404, "upload session not found")
    return rows[0]


def _raw_fail(session: dict, reason: str, delete_object: bool = False) -> None:
    """Best-effort cleanup: abort the multipart, optionally delete a completed
    object, and record an auditable failure reason. Never raises. Never downgrades
    a finalized session."""
    if delete_object:
        try:
            s3store.delete_object(session["object_key"])
        except Exception:  # noqa: BLE001
            pass
    try:
        s3store.abort_multipart(session["object_key"], session["upload_id"])
    except Exception:  # noqa: BLE001
        pass
    try:
        # Conditional: never move a finalized/aborted session to failed.
        _service_patch("raw_upload_sessions",
                       f"id=eq.{session['id']}&status=in.(initiated,completing,"
                       "completed,finalizing,failed)",
                       {"status": "failed", "error_reason": reason[:400],
                        "updated_at": _now()})
    except Exception:  # noqa: BLE001
        pass


def _session_expired(session: dict) -> bool:
    expires_at = session.get("expires_at")
    if not expires_at:
        return False
    try:
        exp = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
    except ValueError:
        return False
    return datetime.now(timezone.utc) >= exp


def _raw_claim(session_id: str, from_status: str, to_status: str,
               extra: dict | None = None) -> bool:
    """Atomically move a session from one status to another. Returns True iff this
    caller won the transition (matched exactly the expected prior status)."""
    body = {"status": to_status, "updated_at": _now(), **(extra or {})}
    matched = _service_patch(
        "raw_upload_sessions", f"id=eq.{session_id}&status=eq.{from_status}", body)
    return bool(matched)


@app.post("/projects/{project_id}/raw-uploads/initiate")
def raw_upload_initiate(project_id: str, body: RawUploadInitiateBody,
                        authorization: str = Header(default="")):
    _require_s3()
    user, _ = _owned_project(project_id, authorization)
    _rate_check(user["id"], "raw_upload")
    try:
        raw_uploads.validate_extension(body.filename)
        raw_uploads.validate_content_type(body.contentType)
        raw_uploads.validate_size(body.size)
    except raw_uploads.UploadValidationError as exc:
        raise HTTPException(422, {"code": exc.code, "message": exc.message})
    asset_id = str(uuid4())
    safe = raw_uploads.safe_filename(body.filename)
    key = raw_uploads.object_key(user["id"], project_id, asset_id, safe)
    plan = raw_uploads.plan_parts(body.size)
    _editor_audit(user["id"], project_id, "raw_upload_initiate",
                  {"asset_id": asset_id, "size": body.size, "key": key})
    upload_id = s3store.create_multipart(key, body.contentType)
    try:
        session = _service_insert("raw_upload_sessions", {
            "user_id": user["id"], "project_id": project_id, "asset_id": asset_id,
            "provider": "s3", "bucket": s3store.bucket(), "object_key": key,
            "upload_id": upload_id, "filename": safe, "content_type": body.contentType,
            "declared_size": body.size, "part_size": plan["part_size"],
            "status": "initiated",
            "expires_at": (datetime.now(timezone.utc)
                           + timedelta(seconds=RAW_UPLOAD_TTL_S)).isoformat()})
    except Exception as exc:  # noqa: BLE001
        # Never leak an orphan multipart if the session row can't be persisted.
        try:
            s3store.abort_multipart(key, upload_id)
        except Exception:  # noqa: BLE001
            pass
        raise HTTPException(503, "could not start upload session") from exc
    return {"sessionId": session["id"], "assetId": asset_id,
            "bucket": s3store.bucket(), "objectKey": key, "uploadId": upload_id,
            "partSize": plan["part_size"], "partCount": plan["part_count"],
            "maxBytes": raw_uploads.MAX_UPLOAD_BYTES}


@app.post("/projects/{project_id}/raw-uploads/{session_id}/sign-parts")
def raw_upload_sign_parts(project_id: str, session_id: UUID,
                          body: RawUploadSignPartsBody,
                          authorization: str = Header(default="")):
    _require_s3()
    user, _ = _owned_project(project_id, authorization)
    _rate_check(user["id"], "raw_upload_sign")
    session = _raw_session(str(session_id), user, project_id)
    if session["status"] != "initiated":
        raise HTTPException(409, f"upload session is {session['status']}")
    if _session_expired(session):
        _raw_fail(session, "session expired before signing", delete_object=False)
        raise HTTPException(409, "upload session has expired; start a new upload")
    max_parts = raw_uploads.plan_parts(
        session["declared_size"], session["part_size"])["part_count"]
    signed = []
    for number in body.partNumbers:
        if number < 1 or number > max_parts:
            raise HTTPException(422, f"part number {number} out of range 1..{max_parts}")
        signed.append({"partNumber": number,
                       "url": s3store.presign_part(session["object_key"],
                                                   session["upload_id"], number)})
    return {"parts": signed, "expiresIn": s3store.PART_URL_EXPIRE_S}


@app.post("/projects/{project_id}/raw-uploads/{session_id}/complete")
def raw_upload_complete(project_id: str, session_id: UUID,
                        body: RawUploadCompleteBody,
                        authorization: str = Header(default="")):
    _require_s3()
    user, _ = _owned_project(project_id, authorization)
    session = _raw_session(str(session_id), user, project_id)
    # Idempotent: a duplicate/replayed completion of an already-finished session
    # returns success rather than clobbering it.
    if session["status"] in ("completed", "finalized"):
        return {"sessionId": session["id"], "status": session["status"],
                "objectKey": session["object_key"]}
    if session["status"] != "initiated":
        raise HTTPException(409, f"upload session is {session['status']}")
    if _session_expired(session):
        _raw_fail(session, "session expired before completion")
        raise HTTPException(409, "upload session has expired; start a new upload")
    if not raw_uploads.key_belongs_to(session["object_key"], user["id"], project_id):
        raise HTTPException(403, "object key ancestry check failed")
    # Validate the manifest BEFORE calling S3.
    expected = raw_uploads.plan_parts(
        session["declared_size"], session["part_size"])["part_count"]
    manifest = [{"partNumber": p.partNumber, "etag": p.etag} for p in body.parts]
    try:
        raw_uploads.validate_part_manifest(manifest, expected)
    except raw_uploads.UploadValidationError as exc:
        raise HTTPException(422, {"code": exc.code, "message": exc.message})
    # Atomically claim the completion so only one caller drives S3.
    if not _raw_claim(session["id"], "initiated", "completing"):
        fresh = _raw_session(str(session_id), user, project_id)
        if fresh["status"] in ("completed", "finalized"):
            return {"sessionId": fresh["id"], "status": fresh["status"],
                    "objectKey": fresh["object_key"]}
        raise HTTPException(409, f"upload session is {fresh['status']}")
    parts = [{"PartNumber": p.partNumber, "ETag": p.etag} for p in body.parts]
    try:
        s3store.complete_multipart(session["object_key"], session["upload_id"], parts)
    except Exception as exc:  # noqa: BLE001 — missing/invalid parts land here
        # If the object is actually present (e.g. a prior completion won), treat as
        # done; otherwise fail the claim we hold.
        try:
            s3store.head_object(session["object_key"])
            _raw_claim(session["id"], "completing", "completed")
            return {"sessionId": session["id"], "status": "completed",
                    "objectKey": session["object_key"]}
        except Exception:  # noqa: BLE001
            _raw_fail({**session, "status": "completing"},
                      f"complete failed: {type(exc).__name__}")
            raise HTTPException(409, "multipart completion failed "
                                     "(missing or invalid parts)") from exc
    _raw_claim(session["id"], "completing", "completed")
    return {"sessionId": session["id"], "status": "completed",
            "objectKey": session["object_key"]}


@app.post("/projects/{project_id}/raw-uploads/{session_id}/finalize")
def raw_upload_finalize(project_id: str, session_id: UUID,
                        authorization: str = Header(default="")):
    from . import mediaprobe

    def _existing_asset(asset_id):
        rows = supa.db_select("media_assets", f"id=eq.{asset_id}&limit=1")
        return rows[0] if rows else None

    _require_s3()
    user, _ = _owned_project(project_id, authorization)
    _rate_check(user["id"], "raw_finalize")
    session = _raw_session(str(session_id), user, project_id)
    # Idempotent: an already-finalized session returns its committed asset.
    if session["status"] == "finalized":
        asset = _existing_asset(session["asset_id"])
        if asset:
            return asset
    if session["status"] != "completed":
        raise HTTPException(409, f"session is {session['status']}, not completed")
    key = session["object_key"]
    if not raw_uploads.key_belongs_to(key, user["id"], project_id):
        raise HTTPException(403, "object key ancestry check failed")
    # Atomically claim finalization: prevents duplicate probes AND blocks abort
    # from deleting the object mid-validation.
    if not _raw_claim(session["id"], "completed", "finalizing"):
        fresh = _raw_session(str(session_id), user, project_id)
        if fresh["status"] == "finalized":
            asset = _existing_asset(fresh["asset_id"])
            if asset:
                return asset
        raise HTTPException(409, f"session is {fresh['status']}, not finalizable")
    session = {**session, "status": "finalizing"}
    try:
        head = s3store.head_object(key)
    except Exception as exc:  # noqa: BLE001
        _raw_fail(session, "object missing at finalize")
        raise HTTPException(409, "uploaded object not found") from exc
    if head["size"] > raw_uploads.MAX_UPLOAD_BYTES:
        _raw_fail(session, "object exceeds 2GB", delete_object=True)
        raise HTTPException(413, "uploaded object exceeds the 2 GB limit")
    if head["size"] != session["declared_size"]:
        _raw_fail(session, "declared/actual size mismatch", delete_object=True)
        raise HTTPException(409, "uploaded object size does not match the declared size")
    content_type = head.get("content_type") or session["content_type"]
    if content_type not in raw_uploads.ALLOWED_CONTENT_TYPES:
        _raw_fail(session, "content-type not allowed", delete_object=True)
        raise HTTPException(415, "uploaded object content type is not allowed")
    probe_url = s3store.presign_get(key, expires=600)
    probe = mediaprobe.probe_video(probe_url)
    if not probe["valid"]:
        _raw_fail(session, "no valid video stream", delete_object=True)
        raise HTTPException(422, "uploaded file is not a valid video")
    _editor_audit(user["id"], project_id, "raw_upload_finalize",
                  {"asset_id": session["asset_id"], "key": key, "size": head["size"]})
    try:
        asset = _service_insert("media_assets", {
            "id": session["asset_id"], "project_id": project_id, "user_id": user["id"],
            "filename": session["filename"], "storage_path": key,
            "storage_provider": "s3", "storage_bucket": session["bucket"],
            "storage_key": key, "etag": head["etag"], "content_type": content_type,
            "mime_type": content_type, "size_bytes": head["size"],
            "duration_seconds": probe["duration"], "validation_status": "validated"})
    except Exception:  # noqa: BLE001 — a prior finalize may have inserted it
        asset = _existing_asset(session["asset_id"])
        if not asset:
            raise
    _raw_claim(session["id"], "finalizing", "finalized")
    supa.db_update("projects", f"id=eq.{project_id}",
                   {"status": "ready",
                    "status_reason": "footage uploaded and validated"})
    return asset


@app.post("/projects/{project_id}/raw-uploads/{session_id}/abort")
def raw_upload_abort(project_id: str, session_id: UUID,
                     authorization: str = Header(default="")):
    _require_s3()
    user, _ = _owned_project(project_id, authorization)
    session = _raw_session(str(session_id), user, project_id)
    if session["status"] == "finalized":
        raise HTTPException(409, "session already finalized")
    if session["status"] == "finalizing":
        # Never delete an object while finalize is validating/committing it.
        raise HTTPException(409, "session is being finalized; cannot abort")
    if session["status"] == "aborted":
        return {"sessionId": session["id"], "status": "aborted"}
    # Only abort from a non-terminal state; guards against racing a finalize that
    # just claimed the session.
    if not _raw_claim(session["id"], session["status"], "aborted"):
        fresh = _raw_session(str(session_id), user, project_id)
        if fresh["status"] == "aborted":
            return {"sessionId": fresh["id"], "status": "aborted"}
        raise HTTPException(409, f"session is {fresh['status']}; cannot abort")
    try:
        s3store.abort_multipart(session["object_key"], session["upload_id"])
    except Exception:  # noqa: BLE001
        pass
    try:
        s3store.delete_object(session["object_key"])
    except Exception:  # noqa: BLE001
        pass
    _editor_audit(user["id"], project_id, "raw_upload_abort",
                  {"session": session["id"]})
    return {"sessionId": session["id"], "status": "aborted"}


class SignBody(BaseModel):
    bucket: str
    path: str
    expires_in: int = 900


@app.post("/projects/{project_id}/sign")
def op_sign_url(project_id: str, body: SignBody,
                authorization: str = Header(default="")):
    """Temporary private preview URLs for operators. Storage RLS scopes users to
    their own paths, so operator previews must be signed server-side — after
    verifying the object belongs to THIS project."""
    import httpx as _hx
    op = _require_operator(authorization)
    project = _get_project(project_id)
    if body.bucket not in ("raw-footage", "exports"):
        raise HTTPException(422, "unknown bucket")
    prefix = f"users/{project['user_id']}/projects/{project_id}/"
    if not body.path.startswith(prefix):
        raise HTTPException(403, "path does not belong to this project")
    _audit(op, "sign_preview", project_id, {"bucket": body.bucket,
                                            "path": body.path})
    r = _hx.post(f"{supa.SUPABASE_URL}/storage/v1/object/sign/{body.bucket}/{body.path}",
                 headers={"apikey": supa.SERVICE_KEY,
                          "Authorization": f"Bearer {supa.SERVICE_KEY}",
                          "Content-Type": "application/json"},
                 json={"expiresIn": max(60, min(3600, body.expires_in))},
                 timeout=30)
    if r.status_code != 200:
        raise HTTPException(404, "object not found or could not be signed")
    return {"url": f"{supa.SUPABASE_URL}/storage/v1{r.json()['signedURL']}"}


@app.post("/projects/{project_id}/assets/{asset_id}/sign")
def op_sign_asset(project_id: str, asset_id: UUID,
                  authorization: str = Header(default="")):
    """Operator preview URL for a raw-footage asset BY ID — provider-aware. Loads
    the asset, verifies it belongs to this project, and signs the stored
    authoritative key (S3 presign or Supabase sign). Works for both S3 and legacy
    Supabase footage."""
    import httpx as _hx
    op = _require_operator(authorization)
    project = _get_project(project_id)
    rows = supa.db_select("media_assets", f"id=eq.{asset_id}&limit=1")
    if (not rows or rows[0]["project_id"] != project_id
            or rows[0]["user_id"] != project["user_id"]):
        raise HTTPException(404, "asset not found for this project")
    asset = rows[0]
    _audit(op, "sign_asset_preview", project_id, {"asset_id": str(asset_id)})
    provider = asset.get("storage_provider") or "supabase"
    if provider == "s3":
        key = asset.get("storage_key") or asset["storage_path"]
        if (asset.get("storage_bucket") != s3store.bucket()
                or not raw_uploads.key_belongs_to(key, project["user_id"], project_id)):
            raise HTTPException(403, "asset key ancestry check failed")
        return {"url": s3store.presign_get(key, expires=900),
                "expiresIn": 900, "provider": "s3"}
    path = asset["storage_path"]
    if not raw_uploads.supabase_path_belongs_to(path, project["user_id"], project_id):
        raise HTTPException(403, "asset path ancestry check failed")
    r = _hx.post(f"{supa.SUPABASE_URL}/storage/v1/object/sign/raw-footage/{path}",
                 headers={"apikey": supa.SERVICE_KEY,
                          "Authorization": f"Bearer {supa.SERVICE_KEY}",
                          "Content-Type": "application/json"},
                 json={"expiresIn": 900}, timeout=30)
    if r.status_code != 200:
        raise HTTPException(404, "asset could not be signed")
    return {"url": f"{supa.SUPABASE_URL}/storage/v1{r.json()['signedURL']}",
            "expiresIn": 900, "provider": "supabase"}


class EvalPatch(BaseModel):
    fields: dict


@app.post("/projects/{project_id}/evaluation")
def op_patch_evaluation(project_id: str, body: EvalPatch,
                        authorization: str = Header(default="")):
    """Operator records manual metrics: correction minutes, ratings, etc."""
    import httpx as _hx
    op = _require_operator(authorization)
    _get_project(project_id)
    ALLOWED = {"clips_manually_replaced", "clips_manually_trimmed",
               "captions_manually_changed", "music_adjustments",
               "human_correction_minutes", "first_draft_rating", "final_rating",
               "user_satisfaction", "user_would_pay", "user_would_return",
               "notes"}
    patch = {k: v for k, v in body.fields.items() if k in ALLOWED}
    if not patch:
        raise HTTPException(422, f"no allowed fields; allowed: {sorted(ALLOWED)}")
    rows = supa.db_select("draft_evaluations",
                          f"project_id=eq.{project_id}&order=created_at.desc&limit=1")
    if not rows:
        raise HTTPException(404, "no evaluation row yet — run generate-draft first")
    _audit(op, "record_evaluation", project_id, patch)
    _hx.patch(f"{supa.SUPABASE_URL}/rest/v1/draft_evaluations?id=eq.{rows[0]['id']}",
              headers={"apikey": supa.SERVICE_KEY,
                       "Authorization": f"Bearer {supa.SERVICE_KEY}",
                       "Content-Type": "application/json",
                       "Prefer": "return=minimal"},
              json=patch, timeout=30).raise_for_status()
    return {"updated": sorted(patch)}


@app.post("/render")
def start_render(authorization: str = Header(default="")):
    """DEPRECATED legacy render surface — DISABLED.

    The customer journey exports through /projects/{id}/editor/render (immutable,
    version-bound, pipeline_jobs); operators use /projects/{id}/render-final. This
    legacy render_jobs path is retained only to return an explicit 410 (instead of a
    silent 404) for any stale client, and is no longer reachable by customers."""
    raise HTTPException(410, "legacy /render is disabled; use the Product Editor export")
