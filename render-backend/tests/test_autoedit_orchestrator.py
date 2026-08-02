"""Direct tests for the autoedit orchestrator: full run with a mocked critic
(no network) and REAL FFmpeg renders, plus failure and cancellation paths."""
import json
import os
import subprocess

import pytest

from app.pipeline import autoedit as ae
from app.pipeline.critic import CriticVerdict, RevisionRequest
from app.pipeline.schemas import Segment
from app.renderer import FFMPEG


def seg(sid, asset, s, e, uses, motion, shot="medium"):
    return Segment(segmentId=sid, assetId=asset, sourceStart=s, sourceEnd=e,
                   storyUses=uses, motionIntensity=motion, focusScore=0.8,
                   exposureScore=0.8, stabilityScore=0.7, audioScore=0.5,
                   semanticRelevance=0.6, shotType=shot, searchText=sid)


@pytest.fixture(scope="module")
def source(tmp_path_factory):
    d = tmp_path_factory.mktemp("ae-src")
    a = str(d / "clip.mp4")
    subprocess.run([FFMPEG, "-y", "-loglevel", "error",
                    "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30:duration=16",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=16",
                    "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac",
                    "-shortest", a], check=True, timeout=120)
    return a


@pytest.fixture()
def catalog():
    return [
        seg("s0", "clip", 0, 4, ["hook", "peak"], 0.9),
        seg("s1", "clip", 4, 7, ["location"], 0.1, shot="wide"),
        seg("s2", "clip", 7, 10, ["early_effort"], 0.4),
        seg("s3", "clip", 10, 13, ["build"], 0.8, shot="close"),
        seg("s4", "clip", 13, 16, ["completion", "reflection"], 0.3),
    ]


class FakeCritic:
    """Deterministic critic: one revision request on the first pass, clean after."""
    calls = 0

    def critique(self, preview_path, blueprint, timeline):
        FakeCritic.calls += 1
        first = [c for t in timeline["tracks"] if t["type"] == "video"
                 for c in t["clips"]][0]
        reqs = []
        if FakeCritic.calls == 1:
            reqs = [RevisionRequest(
                timelineStart=first["timelineStart"],
                timelineEnd=first["timelineEnd"],
                issue="weak hook, trim the opening", suggestion="trim the head",
                severity="major")]
        return CriticVerdict(
            hookStrong=not reqs, storyUnderstandable=True, intensityBuilds=True,
            enoughShotVariety=True, importantActionsVisible=True,
            repetitiveClips=False, awkwardCuts=False, dialogueIntact=True,
            naturalSoundEffective=True, musicBalanced=True, endingPayoff=True,
            overallScore=0.7, summary="test verdict", revisionRequests=reqs)


def test_full_autoedit_with_mocked_critic(catalog, source, tmp_path,
                                          monkeypatch):
    FakeCritic.calls = 0
    monkeypatch.setattr(ae, "get_critic", lambda: FakeCritic())
    out = str(tmp_path / "run")
    report = ae.autoedit(catalog, {"clip": source}, "test brief", out,
                         target_duration=12, title_text="TEST",
                         use_critic=True, render_final=True)
    assert report["status"] == "completed"
    assert report["revisionPasses"] == 1
    steps = {s["step"] for s in report["steps"]}
    assert {"plan", "select", "preview_v1", "validate_v1", "critic_pass1",
            "preview_v2", "critic_pass2", "final_render"} <= steps
    # artifacts all inspectable
    for f in ("blueprint.json", "selection.json", "timeline_v1.json",
              "timeline_v2.json", "critic_pass1.json",
              "revision_ops_pass1.json", "preview_v1.mp4", "preview_v2.mp4",
              "final.mp4", "run.json"):
        assert os.path.exists(os.path.join(out, f)), f"missing {f}"


def test_autoedit_without_critic(catalog, source, tmp_path):
    out = str(tmp_path / "run")
    report = ae.autoedit(catalog, {"clip": source}, "brief", out,
                         target_duration=10, use_critic=False)
    assert report["status"] == "completed"
    assert report["revisionPasses"] == 0


def test_autoedit_fails_cleanly_with_empty_catalog(source, tmp_path):
    report = ae.autoedit([], {"clip": source}, "brief", str(tmp_path / "r"))
    assert report["status"] == "failed"
    assert "no beats" in report["error"]


def test_autoedit_cancellation_between_stages(catalog, source, tmp_path):
    calls = {"n": 0}

    def cancel_after_plan():
        calls["n"] += 1
        return calls["n"] > 1          # first check passes, second cancels

    out = str(tmp_path / "run")
    report = ae.autoedit(catalog, {"clip": source}, "brief", out,
                         use_critic=False, cancel_check=cancel_after_plan)
    assert report["status"] == "cancelled"
    assert report["cancelled_at_stage"] in ("before_preview", "before_critic",
                                            "before_final")
    # no preview was rendered after the cancellation point
    saved = json.load(open(os.path.join(out, "run.json"), encoding="utf-8"))
    assert saved["status"] == "cancelled"


def test_renderer_cancellation_kills_ffmpeg(catalog, source, tmp_path):
    """Active FFmpeg render terminated by cancel_check; partial output deleted."""
    from app.renderer2 import RenderCancelled, render_timeline
    tl = {"version": 1, "width": 1920, "height": 1080, "fps": 30, "duration": 15,
          "tracks": [{"id": "video-1", "type": "video", "clips": [
              {"id": "c1", "assetId": "clip", "sourceStart": 0, "sourceEnd": 15,
               "timelineStart": 0, "timelineEnd": 15, "volume": 1, "speed": 1}]}]}
    out = str(tmp_path / "cancelled.mp4")
    with pytest.raises(RenderCancelled):
        render_timeline(tl, {"clip": source}, out, profile="final",
                        cancel_check=lambda: True)
    assert not os.path.exists(out), "partial output must be deleted"
