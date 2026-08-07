# Stromation — Editorial Quality Review (main @ `39c1c3c`, 2026-08-06)

Assessment of how far the system is from the stated goal: **an AI editor that makes
creative decisions like an experienced human editor.** Read-only review of `main`
plus live production-database evidence. No code was changed.

Companion doc: `MAIN_BRANCH_ARCHITECTURE.md` (what the system *is*). This doc is
what the system *is not yet*, and in what order to fix it.

---

## 0. The headline finding — production is editing blind

Live query against the production project (`iadzcnzgbtuigyodeqas`), the two real
footage projects (`pov`, `wijbefijwe`, 10 assets, the five POV clips):

```
asset_analysis by stage:
  probe/proxy/scenes/mechanical/audio/motion/catalog  → completed  (10 each)
  semantic    → FAILED (10)  "no VideoUnderstandingProvider configured"
  transcript  → FAILED (10)  "no provider configured or no audio stream"

segments for the newest project: 223 rows
  with action:      0
  with shotType:    0
  with transcript:  0
  with storyUses:   0
  probe says all 10 assets DO have audio tracks
```

**`GEMINI_API_KEY` and `OPENAI_API_KEY` are not set on the Railway backend.**
Every edit produced in production so far was cut from blur/exposure/motion
numbers alone, with zero understanding of what is in the footage or what anyone
said. The catalog is technically complete and editorially empty.

Three consequences that dominate everything else in this review:

1. **Enabling the V2 flag today would fail immediately.** `gemini_generate()`
   raises `RuntimeError("GEMINI_API_KEY not configured")`
   (`editorial_planner.py:1524-1526`), so every `editorial_plan` job fails and
   `handle_editorial_plan` drives the project to `analysis_failed`.
2. **Even with the key set, the planner would reject nearly everything.** Its
   grounding rule is that factual text may only use catalog vocabulary
   (`editorial_planner.py:1460-1465`). With empty semantic fields the legal
   vocabulary is the synthetic string `"scene 3 12.0-20.0s motion=0.41"`.
   Almost any real sentence fails `claims_grounded` — a hard gate rule.
3. **Every quality judgment below is currently untested against real data.**
   No plan has ever been produced from a populated catalog in production.

This is a configuration fix, not a code change, and it gates the entire roadmap.

---

## 1. Architecture as it actually behaves

```
CUSTOMER UPLOAD ─ browser→S3 multipart (≤2 GB), server-built keys, service-role
    │            provenance. Wizard collects: name · platform · aspect · vibe.
    ▼
ANALYSIS  (job kind=analysis, resumable, per-stage artifacts)
    probe → proxy(720p+wav) → scenes → mechanical → audio → transcript →
    semantic → motion → catalog
    ▼  OUTPUT: Segment[] (the ONLY thing downstream can reason about)
       segmentId assetId sourceStart sourceEnd | subjects action shotType
       cameraAngle cameraMovement location transcript storyUses emotion
       motionIntensity focus/exposure/stability/audioScore semanticRelevance
       duplicateGroupId problems searchText
       ✗ DISCARDED at catalog merge: composition, continuity,
         natural_sound_value, search_description, motion peak_moments,
         stationary_ranges, Whisper word-level timings, all raw mechanical numbers
    ▼
EDITORIAL PLANNER  (job kind=editorial_plan — ONLY when V2 flag on)
    Gemini 2.5-pro, TWO calls (core plan, then options/captions/graphics/warnings)
    ▼  OUTPUT: EditorialPlan — 3 scored story options, chosen concept, hook,
       beats, contiguous timeline[], pacing[], transitions[], speedRamps[],
       reframes[], captions[], graphics[], audio design, self-assessment
    ▼
DETERMINISTIC VALIDATION  validate_plan() + deterministic_gate()
    15 weighted rules summing to 100, threshold 80, 9 of them HARD.
    Grounding · binding duration/aspect/must-include · tone policy · execution
    geometry (handles, crop bounds, ramp overlap) · honest shortfall.
    Rejection → up to 3 re-prompts with rule-level feedback → PlanRejected.
    ▼  APPROVED plan row in editorial_plans (versioned)
    ▼
PICTURE EDIT ENGINE V2  (pure function, no LLM, ENGINE_VERSION 2.1.0)
    Transcribes the plan into a timeline. Reads only 6 of ~20 Segment fields
    (id, asset, start, end, motionIntensity, shotType).
    Decisions it makes: epsilon trim clamps, executable-vs-pending classification,
    reframe mode, pacing METRICS, continuity ADVISORIES, hashes.
    Everything editorial — order, in/out points, durations, transitions — is
    copied verbatim. Its only real power is refusal (PictureEditRejected).
    ▼  OUTPUT: PictureEditV2 {timeline, segmentMappings, speed/transition/
       reframeInstructions, pacingMetrics, continuityFindings,
       unsupportedExecution, deterministicHash}
       → edit_runs.blueprint (whole payload) + timelines.timeline_json (timeline ONLY)
    ▼
PREVIEW RENDER  picture_render_v2 — xfade dissolve/dip transitions with real
    source handles, static+pan crops, exact fps ("29.97"). Audio hard-concat.
    ▼
BRIDGE  deterministic uuid5 ancestry, timeline-bound
    preproduction_run → picture_edit_run → candidate_run(generation_kind=bridged)
    manifest: picture timeline + EMPTY captions/graphics, identity color,
    music_sound_run_id=None, audio_mix_run_id=None  ← no sound design, by design
    ▼
PRODUCT EDITOR  immutable versioned document, 5 tracks
    8 operations: reorder · trim · split · delete · restore · caption text ·
    music gain · toggle graphic.  Butt-joined reflow, no gaps/overlaps.
    ✗ cannot: add footage, change speed, set transitions/crops/color, retime
      captions, choose music. "AI Studio" panel is a PROTOTYPE echo box.
    ▼
FINAL RENDER  renderer2.render_timeline(profile="final")
    ✗ NO transitions (pure concat) · NO crops · fps truncated int(29.97)=29
    → the exported MP4 does not contain what the preview showed
    ▼
EXPORT  private object → owner-signed URL. Project → completed.
```

