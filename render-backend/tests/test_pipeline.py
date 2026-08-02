"""Pipeline tests on a synthetic multi-scene fixture.

Fixture layout (16 s, built with FFmpeg itself):
  scene A 0-4 s   : testsrc moving pattern   (moderate motion, sharp)
  scene B 4-7 s   : near-black solid         (black-frame detection)
  scene C 7-10 s  : static SMPTE bars        (frozen/static detection)
  scene D 10-16 s : testsrc2 moving content  (highest motion)
Audio: 440 Hz tone for 0-8 s, silence 8-16 s (silence detection).
"""
import json
import os
import subprocess

import pytest

from app.pipeline.media import probe_stage
from app.pipeline.runner import ALL_STAGES, LocalStore, run_pipeline
from app.renderer import FFMPEG


@pytest.fixture(scope="module")
def fixture_video(tmp_path_factory):
    d = tmp_path_factory.mktemp("pipe")
    out = str(d / "fixture.mp4")
    fc = (
        "testsrc=size=640x360:rate=30:duration=4[a];"
        "color=c=0x050505:size=640x360:rate=30:duration=3[b];"
        "smptebars=size=640x360:rate=30:duration=3[c];"
        "testsrc2=size=640x360:rate=30:duration=6[d];"
        "[a][b][c][d]concat=n=4:v=1:a=0[v];"
        "sine=frequency=440:duration=8[t];"
        "anullsrc=channel_layout=mono:sample_rate=44100,atrim=duration=8[s];"
        "[t][s]concat=n=2:v=0:a=1[aud]"
    )
    subprocess.run([FFMPEG, "-y", "-loglevel", "error",
                    "-filter_complex", fc, "-map", "[v]", "-map", "[aud]",
                    "-c:v", "libx264", "-preset", "veryfast",
                    "-c:a", "aac", out], check=True, timeout=180)
    return out


@pytest.fixture(scope="module")
def artifacts(fixture_video, tmp_path_factory):
    """Run the full local pipeline once (no cloud, no AI keys)."""
    out = str(tmp_path_factory.mktemp("artifacts"))
    for k in ("OPENAI_API_KEY", "GEMINI_API_KEY"):
        os.environ.pop(k, None)   # force provider-less path in tests
    store = LocalStore(out)
    state = run_pipeline(fixture_video, store, asset_id="fixture-asset",
                         stages=ALL_STAGES, workdir=out)
    return out, state


def test_probe_metadata(fixture_video):
    p = probe_stage(fixture_video)
    assert 15.5 <= p.duration <= 16.5
    assert p.video.width == 640 and p.video.height == 360
    assert p.has_audio


def test_scene_detection_finds_cuts(artifacts):
    _, state = artifacts
    scenes = state["scenes"].scenes
    assert len(scenes) >= 3, f"expected >=3 scenes, got {len(scenes)}"
    # boundaries near 4s, 7s, 10s (±0.7s)
    bounds = [s.start for s in scenes[1:]]
    for expected in (4.0, 7.0, 10.0):
        assert any(abs(b - expected) < 0.7 for b in bounds), \
            f"no boundary near {expected}s in {bounds}"


def _scene_at(mech, t):
    return next(s for s in mech.scenes if s.start <= t < s.end)


def test_black_scene_detected(artifacts):
    _, state = artifacts
    dark = _scene_at(state["mechanical"], 5.0)
    assert dark.black_frame_fraction > 0.8
    assert "mostly_black" in next(
        s.problems for s in state["segments"] if s.sourceStart <= 5 < s.sourceEnd)


def test_static_scene_detected_as_frozen(artifacts):
    _, state = artifacts
    static = _scene_at(state["mechanical"], 8.5)
    assert static.frozen_frame_fraction > 0.8
    assert static.motion_energy_mean < 1.0


def test_moving_scene_has_higher_motion(artifacts):
    _, state = artifacts
    motion = state["motion"]
    static = next(s for s in motion.scenes if s.start <= 8.5 < s.end)
    moving = next(s for s in motion.scenes if s.start <= 12.0 < s.end)
    assert moving.motion_intensity > static.motion_intensity + 0.2


def test_silence_detected(artifacts):
    _, state = artifacts
    audio = state["audio"]
    assert audio.has_audio
    assert any(r.start >= 7.0 and (r.end - r.start) >= 3 for r in audio.silence_ranges), \
        f"expected long silence after 8s, got {audio.silence_ranges}"


def test_catalog_segments_written(artifacts):
    out, state = artifacts
    segs = state["segments"]
    assert len(segs) >= 3
    assert all(s.sourceEnd > s.sourceStart for s in segs)
    assert all(s.segmentId.startswith("seg_") for s in segs)
    # persisted artifact files exist and are valid JSON (inspectability)
    for kind in ("probe", "scenes", "mechanical", "audio", "motion", "catalog"):
        rec = json.load(open(os.path.join(out, f"{kind}.json"), encoding="utf-8"))
        assert rec["kind"] == kind
    saved = json.load(open(os.path.join(out, "segments.json"), encoding="utf-8"))
    assert len(saved) == len(segs)


def test_providerless_stages_fail_gracefully(artifacts):
    out, _ = artifacts
    for kind in ("transcript", "semantic"):
        rec = json.load(open(os.path.join(out, f"{kind}.json"), encoding="utf-8"))
        assert rec["status"] == "failed"
        assert rec["error_message"]


def test_pipeline_is_resumable(artifacts, fixture_video):
    """Second run must reuse cached artifacts (no recompute)."""
    out, _ = artifacts
    store = LocalStore(out)
    import time
    t0 = time.time()
    run_pipeline(fixture_video, store, asset_id="fixture-asset",
                 stages=["probe", "scenes", "mechanical", "audio", "motion"],
                 workdir=out)
    assert time.time() - t0 < 3.0, "cached rerun should be near-instant"
