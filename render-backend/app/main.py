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
from pydantic import BaseModel, ValidationError

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
    return {"ok": True}


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
