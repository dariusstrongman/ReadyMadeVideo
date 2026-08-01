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
import tempfile
from datetime import datetime, timezone

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, ValidationError

from . import config

config.validate()          # fail fast with clear errors before anything imports supa

from . import supa
from .renderer import RenderError, render
from .timeline import Timeline, plan_render

app = FastAPI(title="Stromation Render Backend", version="0.1.0")

ALLOWED_ORIGINS = os.environ.get(
    "ALLOWED_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["POST", "GET", "OPTIONS"],
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


def _fail_job(job_id: str, message: str) -> None:
    supa.db_update("render_jobs", f"id=eq.{job_id}",
                   {"status": "failed", "error_message": message[:1000],
                    "completed_at": _now()})


def _run_render_job(job_id: str, plan_dict: dict, asset_path: str) -> None:
    """Background worker: download -> render -> upload -> record."""
    from .timeline import RenderPlan
    plan = RenderPlan(**plan_dict)
    tmp = tempfile.mkdtemp(prefix=f"stromation-render-{job_id[:8]}-")
    try:
        supa.db_update("render_jobs", f"id=eq.{job_id}",
                       {"status": "processing", "progress": 10, "started_at": _now()})
        src = os.path.join(tmp, "source" + os.path.splitext(asset_path)[1])
        supa.storage_download("raw-footage", asset_path, src)
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


def _rate_check(user_id: str, bucket: str = "enqueue") -> None:
    import time as _t
    key = f"{user_id}:{bucket}"
    now = _t.time()
    window = [t for t in _rate.get(key, []) if now - t < 60]
    if len(window) >= RATE_LIMIT_PER_MIN:
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
    if job["user_id"] != user["id"]:
        _require_operator(authorization)   # owners or operators only
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
    elapsed_seconds: float = Field(default=0, ge=0, le=12 * 60 * 60)
    note: str = Field(default="", max_length=1000)


@app.post("/projects/{project_id}/timeline-ops")
def op_timeline_ops(project_id: str, body: TimelineOpsBody,
                    authorization: str = Header(default="")):
    """Operator edits the timeline through CONSTRAINED operations only."""
    from .human_ceiling import (HUMAN_DRAFT, correction_type,
                                split_elapsed_seconds)
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
        if session.get("current_timeline_id") != body.base_timeline_id:
            raise HTTPException(409, "base timeline is not the session's current human draft")
    elif rows[0].get("is_immutable"):
        raise HTTPException(
            409, "immutable timeline cannot be edited; start a human edit session")

    _audit(op, "timeline_ops", project_id,
           {"base": body.base_timeline_id, "operations": body.operations,
            "protected_ranges": body.protected_ranges,
            "human_edit_session_id": body.human_edit_session_id,
            "elapsed_seconds": body.elapsed_seconds})
    try:
        ops = parse_operations(body.operations)
        result = apply_operations(tl, ops, actor="user",
                                  protected=[tuple(r) for r in
                                             body.protected_ranges if len(r) == 2])
    except OpError as e:
        raise HTTPException(422, str(e))

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
        existing = supa.db_select(
            "user_corrections",
            f"human_edit_session_id=eq.{session['id']}&order=operation_index.desc&limit=1")
        next_index = (existing[0].get("operation_index") or 0) + 1 if existing else 1
        elapsed = split_elapsed_seconds(body.elapsed_seconds, len(result.applied))
        for offset, applied in enumerate(result.applied):
            kind = correction_type(applied)
            _service_insert("user_corrections", {
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
                "operation_index": next_index + offset,
                "correction_type": kind,
                "elapsed_seconds": elapsed[offset],
                "operator_user_id": op["id"],
            })
        _service_patch("human_edit_sessions", f"id=eq.{session['id']}", {
            "current_timeline_id": new_tl["id"],
            "human_correction_seconds": round(
                float(session.get("human_correction_seconds") or 0)
                + body.elapsed_seconds, 3),
        })
    return {"timeline_id": new_tl["id"], "version": new_tl["version"],
            "lineage": new_tl.get("lineage"),
            "applied": result.applied, "rejected": result.rejected}


class HumanCeilingStartBody(BaseModel):
    autonomous_initial_timeline_id: str
    autonomous_revised_timeline_id: str
    edit_run_id: str | None = None


@app.post("/projects/{project_id}/human-ceiling/start")
def op_start_human_ceiling(project_id: str, body: HumanCeilingStartBody,
                           authorization: str = Header(default="")):
    """Freeze the two autonomous baselines and branch a human draft."""
    from .human_ceiling import (AUTONOMOUS_INITIAL, AUTONOMOUS_REVISED,
                                HUMAN_DRAFT)
    op = _require_operator(authorization)
    project = _get_project(project_id)
    active = supa.db_select("human_edit_sessions",
                            f"project_id=eq.{project_id}&status=eq.active&limit=1")
    if active:
        raise HTTPException(409, "an active human edit session already exists")

    timeline_ids = [body.autonomous_initial_timeline_id,
                    body.autonomous_revised_timeline_id]
    timelines = []
    for timeline_id in timeline_ids:
        rows = supa.db_select("timelines", f"id=eq.{timeline_id}")
        if not rows or rows[0]["project_id"] != project_id:
            raise HTTPException(404, "autonomous baseline not found in this project")
        timelines.append(rows[0])
    if timeline_ids[0] == timeline_ids[1]:
        raise HTTPException(422, "initial and revised baselines must be separate timelines")

    edit_run_id = body.edit_run_id
    if edit_run_id:
        runs = supa.db_select("edit_runs", f"id=eq.{edit_run_id}")
        if not runs or runs[0]["project_id"] != project_id:
            raise HTTPException(404, "edit run not found in this project")
        run = runs[0]
        if (run.get("timeline_v1_id") != timeline_ids[0]
                or run.get("timeline_v2_id") != timeline_ids[1]):
            raise HTTPException(422, "baseline timelines do not match the edit run")
    else:
        runs = supa.db_select("edit_runs",
                              f"project_id=eq.{project_id}&order=created_at.desc")
        run = next((r for r in runs
                    if r.get("timeline_v1_id") == timeline_ids[0]
                    and r.get("timeline_v2_id") == timeline_ids[1]), None)
        edit_run_id = run.get("id") if run else None
    if not edit_run_id:
        raise HTTPException(422, "baselines must belong to the same recorded edit run")

    _audit(op, "start_human_ceiling", project_id, {
        "autonomous_initial_timeline_id": timeline_ids[0],
        "autonomous_revised_timeline_id": timeline_ids[1],
        "edit_run_id": edit_run_id,
    })
    for timeline, lineage in zip(
            timelines, (AUTONOMOUS_INITIAL, AUTONOMOUS_REVISED), strict=True):
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
        "timeline_json": timelines[1]["timeline_json"],
        "lineage": HUMAN_DRAFT,
        "parent_timeline_id": timeline_ids[1],
        "edit_run_id": edit_run_id,
        "is_immutable": False,
    })
    session = _service_insert("human_edit_sessions", {
        "project_id": project_id,
        "user_id": project["user_id"],
        "edit_run_id": edit_run_id,
        "operator_user_id": op["id"],
        "autonomous_initial_timeline_id": timeline_ids[0],
        "autonomous_revised_timeline_id": timeline_ids[1],
        "current_timeline_id": human_timeline["id"],
        "status": "active",
        "human_correction_seconds": 0,
    })
    return {"session": session, "human_timeline": human_timeline}


