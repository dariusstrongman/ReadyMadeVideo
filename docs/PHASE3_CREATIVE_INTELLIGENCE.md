# Phase 3 — Creative Intelligence

> Branch `feat/creative-intelligence-phase3`. Seven flag-gated, deterministic
> subsystems that move the output from *valid* toward *watchable*. All flags
> default OFF; with every flag off, planner behavior is unchanged (proven by
> the Phase 2 golden, whose documented additive deltas are now
> `loops`/`dialogueAdjustments`/`brollInsertions`, each asserted `[]`).
>
> Evidence base: the six research threads distilled in
> `PHASE2_EDITORIAL_INTELLIGENCE.md` (Kim/Guo retention curves, hook
> archetypes, Mayer's multimedia effect sizes, Murch/Pearlman rhythm, sound
> craft, vertical specifics) — the underlying mechanics of the referenced
> creators (MrBeast, Hormozi, Abdaal, Vox, Johnny Harris, Kurzgesagt, …),
> not any one style. Nothing is hardcoded to a creator.

## Architecture

One new module, `app/pipeline/creative_phase3.py`, wired into the planner at
the same four seams Phase 2 uses (prompt parts → normalize repairs →
validation tail → result attachment), plus ONE execution slice that crosses
into the engine and renderers:

```
                     ┌── prompt_parts (rules stated = rules enforced)
plan_editorial ──────┤
                     ├── normalize, pre-arithmetic:  breath_pad_cuts  (AUDIO)
  _normalize... ─────┤       (mutates source ranges; cursor rebuild follows,
                     │        so all bookkeeping reflects the padded trims;
                     │        respects reserved transition handles)
                     ├── normalize, post (final geometry):
                     │       refine_captions  (CAPTIONS — split/emphasis/floors)
                     │       propose_broll    (BROLL — system-authored plan field)
                     ├── validate tail: phase3_violations (joins the revise loop)
                     └── result: retentionReport (CRITIC — advisory, optional floor)

picture_edit_v2 (2.3.0) ── executes plan.brollInsertions into REAL clips:
    host → A1 | broll(audioFrom: host window) | A2   (duration unchanged)
picture_render_v2 + renderer2 ── BOTH honor audioFrom (no preview/final gap)
product_editor ── trim/split keep the donor window in lockstep (speed-aware)
jobs ── every ownership check (final render, bridged editor, M6) covers donors
```

## Subsystems

