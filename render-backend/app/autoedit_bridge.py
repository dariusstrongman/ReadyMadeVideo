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
    """Compute a candidate next version for a per-project (project_id, version) table.
    This alone is race-prone (two concurrent bridges read the same max), so writers must
    go through _insert_versioned, which retries on the unique-violation it can still hit."""
    rows = db_select(table, f"project_id=eq.{project_id}&order=version.desc&limit=1")
    return (int(rows[0]["version"]) + 1) if rows else 1


def _bridge_ancestry(insert, db_select, table: str, project_id: str, det_id: str,
                     body: dict, attempts: int = 6) -> dict:
    """Create-or-reuse one bridge ancestry row, collision-safe on BOTH axes:

    * Duplicate ancestry: the row has a DETERMINISTIC id derived from (project, timeline),
      so concurrent same-timeline bridges (or a recovery re-run) converge on the SAME row —
      exactly one preproduction/picture per timeline, never a duplicate, never cross-linked.
    * Version race: (project_id, version) is unique, and max(version)+1 is race-prone, so a
      unique-violation triggers a bounded recompute-and-retry. A 409 that turns out to be
      OUR deterministic row (a peer inserted it first) is resolved by reusing that row.
    """
    existing = db_select(table, f"id=eq.{det_id}&limit=1")
    if existing:
        return existing[0]
    last = None
    for _ in range(attempts):
        version = _next_version(db_select, table, project_id)
        resp = insert(table, {**body, "id": det_id, "version": version})
        if resp.status_code == 201:
            return resp.json()[0]
        if resp.status_code == 409:
            dup = db_select(table, f"id=eq.{det_id}&limit=1")
            if dup:                       # a peer created our deterministic row -> reuse
                return dup[0]
            last = resp                   # a different row took our version -> recompute
            continue
        resp.raise_for_status()
    if last is not None:
        last.raise_for_status()
    raise RuntimeError(f"could not allocate a {table} version after {attempts} attempts")


def _drain_pending_cleanup(*, db_select, remove, update, now, project_id: str) -> int:
    """Opportunistically retry previously-orphaned storage objects for this project.
    Each row stays pending until its object is actually removed, so a transient storage
    outage is recovered on a later run instead of leaking silently."""
    if not (db_select and remove and update):
        return 0
    try:
        rows = [r for r in db_select("pending_storage_cleanup",
                                     f"project_id=eq.{project_id}")
                if not r.get("cleaned_at")]
    except Exception:  # noqa: BLE001 — janitorial; never blocks the edit
        return 0
    cleaned = 0
    for r in rows:
        try:
            remove(r["bucket"], r["object_path"])
            update("pending_storage_cleanup", f"id=eq.{r['id']}",
                   {"cleaned_at": now(), "attempts": (r.get("attempts") or 0) + 1,
                    "last_attempt_at": now()})
            cleaned += 1
        except Exception as exc:  # noqa: BLE001 — remains pending, retried next run
            try:
                update("pending_storage_cleanup", f"id=eq.{r['id']}",
                       {"attempts": (r.get("attempts") or 0) + 1,
                        "last_attempt_at": now(), "last_error": type(exc).__name__[:200]})
            except Exception:  # noqa: BLE001
                pass
    return cleaned


def _persist_or_reopen_cleanup(*, insert, update, now, project: dict, bucket: str,
                               path: str, reason: str) -> None:
    """Record a pending_storage_cleanup row, or REOPEN an existing one.

    pending_storage_cleanup has UNIQUE(bucket, object_path). If a previously-RESOLVED row
    exists for the same object (cleaned_at set), a plain insert would 409 and the object
    would be orphaned forever (the drain skips resolved rows). So on a unique conflict the
    row is reopened: cleaned_at cleared and retry metadata refreshed, making it eligible
    for the drain worker again. Concurrent reopeners converge on the same row (the UNIQUE
    index prevents duplicates; the reopen UPDATE is idempotent)."""
    if not insert:
        return
    resp = insert("pending_storage_cleanup", {
        "project_id": project["id"], "user_id": project["user_id"],
        "bucket": bucket, "object_path": path, "reason": reason,
        "attempts": 1, "last_attempt_at": now(), "cleaned_at": None})
    status = getattr(resp, "status_code", 500)
    if status == 201:
        return
    if status == 409 and update:      # row already exists — reopen it for the drain worker
        update("pending_storage_cleanup",
               f"bucket=eq.{bucket}&object_path=eq.{path}",
               {"cleaned_at": None, "reason": reason, "last_attempt_at": now(),
                "last_error": "reopened: cleanup failed again"})


