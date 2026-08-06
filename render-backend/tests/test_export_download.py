"""Slice 4 — export + download contract: signed downloads (ownership + completion),
artifacts.output metadata, duplicate-export idempotency, exact-version binding, and
the shared bridged / M1-M6 export handler branch.
"""
import os
from uuid import uuid4

import pytest

os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "fake-service-key")
os.environ["WORKER_ENABLED"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from app import autoedit_bridge, jobs, supa  # noqa: E402
from app.main import app  # noqa: E402
from tests.fake_supa import FakeSupabase, install  # noqa: E402


def _auth(t):
    return {"Authorization": f"Bearer {t}"}


def _setup(monkeypatch, tmp_path):
    from app import main
    main._rate.clear()
    fake = FakeSupabase()
    install(monkeypatch, fake)
    uid, token = fake.add_user("owner@example.com")
    project = fake.add_project(uid, status="draft_ready")
    asset_id = str(uuid4())
    key = f"users/{uid}/projects/{project['id']}/raw/clip.mp4"
    fake.insert("media_assets", {"id": asset_id, "project_id": project["id"],
                                 "user_id": uid, "filename": "clip.mp4",
                                 "storage_path": key, "duration_seconds": 8.0})
    fake.storage[f"raw-footage/{key}"] = b"VIDEO"
    tl = {"version": 1, "width": 1080, "height": 1920, "fps": 30, "duration": 8,
          "tracks": [{"id": "v", "type": "video", "clips": [
              {"id": "clip-a", "assetId": asset_id, "sourceStart": 0, "sourceEnd": 8,
               "timelineStart": 0, "timelineEnd": 8, "speed": 1, "volume": 1}]}]}
    tl_row = fake.insert("timelines", {"project_id": project["id"], "user_id": uid,
                                       "version": 1, "timeline_json": tl,
                                       "lineage": "autonomous_revised"}).json()[0]
    preview = tmp_path / "p.mp4"
    preview.write_bytes(b"P")
    cand = autoedit_bridge.bridge_from_autoedit(
        project, tl_row, str(preview), insert=jobs._insert, db_select=supa.db_select,
        upload_export=jobs._upload_export, now=jobs._now)
    return fake, uid, token, project, cand


def _completed_export(fake, uid, project, job_id=None, **artifacts):
    job_id = job_id or str(uuid4())
    out = f"users/{uid}/projects/{project['id']}/renders/{job_id}-editor-v1.mp4"
    art = {"output": out, "size_bytes": 1500000, "duration": 8.0,
           "width": 1080, "height": 1920, **artifacts}
    fake.insert("pipeline_jobs", {"id": job_id, "project_id": project["id"],
                                  "user_id": uid, "kind": "final_render",
                                  "status": "completed",
                                  "params": {"editor_document_id": "d"},
                                  "artifacts": art})
    fake.storage[f"exports/{out}"] = b"MP4"
    return job_id, out


# ---------------- signed download (ownership + completion) ----------------
def test_signed_download_success(monkeypatch, tmp_path):
    fake, uid, token, project, _ = _setup(monkeypatch, tmp_path)
    job_id, _ = _completed_export(fake, uid, project)
    client = TestClient(app, raise_server_exceptions=False)
    r = client.post(f"/projects/{project['id']}/editor/renders/{job_id}/sign", headers=_auth(token))
    assert r.status_code == 200, r.text
    assert r.json()["url"].startswith("https://fake.supabase.co/storage/v1")
    assert "service" not in r.json()["url"].lower()


def test_signed_download_foreign_rejected(monkeypatch, tmp_path):
    fake, uid, _, project, _ = _setup(monkeypatch, tmp_path)
    job_id, _ = _completed_export(fake, uid, project)
    _, intruder = fake.add_user("intruder@example.com")
    client = TestClient(app, raise_server_exceptions=False)
    r = client.post(f"/projects/{project['id']}/editor/renders/{job_id}/sign", headers=_auth(intruder))
    assert r.status_code in (403, 404)


def test_signed_download_unavailable_before_completion(monkeypatch, tmp_path):
    fake, uid, token, project, _ = _setup(monkeypatch, tmp_path)
    job_id = str(uuid4())
    fake.insert("pipeline_jobs", {"id": job_id, "project_id": project["id"], "user_id": uid,
                                  "kind": "final_render", "status": "processing",
                                  "params": {"editor_document_id": "d"}, "artifacts": {}})
    client = TestClient(app, raise_server_exceptions=False)
    r = client.post(f"/projects/{project['id']}/editor/renders/{job_id}/sign", headers=_auth(token))
    assert r.status_code == 409


def test_analysis_job_is_not_signable_as_export(monkeypatch, tmp_path):
    fake, uid, token, project, _ = _setup(monkeypatch, tmp_path)
    job_id = str(uuid4())
    fake.insert("pipeline_jobs", {"id": job_id, "project_id": project["id"], "user_id": uid,
                                  "kind": "analysis", "status": "completed",
                                  "params": {}, "artifacts": {"assets_analyzed": 1}})
    client = TestClient(app, raise_server_exceptions=False)
    r = client.post(f"/projects/{project['id']}/editor/renders/{job_id}/sign", headers=_auth(token))
    assert r.status_code == 409   # no artifacts.output under the renders/ prefix


# ---------------- artifacts.output metadata ----------------
def test_artifacts_output_present(monkeypatch, tmp_path):
    fake, uid, _, project, _ = _setup(monkeypatch, tmp_path)
    job_id, out = _completed_export(fake, uid, project)
    row = fake.select("pipeline_jobs", f"id=eq.{job_id}")[0]
    assert row["artifacts"]["output"] == out
    assert row["artifacts"]["width"] == 1080 and row["artifacts"]["size_bytes"] == 1500000


# ---------------- export enqueue: version binding + idempotency ----------------
def _open_doc(client, token, project, cand):
    r = client.post(f"/projects/{project['id']}/editor/start",
                    headers=_auth(token), json={"candidateRunId": cand["id"]})
    assert r.status_code == 200, r.text
    return r.json()


def test_export_binds_exact_document_version(monkeypatch, tmp_path):
    fake, _, token, project, cand = _setup(monkeypatch, tmp_path)
    client = TestClient(app, raise_server_exceptions=False)
    doc = _open_doc(client, token, project, cand)
    r = client.post(f"/projects/{project['id']}/editor/render",
                    headers=_auth(token), json={"documentId": doc["id"]})
    assert r.status_code == 200, r.text
    job = r.json()
    assert job["params"]["editor_document_id"] == doc["id"]
    assert job["params"]["editor_document_version"] == doc["version"]
    assert job["kind"] == "final_render"


def test_duplicate_export_click_creates_one_active_job(monkeypatch, tmp_path):
    fake, _, token, project, cand = _setup(monkeypatch, tmp_path)
    client = TestClient(app, raise_server_exceptions=False)
    doc = _open_doc(client, token, project, cand)
    first = client.post(f"/projects/{project['id']}/editor/render",
                        headers=_auth(token), json={"documentId": doc["id"]})
    second = client.post(f"/projects/{project['id']}/editor/render",
                         headers=_auth(token), json={"documentId": doc["id"]})
    assert first.status_code == 200 and second.status_code == 200   # no 500 on dup
    assert first.json()["id"] == second.json()["id"]               # idempotent
    active = [j for j in fake.tables["pipeline_jobs"]
              if j["kind"] == "final_render" and j["status"] in ("queued", "processing")]
    assert len(active) == 1


# ---------------- one export handler; correct bridged / M1-M6 branch ----------------
def test_bridged_export_takes_bridged_path(monkeypatch, tmp_path):
    fake, uid, token, project, cand = _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(jobs, "_render_bridged_editor",
                        lambda *a, **k: {"marker": "bridged"})
    client = TestClient(app, raise_server_exceptions=False)
    doc = _open_doc(client, token, project, cand)
    job = {"id": str(uuid4()), "project_id": project["id"],
           "params": {"editor_document_id": doc["id"], "editor_document_version": doc["version"]}}
    result = jobs.handle_product_editor_render(job, project, str(tmp_path), jobs.JobContext(job))
    assert result == {"marker": "bridged"}


def test_m6_export_does_not_take_bridged_path(monkeypatch, tmp_path):
    fake, uid, token, project, _ = _setup(monkeypatch, tmp_path)

    def boom(*a, **k):
        raise AssertionError("bridged path used")
    monkeypatch.setattr(jobs, "_render_bridged_editor", boom)

    m6 = fake.insert("candidate_runs", {
        "batch_id": str(uuid4()), "project_id": project["id"], "user_id": uid,
        "generation_kind": "initial", "candidate_key": "m6", "candidate_index": 1,
        "music_sound_run_id": "m", "audio_mix_run_id": "a", "graphics_run_id": "g",
        "caption_run_id": "c", "color_run_id": "co", "manifest": {"fabricatedFootage": False},
        "preview_storage_bucket": "exports",
        "preview_storage_path": f"users/{uid}/projects/{project['id']}/editorial-intelligence/x.mp4",
    }).json()[0]
    doc = fake.insert("editor_documents", {
        "project_id": project["id"], "user_id": uid, "candidate_run_id": m6["id"],
        "version": 1, "document": {"width": 1080, "height": 1920, "fps": 30, "duration": 8,
            "attribution": [], "tracks": [
                {"type": "picture", "items": [{"id": "c", "assetId": "x"}]},
                {"type": "music", "items": [{"id": "music-main", "gainDb": -8}]}]}}).json()[0]
    job = {"id": str(uuid4()), "project_id": project["id"],
           "params": {"editor_document_id": doc["id"], "editor_document_version": 1}}
    with pytest.raises(Exception) as exc:   # fails later in the M4/M5 path, NOT the bridged one
        jobs.handle_product_editor_render(job, project, str(tmp_path), jobs.JobContext(job))
    assert "bridged path used" not in str(exc.value)


def test_every_final_render_return_records_its_provider():
    """A completed export whose artifacts lack export_provider gets signed
    against the WRONG storage when the deployment default changes — a real
    172 MB export was stranded in S3 while the sign route looked in Supabase.
    Every final_render return path must self-describe."""
    import inspect
    import re
    from app import jobs
    src = inspect.getsource(jobs)
    returns = re.findall(r'return \{"output": path[^}]+', src)
    assert len(returns) >= 3
    for r in returns:
        assert "export_provider" in r, f"return lacks export_provider: {r[:80]}"


def test_long_renders_heartbeat_so_the_watchdog_cannot_kill_them():
    """A 10-minute encode is ONE opaque step. Without a periodic tick the job
    emits no heartbeat, the UI freezes on a stale percent (a real user read
    40% as 'stuck'), and recover_stale() can requeue a healthy render."""
    import inspect
    from app import jobs, renderer2

    # every final render passes a tick: check the 8 lines following each call
    lines = inspect.getsource(jobs).split("\n")
    starts = [i for i, ln in enumerate(lines) if "render_timeline(" in ln]
    assert starts, "no render_timeline calls found"
    for i in starts:
        block = "\n".join(lines[i:i + 8])
        assert "tick=" in block, f"render without heartbeat tick near: {lines[i].strip()}"

    # and the runner actually invokes it on a schedule
    src = inspect.getsource(renderer2._run_interruptible)
    assert "tick_every" in src and "tick(" in src


def test_tick_fires_during_a_long_run(monkeypatch):
    from app import renderer2
    ticks = []
    # a command that outlives several tick intervals
    rc, _ = renderer2._run_interruptible(
        ["sleep", "1"], timeout=30, tick=lambda s: ticks.append(s),
        tick_every=0.2)
    assert rc == 0
    assert len(ticks) >= 3          # ~0.2s cadence across ~1s
