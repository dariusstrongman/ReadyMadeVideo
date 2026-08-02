# Product Editor Phase 1

## Scope

Phase 1 lets a signed-in project owner open a completed Milestone 6 candidate,
make bounded timeline changes, and export an exact saved revision.

The editor has five tracks:

| Track | Phase 1 controls |
| --- | --- |
| Picture | Reorder, trim, split, delete |
| Captions | Edit text |
| Music | Set gain from -60 dB to +6 dB |
| SFX | Visible in the timeline, read-only |
| Graphics | Show or hide an existing graphic |

The timeline also has a playhead, half-second snapping, horizontal scrolling,
zoom, undo, and redo. Phase 1 does not add transitions, keyframes, effects,
track creation, new media, or arbitrary FFmpeg controls.

## Canonical document and lineage

`editor_documents` stores one complete editor document per candidate version.
Rows are immutable. Version 1 points to the source `candidate_runs` row. Each
later version points to its immediate parent and to a separate immutable
`product_editor` timeline row.

`editor_operations` records every accepted operation. Each record includes:

- `operation_id`
- operation type
- target ID
- base and result document IDs
- candidate ancestry
- operation index
- complete validated arguments
- actor (`user` or `ai`)
- client timestamp
- server timestamp

The unique candidate/version key prevents two writers from saving the same
next version. The API checks the submitted version against the latest saved
row before it creates a revision. A stale client gets HTTP 409 and must reload.

Undo and redo work locally before autosave. Saved revisions remain available
in the history rail, so restoring an earlier cut never rewrites evidence.

## Validation and security

The API checks the authenticated user against the project owner on every
customer editor route. Candidate, document, media, timeline, operation, render,
and storage ancestry must all resolve to the same project and owner.

Operation payloads use discriminated Pydantic models. Validation covers source
bounds, document duration, item count, track shape, actor, target, field limits,
and operation count. The operation contract has no filesystem path, command,
filter, or asset insertion field. The editor cannot fabricate footage or point
at an asset outside the candidate manifest.

The database repeats the critical checks with foreign keys and triggers. Real
PostgreSQL tests cover cross-project candidate references, invalid parents,
duplicate versions, invalid operation direction, duplicate operation indexes,
render-version mismatch, and immutable update/delete attempts.

Editor writes, chat proposals, exports, retries, and signed downloads are rate
limited or audited. An audit-store failure stops the action.

## API workflow

All routes require `Authorization: Bearer <user JWT>`.

1. `GET /projects/{project_id}/workspace` returns the project, Milestone 6
   candidates, publishability reports, editor history, and recent export jobs.
2. `POST /projects/{project_id}/editor/start` with `candidateRunId` creates
   version 1 or returns the latest existing revision for that candidate.
3. `GET /projects/{project_id}/editor/{document_id}` loads one saved revision.
4. `POST /projects/{project_id}/editor/{document_id}/operations` accepts
   `expectedVersion` and up to 50 typed operations. It creates one document,
   one immutable renderer timeline, and append-only operation rows.
5. `POST /projects/{project_id}/editor/revisions/propose` translates a bounded
   text request into the same operation format. It makes no provider call.
6. `POST /projects/{project_id}/editor/render` creates a persistent
   `final_render` job bound to the document ID, version, and timeline ID.
7. `POST /projects/{project_id}/editor/renders/{job_id}/retry` retries a failed
   export without changing its saved revision ancestry.
8. `POST /projects/{project_id}/editor/renders/{job_id}/sign` returns a one-hour
   URL for a completed export under the owners project path.

## Render and attribution rules

The persistent worker renders the immutable timeline stored with the editor
revision. It rebuilds picture from the owned source assets, remixes source audio
and the licensed music track at the saved gain, then applies the saved captions,
enabled graphics, and inherited color instructions. It does not read whichever
document happens to be newest when the job starts. Failed jobs keep their
original document ID and version when retried.

If a document contains an attribution-required asset without rendered
attribution evidence, export returns HTTP 409. Phase 1 does not claim that an
attribution card exists when it does not.

## Customer workflow

1. Open a project.
2. Choose a finished candidate in the Product Editor card.
3. Inspect the real candidate preview and its publishability state.
4. Select clips, captions, music, or graphics in the timeline.
5. Make a bounded change. The browser previews the operation immediately and
   autosaves the batch after 900 ms.
6. Use the revision rail to inspect an earlier saved cut.
7. Export only after the current changes show as saved.
8. Watch queued, processing, completed, or failed status in the editor. Retry a
   failed export or download the completed MP4.

## Tests

- `render-backend/tests/test_product_editor.py` covers the operation engine,
  bounds, user and AI operation parity, authorization, candidate ancestry,
  conflicts, append-only persistence, exact render binding, attribution
  blocking, retry, and signed download paths.
- `app/src/lib/editor.test.js` covers the client reducer, immutable local
  reflow, caption edits, and undo.
- `.github/ci/editorial_intelligence_integrity.sql` contains the Phase 1 real
  PostgreSQL assertions.
- CI runs frontend tests, the production build, the backend suite, coverage
  gates, Ruff, secret scans, dependency audits, fixture renders, migrations
  through 0014, and database integrity scripts.

## Known limits

- The conversational translator supports three bounded request shapes. It is a
  mockable local adapter, not an open-ended language model integration.
- The editor previews the immutable Milestone 6 candidate render. A new preview
  render is not generated after each local operation.
- Existing color instructions remain read-only. Picture, captions, music gain,
  and graphic visibility are rendered from the saved Phase 1 document.
- SFX is inspectable but cannot be changed in Phase 1.
- Autosave creates one revision per pending batch. Atomic insertion is enforced
  by unique constraints and lineage triggers. A failed multi-row PostgREST
  sequence can leave an unreferenced immutable timeline row for later cleanup.
