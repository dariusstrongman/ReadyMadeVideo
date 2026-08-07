# Production Readiness Review — External Principal Engineer Audit

> Branch `feat/editorial-intelligence-phase-2` @ `e18de6d`, 2026-08-08.
> Conducted as an independent review: the mandate was to find reasons NOT to
> merge. Four parallel adversarial audits (Phase 2 module · planner/grounding ·
> engine/bridge/jobs · data model/flags/product) plus direct verification of
> every load-bearing claim. Findings below were verified against source
> (file:line) and, where marked, by executing the failure case. One audit claim
> was struck during verification (a misquoted "prompt corruption"); everything
> retained survived a second look.
>
> Audit only. No code was changed. Nothing merged, nothing deployed.

---

## 1. Executive Summary

This codebase is two systems wearing one name.

The first system — storage ancestry, immutable versioning, deterministic
bridge identity, upload security, honest failure states — is genuinely strong,
DB-enforced, and would survive an acquisition diligence. The second system —
the editorial intelligence that is the actual product thesis — is a
Python-validated pile of JSONB blobs whose central promises do not hold under
adversarial reading:

- **The grounding does not ground.** Evidence is verified per-quote, but a
  claim's words are checked against the *global* catalog vocabulary, not
  against the evidence cited (`editorial_planner.py:711`). A real quote from
  clip 1 legitimizes a fabricated assertion about clip 7. Numbers under three
  characters are never checked at all (`:534`) — every price, count and
  percentage below 100 can be invented freely.
- **The deterministic gate is substring counting.** Rules pass by scanning
  violation *message wording* for hard-coded needles; 16 violation classes
  match no needle, so a plan violating `mustExclude`, wrong aspect, or command
  injection scores 100/100 with `passed: true`. One needle miss is already in
  the tree. On the accepted path the gate contributes nothing — its score has
  essentially two reachable values.
- **The customer approves one video and receives a different one.** Preview
  renders transitions and crops; the final export path structurally cannot
  (instructions live only in `edit_runs.blueprint`, which nothing on the
  export path reads). Every end-to-end test stubs the exact renderer that
  differs.
- **The new Phase 2 tranche has two self-inflicted blockers**: enabling
  `PHASE2_INTEREST_GATE` alone makes 25 of 100 points structurally unearnable
  (verified: score caps at 55–75, journey fails); enabling `PHASE2_DIALOGUE`
  lets the snapper consume transition handles the model reserved, producing a
  deterministic revise ping-pong on any soft transition.
- **Unit economics are invisible.** Worst-case planning burns ~30
  gemini-2.5-pro calls (≈$6/edit at 223 segments; ≈$120+ at 50 clips) while
  telemetry records `5 × $0.0005`. There is no cost guard anywhere.

The test suite (629 backend tests, coverage gates) is extensive and
structurally blind in exactly the places that matter: it pins intended
behavior, stubs the divergent renderer, and asserts storage cleanup against a
fake that cannot see the real S3 gap.

**Recommendation: DO NOT MERGE this branch as-is** (§17), and treat four
pre-existing `main` defects as ship-stoppers for any flag-on rollout.

## 2. Overall Architecture Score: 5/10

The bottom layer (DB triggers, ancestry, immutability, uploads) is 8–9. The
domain layer above it is 3–4: five uncoordinated representations of "the
edit," god modules, message-wording-as-API, JSONB everywhere with schema
enforcement only in Python, and a flag layer that is scattered strings with a
partly fictional doc. Averaging those is misleading, so read 5 as "excellent
foundation, unsound middle."

## 3. Editorial Intelligence Score: 4/10

The two-axis thesis (truth gate + interest floor) is the right architecture,
and the honesty discipline is real and rare. But: the grounding hole above;
the interest gate is proxies with hand-guessed weights (its own doc admits
"tuned against real footage, not guessed" — they were guessed); the hook
"ranking" degenerates to lexicographic order on real catalogs; `_PAYOFF_WORDS`
substring matching declares `rafter_install` a payoff beat; and nothing in the
V2 path ever watches the rendered result. This is rules, not judgment (§18).

