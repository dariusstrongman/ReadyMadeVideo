# Output Intelligence

**Branch:** `feat/output-intelligence` · **Flag:** `OUTPUT_INTELLIGENCE_ENABLED` (default OFF)

The stage between analysis and the Editorial Planner that answers *"what is
worth making?"* before *"how should it be edited?"*. With the flag off, every
endpoint 404s, nothing is written, and analysis chains exactly as before
(covered by test).

## Flow

```
UPLOAD → ANALYSIS → recommendation (deterministic, free, idempotent per
catalog identity) → customer accepts / customizes → deterministic feasibility
→ output_package + output_deliverables → each child: editorial_plan job
(existing) → autoedit (existing) → timeline + bridged candidate → editor /
export (existing, per child)
```

## Design decisions & why

- **The engine is pure and free.** `pipeline/output_intelligence.py` derives
  everything from the stored segment catalog: no model calls, no re-analysis,
  no invented signals. Signals used: `storyUses` (hook/peak/completion/
  reflection — the analyzer's own vocabulary), `problems`, `duplicateGroupId`,
  `speechSpans`, `motionPeaks`, quality scores, `location`/`subjects`,
  source ranges. This kills the N×-inference risk by construction.
- **Usable ≠ raw.** Inventory excludes hard-problem segments (analyzer prose,
  substring-matched), collapses duplicate groups to one representative, and
  everything downstream (durations, quantities) runs off usable seconds.
- **Feasibility is deterministic and final.** `SUPPORTED /
  SUPPORTED_WITH_CONSTRAINTS / NOT_RECOMMENDED / IMPOSSIBLE`, every negative
  with a machine-readable code and the nearest honest alternative. Duration
  bounds reuse the planner's own `MAX_OPEN_COMPRESSION`; this stage never
  green-lights what the planner would reject. Malformed input (quantity ≤ 0,
  non-positive durations) rejects — never coerced.
- **Shorts are semantic moments, not chunks.** Candidates grow from
  hook-evidenced seeds to a payoff or the ceiling; scored on hook strength,
  coherence (payoff present, dialogue-safe edges), measured quality; diversity
  enforced on source overlap (>50% = same moment), duplicate ancestry, and
  repeated (location, action) identity. If only 3 exist, 3 are offered.
- **Long-form needs a dominant story.** Several independent stories (location/
  subject clustering with gap splitting) become several separate videos, never
  one forced container. Speech-dominant footage compresses gently (0.6×) vs
  action (0.35×).
- **One catalog identity everywhere.** `catalog_hash` is algorithm-identical
  to `picture_edit_v2.catalog_hash` (asserted equal by test, not imported):
  recommendations bind it, packages bind it, `advance` re-checks it, GET
  reports staleness live. Stale acceptance → 409; mid-package change →
  remaining children cancelled with the reason; finished children never revoked.
- **Deliverables ride the existing pipeline sequentially.** One child = one
  `editorial_plan` job carrying `deliverable_id`, which the approval chain
  propagates to autoedit. `pipeline_jobs_active_uniq` (one active job per
  project+kind) makes sequencing race-free with zero new locking. Worker hooks
  (`_deliverable_hook` on all three terminal paths) harvest exact
  plan/timeline identities onto the child at creation time.
- **Package status is derived, never stored.** complete / partial / failed /
  processing / cancelled computed from children on read. Retry is single-child
  and idempotent. Budget death fails the child that hit it and
  `budget_blocked`s the rest instead of marching them into the same wall.
- **Persistence (migration 0028, additive):** `output_recommendations`
  (unique project+catalog+engine), `output_packages` (unique request_key —
  double-click/concurrency safe), `output_deliverables` (unique
  package+position; spec, status, plan/timeline ancestry). RLS mirrors 0022:
  select-own ∧ project_not_deleted, operator read, ownership trigger,
  service-role writes.

## Audit ledger (every finding kept, per directive)

| # | Finding | Severity | Root cause | Fix | Regression test |
|---|---------|----------|-----------|-----|-----------------|
| 1 | Deleted-project test failed: queued children survived deletion | med | test simulated deletion without the terminal event production emits | test corrected to drive the autoedit-cancelled event; advance cancels queued children on deleted project | `test_case21…` |
| 2 | `quantity: 0` silently became 1; negatives passed | high | `or 1` coercion in aggregation ran before validation | reject `invalid_quantity`/`invalid_duration`; aggregate only well-formed items | `test_audit1…` |
| 3 | Foreign-job capture: child claimed an operator's active plan job and stranded forever | high | enqueue idempotency returns any active (project, kind) job | verify returned job's `deliverable_id`; else leave child queued for self-heal | `test_audit2…` |
| 4 | Mid-package footage change let later children plan against unseen material | high | staleness only checked at accept | `advance` re-checks catalog hash; cancels remaining queued children honestly | `test_audit3…` |
| 5 | Orphaned children: stale-recovery fails jobs without firing the hook | high | `recover_stale` bypasses `_deliverable_hook` | `reconcile_package` on every list: jobless active child → retryable failure | `test_audit4…` |
| 6 | Two `long_form` items on one story → same video twice | med | spec expansion fell back to `pool[0]` for every item | N long-forms require N stories (`long_form_count_exceeds_stories`); expansion consumes without replacement | `test_audit5…` |
| 7 | Ruff B023 in `renderer2.py` would fail CI's `ruff check app/` | med | pre-existing (same-day punch-in work on main) | bind loop var as default arg | ruff gate |
| 8 | Concurrent-accept 409 loser may briefly return 0 children | low | children inserted after package row | accepted: winner's response correct; poll self-heals in ≤5s | noted, not fixed |
| 9 | Plan job failures still flip project status (`analysis_failed`) while a package runs | low | existing journey machinery, flag-on only, package view is authoritative | accepted for slice; noted for follow-up | — |

## Honest limitations

- **No live end-to-end run.** Flows A–D are verified to the timeline/ancestry
  boundary via the worker-hook contract against the fake DB (plus source-level
  key assertions); a real Gemini plan + real render per child needs staging
  with the flag on. Flow F (flag off) is fully tested.
- **Render remains user-triggered per deliverable** (editor flow, unchanged).
  A deliverable's terminal success state is `ready` (timeline + candidate
  exist); the package view says so and never claims exports happened.
- **Purpose taxonomy is conservative** (story/interview/highlight/condensed/
  excerpt): only what current analysis evidences. `tutorial`/`vlog` need
  richer signals than the catalog stores today.
- **`mustInclude` binding for shorts** pins the seed moment's action text into
  the plan (validated by the planner); scope containment beyond that (planner
  choosing only from the short's source range) would need a planner-side
  constraint — deliberately not added to avoid touching planner semantics.
- **Migration chain validated in CI only** (no local Postgres); 0028 follows
  the proven 0022 idiom exactly.

## Rollout / rollback

1. Merge with flag unset (OFF) — inert everywhere; verify classic journey.
2. Apply migration 0028 (additive only).
3. Set `OUTPUT_INTELLIGENCE_ENABLED=1` on staging; run a real multi-clip
   project end to end (Flows A–D) with real provider calls.
4. Enable in production.
5. Rollback = unset the flag: classic journey resumes instantly; tables remain
   (inert, additive); no schema rollback required.
