"""P4: persistent job worker.

Jobs live in pipeline_jobs (DB) — they survive API restarts. A single background
worker thread claims queued jobs (optimistic claim), heartbeats while working,
and recovers stale jobs at startup and on every loop. The partial unique index
(project, kind, active) makes duplicate requests idempotent at the DB boundary.

Kinds: analysis | autoedit | revision | final_render
Every stage writes inspectable artifacts (asset_analysis / edit_runs / storage);
failures retain diagnostics; temp files always cleaned; retry is safe (stages
are idempotent — completed artifacts are reused).
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import time
import traceback
from datetime import datetime, timezone

import httpx

from . import supa
from .pipeline import telemetry
from .pipeline.schemas import Segment

WORKER_CONCURRENCY = int(os.environ.get("WORKER_CONCURRENCY", "1"))
STALE_AFTER_S = int(os.environ.get("JOB_STALE_AFTER_S", "900"))
POLL_INTERVAL_S = float(os.environ.get("JOB_POLL_INTERVAL_S", "3"))

_service_headers = {
    "apikey": supa.SERVICE_KEY,
    "Authorization": f"Bearer {supa.SERVICE_KEY}",
    "Content-Type": "application/json",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _patch(table, filters, body, prefer="return=representation"):
    r = httpx.patch(f"{supa.SUPABASE_URL}/rest/v1/{table}?{filters}",
                    headers={**_service_headers, "Prefer": prefer},
                    json=body, timeout=30)
    r.raise_for_status()
    return r.json() if prefer != "return=minimal" else None


def _insert(table, body, prefer="return=representation"):
    r = httpx.post(f"{supa.SUPABASE_URL}/rest/v1/{table}",
                   headers={**_service_headers, "Prefer": prefer},
                   json=body, timeout=30)
    return r


def set_project_status(project_id: str, status: str, reason: str) -> None:
    _patch("projects", f"id=eq.{project_id}",
           {"status": status, "status_reason": reason[:400]},
           prefer="return=minimal")


def update_job(job_id: str, body: dict) -> None:
    body["heartbeat_at"] = _now()
    _patch("pipeline_jobs", f"id=eq.{job_id}", body, prefer="return=minimal")


def enqueue_job(project_id: str, user_id: str, kind: str,
                params: dict | None = None) -> dict:
    """Idempotent: an active job of the same kind returns the existing one."""
    r = _insert("pipeline_jobs", {"project_id": project_id, "user_id": user_id,
                                  "kind": kind, "params": params or {}})
    if r.status_code == 201:
        return r.json()[0]
    if r.status_code == 409:  # active duplicate — return it
        rows = supa.db_select(
            "pipeline_jobs",
            f"project_id=eq.{project_id}&kind=eq.{kind}"
            f"&status=in.(queued,processing)&order=created_at.desc&limit=1")
        if rows:
            return rows[0]
    r.raise_for_status()
    return {}


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
    """Requeue (or fail) processing jobs whose heartbeat went silent."""
    rows = supa.db_select("pipeline_jobs", "status=eq.processing")
    n = 0
    for job in rows:
        hb = job.get("heartbeat_at") or job.get("started_at") or job["created_at"]
        age = time.time() - datetime.fromisoformat(
            hb.replace("Z", "+00:00")).timestamp()
        if age > STALE_AFTER_S:
            n += 1
            if job["attempt_count"] >= job["max_attempts"]:
                update_job(job["id"], {"status": "failed",
                                       "error_message": "stale: worker died and "
                                       "max attempts exhausted",
                                       "completed_at": _now()})
            else:
                update_job(job["id"], {"status": "queued",
                                       "error_message": "recovered after stale "
                                       "heartbeat"})
    return n


def _cancelled(job_id: str) -> bool:
    rows = supa.db_select("pipeline_jobs", f"id=eq.{job_id}", "status")
    return bool(rows) and rows[0]["status"] == "cancelled"


# ---------------- handlers ----------------
def _download_sources(project: dict, tmp: str) -> tuple[dict, list[dict]]:
    assets = supa.db_select("media_assets", f"project_id=eq.{project['id']}")
    sources = {}
    for a in assets:
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


def handle_analysis(job: dict, project: dict, tmp: str) -> dict:
    from .pipeline.runner import CloudStore, run_pipeline
    set_project_status(project["id"], "analyzing", f"analysis job {job['id'][:8]}")
    sources, assets = _download_sources(project, tmp)
    if not assets:
        raise RuntimeError("project has no uploaded footage")
    done = 0
    for a in assets:
        if _cancelled(job["id"]):
            raise RuntimeError("cancelled by operator")
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
        telemetry.record("analysis_asset", project["id"], job["id"],
                         round(time.time() - t0, 2), a.get("size_bytes"),
                         units={"gemini_requests": 1, "gemini_video_seconds": dur,
                                "whisper_minutes": dur / 60})
        done += 1
        update_job(job["id"], {"progress": int(done / len(assets) * 100),
                               "current_stage": f"analyzed {done}/{len(assets)}"})
    set_project_status(project["id"], "ready", "analysis completed")
    return {"assets_analyzed": done}


def handle_autoedit(job: dict, project: dict, tmp: str) -> dict:
    from .pipeline.autoedit import autoedit
    params = job.get("params") or {}
    segments = _load_segments(project["id"])
    if not segments:
        raise RuntimeError("no segment catalog — run analysis first")
    sources, _ = _download_sources(project, tmp)
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
        render_final=False)
    if report.get("status") != "completed":
        raise RuntimeError(f"autoedit failed: {report.get('error')}")

    # persist artifacts: timelines -> DB, previews -> private exports bucket
    artifacts: dict = {"previews": [], "run_report": report}
    existing = supa.db_select("timelines",
                              f"project_id=eq.{project['id']}"
                              f"&order=version.desc&limit=1")
    next_ver = (existing[0]["version"] + 1) if existing else 1
    tl_ids = []
    for fname in sorted(f for f in os.listdir(run_dir)
                        if f.startswith("timeline_v") and f.endswith(".json")):
        tl = json.load(open(os.path.join(run_dir, fname), encoding="utf-8"))
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
            artifacts[fname.replace(".json", "")] = json.load(open(p, encoding="utf-8"))

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

    # auto evaluation metrics (P5)
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
        "revision_passes": report.get("revisionPasses", 0)}, prefer="return=minimal")

    telemetry.record("autoedit", project["id"], job["id"],
                     round(time.time() - t0, 2),
                     units={"gemini_requests": 1 + report.get("revisionPasses", 0)})
    set_project_status(project["id"], "draft_ready",
                       f"autoedit job {job['id'][:8]} produced a draft")
    return artifacts


def handle_revision(job: dict, project: dict, tmp: str) -> dict:
    # v1: a revision job is an autoedit pass over the existing catalog with the
    # critic enabled — reusing cached analysis; params may narrow the brief.
    return handle_autoedit(job, project, tmp)


def handle_final_render(job: dict, project: dict, tmp: str) -> dict:
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
        tl = json.loads(tl)
    # verify every clip's asset belongs to this project (spec requirement)
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
    sources, _ = _download_sources(project, tmp)
    out = os.path.join(tmp, "final.mp4")
    update_job(job["id"], {"current_stage": "rendering", "progress": 30})
    t0 = time.time()
    result = render_timeline(tl, sources, out, profile="final")
    path = _upload_export(project, f"renders/{job['id']}.mp4", out)
    telemetry.record("final_render", project["id"], job["id"],
                     round(time.time() - t0, 2), result["size_bytes"])
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
    try:
        projects = supa.db_select("projects", f"id=eq.{job['project_id']}")
        if not projects:
            raise RuntimeError("project vanished")
        project = projects[0]
        artifacts = HANDLERS[job["kind"]](job, project, tmp)
        update_job(job["id"], {"status": "completed", "progress": 100,
                               "artifacts": artifacts,
                               "processing_seconds": round(time.time() - t0, 2),
                               "completed_at": _now()})
    except Exception as e:  # noqa: BLE001 — a job must never kill the worker
        err = f"{type(e).__name__}: {e}"
        update_job(job["id"], {"status": "failed",
                               "error_message": err[:900],
                               "processing_seconds": round(time.time() - t0, 2),
                               "completed_at": _now()})
        try:
            set_project_status(job["project_id"], FAIL_STATUS[job["kind"]],
                               f"job {job['id'][:8]} failed: {err[:200]}")
        except Exception:
            pass
        print(f"[worker] job {job['id'][:8]} failed: {err}\n"
              f"{traceback.format_exc()[-800:]}", flush=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


_stop = threading.Event()


def worker_loop():
    print(f"[worker] started (concurrency={WORKER_CONCURRENCY}, "
          f"stale_after={STALE_AFTER_S}s)", flush=True)
    recover_stale()
    while not _stop.is_set():
        try:
            job = _claim_next()
            if job:
                print(f"[worker] claimed {job['kind']} job {job['id'][:8]} "
                      f"(attempt {job['attempt_count']})", flush=True)
                _run_job(job)
                continue
            recover_stale()
        except Exception as e:  # noqa: BLE001
            print(f"[worker] loop error: {e}", flush=True)
        _stop.wait(POLL_INTERVAL_S)


def start_worker() -> threading.Thread:
    t = threading.Thread(target=worker_loop, daemon=True, name="pipeline-worker")
    t.start()
    return t


def stop_worker():
    _stop.set()
