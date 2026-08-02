"""Milestones 8-15 tests: planner, selector, op engine, multi-clip renderer,
validator, revision agent, conversational (mock provider), preferences."""
import json
import os
import subprocess

import pytest

from app.pipeline.builder import build_timeline, resolve_asset_ids
from app.pipeline.critic import CriticVerdict, RevisionRequest
from app.pipeline.planner import plan_story
from app.pipeline.preferences import (Correction, LocalCorrectionStore,
                                      weight_adjustments)
from app.pipeline.revision import plan_revision_ops
from app.pipeline.schemas import Segment
from app.pipeline.selector import select_segments
from app.pipeline.validator import validate_timeline
from app.renderer import FFMPEG, probe
from app.renderer2 import render_timeline
from app.timeline_ops import (OpError, apply_operations, parse_operations)
from app.pipeline.conversational import CommandPlan, execute_command


# ---------- fixtures ----------
def seg(sid, asset, s, e, uses, motion, focus=0.8, problems=None, shot="medium"):
    return Segment(segmentId=sid, assetId=asset, sourceStart=s, sourceEnd=e,
                   storyUses=uses, motionIntensity=motion, focusScore=focus,
                   exposureScore=0.8, stabilityScore=0.7, audioScore=0.5,
                   semanticRelevance=0.6, shotType=shot,
                   problems=problems or [],
                   searchText=f"{sid} {' '.join(uses)}")


@pytest.fixture()
def catalog():
    return [
        seg("seg_a_000", "assetA", 0.0, 4.0, ["hook", "peak"], 0.9),
        seg("seg_a_001", "assetA", 4.0, 7.0, ["location", "broll"], 0.1, shot="wide"),
        seg("seg_a_002", "assetA", 7.0, 10.0, ["early_effort"], 0.4),
        seg("seg_a_003", "assetA", 10.0, 16.0, ["build", "peak"], 0.8, shot="close"),
        seg("seg_b_000", "assetB", 0.0, 6.0, ["completion", "reflection"], 0.3),
        seg("seg_b_001", "assetB", 6.0, 9.0, ["build"], 0.7, shot="wide"),
        seg("seg_bad", "assetA", 16.0, 17.0, ["peak"], 0.9,
            problems=["mostly_black"]),
    ]


@pytest.fixture(scope="module")
def two_sources(tmp_path_factory):
    """Two real distinguishable clips (17s + 10s)."""
    d = tmp_path_factory.mktemp("srcs")
    a = str(d / "a.mp4"); b = str(d / "b.mp4")
    subprocess.run([FFMPEG, "-y", "-loglevel", "error",
                    "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30:duration=17",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=17",
                    "-c:v", "libx264", "-c:a", "aac", "-shortest", a],
                   check=True, timeout=120)
    subprocess.run([FFMPEG, "-y", "-loglevel", "error",
                    "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=10",
                    "-c:v", "libx264", "-an", b], check=True, timeout=120)
    return {"assetA": a, "assetB": b}


# ---------- planner ----------
def test_planner_produces_valid_blueprint(catalog):
    bp = plan_story("epic morning workout", catalog, target_duration=20)
    assert bp.templateId == "fitness_v1"
    assert len(bp.beats) >= 4
    assert abs(bp.targetDuration - sum(b.targetSeconds for b in bp.beats)) < 0.1
    keys = [b.key for b in bp.beats]
    assert keys.index("hook") < keys.index("peak") < keys.index("completion")


def test_planner_drops_optional_beats_when_footage_scarce(catalog):
    bp = plan_story("short", catalog[:3], target_duration=15)
    # with only 3 segments the optional beats are dropped; required ones stay
    assert all(b.required for b in bp.beats)
    assert len(bp.beats) < 7


# ---------- selector ----------
def test_selector_fills_beats_with_reasons(catalog):
    bp = plan_story("workout", catalog, target_duration=18)
    sel = select_segments(bp, catalog)
    filled = [b for b in sel.beats if b.chosen]
    assert len(filled) >= 4
    hook = next(b for b in sel.beats if b.beatKey == "hook")
    assert hook.chosen == "seg_a_000"          # only high-motion hook candidate
    assert "total=" in hook.reason
    assert any(c.excluded for b in sel.beats for c in b.candidates)


