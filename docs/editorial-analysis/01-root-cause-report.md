# Project One — root-cause report for the 4/10 result

Scope: editorial analysis only. No pipeline code changed; no Project One
artifacts modified. Evidence read from `project-one-local/` (run2) and the
original footage. Where a claim rests on pixels, it was frame-verified.

## Headline
The 4/10 is **~60% footage ceiling, ~40% fixable engine behavior.** The pipeline
executed end-to-end correctly (0 validator errors, all beats filled), understood
the footage accurately, and its own critic diagnosed the real problems. But three
engine subsystems — the **revision loop**, the **ranking/uniqueness logic**, and
the **fixed 7-beat template** — actively degraded a result the footage had
already capped. Critically, the critic's score went **0.4 → 0.3 across two
revision passes**: the autonomous loop made its own output measurably worse.

## Four-way failure attribution (the core ask)

### A. Footage-caused (unavoidable — no engine change recovers these)
- **No close-ups exist.** Every usable segment is a distant wide; the subject is
  a small figure (frame-verified: hook frame = tiny person by a tire). Critic:
  `importantActionsVisible` true→false as trims shrank context. This is the
  single largest cause of the low score and is 100% a capture problem.
- **One camera position, ~one angle.** No tracking, no reverse, no low-angle.
  Shot-variety is structurally impossible; `enoughShotVariety` = false was
  unwinnable.
- **~80% of segments are the same activity (tire flips).** 165 segments, but the
  distinct *content* is thin: tire flips, tire drags/carries, walking across the
  field, a little stretching. A montage of one repeated exercise cannot carry a
  7-beat narrative arc.
- **Audio is two people chatting**, not focused effort sound. `naturalSoundEffective`
  true→false. No de-noise/isolation would create effort SFX that weren't recorded.

### B. Editor-caused (selection/build — fixable in ranking + selection logic)
- **`build` beat cut to an unrelated stretch.** Chose `seg_251006162518_022`
  ("man performs side lunge", src 178s) between tire-flip beats. Frame-verified:
  it is a mid-stretch leg-raise, a different activity and framing. Continuity
  break the critic flagged in BOTH passes. Root cause: `motion_fit`=1.0 +
  `variety`=1.0 outvoted activity continuity; the selector has **no
  same-activity-neighbourhood constraint**.
- **`peak` beat chose an anticlimax.** Chose `seg_251006164130_004` ("completing
  a tire flip and walking away"). Frame-verified: subject upright behind the
  tire, not mid-effort. The #2 candidate (`seg_251006163721_002`, an actual
  mid-flip, semantic 0.95) was pushed down by a **uniqueness penalty** (0.2,
  because the 163721 clip was already used for hook+early_effort). Uniqueness
  weight actively selected a WEAKER peak to diversify sources.
- **Bookends are the same walking shot.** `location`=`seg_251006164634_005` and
  `reflection`=`seg_251006164634_004` are adjacent segments of the same walk.
  The `variety` term only compares to the *immediately previous* pick, so
  non-adjacent duplication is invisible to it.

### C. Critic/revision-loop-caused (the clearest, most fixable failure)
- **The revision agent fought the critic and lost.** Critic pass1 said (among 6
  requests) "too short (26s vs 35s target), add diverse shots." The agent's
  ONLY applicable action was `trim_clip` on the hook → output got *shorter*
  (26.1→25.6s). Pass2 said the same "too short, add diversity" (10 requests);
  the agent trimmed 3 clips and deleted 1 → **25.6→21.5s**. Two passes drove the
  video from 26s to 21.5s while the critic repeatedly demanded 35s. Score fell
  0.4→0.3.
- **Root cause: the revision agent can only trim/delete/replace-from-existing.**
  When the critic asks for content that isn't in the catalog ("closer shots",
  "different angles", "more duration"), the agent has no valid move, so it
  *falls back to trimming the one clip a timestamp matched* — the worst possible
  response to "too short." It should instead recognize an unsatisfiable request
  and STOP, not shrink the edit.
- **Critic pass1 timestamps were unusable.** They came back normalized-looking
  (`0.0-0.0`, `0.1-0.1`, `0.1-0.2`) instead of real seconds, so the agent's
  range-matching collapsed onto the hook clip and addressed almost nothing.
  Pass2 returned real seconds and the agent correctly deleted the stretch — so
  the loop's usefulness is hostage to inconsistent critic output formatting.
- **No convergence guard.** The loop ran to its pass cap while the score
  regressed. Nothing detected "score is not improving — stop and keep the best
  version." run2 shipped v3 (score-worse, shorter) as final instead of v1.

### D. Template/ranking-logic-caused
- **A 7-beat narrative template was forced onto single-activity footage.** The
  fitness template (hook/location/early_effort/build/peak/completion/reflection)
  assumes an arc the session doesn't contain. `location` and `reflection` had no
  real footage, so they were filled with the same walking shot; `build` had no
  escalation footage, so it grabbed the stretch. **3 of 7 beats were filled with
  content that doesn't serve the beat** — a template-fit failure, not a footage
  failure (a shorter hook/effort/peak/completion template would not have created
  these holes).
- **Coverage validator over-promised.** It rated this footage **"STRONG" → 55s
  target** by counting category *presence* (peak matched by motion≥0.75,
  establishing by any low-motion wide, etc.). It does not measure diversity or
  redundancy, so heavily repetitive footage reads as strong. The manual
  `--duration 35` partly corrected this, but the tool's own recommendation
  (55s) would have made the result worse.
- **Duration target unmet regardless.** Plan targeted 35s; selector produced
  only 26.1s (clip-length allocation under-fills when few distinct segments
  survive hard constraints), before revision cut it to 21.5s.

## Decision-by-decision ledger (run2, final v3)
| beat | chosen | why chosen (data) | verdict |
|---|---|---|---|
| hook | 163721_003 tire flip | motion_fit 0.991, top of 137 | **good** — action-first, correct call |
| location | 164634_005 walk | semantic 0.812, establishing | acceptable but = reflection pick |
| early_effort | 163721_005 tire flip | motion_fit 0.982 | ok, but 3rd tire-flip wide |
| build | 162518_022 stretch | motion_fit 1.0 beat semantic | **BAD** — activity discontinuity (deleted in v3) |
| peak | 164130_004 "walking away" | semantic 0.9, uniqueness pushed real flip down | **BAD** — anticlimax |
| completion | 163915_005 finish flip | semantic 0.875 | ok |
| reflection | 164634_004 walk | = location neighbour | **repetitive** |

Net: 2 clearly bad picks (build, peak), 1 repetition (bookends), 1 strong pick
(hook), 3 acceptable-given-footage. The bad picks trace to **ranking weights**
(motion_fit and uniqueness dominating continuity/quality), not to missing
footage — those are the highest-value engine fixes.

## One-line conclusion
The machine correctly saw what it was given, made a defensible skeleton, and then
its revision loop and ranking logic subtracted value on top of a hard footage
ceiling. Fix the loop and the ranking and this exact footage likely reaches ~5–6;
only new footage (close-ups, angles) breaks past that.
