"""Timeline validation tests (spec Step 9)."""
import pytest
from pydantic import ValidationError

from app.timeline import Timeline, plan_render


def base_timeline(**over):
    tl = {
        "version": 1, "width": 1920, "height": 1080, "fps": 30, "duration": 15,
        "tracks": [
            {"id": "video-1", "type": "video", "clips": [
                {"id": "clip-1", "assetId": "a" * 36, "sourceStart": 0,
                 "sourceEnd": 12, "timelineStart": 2, "timelineEnd": 14,
                 "volume": 1}]},
            {"id": "text-1", "type": "text", "clips": [
                {"id": "title-1", "text": "STROMATION PROJECT ONE",
                 "timelineStart": 0, "timelineEnd": 2, "fontSize": 72,
                 "position": "center"}]},
        ],
    }
    tl.update(over)
    return tl


def test_valid_timeline_parses():
    tl = Timeline(**base_timeline())
    plan = plan_render(tl)
    assert plan.title_text == "STROMATION PROJECT ONE"
    assert plan.source_end == 12


def test_missing_required_fields_rejected():
    bad = base_timeline()
    del bad["width"]
    with pytest.raises(ValidationError):
        Timeline(**bad)


def test_missing_asset_id_rejected():
    bad = base_timeline()
    del bad["tracks"][0]["clips"][0]["assetId"]
    with pytest.raises(ValidationError):
        Timeline(**bad)


def test_negative_source_start_rejected():
    bad = base_timeline()
    bad["tracks"][0]["clips"][0]["sourceStart"] = -1
    with pytest.raises(ValidationError):
        Timeline(**bad)


def test_source_end_before_start_rejected():
    bad = base_timeline()
    bad["tracks"][0]["clips"][0]["sourceStart"] = 10
    bad["tracks"][0]["clips"][0]["sourceEnd"] = 5
    with pytest.raises(ValidationError):
        Timeline(**bad)


def test_timeline_end_before_start_rejected():
    bad = base_timeline()
    bad["tracks"][0]["clips"][0]["timelineStart"] = 14
    bad["tracks"][0]["clips"][0]["timelineEnd"] = 2
    with pytest.raises(ValidationError):
        Timeline(**bad)


def test_text_timeline_end_before_start_rejected():
    bad = base_timeline()
    bad["tracks"][1]["clips"][0]["timelineEnd"] = 0.0
    with pytest.raises(ValidationError):
        Timeline(**bad)


def test_no_video_clip_rejected():
    bad = base_timeline()
    bad["tracks"] = [bad["tracks"][1]]  # text only
    with pytest.raises(ValidationError):
        Timeline(**bad)


def test_multiple_video_clips_rejected_by_v1_planner():
    tl = base_timeline()
    clip2 = dict(tl["tracks"][0]["clips"][0], id="clip-2")
    tl["tracks"][0]["clips"].append(clip2)
    parsed = Timeline(**tl)
    with pytest.raises(ValueError, match="exactly one video clip"):
        plan_render(parsed)


def test_wrong_version_rejected():
    with pytest.raises(ValidationError):
        Timeline(**base_timeline(version=2))