def test_selector_hard_constraints(catalog):
    bp = plan_story("workout", catalog, target_duration=18)
    sel = select_segments(bp, catalog)
    # the mostly_black segment must never be chosen and must carry a reason
    assert all(b.chosen != "seg_bad" for b in sel.beats)
    reasons = [c.excludeReason for b in sel.beats for c in b.candidates
               if c.segmentId == "seg_bad"]
    assert any("unusable" in r for r in reasons)
    # no overlapping reuse of the same source range
    used = [(b.chosen, b.sourceStart, b.sourceEnd) for b in sel.beats if b.chosen]
    by_asset = {}
    for sid, s, e in used:
        aid = next(x.assetId for x in catalog if x.segmentId == sid)
        for (s2, e2) in by_asset.get(aid, []):
            assert not (s < e2 and e > s2), "source range reused"
        by_asset.setdefault(aid, []).append((s, e))


# ---------- op engine ----------
def make_tl(catalog):
    bp = plan_story("workout", catalog, target_duration=16)
    sel = select_segments(bp, catalog)
    return resolve_asset_ids(build_timeline(bp, sel, title_text="TEST"), catalog)


def test_ops_insert_trim_delete_speed(catalog):
    tl = make_tl(catalog)
    n0 = len([c for t in tl["tracks"] if t["type"] == "video" for c in t["clips"]])
    ops = parse_operations([
        {"op": "insert_clip", "trackId": "video-1", "index": 0,
         "clipId": "x1", "assetId": "assetB", "sourceStart": 0, "sourceEnd": 2},
        {"op": "change_speed", "clipId": "x1", "speed": 2.0},
        {"op": "add_caption", "text": "let's go", "timelineStart": 1,
         "timelineEnd": 3},
    ])
    res = apply_operations(tl, ops, actor="user")
    assert not res.rejected
    clips = [c for t in res.timeline["tracks"] if t["type"] == "video"
             for c in t["clips"]]
    assert len(clips) == n0 + 1
    x1 = next(c for c in clips if c["id"] == "x1")
    assert x1["speed"] == 2.0
    # 2s source at 2x = 1s on the timeline
    assert abs((x1["timelineEnd"] - x1["timelineStart"]) - 1.0) < 0.01
    # positions recomputed sequentially after title (2s)
    assert abs(clips[0]["timelineStart"] - 2.0) < 0.01


def test_ops_reject_invalid(catalog):
    tl = make_tl(catalog)
    with pytest.raises(OpError):
        parse_operations([{"op": "run_ffmpeg", "cmd": "rm -rf /"}])
    res = apply_operations(tl, parse_operations([
        {"op": "trim_clip", "clipId": "clip-hook", "sourceStart": 5,
         "sourceEnd": 2}]), actor="user")
    assert res.rejected and "invalid trim" in res.rejected[0]["error"]


def test_protected_range_enforced(catalog):
    tl = make_tl(catalog)
    clips = [c for t in tl["tracks"] if t["type"] == "video" for c in t["clips"]]
    last = clips[-1]
    protected = [(last["timelineStart"], last["timelineEnd"])]
    ops = parse_operations([{"op": "delete_clip", "clipId": last["id"]}])
    with pytest.raises(OpError, match="protected"):
        apply_operations(tl, ops, actor="user", protected=protected)


# ---------- multi-clip render (real ffmpeg) ----------
def test_multiclip_render_preview(catalog, two_sources, tmp_path):
    tl = make_tl(catalog)
    out = str(tmp_path / "preview.mp4")
    r = render_timeline(tl, two_sources, out, profile="preview")
    assert os.path.exists(out) and r["size_bytes"] > 0
    assert abs(r["duration"] - tl["duration"]) < 1.2
    info = probe(out)
    assert info.has_audio


def test_render_with_music_and_captions(catalog, two_sources, tmp_path):
    tl = make_tl(catalog)
    tl["tracks"][1]["clips"].append({
        "id": "cap-1", "role": "caption", "text": "halfway there",
        "timelineStart": 3, "timelineEnd": 5, "fontSize": 48,
        "position": "bottom"})
    tl.setdefault("music", {})["duckDb"] = -12
    music = str(tmp_path / "music.wav")
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi",
                    "-i", "sine=frequency=220:duration=5", music],
                   check=True, timeout=60)
    out = str(tmp_path / "preview_music.mp4")
    r = render_timeline(tl, two_sources, out, profile="preview",
                        music_path=music)
    assert r["size_bytes"] > 0