**Feedback loops present:** planner ↔ deterministic gate (text-level, ≤3 rounds).
**Feedback loops absent:** nothing ever looks at the rendered video; nothing ever
learns from the customer.

---

## 2. Subsystem maturity

Scored on *readiness to produce human-quality edits*, not code quality. Several
subsystems are well-engineered (8-9 as software) yet score low here because they
are unreachable, unused, or measure the wrong thing.

| # | Subsystem | Score | Why |
|---|---|---|---|
| 1 | Upload + storage (S3 multipart, RLS, ancestry) | **9** | 2 GB direct-to-S3, server-built keys, service-role-only provenance, 36 safety tests. Solved. |
| 2 | Bridge / candidate + timeline ancestry | **9** | Deterministic uuid5 ids, timeline-bound reuse, DB-enforced exact ancestry, orphan-cleanup persistence. Genuinely hard problem, genuinely finished. |
| 3 | Deterministic validator + gate | **8** | The best-designed component. 15 weighted rules, hard failures unoverridable, honest shortfall computed not trusted. Caveat: it certifies *legality*, never *quality*. |
| 4 | Job / worker pipeline | **7** | Claim races, heartbeats, stale recovery, cancellation, telemetry all correct. Single-threaded (`WORKER_CONCURRENCY` read, logged, unused); 25-run idempotency horizon. |
| 5 | Product Editor | **7** | Immutable versioning, 409-rebase, anti-spoof AI actor, exact-version export binding. Vocabulary is deliberately narrow — the customer cannot add a shot. |
| 6 | Customer frontend | **7** | Honest states, real per-clip activity log, stall detection, no fake progress, preview-vs-final labelled correctly. Collects only 3 answers as the entire brief. |
| 7 | Mechanical / technical analysis | **7** | Focus, exposure, black/frozen, shake, pHash dedupe — all real measurements, well normalized. |
| 8 | Test + CI infrastructure | **7 / 0** | 7 for contracts and safety (~576 backend + ~103 JS tests, coverage gates). **0 for quality: not one test asserts a cut is good.** |
| 9 | Preview renderer (picture_render_v2) | **6** | Real xfade with handle math, crops, exact fps. Audio always hard-concat. |
| 10 | Editorial Planner | **6** | Ambitious and well-defended: 3 scored options, evidence-bound claims, binding tone policy. But single-shot authorship, never sees a frame, and its strictest safety rule (catalog-only vocabulary) may be starving it creatively. |
| 11 | Picture Edit Engine V2 | **6** | Correct, deterministic, honest about what it cannot render. Makes **zero editorial decisions** — an executor with veto power. Ignores 14 of ~20 catalog fields. |
| 12 | Semantic understanding (Gemini) | **3** | **Off in production.** Prompt is hardcoded to "fitness/lifestyle". Returns `composition`, `continuity`, `natural_sound_value` — all thrown away at catalog merge. Partial responses degrade silently. |
| 13 | Transcription (Whisper) | **3** | **Off in production.** No diarization, no confidence, no prosody. Word-level timings are stored and never consumed — so no beat-accurate captions and no J/L cut placement. |
| 14 | Segment catalog | **4** | **The structural bottleneck.** Scenes >12 s are chopped into arbitrary ~8 s windows, then given ONE shotType/emotion/action each. Within-shot variation is unrepresentable, and the richest AI fields are dropped on the floor. |
| 15 | Final renderer (renderer2) | **4** | No transitions, no crops, fps truncation, fixed-dB duck. Delivers less than the preview promised. |
| 16 | Motion analysis | **5** | Foreground-sensitive top-2% frame-diff is a smart fix, but values are normalized *within* a clip (not comparable across clips) and `peak_moments` never reach the planner. |
| 17 | Music / sound design | **5 built · 2 reachable** | `music_supervisor` + `audio_rendering` do beat grids, bar/phrase maps, energy curves, explicit ducking, phrase-resolved endings, loudness normalization. **None of it is in the customer path** — bridged candidates pin music/audio ancestry to `None`. Customer edits ship with raw camera audio. |
| 18 | Visual finishing (captions/graphics/color) | **5 built · 2 reachable** | Palette/contrast/safe-area validation, word-timed captions, chatter exclusion — operator-only. Bridged manifests carry empty captions/graphics and identity color. |
| 19 | Critic + tournament (editorial intelligence) | **5 built · 0 in V2** | Nine dimensions incl. `hook_quality`, `pacing`, `emotional_payoff`, each evidence-backed; deterministic pairwise tournament. **V2 never calls it.** Legacy did render→critique→revise; V2 traded that away for a text-level gate. |
| 20 | Reframing / aspect handling | **3** | Crop rectangles come from the LLM with no saliency, face, or subject data to base them on; `subjectTarget`/`trackingMode` are carried as strings and never consulted. Without a planned reframe, 16:9→9:16 is **pillarboxed with black bars** — and the final renderer drops crops entirely. |
| 21 | Feedback / learning loop | **1** | `editor_operations` durably records every trim, delete and reorder a customer makes — the highest-signal correction data in the system — and **nothing reads it**. No rating, no approve/reject; the only feedback affordance in the product is a `mailto:` link. |
| 22 | Quality measurement | **1** | `draft_evaluations` and the human-ceiling apparatus exist but are operator-only, unwired, and never written by V2. There is no golden set, no regression baseline, no way to tell whether a change made edits better or worse. |