## 4. Maintainability Score: 4/10

`editorial_planner.py` is 1,931 lines and five modules in a trench coat.
`validate_plan`'s prose messages are `deterministic_gate`'s undocumented API —
rewording "is not contiguous" silently disarms a 10-point hard rule. The
normalize layer ghost-writes model output (rebinds the hook, deletes captions,
rewrites pacing) with **no audit record of what changed**, making seven
validator rules unreachable dead code. `jobs.py` (1,168 lines) owns the worker,
all handlers, chaining policy, storage routing, status mapping and customer
copy; `main.py` calls its underscore-privates. The migration chain repairs
itself via a hand-ordered reapply list whose comments admit the pattern.

## 5. Scalability Score: 3/10

- **Multi-worker is corrupting, today**: heartbeat is written once *before*
  planning and frozen for the entire run (up to ~20 min of Gemini calls) while
  `JOB_STALE_AFTER_S=900`; a second worker's `recover_stale` re-queues a live
  job → duplicate spend, duplicate plans, duplicate deliverables. The V2
  preview render has the same frozen heartbeat with `timeout=900 == stale`.
- **Unbounded selects everywhere** (`_load_segments`, workspace, recover):
  PostgREST's row cap will silently truncate a >1000-segment catalog into a
  partial catalog, which V2 then rejects as "invented segments" — and loops.
- **`ctx.cancelled()` polls the database every 0.5s for the length of every
  render** — a 20-minute export is 2,400 PostgREST queries to watch a boolean.
- **Catalog is never pruned or paginated** into the prompt: 50 clips ≈ 300k
  tokens twice per attempt, crossing Gemini's pricing tier. No cost cap exists.
- **Storage**: ~0.5–1 MB of `segments.data` per 10-min asset (word timings
  duplicated out of the transcript artifact), 10:1 write-amplification vs
  used footage, one unpartitioned table with a GIN index **no code ever
  queries**, and soft-deleted projects retain every row forever.

## 6. AI Design Score: 5/10

Genuinely good patterns: injected `generate` (fully testable planner),
schema-validated structured output, verbatim-plan repair feedback, honest
`insufficient_footage`. Against that: roughly all of `gemini_generate` and half
the schema builder is gemini-2.5-pro scar tissue (state-budget ladder, two-call
split, uppercase types, wire-maxima hacks) with no provider abstraction — a
model swap rewrites the orchestration layer. The two-call split truncates the
core plan at `[:20000]` chars (~55–70 timeline entries) into *syntactically
invalid JSON* handed to call 2, then blind-merges the result with zero
cross-validation. The degradation ladder's bottom rung (`{"type":"OBJECT"}`)
produces plans with no wire enforcement at all, and nothing records which rung
produced a given plan. Token usage from responses is discarded.

## 7. Biggest Architectural Strengths

1. **DB-enforced ancestry and immutability** (0013/0014 triggers, bridged
   candidate constraints, deterministic uuid5 identity). This is the best work
   in the repo and survives everything above it being wrong.
2. **The honesty discipline as a design value** — `pending_renderer_support`,
   `fabricated_footage=false` checks, no-silent-fallback, computed (not
   trusted) shortfalls. Rare, and the reason this audit could be precise.
3. **Upload security** (server-built keys, service-role-only provenance,
   36 adversarial tests) — done.
4. **The Product Editor's version machinery** (immutable rows, 409-rebase,
   anti-spoof AI actor, exact-version export binding).
5. **Testability seams** (fake_supa, fake_s3, injected model calls) — the
   *instrument* is good even where the coverage aims at the wrong things.
6. **The two-axis thesis itself** — interest subject to truth is the correct
   shape for this product; the implementation, not the idea, is what failed
   this audit.

## 8. Biggest Architectural Weaknesses

Ranked, with verification status:

