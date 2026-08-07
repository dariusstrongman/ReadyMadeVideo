"""Strategy-B bridge: expose a basic-autoedit result as a Product Editor candidate.

The Product Editor consumes only candidate_runs / editor_documents / editor_operations.
Editorial Intelligence (M1-M6) produces 'initial'/'revised' candidates with a full
music/audio ancestry. Basic autoedit has no music, so this bridge produces an honest
'bridged' candidate: REAL preproduction + picture ancestry, REAL source assets and
picture timeline, identity color in the manifest, and NO music/captions/graphics.

Nothing here fabricates tempo, beats, energy, a license, captions, graphics, or music.
The bridge is idempotent PER (project, SOURCE TIMELINE): at most one bridged
candidate per timeline, and a candidate is only ever reused for the exact
timeline it descends from — never merely because it belongs to the same project.

Dependencies (insert/patch/db_select/upload_export/now) are injected so the module is
unit-testable without a live database.
"""
from __future__ import annotations

import uuid

from .logging_util import log_event

# Deterministic namespace so a (project, timeline) pair always maps to the same
# bridged batch_id — repeated same-timeline runs collide on
# (batch_id, candidate_key) and are skipped, not duplicated, while different
# timelines' candidates coexist under distinct batches.
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
    index prevents duplicates; the reopen UPDATE is idempotent).

    RAISES if the record can be neither created nor reopened (missing dependency, non-201/
    non-409 insert response, or a failed reopen UPDATE) — a cleanup failure must never
    become silently untracked."""
    if not insert:
        raise RuntimeError("cleanup persistence unavailable: no insert dependency")
    resp = insert("pending_storage_cleanup", {
        "project_id": project["id"], "user_id": project["user_id"],
        "bucket": bucket, "object_path": path, "reason": reason,
        "attempts": 1, "last_attempt_at": now(), "cleaned_at": None})
    status = getattr(resp, "status_code", None)
    if status == 201:
        return
    if status == 409:                 # row already exists — reopen it for the drain worker
        if not update:
            raise RuntimeError("cleanup reopen unavailable: no update dependency")
        update("pending_storage_cleanup",     # raises on failure — never silent
               f"bucket=eq.{bucket}&object_path=eq.{path}",
               {"cleaned_at": None, "reason": reason, "last_attempt_at": now(),
                "last_error": "reopened: cleanup failed again"})
        return
    # Unexpected persistence response (e.g. 500): surface it, never ignore it.
    raise_for = getattr(resp, "raise_for_status", None)
    if callable(raise_for):
        raise_for()
    raise RuntimeError(f"cleanup persistence failed with status {status}")


def _cleanup_or_persist(*, remove, insert, update, now, project: dict, bucket: str,
                        path: str, reason: str) -> bool:
    """Remove an orphaned preview object. If removal fails the failure is NOT swallowed —
    a retryable pending_storage_cleanup row is persisted (or reopened) so a later run
    drains it. Returns True when removed immediately.

    If BOTH the removal and its tracking record fail, the double failure is logged and
    RAISED — an orphaned object must never become silently untracked. (The bridge only
    calls this on the failed-candidate path, where the edit is already failing; successful
    edits are never affected.)"""
    if not (remove and path):
        return False
    try:
        remove(bucket, path)
        return True
    except Exception as cleanup_exc:  # noqa: BLE001 — surfaced via the retry row below
        try:
            _persist_or_reopen_cleanup(insert=insert, update=update, now=now,
                                       project=project, bucket=bucket, path=path,
                                       reason=reason)
        except Exception as persist_exc:
            log_event("CLEANUP-PERSIST-FAILED", bucket=bucket, object_path=path,
                      project_id=project.get("id"),
                      cleanup_error=f"{type(cleanup_exc).__name__}: {cleanup_exc}"[:300],
                      persist_error=f"{type(persist_exc).__name__}: {persist_exc}"[:300])
            raise
        return False


def bridged_picture_id(project_id: str, timeline_id: str) -> str:
    """Deterministic picture_edit_runs id for one (project, timeline) bridge."""
    return str(uuid.uuid5(_BRIDGE_NS, f"{project_id}:{timeline_id}:picture"))


def find_bridged_candidate(db_select, project_id: str, timeline_id: str):
    """The bridged candidate DESCENDING FROM this exact timeline, or None.

    Reuse is ancestry-bound: a candidate qualifies only when its
    picture_edit_run is the deterministic per-(project, timeline) picture row —
    a candidate that belongs to the same project but descends from a DIFFERENT
    timeline (e.g. an older engine version's output) is never returned.
    """
    rows = db_select(
        "candidate_runs",
        f"project_id=eq.{project_id}&generation_kind=eq.bridged"
        f"&picture_edit_run_id=eq.{bridged_picture_id(project_id, timeline_id)}"
        "&limit=1")
    return rows[0] if rows else None


def bridge_from_autoedit(project: dict, timeline_row: dict, preview_local_path: str,
                         *, insert, db_select, upload_export, now,
                         remove=None, update=None, json_loads=None,
                         export_provider: str = "supabase") -> dict | None:
    """Create (idempotently) a bridged candidate_runs from a basic-autoedit timeline.

    Idempotent PER (project, timeline): repeated bridges of the same timeline
    return the same candidate; a different timeline (a new engine version's
    output, a re-edit) gets its OWN candidate — candidates of older timelines
    are never reused for a new timeline and are never mutated. Returns the
    candidate row (new or pre-existing), or None if the timeline has no usable
    picture clips to bridge.
    """
    import json as _json
    loads = json_loads or _json.loads

    # Opportunistically drain any storage orphaned by a prior run's failed cleanup.
    _drain_pending_cleanup(db_select=db_select, remove=remove, update=update, now=now,
                           project_id=project["id"])

    # Idempotency: one bridged candidate per (project, SOURCE TIMELINE) —
    # ancestry-bound, never merely project-bound.
    existing = find_bridged_candidate(db_select, project["id"],
                                      str(timeline_row["id"]))
    if existing:
        return existing

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
    pic_id = bridged_picture_id(project["id"], timeline_id)

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
    # Per-(project, TIMELINE) preview key: coexisting timelines never share or
    # overwrite each other's preview object.
    candidate_id_hint = uuid.uuid5(_BRIDGE_NS, f"{project['id']}:{timeline_id}:cand")
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

    # Per-(project, TIMELINE) batch: unique(batch_id, candidate_key) then allows
    # exactly one bridged candidate per timeline while candidates of different
    # timelines (older engine versions) coexist immutably.
    batch_id = str(uuid.uuid5(_BRIDGE_NS, f"{project['id']}:{timeline_id}"))
    row = {
        "batch_id": batch_id, "project_id": project["id"], "user_id": uid,
        "preproduction_run_id": pre["id"], "picture_edit_run_id": pic["id"],
        "music_sound_run_id": None, "audio_mix_run_id": None,
        "graphics_run_id": None, "caption_run_id": None, "color_run_id": None,
        "parent_candidate_run_id": None,
        "candidate_key": "bridged", "candidate_index": 1,
        "generation_kind": "bridged",
        "source_picture_candidate_id": picture_candidate_id,
        # Self-describing preview provenance: upload_export routes to S3 or to
        # Supabase storage depending on EXPORT_STORAGE_PROVIDER, and the reader
        # must sign against the store the object was actually written to. Recorded
        # per row (never inferred from the deployment's current setting) so
        # flipping the env can never mis-sign an older candidate's preview.
        "variant_config": {"origin": "basic_autoedit",
                           "previewStorageProvider": export_provider},
        "manifest": manifest,
        "render_qc": {"origin": "basic_autoedit", "checks": "picture+original_audio"},
        "preview_storage_bucket": "exports", "preview_storage_path": preview_path,
        "fabricated_footage": False, "created_by": uid,
    }
    resp = insert("candidate_runs", row)
    if resp.status_code == 201:
        return resp.json()[0]
    if resp.status_code == 409:  # lost an idempotency race — return the winner
        again = find_bridged_candidate(db_select, project["id"], timeline_id)
        if again:
            again = [again]
        if again:
            # The preview object key is deterministic PER PROJECT, so our upload wrote the
            # exact object the winning candidate references — it is NOT an orphan and must
            # be left intact. The edit succeeded; return the winner.
            return again[0]
    # Candidate creation failed for real: no candidate references the just-uploaded preview
    # (the per-project idempotency check above guarantees no prior bridged candidate exists),
    # so it is a true orphan. Remove it; a cleanup failure is persisted for retry. If even
    # the persistence fails, that double failure is logged and raised (never silent) — the
    # finally still raises the ORIGINAL candidate-insert error as the root cause, with the
    # persistence failure chained into the traceback. The job fails loudly either way and a
    # retry re-runs the bridge against the same deterministic preview key.
    try:
        _cleanup_or_persist(remove=remove, insert=insert, update=update, now=now,
                            project=project, bucket="exports", path=preview_path,
                            reason="bridged candidate insert failed: orphaned preview")
    finally:
        resp.raise_for_status()
    return None
