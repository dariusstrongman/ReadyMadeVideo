# Project One human-ceiling comparison workflow

This workflow measures the editorial gap without changing the selector, planner,
critic, revision agent, or renderer. It compares two or three explicit timeline
lineages from the same footage:

1. `autonomous_initial` - the first autonomous draft.
2. `autonomous_revised` - the critic-revised autonomous draft, when the editor
   actually produced a distinct revision.
3. `human_approved` - a separately branched and manually approved timeline.

The autonomous versions are evidence, not working copies. Starting a human edit
marks every available autonomous timeline immutable and clones the revised
timeline, or the initial timeline when no revision exists, into a new
`human_draft` lineage. It never manufactures a revised baseline. Approval
freezes the human timeline and its `approved_by`/`approved_at` provenance.

## Operator sequence

1. Open the project in the operator console.
2. In **Human-ceiling comparison**, select the autonomous initial and, when one
   exists, revised timeline from the same edit run.
3. Select **Freeze baselines + begin human lineage**.
4. The server starts authoritative timing automatically. The visible client
   timer is labeled as a diagnostic hint only.
5. Apply each manual decision through one constrained timeline operation.
6. Give the decision a short reason. The system stores one correction record per
   applied operation, even when an API request contains multiple operations.
7. Use **Pause** and **Resume** when editing stops. Both actions are authorized,
   audited, and persisted as server-timestamped events.
8. Approve the human timeline. Approval is rejected for inconsistent timing or
   when real operations have zero server-measured time.
9. Score the initial, optional revised, and human-approved versions independently.
10. Generate the side-by-side report.

An operator can instead choose **Abandon session**, confirm the action, and give
a reason. The server freezes the current `human_draft` as non-approved evidence,
stores the reason/operator/time, closes the session, and allows a new session.
Neither autonomous baseline is changed and no `human_approved` row is created.

## Recorded correction types

| Manual decision | Constrained operation | Recorded type |
|---|---|---|
| Replace a selected clip | `replace_clip` | `replacement` |
| Change in/out points | `trim_clip` | `trim` |
| Change clip order | `move_clip` | `reorder` |
| Change clip volume | `change_volume` | `audio` |
| Duck music | `duck_music` | `audio` |
| Add/change title treatment | `add_title` | `title` |
| Insert a clip | `insert_clip` | `insert` |
| Remove a clip | `delete_clip` | `delete` |
| Change speed | `change_speed` | `speed` |
| Add/change caption treatment | `add_caption` | `caption` |

Every correction records its operation index, base timeline, resulting timeline,
operation JSON, operator, reason, server-measured interval, and optional client
timer hint. Existing
`user_corrections` remains the learning-data source; the human-ceiling workflow
adds lineage and session context rather than creating an unrelated event log.

## Scorecard

Each version receives an overall 1-10 score, optional publishable decision, and
the same named dimensions:

- Hook
- Story clarity
- Shot selection
- Shot variety
- Pacing
- Continuity
- Action visibility
- Emotional intensity
- Natural audio
- Audio mix
- Captions/titles
- Color consistency
- Ending/payoff

Dimensions may be left unscored when they do not apply. Unknown dimensions and
values outside 1-10 are rejected.

## Comparison report

The generated report contains:

- Deterministic metrics for all available timelines (duration, clips, distinct
  sources, title/caption/audio/speed changes).
- The latest scorecard for each timeline.
- Overall-rating deltas between initial, revised, and human-approved versions.
- Authoritative server-measured correction time and separately labeled optional
  client-reported diagnostic time.
- Every manual operation in order and counts by correction type.
- A portable Markdown summary.

The report requires one immutable initial baseline. It adds the immutable revised
baseline only when the edit run has a distinct revision, yielding either
initial-vs-human or initial-vs-revised-vs-human without falsifying evidence.

## Timing architecture

`human_edit_timing_events` persists server timestamps for start, pause, resume,
each operation, approval, and abandonment. The backend rebuilds the session
clock from that event stream on every action. Running intervals count; paused
intervals do not. Each gap between active events is capped by
`HUMAN_EDIT_IDLE_GAP_CAP_SECONDS` (default 300 seconds, allowed 1-3600), so an
open editor cannot accumulate unlimited active time. The resulting
`server_measured_seconds` drives scorecards, reports,
`human_correction_minutes`, and business metrics. `client_reported_seconds` and
the legacy request aliases are diagnostic only.

Operation indexes use the database unique constraint plus a bounded collision
retry (`HUMAN_OPERATION_INDEX_RETRIES`, default 3). This keeps assignment safe
under concurrent requests without changing the timeline operation model.

## Data and security notes

- Migration `20260801_0007_human_ceiling_evaluation.sql` adds timeline lineage,
  immutable-baseline enforcement, human edit sessions, correction context, and
  timeline scorecards.
- Writes remain service-role-only and pass through operator-authenticated,
  audited API endpoints.
- Timeline JSON is never edited in place. Every operation creates a new version.
- No private footage, transcript, proxy, preview, or Project One local artifact
  is committed or overwritten by this workflow.
- Autonomous timeline rows receive `edit_run_id` when the editing job creates
  them; migration 0007 safely backfills older rows from edit-run v1/v2 links.
- Database writes span multiple PostgREST requests, so they are not a single SQL
  transaction. The audit-before-action rule and append-only evidence minimize
  ambiguity; a future production hardening pass can move the multi-row operation
  into one database RPC if live failure testing shows that it is necessary.

## Acceptance checks

- Autonomous initial and revised timeline JSON remain byte-for-byte unchanged.
- Every available autonomous row is immutable before the human edit starts.
- Human work begins as a clone in a separate lineage.
- Every applied manual operation has exactly one correction record.
- The approved human timeline is immutable.
- Server event time, not submitted client time, drives benchmark metrics.
- Pause time is excluded and idle gaps are capped.
- An abandoned draft remains preserved and non-approved, and a new session starts.
- Available comparison timeline IDs have independent scorecards.
- The report includes two versions when no real revision exists and three when it does.
