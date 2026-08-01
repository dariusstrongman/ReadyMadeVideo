# Review round 2 — `codex/human-ceiling-evaluation` delta

Read-only re-review of the fix delta only: `279a87c` → `99822f1` "fix human
ceiling evaluation integrity" (one commit, ~1250 insertions). No implementation
code modified, no worktree switched, no merge, no deploy. Traced every item
through implementation, migration, CI, and tests.

Files in the delta: `.github/ci/human_ceiling_integrity.sql` (new),
`.github/workflows/ci.yml`, `app/src/pages/Operator.jsx`,
`docs/.../08-human-ceiling-workflow.md`, `render-backend/app/human_ceiling.py`,
`render-backend/app/jobs.py`, `render-backend/app/main.py`,
`render-backend/tests/fake_supa.py`, `test_human_ceiling.py`,
`test_job_handlers.py`, `test_operator_api.py`, and migration
`20260801_0007_human_ceiling_evaluation.sql`.

## Item-by-item confirmation (all fixed, all tested)

| # | Item | Status | Evidence |
|---|---|---|---|
| 1 | Server-authoritative correction timing | Fixed | `measure_server_time()` derives time from persisted server-stamped events; client value demoted to a labeled `client_reported_seconds` hint; approve/scorecard/report all use the server value; `if corrections and server_measured<=0 -> 422` kills the zero-able exploit. Tests: `test_client_time_cannot_override_server_measured_time`, `test_zero_server_time_approval_is_rejected_when_operations_exist`. |
| 2 | Pause/resume timing | Fixed | `/pause` + `/resume` endpoints; `timing_state` machine; editing blocked unless `running`; paused spans contribute zero; each running interval independently idle-capped (default 300s) so an abandoned tab cannot inflate. Test: `test_server_timing_excludes_pauses_and_caps_idle_gaps`. |
| 3 | Abandon-session lifecycle | Fixed | `/abandon` releases the active slot, freezes the draft as non-approved immutable evidence, records reason + `abandoned_by`. Test: `test_abandon_preserves_nonapproved_draft_and_releases_active_slot`. |
| 4 | 401/403 authorization tests | Fixed | `test_human_ceiling_authorization_regressions_for_every_action` asserts 401 (no auth) + 403 (owner) for start/pause/resume/approve/scorecard/abandon/report + timeline-ops, and checks each action was audited. |
| 5 | Real-PostgreSQL trigger/integrity tests | Fixed | `.github/ci/human_ceiling_integrity.sql` wired into the migrations job with `ON_ERROR_STOP`; `expect_rejected()` proves immutable UPDATE/DELETE, cross-project session/corrections/scorecards, and provenance-rewrite are all rejected, plus a positive persistence check, in a rolled-back transaction. |
| 6 | Zero-revision workflow | Fixed | `autonomous_revised_timeline_id` nullable end-to-end; `start` handles single baseline; report runs `initial_vs_human` mode without inventing a revised version. Tests: `test_two_way_report_does_not_invent_revised_version`, `test_zero_revision_project_uses_initial_as_draft_parent_without_fake_baseline`. |
| 7 | Immutable approval provenance | Fixed | Immutability trigger now also blocks `approved_by`/`approved_at` changes; the DB integrity test proves the provenance rewrite is rejected. |
| 8 | Operation-index collision handling | Fixed | `_insert_correction_with_retry` (bounded, clean 409 on exhaustion); `test_operation_index_unique_collision_is_retried` injects a collision and confirms recovery. |
| 9 | Edit-run association | Fixed | `jobs.py` now creates the edit-run *before* timelines and sets `edit_run_id` at insert; migration backfills existing rows. |

## Remaining merge blockers
None. Both blockers from the prior review are fully resolved with implementation
and tests.

## Important (non-blocking) issues
1. **`human_edit_timing_events` is not tamper-hardened.** The rework makes timing
   server-authoritative *versus the client*, but the events table it derives from
   has no append-only guard (timelines get a DELETE/UPDATE-blocking trigger;
   timing events do not). Writes are service-role-only with no API mutation
   surface, so it is safe in practice, but a service-role bug/actor could rewrite
   `occurred_at` and change the measured benchmark. Add an append-only trigger
   (block UPDATE/DELETE) to complete the integrity story. Fast-follow, not a
   blocker.
2. **Idle-gap cap attributes silent think-time (up to the cap) to the next
   operation.** Correct anti-inflation behavior, but genuine long pauses without
   an explicit `/pause` count up to 300s each. Documented and the operator can
   pause; just be aware the measured number is "active editing with capped gaps,"
   not pure hands-on-keys.
3. **A corrupted event sequence makes a session unrecoverable**
   (`measure_server_time` raises -> 409 on every subsequent call, no repair
   path). Fail-safe over available; acceptable, worth a note.

## Test adequacy
Strong. Unit coverage of the timing state machine (pause exclusion, idle cap,
inconsistent-transition rejection), API coverage of every new endpoint including
the full authz matrix, the zero-time-approval guard, client-can't-override,
abandon lifecycle, zero-revision path, and the collision retry — with the fake
extended to model both unique constraints. Plus real-Postgres CI assertions for
the DB triggers (the one gap flagged in round 1). The only thing not directly
unit-tested is the append-only property of the timing table (because it is not
enforced yet — item #1).

## Security assessment
Solid, defense-in-depth intact. Every new endpoint is operator-gated
(`_require_operator`) and audited before the action (audit-before-action -> 503
on audit failure, now proven by the authz regression test). All new tables carry
RLS (owner-or-operator read, service-role write), `enforce_project_ownership`,
and cross-reference integrity triggers, now verified against real Postgres in CI.
Immutability is enforced at both the API and DB layers, and provenance is frozen.
No injection surface (constrained ops only; no raw SQL/FFmpeg from input). The
one open edge is the timing-events tamper surface (item #1), which is theoretical
given no mutation API.

## Final verdict: APPROVE
Codex addressed all nine items thoroughly and honestly — server-authoritative
timing with idle-capping is the right model, the zero-time exploit is closed, the
abandon dead-end is gone, and the DB triggers are now CI-verified against real
Postgres. This is merge-ready. Merge as-is and file item #1 (append-only guard on
`human_edit_timing_events`) as a fast-follow to complete the tamper-resistance
guarantee.

---
Reviewer: Claude (branch `claude/editorial-analysis`). Round 1 of this review is
in `09-codex-human-ceiling-review.md`; this round covers only the fix delta
`279a87c..99822f1`.
