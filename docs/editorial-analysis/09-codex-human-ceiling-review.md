# Review — `codex/human-ceiling-evaluation` vs `feature/real-video-pipeline`

Read-only review (no implementation code modified, no worktree switched, no merge,
no deploy). Scope: one commit `279a87c` "feat: add human-ceiling comparison
workflow", cleanly based on the current feature tip, +1482 / −22 across:
migration `20260801_0007_human_ceiling_evaluation.sql`, `render-backend/app/
human_ceiling.py`, `render-backend/app/main.py` (5 new endpoints), `jobs.py`,
`app/src/pages/Operator.jsx`, and tests (`test_human_ceiling.py`,
`test_operator_api.py` additions, `fake_supa.py`).

## Verdict up front
Strong, careful work. Immutability, lineage separation, ownership/RLS,
authorization, and audit are all correctly built with genuine defense-in-depth.
The gaps are in **metric integrity, one lifecycle dead-end, and test coverage** —
targeted fixes, not a redesign.

## What's genuinely solid (verified against each criterion)
- **Autonomous baselines immutable — YES.** Enforced twice: a CHECK constraint
  (`autonomous_initial`/`autonomous_revised`/`human_approved` must be
  `is_immutable`) AND a `protect_immutable_timeline` trigger blocking DELETE and
  blocking UPDATE of content/lineage/ancestry/version (one-way freeze; can't
  un-freeze). `jobs.py` freezes v1 (initial) and the final (revised) at creation.
  `build_comparison_report` re-asserts immutability at read time.
- **Human timeline = separate lineage — YES.** Distinct `lineage` enum +
  `parent_timeline_id` ancestry. Human work branches from the revised baseline
  into `human_draft` → `human_approved`; autonomous rows are never mutated.
- **Every manual op recorded — YES (within a session).** Each applied op writes a
  `user_corrections` row: `correction_type` (all 10 op types mapped),
  monotonic `operation_index` (unique-indexed per session), base/result timeline,
  `operator_user_id`. Note: recording is session-scoped (edits without a session
  are not logged — acceptable, the human-ceiling flow requires a session).
- **Ownership/RLS — YES.** All new tables RLS-enabled (owner-or-operator read,
  service-role write) with `enforce_project_ownership` + dedicated cross-reference
  triggers ensuring every referenced timeline/session/edit_run shares the same
  project+user. `security definer` functions set `search_path`. Matches the
  established hardening pattern.
- **Operator APIs authorized + audited — YES.** Every new endpoint calls
  `_require_operator` first and `_audit(...)` BEFORE the mutation (audit-before-
  action → 503 on audit failure). Consistent across start/approve/scorecard/
  report/timeline-ops.
- **Fair three-way comparison — YES.** `build_comparison_report` requires all
  three lineages, verifies baselines immutable, produces per-version metrics +
  scorecards + rating/duration deltas; scorecards constrained to exactly the
  three comparison timelines. Deterministic and provably non-mutating of evidence
  (tested).

## Merge blockers (fix before merge)
1. **Correction time is client-supplied — the feature's headline metric is not
   trustworthy.** `elapsed_seconds` (per batch, `main.py` `TimelineOpsBody`) and
   `total_human_seconds` (at approve, `HumanCeilingApproveBody`) come from the
   request body; the frontend runs a client-side stopwatch the operator can pause
   (`Operator.jsx` `setInterval` on `timerRunning`). The only server check is a
   floor (`total_human_seconds + 0.001 < counted_seconds` → 422), and **both
   values can legitimately be 0** (0 ≥ 0 passes). The DB already stores
   `human_edit_sessions.started_at` and per-correction `created_at`; the server
   should derive an authoritative elapsed figure from its own timestamps (with
   idle-gap capping) and treat the client timer as a hint. For a benchmark whose
   entire output is "human took X minutes vs the machine," an unverifiable,
   zero-able number cannot ship as the record.
2. **Session lifecycle dead-end — no abandon path.** Schema has an `abandoned`
   status and a `human_edit_one_active_per_project` unique index, but no endpoint
   sets `abandoned`. Start a session with wrong baselines and you can never start
   another on that project; the only escape is approving a bogus human timeline,
   which then becomes immutable evidence. Add an operator-gated, audited
   `human-ceiling/abandon` endpoint that releases the active session.

## Important improvements
3. **No authorization tests on the new endpoints.** The 5 routes ARE protected in
   code (verified), but every new test asserts 200/409/422 — none assert a normal
   user → 403 or unauthenticated → 401. Add explicit authz regression tests
   (existing `test_normal_user_403` pattern) for start/approve/scorecard/report
   and timeline-ops-with-session.
4. **The DB triggers that provide the immutability guarantee are untested.**
   `fake_supa` only sets defaults; it does not model `protect_immutable_timeline`
   or the ref-integrity triggers, and `scripts/test_db_integrity.py` was not
   extended. So the actual enforcement mechanism has zero automated coverage —
   only the API-level 409 is tested. Add a real-Postgres test (the
   `test_db_integrity.py` pattern) proving UPDATE/DELETE of an immutable timeline
   is rejected and cross-project refs are blocked.
5. **"No revised baseline" edge case.** If an autoedit run had zero revision
   passes, `jobs.py` produces only `autonomous_initial` (the `if index==0` wins
   over `elif index==len-1`) and `edit_run.timeline_v2_id` is null — so
   `human-ceiling/start` can never run on that project. Many strong first drafts
   have 0–1 revisions. Decide intended behavior (allow initial==revised when no
   revision occurred, or support an initial-vs-human-only comparison) rather than
   silently blocking.

## Minor issues
6. `approved_by`/`approved_at` remain mutable after freezing — the immutability
   trigger's allow-list permits rewriting approval attribution on a frozen
   timeline (service-role only). Tighten if approval provenance matters.
7. `operation_index` is computed max+1 via read-then-write; concurrent edits
   could collide (unique index → 500). Fine for one operator; note it.
8. `timelines.edit_run_id` is not set by `jobs.py` at baseline creation (only
   later by `start`), leaving the `(project, edit_run, lineage, version)` index
   partly unpopulated until a session starts. Cosmetic.
9. No unnecessary complexity found — `human_ceiling.py` is cleanly separated from
   selection/render logic, which is the right call.

## Final recommendation: REQUEST CHANGES (small, targeted)
The architecture and security are approve-quality — the immutability / lineage /
ownership / audit design should merge as-is. Hold merge for the two blockers —
**server-authoritative correction time (#1)** and the **abandon endpoint (#2)** —
plus close the **authz + DB-trigger test gaps (#3, #4)** given this is
security-sensitive infrastructure. None require rework; they are additive. Once
those land, this is a clear approve.

---
Reviewer: Claude (branch `claude/editorial-analysis`). Complementary to the
editorial root-cause analysis in this folder: doc 04 estimates the human ceiling;
this branch is the tooling that will measure it — so #1 (trustworthy correction
time) directly gates the credibility of that measurement.