**Hook Engine V2** (`PHASE3_HOOK_V2`) — hard rules: no stock opening
(`today we're… / hi guys / welcome / in this video / my name is`, checked on
the hook's *spoken* line and `hook.text`); the chronologically-first moment
may open the video only if it also ranks top-2 on merit. Builds on Phase 2's
archetype shortlist (which already vetoes discourse-opener in-media-res).

**Intelligent B-roll** (`PHASE3_BROLL`) — the deterministic planner scores
lexical overlap between what the host clip *says* and what candidate footage
*shows* (`illustrate_claim`, confidence = overlap strength); one
`pacing_relief` breather allowed on 8s+ uncovered talking stretches.
Constraints: never the hook, never an emotional close-up (face-protection
predicate), 0.8s edge guards so no sentence's start or landing is covered,
1–3s duration, 6s spacing, confidence floor. **Provider seam**: catalog /
uploaded / generated-placeholder providers ship now; stock providers plug in
by yielding the same candidate dicts — no architectural change. Execution is
a *full vertical slice* (engine → both renderers → editor), audio-under: the
host's speech is never interrupted, so dialogue integrity is untouched.
System-authored: the model cannot write `brollInsertions`; hand-authored
abuse is caught by `broll_violations`.

**Motion Graphics Planner** (`PHASE3_GRAPHICS`) — closed renderer-ready
vocabulary (callout, arrow, circle, highlight, zoom_emphasis, text_build,
lower_third, stat_card, timeline_marker, label, comparison); deterministic
opportunity detection from the catalog (spoken numbers → stat_card,
comparative language → comparison) surfaced to the model; validation: typed
vocabulary, data graphics need data in text AND evidence, 8s minimum spacing
(Mayer: signaling works when sparing). Rendering remains plan-level pending
its renderer — honestly, per the `pending_renderer_support` convention.

**Better Captions** (`PHASE3_CAPTIONS`) — system refinement: captions over
42 chars split at a clause boundary with proportional time split; every
caption gets at most ONE `emphasisWord` (number > negation > longest
substantive — no karaoke); sub-0.83s captions extended into available slack.
Validation: no walls (>84 chars), readability floor, emphasis must exist in
the text.

**Audio-Aware Editing** (`PHASE3_AUDIO_EDIT`) — sentence-end cuts get up to
0.2s of breath room where unused source allows, recorded in the dialogue
ledger, bounded by reserved transition handles, and run BEFORE the
arithmetic rebuild so no bookkeeping goes stale. Prompt directs music swell
into the payoff + ducking (structured `audio` fields already exist).

**Visual Rhythm** (`PHASE3_RHYTHM`) — hard rules: >3 consecutive same-size
shots (when the catalog offers variety) and metronomic timing (cv < 0.25)
reject; `rhythm_report` computes shot stats, payoff-slowdown ratio, and
punch-in suggestions for 7s+ talking shots (surfaced; reframes execute in
preview only, so they stay suggestions rather than silent divergence).

**Retention Critic** (`PHASE3_RETENTION_CRITIC`) — seven deterministic axes
(first3s, first10s, pacing, visualVariety, curiosity, emotionalPayoff,
clarity), each 0–100 with actionable advice; `wouldKeepWatching` at ≥70.
Advisory by default (attached to every approved result);
`PHASE3_RETENTION_FLOOR` turns the overall into a floor whose three weakest
axes become revise feedback. Proxies only — story/emotion judgment stays
with the model, per the Murch-inversion rule.

## Rollout

No migrations. Flags-off deploy is behavior-neutral. Suggested order:
`PHASE3_AUDIO_EDIT` → `PHASE3_CAPTIONS` → `PHASE3_HOOK_V2` →
`PHASE3_RHYTHM` → `PHASE3_BROLL` (re-cut a test project; b-roll requires
the V2 journey + Phase 2 substrate data) → `PHASE3_GRAPHICS` →
`PHASE3_RETENTION_CRITIC` (advisory first; set a floor only after reading a
few reports). Rollback for any flag: unset + restart. Note: enabling BROLL
changes `ENGINE_VERSION` output identity only when insertions exist; the
2.3.0 bump already retires cross-version reuse safely.

## Known limitations (honest list)

- **Soft transitions and reframes execute in the PREVIEW renderer only.**
  The final export path (`renderer2`) has no xfade or crop support yet, so
  `status: "executable"` means "the preview can execute this"; the final
  file renders those boundaries as hard cuts. Closing this is the next
  renderer milestone.
- Graphics are planned/validated but never reach a rendered frame OR the
  engine blueprint yet (the bridge emits an empty graphics track); the
  vocabulary is renderer-*ready*, not renderer-*backed*. Same for
  `emphasisWord`: no renderer consumes it today.
- B-roll candidate quality is lexical-overlap only — no visual-semantic
  matching until embeddings exist. The provider seam requires candidates to
  exist in the SOURCE CATALOG (grounding rule): stock/generated providers
  must ingest their footage as segments first; a candidate outside the
  catalog is correctly rejected by validation.
- B-roll placement is geometrically centered inside the host clip with edge
  guards; it does not consult `wordTimings`, so the picture swap can land
  mid-phrase (speech itself is never interrupted — audio-under). Word-level
  boundary snapping is a follow-up.
- `pacing_relief` insertions carry `confidence = 0.25` as a floor sentinel,
  not a measured score — read `motivation` + empty `matchedTokens`.
- The retention critic scores proxies; three of seven axes read
  model-authored fields (energies, claim types, loops), so a floor can be
  gamed by rewriting prose — grounding/tension gates police those fields
  independently. Advisory use is the intended mode; it does not watch the
  rendered video (that remains the rendered-cut critic, Batch D1 ordering).
- `PHASE3_AUDIO_EDIT` breath padding is largely absorbed when
  `PHASE2_DIALOGUE` is on (snapping already lands cuts on word boundaries);
  rolled out alone it pads only cuts within 0.15s of a sentence end.
- An `audio_rendering` source-gain instruction targeting an audio-under
  clip is a no-op (the donor branch has no volume filter yet).
- Music swell/duck remain plan-level intent; the audio engine executes them
  in a later phase.
- Caption timing refinement splits proportionally by text length, not by
  word timings, when a caption spans a clause (word-timed splitting is a
  cheap follow-up).