---

## 3. The biggest weaknesses preventing human-quality edits

**W1 — The AI is blind in production.** No semantic, no transcript. Everything
below is theoretical until this is fixed. *(config)*

**W2 — The catalog cannot support editorial reasoning.** An editor decides from
faces, eyelines, gesture, emphasis in the voice, the beat under a moment, where
the laugh is, whether two shots cut together. The Segment carries none of that.
Gemini already produces `composition` and `continuity` and the motion stage
already finds `peak_moments`; the merge step discards them. The planner cannot
choose a cut point on a motion peak or a breath because it never sees one.

**W3 — Nothing ever watches the result.** V2 renders once and ships. The legacy
path had a genuine render→critic→revise loop; V2 replaced it with a gate on plan
*text*. A plan can be perfectly legal, perfectly grounded, score 100 — and be a
boring video. The system has no way to notice.

**W4 — All creative authorship is one Gemini call.** V2 contributes nothing
editorial. There is no candidate generation, no comparison, no selection among
alternatives in the customer path — even though a scored tournament already
exists. One shot, no second opinion.

**W5 — Preview ≠ final.** Transitions and crops live in `edit_runs.blueprint`,
but only `timeline_json` reaches the exporter, and `renderer2` supports neither.
The customer approves a preview with dissolves and reframes, then downloads a
hard-cut, pillarboxed file at 29 fps instead of 29.97.

**W6 — Silence where the emotion is.** No music, no sound design, no J/L cuts in
any customer edit. Pacing, energy and emotional payoff are largely *carried* by
sound in real editing. The machinery is built and sitting one wiring job away.

**W7 — The system cannot learn.** Every correction a customer makes is recorded
and ignored. There is no path from "the human moved this clip" to "cut it there
next time".

**W8 — Quality is unfalsifiable.** 576 tests prove the system never lies about
footage. Zero tests, and zero metrics, say whether the video is good. Without a
measurement harness, every item on this roadmap is unverifiable.