1. **Grounding hole** — evidence decoupled from claim content
   (`editorial_planner.py:711`, executed). Digits <3 chars invisible (`:534`).
   The product's central claim is not delivered.
2. **Preview ≠ final, structurally** (feature map verified; transport is
   blueprint-only; zero consumers of transition/reframe instructions on the
   export path; tests stub the divergence).
3. **Gate = substring counting** — 16 unmatched violation classes; needle
   already broken once (`durationSeconds does not match` vs `duration does not
   match`); score nearly constant on the accepted path.
4. **Five representations of the edit** (plan.timeline, blueprint.timeline,
   timeline_json, manifest.pictureTimeline, document.tracks) — four go stale
   on the first customer trim, staleness is user-visible today
   (`Project.jsx:524` shows pre-trim numbers as fact), and none is marked
   authoritative.
5. **Segment schema-version coexistence** — v2 and v3 rows are separate rows
   under `unique(asset_id, segment_key, schema_version)`; `_load_segments`
   filters by neither version nor order; the dict-by-id keeps a
   nondeterministic winner and `catalog_hash` changes shape. The v3 bump on
   THIS branch arms this bug for every previously analyzed project.
6. **Customer-writable `timelines`** — RLS `FOR ALL` lets an owner PATCH
   `timeline_json` on non-immutable rows that the server then renders;
   Python-only caps (500 items/3600s) bypassed. Not cross-tenant (asset
   ownership is re-checked at render), but unmetered compute abuse via curl.
7. **Normalize-layer ghost-writing with no audit trail** — including a
   guaranteed infinite loop on any `playbackSpeed != 1` (`cursor += s_out -
   s_in` at `:1604` vs validator's `/speed` at `:788`; executed) and an
   unfixable captionPolicy loop (dropped captions + "captions required" +
   already-mutated plan as feedback).
8. **The Phase 2 tranche's own defects** (see §10 — flag interlock, snap
   ping-pong, discarded ledger, model-writable system field).
9. **Cost blindness** — pricing.json has no Pro line item; failure paths
   record zero Gemini units; no budget guard.
10. **45 hand-rolled auth checks in three inconsistent patterns**, one endpoint
    (`GET /jobs/{id}`) skipping the soft-delete gate entirely.

## 9. Future Bottlenecks

- **The prompt is the database.** Whole-catalog-in-context planning caps
  project size at whatever Gemini's context and your wallet tolerate. Nothing
  retrieves, ranks or pages segments for the model.
- **One worker, frozen heartbeats, optimistic version writers** — the moment
  `WORKER_CONCURRENCY` is honored: duplicate execution (stale recovery),
  duplicate timeline versions (`timelines` has **no** unique(project,version);
  three independent max+1 writers), and the 25-row idempotency horizon.
- **`segments` table growth** with an unqueried GIN index and no
  partition/retention story.
- **The reapply-list migration model** — every future migration touching a
  trigger/policy an earlier one owns grows the hand-ordered list.
- **Message-string coupling** guarantees the gate decays as messages evolve.

## 10. Technical Debt (verified inventory)