class HumanCeilingApproveBody(BaseModel):
    session_id: str
    total_human_seconds: float = Field(ge=0, le=24 * 60 * 60)


@app.post("/projects/{project_id}/human-ceiling/approve")
def op_approve_human_ceiling(project_id: str, body: HumanCeilingApproveBody,
                             authorization: str = Header(default="")):
    """Approve and freeze the separate human timeline lineage."""
    from collections import Counter
    from .human_ceiling import HUMAN_APPROVED, HUMAN_DRAFT
    op = _require_operator(authorization)
    _get_project(project_id)
    sessions = supa.db_select("human_edit_sessions", f"id=eq.{body.session_id}")
    if not sessions or sessions[0]["project_id"] != project_id:
        raise HTTPException(404, "human edit session not found in this project")
    session = sessions[0]
    if session["status"] != "active":
        raise HTTPException(409, f"human edit session is {session['status']}")
    timelines = supa.db_select("timelines", f"id=eq.{session['current_timeline_id']}")
    if not timelines or timelines[0].get("lineage") != HUMAN_DRAFT:
        raise HTTPException(409, "session has no mutable human draft to approve")

    corrections = supa.db_select(
        "user_corrections", f"human_edit_session_id=eq.{session['id']}")
    counted_seconds = sum(float(c.get("elapsed_seconds") or 0) for c in corrections)
    if body.total_human_seconds + 0.001 < counted_seconds:
        raise HTTPException(422, "total human time cannot be less than recorded operation time")
    _audit(op, "approve_human_ceiling", project_id, {
        "session_id": session["id"],
        "timeline_id": session["current_timeline_id"],
        "total_human_seconds": body.total_human_seconds,
    })
    approved = _service_patch("timelines", f"id=eq.{session['current_timeline_id']}", {
        "lineage": HUMAN_APPROVED,
        "is_immutable": True,
        "approved_by": op["id"],
        "approved_at": _now(),
    })[0]
    _service_patch("human_edit_sessions", f"id=eq.{session['id']}", {
        "status": "approved",
        "approved_timeline_id": approved["id"],
        "human_correction_seconds": body.total_human_seconds,
        "approved_at": _now(),
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
            "human_correction_minutes": round(body.total_human_seconds / 60, 3),
        })
    return {"session_id": session["id"], "approved_timeline_id": approved["id"],
            "lineage": approved["lineage"],
            "human_correction_seconds": body.total_human_seconds,
            "correction_counts": dict(counts)}


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
    allowed_ids = {session.get("autonomous_initial_timeline_id"),
                   session.get("autonomous_revised_timeline_id"),
                   session.get("approved_timeline_id")}
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
    return row


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
def start_render(req: RenderRequest, background: BackgroundTasks,
                 authorization: str = Header(default="")):
    # 1. authenticate
    token = authorization.removeprefix("Bearer ").strip()
    try:
        user = supa.verify_user(token)
    except supa.AuthError as e:
        raise HTTPException(401, str(e))
    uid = user["id"]

    # 2. load job + ownership
    jobs = supa.db_select("render_jobs", f"id=eq.{req.job_id}")
    if not jobs:
        raise HTTPException(404, "render job not found")
    job = jobs[0]
    if job["user_id"] != uid:
        raise HTTPException(403, "you do not own this render job")
    if job["status"] not in ("queued", "failed"):
        raise HTTPException(409, f"job is {job['status']}, not renderable")

    # 3. ownership chain: project -> timeline -> asset
    projects = supa.db_select("projects", f"id=eq.{job['project_id']}")
    if not projects or projects[0]["user_id"] != uid:
        raise HTTPException(403, "project ownership check failed")
    timelines = supa.db_select("timelines", f"id=eq.{job['timeline_id']}")
    if not timelines or timelines[0]["user_id"] != uid \
            or timelines[0]["project_id"] != job["project_id"]:
        raise HTTPException(403, "timeline ownership check failed")

    # 4. validate timeline + resolve asset
    tl_json = timelines[0]["timeline_json"]
    if isinstance(tl_json, str):
        tl_json = json.loads(tl_json)
    try:
        tl = Timeline(**tl_json)
        plan = plan_render(tl)
    except (ValidationError, ValueError) as e:
        _fail_job(req.job_id, f"invalid timeline: {e}")
        raise HTTPException(422, f"invalid timeline: {e}")

    assets = supa.db_select("media_assets", f"id=eq.{plan.asset_id}")
    if not assets or assets[0]["user_id"] != uid \
            or assets[0]["project_id"] != job["project_id"]:
        _fail_job(req.job_id, "timeline references an asset you do not own")
        raise HTTPException(422, "timeline references an invalid or foreign asset")

    # reset for retry case
    supa.db_update("render_jobs", f"id=eq.{req.job_id}",
                   {"status": "queued", "progress": 0, "error_message": None})

    # 5. run
    background.add_task(_run_render_job, req.job_id, plan.model_dump(),
                        assets[0]["storage_path"])
    return {"job_id": req.job_id, "status": "queued"}