**W9 — Segmentation is arbitrary.** ~8 s windows on continuous footage, one
label each. Real cutting happens at sub-second boundaries the catalog cannot
name.

**W10 — The brief is three words.** Name, platform, vibe. A human editor starts
from intent, audience and reference. Tone/style/duration/must-include reach the
planner as `None` in the customer path — so most of its binding-policy machinery
is dormant.

---

## 4. Prioritized roadmap — ordered by expected impact on video quality

### Phase 0 — Restore sight *(config only; hours)*
Set `GEMINI_API_KEY` + `OPENAI_API_KEY` on Railway. Re-run analysis on the
existing 5-clip project. Verify segments come back with real `action`,
`shotType`, `storyUses`, `transcript`. **Then** enable
`PICTURE_EDIT_ENGINE_V2_ENABLED` and run the controlled smoke test.
*Impact: unblocks everything. Nothing else is worth doing first.*

### Phase 1 — Make the catalog worth reasoning over *(highest lasting impact)*
1. Stop discarding what already exists: carry `composition`, `continuity`,
   `natural_sound_value`, and `search_description` into `Segment`; carry motion
   `peak_moments` + `stationary_ranges`; carry Whisper word timings.
2. Add per-segment **audio energy envelope** (RMS/onset), not one whole-file LUFS.
3. Add **face / subject detection** (bbox + size + position) — the missing input
   for reframing, hook selection and shot-size verification.
4. Replace arbitrary 8 s windows with **speech- and motion-aware boundaries**.
5. Widen the planner's catalog projection to include the new fields.
*Impact: raises the ceiling on every downstream decision. W2, W9.*

### Phase 2 — Deliver what the preview promised *(visible, bounded)*
Execute transitions and crops in the final render (port the `picture_render_v2`
xfade/crop path, or render finals through it), fix the fps truncation, and
implement **subject-aware reframing** for 9:16 using Phase 1 face data instead of
pillarboxing. *Impact: removes a straight quality regression between preview and
delivered file. W5, W20.*

### Phase 3 — Put a critic back in the loop, on the rendered video
Re-attach the existing critic to V2: render preview → critic watches with the
nine evidence-backed dimensions → structured revision requests → bounded
deterministic re-cut. Write `draft_evaluations` from V2 so runs become
comparable. *Impact: the single biggest structural gap between V2 and a human
editor's process — humans watch their cut. W3.*

### Phase 4 — Sound, because sound is the emotion
Wire `music_supervisor` + `audio_rendering` into the customer V2 path: licensed
track selection, beat-aligned cut points, ducking under speech, phrase-resolved
endings, J/L cuts using Phase 1 word timings. *Impact: the largest perceived-
quality jump per unit of new code, because the machinery already exists. W6.*

### Phase 5 — Learn from the humans already correcting you
Mine `editor_operations`: every trim/delete/reorder is a labeled correction
against a known plan. Feed aggregate signals into selector weights and planner
context. Add one lightweight explicit signal (keep / re-cut / why). *Impact:
compounding — the only mechanism that improves without new engineering. W7.*

### Phase 6 — Make quality measurable
A golden set of real projects; human scores on hook, pacing, story, payoff,
technical; tracked per release; the human-ceiling harness wired to it as the
benchmark. A CI quality report (not a blocking gate at first). *Impact: converts
every later phase from opinion to evidence. W8.*

### Phase 7 — Give the machine a real creative process
Generate N plan candidates, score them with the existing tournament, ship the
winner (or offer the top two). Richer brief intake (audience, reference, must-
include moments, duration intent) so the binding-policy machinery actually
engages. Retention-shaped pacing models per platform. B-roll/A-roll layering.
Execute speed ramps. *Impact: moves from "one competent cut" to "chose the best
of several", which is what an experienced editor actually does. W4, W10.*

---

## 5. What NOT to change

- The grounding/anti-fabrication architecture. It is the most valuable asset in
  the codebase and the reason this product can be trusted. Loosen only the
  *vocabulary* rule if Phase 1 shows it starving the planner — never the
  evidence requirement.
- The deterministic gate. Add quality signals *alongside* it, not inside it.
- Ancestry, immutability and versioning (bridge, Product Editor, exports).
- The honest `pending_renderer_support` convention — it is why this review could
  be written accurately at all.

---

*Review method: full read of `main` at `39c1c3c` (planner, V2 engine, renderers,
jobs, bridge, Product Editor, frontend, all tests) plus live production database
queries. Nothing was modified.*