# ---------- validator ----------
def test_validator_catches_errors(catalog, two_sources):
    tl = make_tl(catalog)
    good = validate_timeline(tl, catalog, target_duration=16,
                             asset_durations={"assetA": 17.0, "assetB": 10.0})
    assert good.ok
    # inject a beyond-source clip + unusable overlap
    bad = json.loads(json.dumps(tl))
    bad["tracks"][0]["clips"][0]["sourceEnd"] = 99.0
    bad["tracks"][0]["clips"].append({
        "id": "black", "assetId": "assetA", "sourceStart": 16.2,
        "sourceEnd": 16.9, "timelineStart": 90, "timelineEnd": 90.7,
        "volume": 1, "speed": 1})
    rep = validate_timeline(bad, catalog, target_duration=16,
                            asset_durations={"assetA": 17.0, "assetB": 10.0})
    codes = {i.code for i in rep.issues}
    assert not rep.ok
    assert "beyond_source" in codes and "unusable_footage" in codes


# ---------- revision agent ----------
def test_revision_converts_critic_requests_to_ops(catalog):
    tl = make_tl(catalog)
    bp = plan_story("workout", catalog, target_duration=16)
    sel = select_segments(bp, catalog)
    first = [c for t in tl["tracks"] if t["type"] == "video"
             for c in t["clips"]][0]
    verdict = CriticVerdict(
        hookStrong=False, storyUnderstandable=True, intensityBuilds=True,
        enoughShotVariety=False, importantActionsVisible=True,
        repetitiveClips=True, awkwardCuts=False, dialogueIntact=True,
        naturalSoundEffective=True, musicBalanced=True, endingPayoff=True,
        overallScore=0.55, summary="weak hook",
        revisionRequests=[RevisionRequest(
            timelineStart=first["timelineStart"], timelineEnd=first["timelineEnd"],
            issue="weak hook, repetitive", suggestion="replace with a stronger shot",
            severity="major")])
    ops = plan_revision_ops(verdict, tl, sel, catalog)
    assert ops, "revision agent produced no ops"
    parsed = parse_operations(ops)
    res = apply_operations(tl, parsed, actor="revision_agent")
    assert res.applied and not res.rejected


# ---------- conversational (mock provider) ----------
class MockCmdProvider:
    def __init__(self, plan):
        self._plan = plan

    def plan(self, command, timeline):
        return self._plan


def test_conversational_flow_with_protected_range(catalog):
    tl = make_tl(catalog)
    clips = [c for t in tl["tracks"] if t["type"] == "video" for c in t["clips"]]
    # "make the opening faster, keep everything after 8s unchanged"
    plan = CommandPlan(intent="speed up opening",
                       protectedRanges=[[8.0, tl["duration"]]],
                       operations=[{"op": "change_speed",
                                    "clipId": clips[0]["id"], "speed": 1.5}])
    out = execute_command("make the opening faster", tl, MockCmdProvider(plan))
    assert out["status"] == "proposed"
    # a plan that would violate the protected range must be rejected
    bad = CommandPlan(intent="delete ending",
                      protectedRanges=[[clips[-1]["timelineStart"],
                                        tl["duration"]]],
                      operations=[{"op": "delete_clip",
                                   "clipId": clips[-1]["id"]}])
    out2 = execute_command("delete the last clip", tl, MockCmdProvider(bad))
    assert out2["status"] == "rejected" and "protected" in out2["error"]


# ---------- preferences ----------
def test_preference_recording_and_adjustment(tmp_path):
    store = LocalCorrectionStore(str(tmp_path / "corr.jsonl"))
    for _ in range(3):
        store.record(Correction(
            projectId="p1", originalTimelineVersion=1,
            requestedChange="too repetitive, use different shots",
            appliedOperations=[{"op": "replace_clip", "clipId": "c",
                                "comment": "variety swap"}],
            accepted=True))
    adj = weight_adjustments(store.load())
    assert adj.get("variety", 0) > 0
    assert all(abs(v) <= 0.05 for v in adj.values())