**In the new tranche (mine to own):**
- `PHASE2_INTEREST_GATE` alone caps at 75 (loops rules unearnable without
  `PHASE2_HOOK`'s schema+prompt) and at 55 on spoken footage without
  `PHASE2_DIALOGUE`'s repair — flags documented independent, actually
  interlocked. *Executed: score 55, unfixable by revision.*
- Snap widens `sourceOut`, consuming reserved transition handles →
  deterministic revise ping-pong; snap also inflates total duration into the
  requested-range rule. *Executed.*
- Malformed entry mid-timeline → bare `return` discards the adjustments
  ledger after earlier entries were already mutated (silent re-trim, stale
  arithmetic). `dialogueAdjustments` is model-writable despite its
  "system-written" label, and survives the schema ladder's bottom rung.
- Two contradictory definitions of "hook opens the loop" (time vs identity);
  loop seconds validated against a duration normalize rewrote after emission;
  time-frame (timeline vs source) never stated to the model.
- Prompt/code drift: "final act" is secretly 0.6×; word-level rule graded
  while only span-level data is shipped; coherence rule bypassed by adding one
  transcript evidence to a picture-narrating caption.
- `catalog_utilization` floor forces near-total usage on small catalogs
  (3-of-3), penalizing the correct editorial act of dropping bad footage.
- Lexicographic shortlist degeneration; `rafter_install`→payoff; `int()`
  crash on non-integer threshold env (bypasses PlanRejected auto-resume);
  hook out-point re-derived after snapping can itself sever a word; `loops`/
  `dialogueAdjustments`/`interestGate` are write-only data (no consumer);
  `_MIN_CLIP_S=0.75` vs planner's 0.8 with a comment claiming they mirror.
- The byte-identity claim is **not** proven by the flags-off test (four pure
  functions + one stub call; no artifact comparison, no snap/normalize
  integration, no flag matrix, no fixture with word timings *and* quality
  scores — the shape of all real input).

**Pre-existing on main:**
- Grounding/gate/normalize items (§8.1, 8.3, 8.7); insufficient_footage
  bypasses gate-failure reporting (`:1800` condition); `_stem` false-accepts
  ("wine" from "won", "cars" from "care") while the label vocab false-rejects
  `"3 Steps"`, `"Finally"`, `"Link In Bio"` — the commonest CTA in the medium
  cannot be written.
- Stale approved plan → `PictureEditRejected` loop with the raw exception as
  customer copy; `catalog_hash` computed, stored, **never compared by anyone**.
- `EXPORT_STORAGE_PROVIDER=s3` breaks every candidate preview (bridge
  hardcodes bucket "exports"; preview signing is Supabase-only). *The fix
  already exists as unmerged PR #9* — this audit independently rediscovered it.
- Project deletion returns `"cleanup": "complete"` while never deleting S3
  raw footage or S3 exports — a false erasure statement (GDPR exposure), with
  a green test asserting cleanup against a fake that can't see S3.
- V2 preview render: cancel reports *failed* (raises RenderError after
  blocking `subprocess.run`, contradicting the documented cancellation model);
  no heartbeat during render.
- Handle validation asymmetric to the renderer (rejects valid head-handle-zero
  edits; latent frozen-frame bug when file duration < segment sourceEnd).
- `enqueue_job` dedupe misses `cancel_requested` (DB index covers only
  queued/processing) → two active jobs of one kind; editor-render duplicate
  check inspects only the first active render.
- Telemetry: `cpu_hours: 0` hardcoded on Product Editor exports;
  `gemini_requests` counts attempts, not calls, priced as Flash.
- Flag topology: 7 of 12 documented PHASE2 flags don't exist;
  `PHASE2_SUBSTRATE` documented but the substrate ships unflagged (the most
  storage-expensive change on the branch); `.env.example` documents none of
  it; zero tests combine PHASE2_* with `PICTURE_EDIT_ENGINE_V2_ENABLED`.
- Debuggability: "why is this cut here" = six joins across five tables plus
  JSON-field links with no indexes; split clips break provenance permanently;
  `continuityFindings`/`technicalWarnings`/`unsupportedExecution` never reach
  any surface — the engine's "surfaced, never silently replaced" claim is
  untrue at the product boundary.

## 11. Systems That Should Be Redesigned (before Phase 3)

1. **Violation transport**: structured codes (`{code, msg, data}`) replacing
   message-substring needles, with a test asserting every emitted code maps to
   exactly one gate rule. This single change de-risks the planner more than
   anything else.
2. **One edit representation with derived projections** — pick the blueprint
   as source of truth, mark every other copy as a projection with a version
   stamp, and add staleness detection at the editor and workspace boundaries.
3. **The normalize layer** → a logged, bounded "repairs" pipeline that emits
   a `normalizations[]` record persisted beside the plan; stop rebinding
   `hook.segmentId`; fix the playbackSpeed cursor.
4. **Flag registry**: one typed module, one doc, one `.env.example` section,
   an explicit dependency graph (INTEREST ⇒ HOOK+DIALOGUE), and a
   flags-recorded-on-artifact rule so every plan row knows the regime that
   produced it.
5. **Grounding scope**: allowed vocabulary = cited segments + quotes, not the
   global pool; digit tokens always checked; `_stem`'s `+e`/reverse-e rules
   deleted.
6. **Cost governance**: real token accounting from `usageMetadata`, a per-job
   budget with an honest failure, and Pro-tier line items.

## 12. Systems That Are Ready

- The bridge and candidate ancestry (timeline-bound uuid5 identity,
  persist-or-reopen cleanup) — ship-grade.
- S3 multipart upload path and its test suite.
- Product Editor operation/versioning core (given M-class fixes around
  duplicate render checks).
- The analysis pipeline's resumable per-stage artifact model.
- fake_supa/fake_s3 harnesses — the right instruments, extend their reach.

## 13. Hidden Risks

- **Green tests over real defects** — the S3-cleanup test and the stubbed-
  renderer e2e are worse than absent tests: they certify the wrong thing.
- **The vocabulary gate quietly shapes the product voice** — it structurally
  cannot produce standard CTAs or ordinal labels, so "the AI writes bland
  titles" will be misdiagnosed as a model problem when it is a validator
  problem.
- **Operator plans inherit customer Phase 2 rules** invisibly (flags are
  global inside `plan_editorial`; nothing on the row records which regime).
- **`human_ceiling.py` hardcodes `schema_version: 2`** — a third meaning of
  the field, drifting silently.
- **PR #9 (preview S3 fix) is sitting unmerged** while the defect it fixes is
  listed as supported configuration.
- **Two full editing engines are live** behind one env var; the legacy one is
  reachable, must stay tested, and silently *replaces* an approved V2 plan if
  the flag flips mid-journey (customer gets a template cut with no log).

## 14. What Will Break During Phase 3 (critic · render parity · B-roll)

- **Fixing preview/final parity detonates the stale-blueprint landmine**: the
  moment export reads `transitionInstructions`/`reframeInstructions`, every
  editor operation since bridging invalidates geometry (`boundaryIndex` into a
  reordered timeline, pan interpolation against changed durations). Parity
  work REQUIRES the single-representation redesign (§11.2) first, or it will
  render garbage with a zero exit code.
- **The critic multiplies ungoverned Gemini spend** on top of an already
  uncounted ~$6 worst case, with no budget mechanism to bound the loop.
- **B-roll/split edits break `_reflow`'s butt-joined world**: the Product
  Editor's timeline model has no gaps, no overlaps, no audio-independent
  picture — J/L cuts are unrepresentable in the editor's document schema, so
  Phase 3's flagship feature cannot round-trip through the customer's own
  editing surface.
- Every new plan section grows the two-call split's truncation pressure
  (already invalid past ~60 entries).

## 15. What Will Break During Phase 4 (audio · tournament · learning)

- **Audio-to-customer requires surgery across six files + a migration**
  (HANDLERS/FAIL_STATUS, both chaining functions, the kind CHECK, the bridge's
  `audio_mix_run_id is None` fork, two divergent export paths, frontend state
  derivation) — the god-module bill comes due here.
- **Tournament (N plans) multiplies cost N× with no accounting and collides
  with the 25-row idempotency horizon and version-race writers.**
- **Learning loops need provenance that doesn't exist**: Segment has no
  measured-vs-hallucinated marker; `editor_operations` correction data can't
  be joined to plan decisions (`reason` is dropped at the editor boundary,
  split ids break the string-match); `interestGate` telemetry is write-only.
  You cannot learn from data you cannot attribute.
- **Multi-worker is a precondition for all of Phase 4's throughput and is
  currently corrupting** (§5).

## 16. What I Would Build Differently Starting Today

1. **Retrieval, not recitation**: a planner that queries the catalog (ranked
   candidates per beat) instead of pasting 300k tokens of it, with the
   vocabulary check scoped to what was retrieved.
2. **One typed Edit object** in one table, projections generated and stamped,
   DB uniqueness on (project, version) for anything ordered.
3. **Structured violations from day one** — codes as the contract among
   validator, gate, feedback and telemetry.
4. **A provider boundary**: `generate_structured(prompt, schema, budget) ->
   (obj, usage, enforcement_level)`, with the Gemini ladder as one
   implementation detail and enforcement level recorded on every artifact.
5. **Budget as a first-class job field**, spent from on every model call,
   with honest exhaustion.
6. **Flags as code** (typed registry, dependencies, per-artifact recording).
7. **Judgment where judgment belongs**: heuristics (regex hooks, magic
   thresholds) only ever as *candidate generators and floors*; selection and
   quality decided by a critic that watches rendered output plus measured
   audience/customer signal — the current design's rules would be the
   scaffolding, never the verdict.

## 17. Merge Recommendation

**DO NOT MERGE.**

Not because the tranche is sloppy — it is well-tested against its own
intentions — but because:

1. Two of its five flags are unsafe to enable as shipped (INTEREST bricks the
   journey alone; DIALOGUE ping-pongs on soft transitions), and the flag
   independence the PR claims is false.
2. It arms a pre-existing blocker: the v3 schema bump creates coexisting v2/v3
   segment rows with nondeterministic catalog loading for every already-
   analyzed project on re-analysis.
3. Its checks are wired into a validator whose grounding and gate have holes
   that the new rules inherit (violations that match no needle, evidence
   decoupled from claims).
4. Its own byte-identity guarantee is asserted but not proven by the tests.

Merge path: fix the tranche's two flag blockers + the ledger/`playbackSpeed`/
threshold-crash items, add the flag-dependency interlock, prove flags-off
byte-identity with a real artifact comparison, and land migration-side
handling for the v2/v3 row coexistence (filter latest schema_version at
`_load_segments`) — then merge with all flags off, and treat §11 as the
Phase 3 gate. Separately: merge PR #9, and fix the false `"cleanup:
complete"` before anyone says "GDPR" in a sales call.

## 18. Brutally Honest Final Thoughts

This is the most disciplined solo-built pipeline I have reviewed, and that
discipline is concentrated exactly one layer below where the product lives.
The team can clearly build correct *systems*; what's on top is a correct-
looking system wrapped around unverified promises. Three patterns recur:

**Promises enforced by prose.** The gate trusts message wording; the model is
graded on rules stated to it in different units than the code checks; the docs
list flags that don't exist. Wherever the contract is a string, it has already
drifted — this codebase's single most consistent failure mode.

**Honesty at the artifact, silence at the surface.** Enormous care goes into
recording truthful data (`pending_renderer_support`, `continuityFindings`,
computed shortfalls, `interestGate` breakdowns) that no customer, operator,
or process ever reads. Honesty that reaches no one is a cost center, not a
virtue. Close the loop or stop paying for it.

**Rules mistaken for taste.** The Phase 2 layer encodes what editors *do*
(cut at sentence ends, escalate energy, open loops) as thresholds, and the
audit shows what always happens next: `rafter_install` becomes a payoff,
"Link In Bio" becomes fabrication, a 3-cut edit becomes "under-utilization."
Heuristics are fine as floors and candidate generators — the roadmap's own
Murch warning says so — but nothing currently sits above them to exercise
judgment, because the one component that would (a critic watching the render)
was traded away in V2 and hasn't returned. Until something watches the video,
this system can only ever converge on *defensible* edits. The stated goal is
*compelling* ones. Those are different asymptotes.

The good news, stated plainly: the foundation deserves the investment, the
thesis is right, and every blocker in this report is fixable in weeks, not
quarters — provided the team treats this audit's §11 as the price of Phase 3
rather than a list of disagreements to litigate.
