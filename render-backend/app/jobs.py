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

WORKER_CONCURRENCY = int(os.environ.get("WORKER_CONCURRENCY", "1"))
STALE_AFTER_S = int(os.environ.get("JOB_STALE_AFTER_S", "900"))
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
        f"user_id=eq.{user_id}&status=in.({','.join(ACTIVE_STATES)})", "id,kind,project_id")
    dup = [a for a in active if a["project_id"] == project_id and a["kind"] == kind]
    if not dup and len(active) >= MAX_ACTIVE_JOBS_PER_USER:
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

    def __init__(self, job: dict):
        self.job = job
        self.expected = 0
        self.recorded = 0

    def cancelled(self) -> bool:
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
def _download_sources(project: dict, tmp: str, ctx: JobContext | None = None):
    assets = supa.db_select("media_assets", f"project_id=eq.{project['id']}")
    sources = {}
    for a in assets:
        if ctx:
            ctx.checkpoint("download_sources")
        dst = os.path.join(tmp, a["id"] + os.path.splitext(a["filename"])[1])
        supa.storage_download("raw-footage", a["storage_path"], dst)
        sources[a["id"]] = dst
    return sources, assets


def _load_segments(project_id: str) -> list[Segment]:
    rows = supa.db_select("segments", f"project_id=eq.{project_id}")
    return [Segment(**r["data"]) for r in rows]


def _upload_export(project: dict, rel: str, local: str) -> str:
    path = f"users/{project['user_id']}/projects/{project['id']}/{rel}"
    supa.storage_upload("exports", path, local)
    return path


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

        run_pipeline(sources[a["id"]], store, asset_id=a["id"], workdir=wd,
                     upload_cb=upload_cb,
                     context=(job.get("params") or {}).get("context", ""))
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
    return {"assets_analyzed": done}


def handle_autoedit(job: dict, project: dict, tmp: str, ctx: JobContext) -> dict:
    import json as _json

    from .pipeline.autoedit import autoedit
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
        platform=params.get("platform", "horizontal"),
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
    existing = supa.db_select("timelines",
                              f"project_id=eq.{project['id']}"
                              f"&order=version.desc&limit=1")
    next_ver = (existing[0]["version"] + 1) if existing else 1
    tl_ids = []
    for fname in sorted(f for f in os.listdir(run_dir)
                        if f.startswith("timeline_v") and f.endswith(".json")):
        tl = _json.load(open(os.path.join(run_dir, fname), encoding="utf-8"))
        r = _insert("timelines", {"project_id": project["id"],
                                  "user_id": project["user_id"],
                                  "version": next_ver, "timeline_json": tl})
        tl_ids.append(r.json()[0]["id"])
        next_ver += 1
    for fname in sorted(f for f in os.listdir(run_dir) if f.endswith(".mp4")):
        artifacts["previews"].append(_upload_export(
            project, f"drafts/{job['id']}/{fname}", os.path.join(run_dir, fname)))
    for fname in ("blueprint.json", "selection.json", "validator_v1.json",
                  "critic_pass1.json", "revision_ops_pass1.json"):
        p = os.path.join(run_dir, fname)
        if os.path.exists(p):
            artifacts[fname.replace(".json", "")] = _json.load(
                open(p, encoding="utf-8"))

    er = _insert("edit_runs", {
        "project_id": project["id"], "user_id": project["user_id"],
        "status": "completed", "brief": params.get("brief"),
        "blueprint": artifacts.get("blueprint"),
        "selection": artifacts.get("selection"),
        "validator_report": artifacts.get("validator_v1"),
        "critic_verdict": artifacts.get("critic_pass1"),
        "revision_ops": artifacts.get("revision_ops_pass1"),
        "timeline_v1_id": tl_ids[0] if tl_ids else None,
        "timeline_v2_id": tl_ids[-1] if len(tl_ids) > 1 else None,
        "preview_paths": artifacts["previews"], "completed_at": _now()})
    edit_run_id = er.json()[0]["id"] if er.status_code == 201 else None

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

    dur = time.time() - t0
    ctx.rec("autoedit", round(dur, 2),
            units={"gemini_requests": 1 + report.get("revisionPasses", 0),
                   "cpu_hours": dur / 3600})
    set_project_status(project["id"], "draft_ready",
                       f"autoedit job {job['id'][:8]} produced a draft")
    return artifacts


def handle_revision(job: dict, project: dict, tmp: str, ctx: JobContext) -> dict:
    return handle_autoedit(job, project, tmp, ctx)


def handle_final_render(job: dict, project: dict, tmp: str, ctx: JobContext) -> dict:
    import json as _json

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
    set_project_status(project["id"], "rendering",
                       f"final render job {job['id'][:8]}")
    sources, _ = _download_sources(project, tmp, ctx)
    out = os.path.join(tmp, "final.mp4")
    update_job(job["id"], {"current_stage": "rendering", "progress": 30})
    ctx.checkpoint("before_render")
    t0 = time.time()
    result = render_timeline(tl, sources, out, profile="final",
                             cancel_check=lambda: ctx.cancelled())
    ctx.checkpoint("before_upload")               # never upload a partial export
    path = _upload_export(project, f"renders/{job['id']}.mp4", out)
    dur = time.time() - t0
    ctx.rec("final_render", round(dur, 2), result["size_bytes"],
            units={"cpu_hours": dur / 3600})
    set_project_status(project["id"], "completed",
                       f"final render {job['id'][:8]} completed")
    return {"output": path, **{k: result[k] for k in
                               ("duration", "width", "height", "size_bytes")}}


HANDLERS = {"analysis": handle_analysis, "autoedit": handle_autoedit,
            "revision": handle_revision, "final_render": handle_final_render}
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
    except JobCancelled as e:
        update_job(job["id"], {"status": "cancelled",
                               "error_message": f"cancelled at checkpoint: {e}",
                               "artifacts": {"telemetry_status":
                                             ctx.telemetry_status()},
                               "processing_seconds": round(time.time() - t0, 2),
                               "completed_at": _now()})
        try:
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
        try:
            set_project_status(job["project_id"], FAIL_STATUS[job["kind"]],
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
