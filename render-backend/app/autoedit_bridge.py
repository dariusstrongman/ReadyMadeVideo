"""Strategy-B bridge: expose a basic-autoedit result as a Product Editor candidate.

The Product Editor consumes only candidate_runs / editor_documents / editor_operations.
Editorial Intelligence (M1-M6) produces 'initial'/'revised' candidates with a full
music/audio ancestry. Basic autoedit has no music, so this bridge produces an honest
'bridged' candidate: REAL preproduction + picture ancestry, REAL source assets and
picture timeline, identity color in the manifest, and NO music/captions/graphics.

Nothing here fabricates tempo, beats, energy, a license, captions, graphics, or music.
The bridge is idempotent: at most one bridged candidate per project.

Dependencies (insert/patch/db_select/upload_export/now) are injected so the module is
unit-testable without a live database.
"""
from __future__ import annotations

import uuid

# Deterministic namespace so a project always maps to the same bridged batch_id —
# repeated runs collide on (batch_id, candidate_key) and are skipped, not duplicated.
_BRIDGE_NS = uuid.UUID("5f3b9d2e-0000-4000-a000-000000000b21")

IDENTITY_COLOR = {
    "status": "identity",
    "nonDestructive": True,
    "adjustments": [],
    "note": "basic autoedit — no color treatment applied",
}


def _clips_from_timeline(timeline: dict) -> list[dict]:
    return [clip for track in timeline.get("tracks", [])
            if track.get("type") == "video" for clip in track.get("clips", [])]


def _next_version(db_select, table: str, project_id: str) -> int:
    """Allocate a non-colliding version for a per-project (project_id, version) table
    so the bridge never conflicts with existing Milestone ancestry at version 1."""
    rows = db_select(table, f"project_id=eq.{project_id}&order=version.desc&limit=1")
    return (int(rows[0]["version"]) + 1) if rows else 1


def _find_bridge_preproduction(db_select, project_id: str) -> dict | None:
    """Reuse a bridge preproduction row left by a prior partial run (recovery),
    so a failed bridge never orphans immutable ancestry or creates duplicates."""
    for row in db_select("preproduction_runs", f"project_id=eq.{project_id}&order=version.desc"):
        if (row.get("request") or {}).get("origin") == "basic_autoedit":
            return row
    return None


def bridge_from_autoedit(project: dict, timeline_row: dict, preview_local_path: str,
                         *, insert, db_select, upload_export, now,
                         remove=None, json_loads=None) -> dict | None:
    """Create (idempotently) a bridged candidate_runs from a basic-autoedit timeline.

    Returns the candidate row (new or pre-existing), or None if the timeline has no
    usable picture clips to bridge.
    """
    import json as _json
    loads = json_loads or _json.loads

    # Idempotency: one bridged candidate per project.
    existing = db_select(
        "candidate_runs",
        f"project_id=eq.{project['id']}&generation_kind=eq.bridged&limit=1")
    if existing:
        return existing[0]

    timeline = timeline_row["timeline_json"]
    if isinstance(timeline, str):
        timeline = loads(timeline)
    clips = _clips_from_timeline(timeline)
    source_asset_ids = sorted({str(clip["assetId"]) for clip in clips})
    if not clips or not source_asset_ids:
        return None  # nothing real to bridge

    uid = project["user_id"]
    brief = project.get("name") or "basic autoedit"

    # Real, minimal preproduction ancestry (honest defaults; no fabricated analysis).
    # Reuse a prior bridge run's ancestry if present (recovery — no orphan/duplicate),
    # and allocate a safe version otherwise.
    pre = _find_bridge_preproduction(db_select, project["id"])
    if not pre:
        pre = insert("preproduction_runs", {
            "project_id": project["id"], "user_id": uid,
            "version": _next_version(db_select, "preproduction_runs", project["id"]),
            "status": "ready",
            "request": {"origin": "basic_autoedit", "brief": brief},
            "creative_treatment": {"origin": "basic_autoedit", "brief": brief},
            "capture_quality_report": {"origin": "basic_autoedit"},
            "composition_by_segment": {}, "story_variants": [],
        }).json()[0]

    # Real picture ancestry derived from the actual autoedit timeline (reuse-or-create).
    picture_candidate_id = f"bridged-{timeline_row['id']}"
    existing_pic = db_select("picture_edit_runs",
                             f"preproduction_run_id=eq.{pre['id']}&limit=1")
    if existing_pic:
        pic = existing_pic[0]
        picture_candidate_id = pic.get("selected_candidate_id") or picture_candidate_id
    else:
        pic = insert("picture_edit_runs", {
            "project_id": project["id"], "user_id": uid,
            "preproduction_run_id": pre["id"],
            "version": _next_version(db_select, "picture_edit_runs", project["id"]),
            "status": "ready", "request": {"origin": "basic_autoedit"},
            "visual_rhythm_plans": [],
            "candidates": [{
                "candidateId": picture_candidate_id, "source": "basic_autoedit",
                "sourceAssetIds": source_asset_ids, "timeline": timeline,
                "clipCount": len(clips),
            }],
            "selected_candidate_id": picture_candidate_id,
        }).json()[0]

    # Preview under the documented bridged/autoedit prefix (real autoedit render).
    candidate_id_hint = uuid.uuid5(_BRIDGE_NS, f"{project['id']}:cand")
    preview_path = upload_export(
        project, f"autoedit/{candidate_id_hint}.mp4", preview_local_path)

    manifest = {
        "schemaVersion": 1,
        "origin": "basic_autoedit",
        "sourceAssetIds": source_asset_ids,
        "pictureTimeline": timeline,
        "captions": {"groups": []},
        "graphics": {"events": []},
        "color": IDENTITY_COLOR,
        "fabricatedFootage": False,
    }

    batch_id = str(uuid.uuid5(_BRIDGE_NS, str(project["id"])))
    row = {
        "batch_id": batch_id, "project_id": project["id"], "user_id": uid,
        "preproduction_run_id": pre["id"], "picture_edit_run_id": pic["id"],
        "music_sound_run_id": None, "audio_mix_run_id": None,
        "graphics_run_id": None, "caption_run_id": None, "color_run_id": None,
        "parent_candidate_run_id": None,
        "candidate_key": "bridged", "candidate_index": 1,
        "generation_kind": "bridged",
        "source_picture_candidate_id": picture_candidate_id,
        "variant_config": {"origin": "basic_autoedit"},
        "manifest": manifest,
        "render_qc": {"origin": "basic_autoedit", "checks": "picture+original_audio"},
        "preview_storage_bucket": "exports", "preview_storage_path": preview_path,
        "fabricated_footage": False, "created_by": uid,
    }
    resp = insert("candidate_runs", row)
    if resp.status_code == 201:
        return resp.json()[0]
    if resp.status_code == 409:  # lost an idempotency race — return the winner
        again = db_select(
            "candidate_runs",
            f"project_id=eq.{project['id']}&generation_kind=eq.bridged&limit=1")
        if again:
            return again[0]
    # Candidate creation failed: clean the orphan preview object (the reusable
    # preproduction/picture ancestry is picked up on the next retry). No orphan
    # storage, no orphan candidate.
    if remove:
        try:
            remove("exports", preview_path)
        except Exception:  # noqa: BLE001
            pass
    resp.raise_for_status()
    return None
