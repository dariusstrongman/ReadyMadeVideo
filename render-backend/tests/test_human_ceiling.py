"""Human-ceiling comparison logic: no provider calls and no rendering."""
import copy

import pytest

from app.human_ceiling import (
    HumanCeilingError,
    build_comparison_report,
    correction_type,
    split_elapsed_seconds,
    validate_scores,
)


def _timeline(timeline_id, lineage, duration, immutable=True):
    return {
        "id": timeline_id,
        "version": {"i": 1, "r": 2, "h": 4}[timeline_id],
        "lineage": lineage,
        "is_immutable": immutable,
        "timeline_json": {
            "duration": duration,
            "tracks": [{"type": "video", "clips": [
                {"id": f"{timeline_id}-1", "assetId": "a1", "volume": 1, "speed": 1},
                {"id": f"{timeline_id}-2", "assetId": "a2", "volume": 0, "speed": 1.2},
            ]}],
        },
    }


def test_operation_categories_cover_manual_workflow():
    expected = {
        "replace_clip": "replacement", "trim_clip": "trim",
        "move_clip": "reorder", "change_volume": "audio",
        "duck_music": "audio", "add_title": "title",
        "insert_clip": "insert", "delete_clip": "delete",
        "change_speed": "speed", "add_caption": "caption",
    }
    assert {op: correction_type({"op": op}) for op in expected} == expected
    with pytest.raises(HumanCeilingError):
        correction_type({"op": "shell"})


def test_elapsed_allocation_preserves_measured_total():
    values = split_elapsed_seconds(10, 3)
    assert len(values) == 3
    assert sum(values) == 10
    assert split_elapsed_seconds(0, 0) == []
    with pytest.raises(HumanCeilingError):
        split_elapsed_seconds(-1, 2)


def test_scorecard_dimensions_are_strict_and_nullable():
    scores = validate_scores({"hook": 5, "natural_audio": None})
    assert scores["hook"] == 5
    assert scores["natural_audio"] is None
    with pytest.raises(HumanCeilingError):
        validate_scores({"hook": 11})
    with pytest.raises(HumanCeilingError):
        validate_scores({"cinematic_magic": 9})


def test_three_way_report_is_deterministic_and_does_not_mutate_evidence():
    session = {
        "id": "s1", "project_id": "p1", "status": "approved",
        "autonomous_initial_timeline_id": "i",
        "autonomous_revised_timeline_id": "r",
        "approved_timeline_id": "h", "human_correction_seconds": 600,
    }
    timelines = [
        _timeline("i", "autonomous_initial", 26.1),
        _timeline("r", "autonomous_revised", 21.6),
        _timeline("h", "human_approved", 18.0),
    ]
    scorecards = [
        {"timeline_id": "i", "overall_rating": 4, "scores": {"hook": 3}},
        {"timeline_id": "r", "overall_rating": 3, "scores": {"hook": 3}},
        {"timeline_id": "h", "overall_rating": 6, "scores": {"hook": 5},
         "publishable": True},
    ]
    corrections = [
        {"operation_index": 2, "correction_type": "trim",
         "applied_operations": [{"op": "trim_clip"}], "elapsed_seconds": 60},
        {"operation_index": 1, "correction_type": "replacement",
         "applied_operations": [{"op": "replace_clip"}], "elapsed_seconds": 120},
    ]
    evidence = copy.deepcopy((session, timelines, scorecards, corrections))
    report = build_comparison_report(session, timelines, scorecards, corrections)
    assert report["versions"]["autonomous_initial"]["duration_seconds"] == 26.1
    assert report["human_work"]["operation_count"] == 2
    assert report["human_work"]["operations"][0]["type"] == "replacement"
    assert report["deltas"]["human_vs_revised_rating"] == 3
    assert "Autonomous initial" in report["markdown"]
    assert evidence == (session, timelines, scorecards, corrections)


def test_report_rejects_mutable_autonomous_evidence():
    session = {"autonomous_initial_timeline_id": "i",
               "autonomous_revised_timeline_id": "r",
               "approved_timeline_id": "h"}
    timelines = [_timeline("i", "autonomous_initial", 10, immutable=False),
                 _timeline("r", "autonomous_revised", 9),
                 _timeline("h", "human_approved", 8)]
    with pytest.raises(HumanCeilingError, match="not immutable"):
        build_comparison_report(session, timelines, [], [])
