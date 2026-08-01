# Project One human-ceiling comparison workflow

This workflow measures the editorial gap without changing the selector, planner,
critic, revision agent, or renderer. It compares three explicit timeline
lineages from the same footage:

1. `autonomous_initial` - the first autonomous draft.
2. `autonomous_revised` - the critic-revised autonomous draft.
3. `human_approved` - a separately branched and manually approved timeline.

The autonomous versions are evidence, not working copies. Starting a human edit
marks both autonomous timelines immutable and clones the revised timeline into a
new `human_draft` lineage. Approval freezes the human timeline too.

## Operator sequence

1. Open the project in the operator console.
2. In **Human-ceiling comparison**, select the autonomous initial and revised
   timelines from the same edit run.
3. Select **Freeze baselines + begin human lineage**.
4. Run the timer only while making editorial decisions.
5. Apply each manual decision through one constrained timeline operation.
6. Give the decision a short reason. The system stores one correction record per
   applied operation, even when an API request contains multiple operations.
7. Pause or correct the measured time if the session is interrupted.
8. Approve the human timeline. Approval is rejected if the submitted total time
   is less than the time already attributed to recorded operations.
9. Score the autonomous initial, autonomous revised, and human-approved versions
   independently.
10. Generate the side-by-side report.

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
operation JSON, operator, reason, and attributed correction time. Existing
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

- Deterministic metrics for all three timelines (duration, clips, distinct
  sources, title/caption/audio/speed changes).
- The latest scorecard for each timeline.
- Overall-rating deltas between initial, revised, and human-approved versions.
- Total measured human correction time.
- Every manual operation in order and counts by correction type.
- A portable Markdown summary.

The report refuses to run if either autonomous baseline is missing or not marked
immutable. This prevents a comparison from silently using changed evidence.

## Data and security notes

- Migration `20260801_0007_human_ceiling_evaluation.sql` adds timeline lineage,
  immutable-baseline enforcement, human edit sessions, correction context, and
  timeline scorecards.
- Writes remain service-role-only and pass through operator-authenticated,
  audited API endpoints.
- Timeline JSON is never edited in place. Every operation creates a new version.
- No private footage, transcript, proxy, preview, or Project One local artifact
  is committed or overwritten by this workflow.
- Database writes span multiple PostgREST requests, so they are not a single SQL
  transaction. The audit-before-action rule and append-only evidence minimize
  ambiguity; a future production hardening pass can move the multi-row operation
  into one database RPC if live failure testing shows that it is necessary.

## Acceptance checks

- Autonomous initial and revised timeline JSON remain byte-for-byte unchanged.
- Both autonomous rows are immutable before the human edit starts.
- Human work begins as a clone in a separate lineage.
- Every applied manual operation has exactly one correction record.
- The approved human timeline is immutable.
- Submitted total human time is at least the recorded operation time.
- All three timeline IDs have independent scorecards.
- The report includes exactly the initial, revised, and human-approved versions.