def _cleanup_or_persist(*, remove, insert, update, now, project: dict, bucket: str,
                        path: str, reason: str) -> bool:
    """Remove an orphaned preview object. If removal fails the failure is NOT swallowed —
    a retryable pending_storage_cleanup row is persisted (or reopened) so a later run
    drains it. Returns True when removed immediately."""
    if not (remove and path):
        return False
    try:
        remove(bucket, path)
        return True
    except Exception:  # noqa: BLE001 — surfaced via the persisted/reopened retry row
        try:
            _persist_or_reopen_cleanup(insert=insert, update=update, now=now,
                                       project=project, bucket=bucket, path=path,
                                       reason=reason)
        except Exception:  # noqa: BLE001 — last resort; never fails a successful edit
            pass
        return False


def bridge_from_autoedit(project: dict, timeline_row: dict, preview_local_path: str,
                         *, insert, db_select, upload_export, now,
                         remove=None, update=None, json_loads=None) -> dict | None:
    """Create (idempotently) a bridged candidate_runs from a basic-autoedit timeline.

    Returns the candidate row (new or pre-existing), or None if the timeline has no
    usable picture clips to bridge.
    """
    import json as _json
    loads = json_loads or _json.loads

    # Opportunistically drain any storage orphaned by a prior run's failed cleanup.
    _drain_pending_cleanup(db_select=db_select, remove=remove, update=update, now=now,
                           project_id=project["id"])

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
    timeline_id = str(timeline_row["id"])

    # Deterministic ancestry ids per (project, timeline): concurrent same-timeline bridges
    # and recovery re-runs converge on ONE preproduction + ONE picture row — never a
    # duplicate, never cross-linked to another timeline.
    pre_id = str(uuid.uuid5(_BRIDGE_NS, f"{project['id']}:{timeline_id}:preproduction"))
    pic_id = str(uuid.uuid5(_BRIDGE_NS, f"{project['id']}:{timeline_id}:picture"))

    # Real, minimal preproduction ancestry (honest defaults; no fabricated analysis).
    pre = _bridge_ancestry(insert, db_select, "preproduction_runs", project["id"], pre_id, {
        "project_id": project["id"], "user_id": uid,
        "status": "ready",
        "request": {"origin": "basic_autoedit", "brief": brief, "timeline_id": timeline_id},
        "creative_treatment": {"origin": "basic_autoedit", "brief": brief},
        "capture_quality_report": {"origin": "basic_autoedit"},
        "composition_by_segment": {}, "story_variants": [],
    })

    # Real picture ancestry derived from the actual autoedit timeline, bound to THIS
    # preproduction (reuse-or-create on the deterministic id).
    picture_candidate_id = f"bridged-{timeline_id}"
    pic = _bridge_ancestry(insert, db_select, "picture_edit_runs", project["id"], pic_id, {
        "project_id": project["id"], "user_id": uid,
        "preproduction_run_id": pre["id"],   # picture ancestry bound to THIS preproduction
        "status": "ready",
        "request": {"origin": "basic_autoedit", "timeline_id": timeline_id},
        "visual_rhythm_plans": [],
        "candidates": [{
            "candidateId": picture_candidate_id, "source": "basic_autoedit",
            "sourceAssetIds": source_asset_ids, "timeline": timeline,
            "clipCount": len(clips),
        }],
        "selected_candidate_id": picture_candidate_id,
    })
    picture_candidate_id = pic.get("selected_candidate_id") or picture_candidate_id

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
            # The preview object key is deterministic PER PROJECT, so our upload wrote the
            # exact object the winning candidate references — it is NOT an orphan and must
            # be left intact. The edit succeeded; return the winner.
            return again[0]
    # Candidate creation failed for real: no candidate references the just-uploaded preview
    # (the per-project idempotency check above guarantees no prior bridged candidate exists),
    # so it is a true orphan. Remove it; a cleanup failure is persisted for retry (not
    # swallowed), then the original candidate-insert error is raised.
    _cleanup_or_persist(remove=remove, insert=insert, update=update, now=now, project=project,
                        bucket="exports", path=preview_path,
                        reason="bridged candidate insert failed: orphaned preview")
    resp.raise_for_status()
    return None
