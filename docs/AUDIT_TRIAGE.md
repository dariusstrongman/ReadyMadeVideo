# Audit Triage — Finding-by-Finding Reproduction and Fix Plan

> Companion to `PRODUCTION_READINESS_REVIEW.md`. Every finding was reproduced
> (EXECUTED = failure case run in this worktree via `repro_audit.py`;
> READ = verified against source/migrations at the cited lines) and ruled on.
> One audit claim was REJECTED (misquoted "prompt corruption" — the two
> sentences at `editorial_planner.py:1913/1922` are legitimate parallel
> instructions). Everything else survived.
>
> **No code has been changed. Each batch below awaits explicit approval.**

Legend: impact = **A** (architectural — shapes future work) / **L** (local —
contained fix). All "smallest fix" entries are the minimal *correct* change,
not the minimal diff.

---

## Batch A — this branch's own defects (fix before its merge)

| # | Finding | Repro | Verdict | Root cause | Impact | Smallest correct fix |
|---|---|---|---|---|---|---|
| A1 | `PHASE2_INTEREST_GATE` alone: 25/100 points unearnable (score 75 ceiling; 55 on spoken footage) | EXECUTED (R5) | **Correct** | Gate scores subsystems whose *inputs* other flags provide (loops need HOOK's schema+prompt; dialogue repair needs DIALOGUE's catalog data) | A — false flag-independence claim | Interest gate includes only rules whose feeding flag is ON; score normalized to the applicable weight total. Flags become genuinely independent |
| A2 | DIALOGUE snap widens `sourceOut`, consumes reserved transition handles → deterministic revise ping-pong | EXECUTED (R6: 4.5→4.9, violation created by the system) | **Correct** | Snapper is blind to `raw["transitions"]` reservations | A — system-caused rejections poison the repair loop | Snap upper bound = `sourceEnd − reserved_tail_handle(entry)` (reserved from the plan's own transitions); prefer inward snaps when the reserved bound blocks outward |
| A3 | Malformed entry mid-loop: bare `return` keeps earlier mutations, discards the adjustments ledger | EXECUTED (R7) | **Correct** | `return` vs `continue`; ledger written only at function end | L | `continue` on per-entry parse failure; write the ledger before any early exit |
| A4 | Normalize cursor ignores `playbackSpeed` → permanent normalize/validator disagreement, infinite repair loop | EXECUTED (R3: 8.0 vs 4.0) | **Correct** (pre-existing, but this branch touches the function) | Docstring assumed "speed fixed 1.0"; schema permits ≤8 | L, high blast | `cursor += (s_out − s_in) / (playbackSpeed or 1)` |
| A5 | `int()` crash on non-integer `PHASE2_INTEREST_THRESHOLD` → `analysis_failed` with a traceback, bypassing PlanRejected auto-resume | EXECUTED (R11) | **Correct** | Unguarded env parse | L | Parse with fallback to 60 + log a config warning |
| A6 | `dialogueAdjustments` is model-writable despite "system-written" label | READ (schema ladder bottoms at bare OBJECT) | **Correct** | Field on the shared model with no wire exclusion | L | `raw.pop("dialogueAdjustments", None)` at normalize entry — the system re-derives it or it doesn't exist |
| A7 | Hook out-point re-derived *after* snapping can itself sever a word | READ (`editorial_planner.py` hook rebind runs post-snap) | **Correct** | Order of operations in normalize | L | Derive `hook.sourceOut` from the already-snapped `timeline[0]`, then apply the same word check with shrink-to-safe |
| A8 | `_MIN_CLIP_S=0.75` vs planner clamp 0.8, comment claims they mirror | READ | **Correct** | Duplicated constant | L | Import the planner's constant; delete the local one |
| A9 | Two contradictory "hook opens loop" definitions (time vs identity); loop seconds validated against a duration normalize rewrote; time-frame never stated | READ | **Correct** | Rule authored twice; prompt under-specified | L | One shared predicate (identity AND `openedAt ≤ hook.duration`); prompt states "timeline seconds"; validate loops *after* normalize (already the case) with the rewritten duration + tolerance |
| A10 | `_PAYOFF_WORDS` substring match: `rafter_install` → payoff | EXECUTED (R10) | **Correct** | `in` on raw string | L | Tokenize beat name on non-alpha; whole-token match |
| A11 | `catalog_utilization` floor forces near-total usage on small catalogs (3-of-3) | READ + arithmetic | **Correct** — and editorially wrong, contradicts drop-bad-footage | Guessed formula | L | Apply the rule only when `len(segments) ≥ 20`; floor `len//10` |
| A12 | Hook shortlist degenerates to lexicographic order on flat catalogs | READ (tie-break on segmentId) | **Correct** | Quality-only candidates admitted at identical scores | L | Admit only archetype- or storyUses-hook candidates; richer deterministic tie-break (motion percentile, focus) for the rest. Empty-shortlist-never-blocks stays |
| A13 | Coherence rule bypassed by adding one transcript evidence to a picture-narrating caption | READ | **Correct** | Rule tests the evidence *set shape*, not verification | L | Require ≥1 **verified** transcript/user quote (reuse the existing verbatim check), not mere presence |
| A14 | Temporal-contiguity silently no-ops on quotes spanning sentence boundaries; `break` half-checks multi-evidence captions | READ | **Correct** | Single-span substring lookup | L | Search the joined span text; iterate to the first *resolvable* evidence |
| A15 | Prompt/code drift: "final act" vs 0.6×; word-level rule graded while only span-level data shipped | READ | **Correct** | Prose written separately from code | L | Prompt states "closes in the final 40%"; dialogue prompt says "cut at the span edges listed" (span edges are always word-safe, so the shipped data suffices) |
| A16 | Byte-identity claim asserted, not proven; no snap+validate integration test; no flag matrix; fixtures lack word timings + quality together | READ (test inspection) | **Correct** | Tests pinned intent, not invariants | A (test architecture) | Golden-artifact flags-off comparison; snap→normalize→validate integration test; flag-pair matrix incl. INTEREST-alone; one realistic fixture (words + spans + scores) used across the file |

Also folded into Batch A: interest-failure feedback names a shortlist the
model never saw (resolved by A1's interlock — the rule only fires when the
shortlist was shown).

## Batch B — small, high-confidence fixes on main (separate PR, no behavior redesign)

| # | Finding | Repro | Verdict | Root cause | Impact | Smallest correct fix |
|---|---|---|---|---|---|---|
| B1 | `EXPORT_STORAGE_PROVIDER=s3` breaks every candidate preview | EXECUTED in production (Aug 7) — **fix already exists as PR #9** | **Correct** | Preview signing never got the provider branch the export path has | L | **Merge PR #9** (provider recorded on candidate + both-store fallback; 4 tests) |
| B2 | Segment v2/v3 rows coexist; loader takes a nondeterministic winner; catalog_hash shape changes | READ (unique triple + unfiltered, unordered select; dict last-wins) | **Correct** — armed by this branch's v3 bump | Version-carrying unique key with no read-side resolution | A | `_load_segments`: keep max `schema_version` per `(asset_id, segment_key)` in Python + stable order. (Row GC is a later cleanup task) |
| B3 | Project deletion returns `"cleanup": "complete"` while S3 raw footage + S3 exports survive | READ (`_cleanup_project_storage` iterates Supabase buckets only; `s3store.delete_object` called only from abort paths) | **Correct** — false erasure statement | Delete path predates S3 support | L→A (compliance) | Delete S3 keys recorded on `media_assets` + S3 exports by prefix; response reports itemized, truthful results |
| B4 | V2 preview render cancel → "failed" + raw error; no heartbeat while `timeout == STALE_AFTER` | READ (`subprocess.run` blocks; `RenderError("cancelled")`) | **Correct** | Render call copied without `renderer2`'s `_run_interruptible` | L | Adopt `_run_interruptible` + raise `JobCancelled` + heartbeat tick in the poll loop |
| B5 | `enqueue_job` dedupe misses `cancel_requested` (DB index covers queued/processing only) → two active jobs | READ (0005 partial index vs `ACTIVE_STATES`) | **Correct** | Code and index disagree on "active" | L | Return the found duplicate before inserting (code-only; no migration) |
| B6 | V2 reuse path never sets project status → valid draft hidden behind a stale failure state | READ | **Correct** | Only success path without `set_project_status` | L | Set `draft_ready` on the reuse return |
| B7 | Digit tokens <3 chars never checked in factual claims | EXECUTED (R2) | **Correct** | Length filter predates digit reasoning | L | Any token containing a digit is a content token |
| B8 | Heartbeat frozen for the whole planning run; multi-worker stale-recovery duplicates jobs | READ (single pre-planning `update_job`; 900s threshold vs ≤20min runs) | **Correct** | Heartbeat piggybacked on progress updates only | A (blocks concurrency) | `update_job` heartbeat at the top of each planner attempt; assert `STALE_AFTER > attempts × 2 × timeout` at startup |
| B9 | Cost: Pro calls priced as Flash-per-request; attempts counted, calls not; failure path records zero; no budget | READ (pricing.json + `ctx.rec` sites) + arithmetic | **Correct** (~2,500× understatement stands) | Telemetry predates the two-call Pro planner | A (economics) | Record `usageMetadata` tokens from Gemini responses; add Pro token line items; record units on the PlanRejected path; `PLAN_BUDGET_USD` guard with honest failure |
| B10 | `human_ceiling.py` hardcodes `schema_version: 2` (third meaning of the field) | READ | **Correct** | Name collision | L | Rename the report field `reportSchemaVersion` |
| B11 | Docs list 7 nonexistent flags; substrate documented as flagged but ships unflagged; `.env.example` documents none of it | READ | **Correct** | Doc written before implementation narrowed | L | Correct the doc to shipped flags; document all real flags + defaults in `.env.example`; note substrate is always-on by design |

## Batch C — correctness majors needing judgment (each its own PR + tests)

| # | Finding | Repro | Verdict | Root cause | Impact | Smallest correct fix |
|---|---|---|---|---|---|---|
| C1 | **Grounding hole**: evidence verified per-quote, claim words checked against the global pool | EXECUTED (R1: zero violations for cross-segment recombination) | **Correct** — the central product claim fails | `allowed = pool + quotes` at `:711`; pool spans all segments + constraints (incl. mustExclude terms) | A | Scope `allowed` to **cited segments' text + verified quotes + user text + neutral words**. Expect a rejection-rate rise; roll out with measurement |
| C2 | **Gate = substring needles**: 16 violation classes unmatched; `passed: true` over real violations; wording is load-bearing API; one needle already broken | EXECUTED (R4: score 100/passed over mustExclude) | **Correct** | Messages double as machine contract | A | Interim: add the missing needles + a meta-test walking every `v.append` message class to ≥1 gate rule; fix the `durationSeconds` needle. Full structured codes = Phase 3 (D2) |
| C3 | `insufficient_footage` bypasses gate-failure reporting | READ (`:1800` condition) | **Correct** | Guard written for the approved path | L | Append gate failures for shortfall plans too; keep `duration_compliant` shortfall-aware (it already is) |
| C4 | `_stem` false accepts ("wine"←"won", "cars"←"care"); label vocab rejects "3 Steps"/"Finally"/"Link In Bio" | EXECUTED (R8, R9) | **Correct both directions** | `+e`/reverse-e collision rules; no stemming in the label path; missing CTA terms | L | Delete the `st+"e"`/reverse-e rules; apply `_stem` inside `_non_neutral_tokens`; extend the closed CTA list deliberately (product call on the exact words) |
| C5 | **Stale approved plan → infinite failure loop**; `catalog_hash` computed and compared by no one; raw exception as customer copy | READ (grep: zero comparisons; retry path re-selects same plan) | **Correct** | Binding designed, never enforced | A | In `handle_autoedit_v2`: stored `sourceCatalogHash` ≠ current ⇒ mark plan `superseded` + auto-enqueue one re-plan (customer path); humanize the rejection copy |
| C6 | Transition handle validation asymmetric to renderer (rejects valid head-handle-0 edits); latent frozen-frame when file < segment end | READ (validator `min(tail,head)` vs renderer tail-only) | **Correct** | Validator written to an idealized xfade | L | Validate what the renderer consumes (tail handle only); clamp `e_ext` against probed duration *and* warn on shortfall |
| C7 | Unbounded selects hit PostgREST row cap → silently partial catalog → "invented segment" rejections | READ (`db_select` never sends limit/Range) | **Correct** | Convenience wrapper | A | `db_select` accepts `limit`; segment loads paginate with a count check; workspace queries bounded |
| C8 | `timelines` has no `unique(project,version)`; three optimistic writers; "latest" pick is arbitrary under collision | READ (0001 vs 0022) | **Correct** | Missing constraint | A (migration) | Migration: unique index + writers retry on conflict (bridge already has the pattern) |
| C9 | Cancel poll = 1 DB query/0.5s for entire renders | READ | **Correct** | Simple poll | L | 3s interval + reuse the query as the heartbeat write |
| C10 | Normalize ghost-writes with no audit record; captionPolicy drop-loop; 7 dead validator rules | READ | **Correct** | Repairs accreted without a ledger | A | Emit `normalizations[]` on the result, persist beside the plan; feedback names dropped captions. (Hook-rebind removal deferred — behavior change needing data) |
| C11 | Two-call split truncates core plan at 20k chars into invalid JSON; blind merge; ladder bottom rung unrecorded | READ | **Correct** | Pragmatic limits, no guards | A | Hard-fail (violation) when serialized core exceeds the window instead of truncating; record the ladder rung used on the result; cross-check strict-call segment refs against the core timeline |
| C12 | `timelines` RLS `FOR ALL` lets owners PATCH `timeline_json` the server renders (Python-only caps bypassed) | READ (0019 policy; render path) | **Correct** (compute abuse, not cross-tenant — asset ownership re-checked at render) | Policy predates server-authored timelines | A (migration) | Migration: authenticated = SELECT-only on `timelines`; all writes already go through the service role |

## Batch D — architectural (Phase 3 gate; design docs first, then build)

| # | Finding | Verdict | Smallest correct *path* (not a patch) |
|---|---|---|---|
| D1 | **Preview ≠ final, structurally** — and fixing it detonates the stale-blueprint landmine (editor ops invalidate transition/reframe geometry) | **Correct**; the single most customer-damaging issue | Design first: blueprint becomes the source of truth with stamped projections + staleness detection at editor/workspace boundaries; final render consumes the same instruction set as preview (converge on `picture_render_v2`'s capabilities). Do NOT wire blueprint→export before the staleness work |
| D2 | Structured violation codes replacing message needles | **Correct** | `{code,msg,data}` transport; gate keyed on codes; C2's meta-test becomes the migration harness |
| D3 | Flag registry + per-artifact regime recording | **Correct** | Typed module, dependency graph (INTEREST⇒HOOK+DIALOGUE), flags recorded on every plan row |
| D4 | Catalog-in-prompt scaling (300k tokens at 50 clips) | **Correct** | Retrieval/ranking of segments per beat before prompting; hard cap with honest failure meanwhile |
| D5 | Debuggability: 6-join provenance, split-clip attribution loss, honesty artifacts unread | **Correct** | Correlation id threaded plan→run→job→document; surface `pendingExecution`/warnings in operator UI |
| D6 | `jobs.py` god module / audio-milestone surgery | **Correct** | Extract handler registry + chaining policy before Phase 4 audio work |
| D7 | Segment provenance (measured vs AI) + `segments` growth/retention | **Correct** | Provenance envelope in Segment v4; retention policy for soft-deleted projects |

## Rejected / adjusted audit claims

- **REJECTED**: "shipped prompt corruption" at `editorial_planner.py:1919-22` — misquote; both sentences are coherent parallel instructions.
- **Adjusted**: "gate contributes nothing" — overstated; six structural rules do compute independently. The accurate claim (score nearly constant on the accepted path; 16 unmatched classes) stands and is what C2 fixes.
- **Adjusted**: "five representations, no source of truth" — the copies exist deliberately for immutability/audit; the *defect* is no stamping or staleness detection, which is what D1 addresses (not a collapse to one table).
- **Noted**: unused GIN index on `segments.search_text` — real, but removal is a product decision (search is a plausible roadmap feature); parked in D7.

## Proposed order

1. **Batch A** on this branch → re-run gates → then the branch is honestly mergeable (flags off).
2. **Batch B** as one small PR on main (plus merging PR #9, which predates this audit).
3. **Batch C** as individual PRs, C1/C2/C5 first — they are the product's credibility.
4. **Batch D** as the Phase 3 entry gate, D1 first, design doc before code.

**Stopped here. No code changed. Which batch (or subset) do you approve?**
