# Engine improvements, ranked by expected impact

Each item: the failure it fixes (doc 01 category), expected impact, effort,
overfit risk (doc 05), and how Project Two will validate it. **No code is changed
in this branch** — this is the implementation backlog.

Impact = expected points on the honest scorecard across GENERAL footage, not just
Project One. H/M/L effort. All target `render-backend/app/pipeline/`.

---

### P1 — Revision loop: never subtract value  ★ highest impact
- **Fixes:** C (loop) mistakes #1, #2, #10.
- **Change:** (a) if the critic's requests are unsatisfiable with the current
  catalog (ask implies content that doesn't exist — "closer", "different angle",
  "more footage"), STOP and record the ceiling instead of trimming. (b) Add a
  convergence guard: keep the highest-critic-score version; never ship a
  regressed one. (c) Never apply a `trim`/`delete` in response to a "too short /
  add more" request.
- **Impact:** High. Directly reverses the 0.4→0.3 regression; would have shipped
  v1 (26s) over v3 (21.5s). Also prevents wasted provider spend on futile passes.
- **Effort:** M. **Overfit risk:** Low (general loop safety).
- **P2 validation:** with richer footage, revision passes should now *raise* the
  score or stop cleanly — never lower it.

### P2 — Peak selection targets the motion-PEAK moment  ★ high
- **Fixes:** B (editor) — anticlimactic peak.
- **Change:** for the peak beat (and any "highest_motion" beat), pick the segment
  by its `peak_moments`/max-motion window (already in motion.json), and set the
  clip in/out around that peak, not the segment average. De-prioritize
  `uniqueness` for the single climax beat.
- **Impact:** High. The climax is the most-watched moment; "walking away" → real
  flip is a visible upgrade.
- **Effort:** M. **Overfit risk:** Low (use existing per-segment peak data;
  don't hard-code magnitudes).

### P3 — Activity-continuity constraint between adjacent beats  ★ high
- **Fixes:** B/D — the stretch-in-build break.
- **Change:** add a soft penalty when an adjacent pick's `action`/activity class
  differs from its neighbours within a continuous-activity section; allow
  deliberate breaks only at cooldown/reflection beats.
- **Impact:** Med-High. Removes the most jarring cut type.
- **Effort:** M. **Overfit risk:** Med — encode as general "activity coherence",
  NOT "stretches are bad" (see doc 05 #2).

### P4 — Global timeline redundancy check  ★ med-high
- **Fixes:** B — identical location/reflection bookends; 3 near-identical wides.
- **Change:** replace/augment the prev-pick-only `variety` term with a
  whole-timeline dedup: penalize a candidate by similarity (duplicateGroup, same
  action + same source neighbourhood) to ALL already-selected picks, not just the
  last one.
- **Impact:** Med-High on variety perception.
- **Effort:** M. **Overfit risk:** Low.

### P5 — Adaptive template length from measured diversity  ★ med-high
- **Fixes:** D — 7-beat arc forced onto single-activity footage.
- **Change:** choose the beat set from measured content diversity (distinct
  activities / usable non-redundant segments), not a fixed 7. Single-activity →
  compact hook/effort/peak/completion; diverse → full arc. Feed the coverage
  band into this.
- **Impact:** Med-High. Eliminates the empty location/build/reflection holes.
- **Effort:** M-H. **Overfit risk:** Med — make it a function of diversity, don't
  just shrink the default (doc 05 #3).

### P6 — Coverage validator measures diversity, not just presence  ★ med
- **Fixes:** D — "STRONG/55s" false positive on repetitive footage.
- **Change:** band should down-rate redundancy (many segments, few distinct
  activities/angles → not STRONG). Report "repetitive" explicitly and lower the
  duration recommendation accordingly.
- **Impact:** Med (prevents padded targets; sets honest expectations).
- **Effort:** L-M. **Overfit risk:** Low.

### P7 — Action-aligned clip in/out points (rhythm)  ★ med
- **Fixes:** editor/human-gap #4 — whole-window slices, no rhythm.
- **Change:** trim each action clip to start just before its motion onset and cut
  near the motion peak/drop, using motion.json; vary fade by beat (punchier on
  peak).
- **Impact:** Med (pacing is a scorecard line; helps every project).
- **Effort:** M. **Overfit risk:** Low (uses per-clip motion, not constants).

### P8 — Motion comparability across clips  ★ med (correctness)
- **Fixes:** latent bug — per-video adaptive normalization makes motionIntensity
  non-comparable across source files, so cross-clip `motion_fit` ranking is
  subtly wrong.
- **Change:** document the semantics and move to a scene-relative-plus-corpus
  scheme so a 0.8 means the same thing across clips; validate on ≥2 activities.
- **Impact:** Med (fixes ranking correctness that this single project masked).
- **Effort:** M. **Overfit risk:** HIGH if tuned to tire flips — must validate on
  Project Two's different activities before trusting (doc 05 #1).

### P9 — Duration-target fill  ★ low-med
- **Fixes:** D — planned 35s, delivered 26s at selection.
- **Change:** if assembled duration < target and quality candidates remain,
  extend clip lengths or add beats before revision; if footage can't reach
  target, lower the target honestly rather than pad.
- **Effort:** L-M. **Overfit risk:** Low.

### P10 — Surface the footage ceiling to the operator  ★ low-med (product)
- **Fixes:** C #10 — no visible "this is a footage limit" signal.
- **Change:** the run report/console should state which critic requests were
  unsatisfiable due to missing coverage, tying back to the capture guide.
- **Effort:** L. **Overfit risk:** None.

---

## Recommended sequencing
Ship **P1 first** (stops active self-harm, cheapest big win), then **P2+P3+P4**
(the selection-quality cluster that closes most of the human gap in doc 04), then
**P5+P6** (template/coverage honesty), then **P7–P10**. Validate the whole set on
Project Two footage (doc 07) before merging any threshold changes.

**Expected combined effect on comparable footage:** P1–P4 alone should move the
autonomous result from ~4 toward the estimated human ceiling (~5–6) on THIS kind
of footage; the jump past 6 requires the Project Two capture improvements, not
code.
