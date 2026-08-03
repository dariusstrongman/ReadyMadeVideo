"""Real FFmpeg render tests (spec Step 9). Requires ffmpeg/ffprobe on PATH.

Fixtures are generated with ffmpeg itself (testsrc + sine), so the suite is
self-contained and renders REAL video — no pre-rendered sample files.
"""
import glob
import os
import subprocess
import tempfile

import pytest

from app.renderer import FFMPEG, RenderError, probe, render
from app.timeline import RenderPlan


@pytest.fixture(scope="module")
def fixtures(tmp_path_factory):
    d = tmp_path_factory.mktemp("media")
    with_audio = str(d / "with_audio.mp4")
    no_audio = str(d / "no_audio.mp4")
    corrupt = str(d / "corrupt.mp4")
    subprocess.run([FFMPEG, "-y", "-loglevel", "error",
                    "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30:duration=6",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
                    "-c:v", "libx264", "-c:a", "aac", "-shortest", with_audio],
                   check=True, timeout=120)
    subprocess.run([FFMPEG, "-y", "-loglevel", "error",
                    "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30:duration=6",
                    "-c:v", "libx264", "-an", no_audio], check=True, timeout=120)
    with open(corrupt, "wb") as f:
        f.write(b"this is not a video file" * 1000)
    return {"with_audio": with_audio, "no_audio": no_audio, "corrupt": corrupt}


def make_plan(**over):
    base = dict(width=1280, height=720, fps=30, asset_id="x" * 36,
                source_start=1.0, source_end=4.0,
                title_text="TEST RENDER", title_duration=1.0,
                title_font_size=64, title_position="center")
    base.update(over)
    return RenderPlan(**base)


def test_render_with_audio(fixtures, tmp_path):
    dst = str(tmp_path / "out.mp4")
    result = render(make_plan(), fixtures["with_audio"], dst)
    assert os.path.exists(dst) and result.size_bytes > 0
    # 1s title + 3s trim = ~4s
    assert 3.5 <= result.duration_seconds <= 4.6
    assert (result.width, result.height) == (1280, 720)
    info = probe(dst)
    assert info.has_audio


def test_render_without_audio_source(fixtures, tmp_path):
    dst = str(tmp_path / "out.mp4")
    result = render(make_plan(), fixtures["no_audio"], dst)
    assert result.duration_seconds >= 3.5
    assert probe(dst).has_audio  # silence track injected -> valid AAC stream


def test_render_without_title(fixtures, tmp_path):
    dst = str(tmp_path / "out.mp4")
    result = render(make_plan(title_text=None, title_duration=0), fixtures["with_audio"], dst)
    assert 2.5 <= result.duration_seconds <= 3.5


def test_corrupt_source_fails_cleanly(fixtures, tmp_path):
    dst = str(tmp_path / "out.mp4")
    with pytest.raises(RenderError):
        render(make_plan(), fixtures["corrupt"], dst)
    assert not os.path.exists(dst) or os.path.getsize(dst) == 0


def test_trim_beyond_source_fails_cleanly(fixtures, tmp_path):
    dst = str(tmp_path / "out.mp4")
    with pytest.raises(RenderError, match="outside"):
        render(make_plan(source_start=50, source_end=60), fixtures["with_audio"], dst)


def test_job_worker_cleans_temp_files(monkeypatch, fixtures):
    """_run_render_job must remove its temp dir even when the render fails."""
    monkeypatch.setenv("SUPABASE_URL", "https://example.invalid")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test")
    import importlib
    from app import supa as supa_mod
    importlib.reload(supa_mod)
    from app import main as main_mod
    importlib.reload(main_mod)

    calls = {"updates": []}
    monkeypatch.setattr(main_mod.supa, "db_update",
                        lambda t, f, p: calls["updates"].append(p))
    monkeypatch.setattr(main_mod.supa, "db_select",
                        lambda t, f, s="*": [{"user_id": "u", "project_id": "p"}])
    monkeypatch.setattr(main_mod.supa, "storage_download",
                        lambda b, p, d: open(d, "wb").write(b"junk"))

    before = set(glob.glob(os.path.join(tempfile.gettempdir(), "stromation-render-*")))
    project = {"id": "p", "user_id": "u"}
    asset = {"id": "a", "user_id": "u", "project_id": "p", "filename": "x.mp4",
             "storage_provider": "supabase",
             "storage_path": "users/u/projects/p/raw/x.mp4"}
    main_mod._run_render_job("deadbeef-0000", make_plan().model_dump(), asset, project)
    after = set(glob.glob(os.path.join(tempfile.gettempdir(), "stromation-render-*")))
    assert after <= before, "temp dir leaked"
    assert any(u.get("status") == "failed" for u in calls["updates"]), \
        "corrupt download must mark job failed, not crash"
