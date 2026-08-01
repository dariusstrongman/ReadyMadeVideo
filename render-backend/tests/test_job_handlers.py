"""End-to-end handler tests: the REAL handle_analysis / handle_autoedit /
handle_final_render running through _run_job against the fake store, with real
FFmpeg output and no AI providers (graceful degradation path)."""
import os
import subprocess
import uuid

import pytest

os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "fake-service-key")

from app import jobs  # noqa: E402
from app.renderer import FFMPEG  # noqa: E402
from tests.fake_supa import FakeSupabase, install  # noqa: E402


@pytest.fixture(scope="module")
def clip_bytes(tmp_path_factory):
    d = tmp_path_factory.mktemp("handler-src")
    p = str(d / "clip.mp4")
    subprocess.run([FFMPEG, "-y", "-loglevel", "error",
                    "-filter_complex",
                    "testsrc=size=640x360:rate=30:duration=6[a];"
                    "testsrc2=size=640x360:rate=30:duration=6[b];"
                    "[a][b]concat=n=2:v=1:a=0[v];sine=frequency=440:duration=12[aud]",
                    "-map", "[v]", "-map", "[aud]",
                    "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", p],
                   check=True, timeout=120)
    return open(p, "rb").read()


@pytest.fixture()
def env(monkeypatch, clip_bytes):
    fake = FakeSupabase()
    install(monkeypatch, fake)
    for k in ("GEMINI_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    uid, _ = fake.add_user("handler@example.com")
    project = fake.add_project(uid, "Handler Test", status="ready")
    asset_id = str(uuid.uuid4())
    path = f"users/{uid}/projects/{project['id']}/raw/{asset_id}/clip.mp4"
    fake.storage[f"raw-footage/{path}"] = clip_bytes
    fake.insert("media_assets", {
        "id": asset_id, "project_id": project["id"], "user_id": uid,
        "filename": "clip.mp4", "storage_path": path,
        "mime_type": "video/mp4", "size_bytes": len(clip_bytes),
        "duration_seconds": 12.0})
    return fake, project, asset_id


def run_kind(fake, project, kind, params=None):
    j = jobs.enqueue_job(project["id"], project["user_id"], kind, params or {})
    claimed = jobs._claim_next()
    jobs._run_job(claimed)
    return fake.select("pipeline_jobs", f"id=eq.{j['id']}")[0]


def test_handle_analysis_real_pipeline(env):
    fake, project, asset_id = env
    row = run_kind(fake, project, "analysis")
    assert row["status"] == "completed", row["error_message"]
    # artifacts + segments landed; providers degraded gracefully
    kinds = {a["kind"] for a in fake.tables["asset_analysis"]}
    assert {"probe", "proxy", "scenes", "mechanical", "audio", "motion",
            "catalog"} <= kinds
    assert len(fake.tables["segments"]) >= 2
    assert fake.select("projects", f"id=eq.{project['id']}")[0]["status"] == "ready"
    assert row["artifacts"]["telemetry_status"]["complete"] is True


def test_handle_autoedit_then_final_render(env):
    fake, project, asset_id = env
    run_kind(fake, project, "analysis")
    row = run_kind(fake, project, "autoedit",
                   {"brief": "handler test edit", "target_duration": 8,
                    "use_critic": False, "title": "HANDLER"})
    assert row["status"] == "completed", row["error_message"]
    assert row["artifacts"]["previews"], "draft previews must be uploaded"
    assert any(k.startswith("exports/") for k in fake.storage)
    assert fake.tables["edit_runs"] and fake.tables["draft_evaluations"]
    timelines = fake.select("timelines", f"project_id=eq.{project['id']}")
    assert timelines[0]["lineage"] == "autonomous_initial"
    assert timelines[0]["is_immutable"] is True
    ev = fake.tables["draft_evaluations"][0]
    assert ev["beats_requested"] >= ev["beats_filled"] >= 1
    assert fake.select("projects",
                       f"id=eq.{project['id']}")[0]["status"] == "draft_ready"

    final = run_kind(fake, project, "final_render")
    assert final["status"] == "completed", final["error_message"]
    assert final["artifacts"]["output"].endswith(".mp4")
    assert f"exports/{final['artifacts']['output']}" in fake.storage
    assert fake.select("projects",
                       f"id=eq.{project['id']}")[0]["status"] == "completed"


def test_handle_analysis_no_footage_fails(env):
    fake, project, _ = env
    fake.tables["media_assets"].clear()
    row = run_kind(fake, project, "analysis")
    assert row["status"] == "failed"
    assert "no uploaded footage" in row["error_message"]
    assert fake.select("projects",
                       f"id=eq.{project['id']}")[0]["status"] == "analysis_failed"


def test_handle_autoedit_requires_catalog(env):
    fake, project, _ = env
    row = run_kind(fake, project, "autoedit", {"use_critic": False})
    assert row["status"] == "failed"
    assert "run analysis first" in row["error_message"]


def test_final_render_rejects_foreign_asset_timeline(env):
    fake, project, asset_id = env
    tl = {"version": 1, "width": 640, "height": 360, "fps": 30, "duration": 3,
          "tracks": [{"id": "video-1", "type": "video", "clips": [
              {"id": "c1", "assetId": str(uuid.uuid4()), "sourceStart": 0,
               "sourceEnd": 3, "timelineStart": 0, "timelineEnd": 3,
               "volume": 1, "speed": 1}]}]}
    fake.insert("timelines", {"project_id": project["id"],
                              "user_id": project["user_id"], "version": 1,
                              "timeline_json": tl})
    row = run_kind(fake, project, "final_render")
    assert row["status"] == "failed"
    assert "foreign asset" in row["error_message"]


def test_analysis_cancelled_between_assets(env, monkeypatch):
    fake, project, asset_id = env
    j = jobs.enqueue_job(project["id"], project["user_id"], "analysis")
    claimed = jobs._claim_next()
    # request cancellation before the worker reaches the first checkpoint
    jobs.request_cancel(fake.select("pipeline_jobs", f"id=eq.{j['id']}")[0],
                        requested_by="op-cancel")
    jobs._run_job(claimed)
    row = fake.select("pipeline_jobs", f"id=eq.{j['id']}")[0]
    assert row["status"] == "cancelled"
    assert fake.select("projects",
                       f"id=eq.{project['id']}")[0]["status"] == "ready"
    # nothing was uploaded for this cancelled run
    assert not any(k.startswith("exports/") for k in fake.storage)
