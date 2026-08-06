# Phase 2 — Editorial Intelligence Architecture Proposal

> Branch `feat/editorial-intelligence-phase-2`, cut from `main` @ `fb1f03b`.
> **Proposal only. No code written. Nothing merged, nothing deployed.**
> Every subsystem below is flag-gated and backward compatible by construction.

Companion docs: `MAIN_BRANCH_ARCHITECTURE.md` (what exists), `EDITORIAL_QUALITY_REVIEW.md`
(Phase 1 assessment). This document answers one question:

**"If a professional editor watched this video, what would they instinctively change next?"**

---

## 1. What the current output actually is

Measured from the real POV plan in production (v1, gate **100/100**, 85.3 s, 9:16, 22 clips).
Not impressions — queries against `editorial_plans`, `segments`, and `asset_analysis`.

| Measure | Value | Editorial reading |
|---|---|---|
| Catalog used | **22 of 223 segments (9.9%)** | 90% of the material never entered contention |
| B-roll used | **7 of 91 available** | The B-roll gap is *selection*, not capture |
| Cuts severing speech | **11 of 22 outgoing (50%)**, 7 incoming | "Voice gets cut off", quantified |
| Shot-length trend | ~2.4 s early → ~6.5 s late; **11.73 s shot at t=68 s** | Pace *decelerates*; longest shot at the worst drop-off point |
| Pacing energy | **0.5, 0.5, 0.6, 0.5, 0.5, 0.5, 0.5** | No escalation; payoff = same energy as setup |
| Transitions | **21/21 hard_cut** | No punctuation, no chapter breaks |
| Graphics / speed ramps | **0 / 0** | No informational or kinetic layer |
| Hook | 1.23 s, chronological first frame | Opens where the camera started |
| Hook candidates in catalog | **1 of 223** | Hook choice is starved by analysis |
| shotType vocabulary | **8 spellings for 3 categories** | Silently inflates the variety gate rule |
| Selected vs rejected footage | motion 0.817 vs 0.835; **76 emotive segments unused vs 10 used** | Selection discriminates on nothing |

Verbatim from the plan — this is the whole problem in three lines:

- Hook: *"Made it to the last stop of the day for a quick monitor, with no demo needed."*
- Viewer promise: *"This video shows a person using moisture meters in a pantry, kitchen, and living room…"*
- First captions: *"Ringing the doorbell"*, *"Opening the pantry door"*

The hook is where the camera started. The promise **describes the file** rather than promising
the viewer anything. The captions narrate what is already visible — cognitive load with zero
information gain. And "quick monitor, no demo needed" actively *lowers* the stakes.

---

## 2. The root cause (one sentence)

**The grounding system — the product's greatest safety asset — is what produces the blandness.**

Every factual claim must trace to catalog vocabulary, so the cheapest way to pass the gate is to
literally describe what is on screen. The plan scored **100/100 while being editorially inert**,
because the gate measures *truthfulness, legality and executability* and **nothing measures
interest**. The system is a single-axis optimizer and it has saturated that axis.

Phase 2 is therefore not a feature list. It is the addition of a **second, orthogonal axis**:

```
              TRUTH AXIS (exists, keep exactly as is)
              grounded · policy-compliant · executable · honest shortfall
                                  │
                                  │   plans must pass
                                  ▼
              INTEREST AXIS (Phase 2)  ─────────────────────────────►
              hook · open loops · escalation · dialogue integrity ·
              motivated b-roll · contrast · payoff placement

    Plans compete on INTEREST, subject to TRUTH. Never the reverse.
```

This framing is what keeps Phase 2 safe: the truth gate stays a hard veto, and interest is only
ever allowed to choose *among plans that already passed it*.

---

## 3. Evidence base

Three research threads completed; three died on session limits (noted in §7). Findings are
labelled by strength, because a lot of what circulates about retention is folklore.

### Load-bearing, peer-reviewed

| Finding | Source | What it implies here |
|---|---|---|
| **36.6% of all sessions abandon within the first 3% of runtime**; 55.2% never finish | Kim, Guo, Seaton, Mitros, Gajos, Miller, *L@S 2014* (862 videos, second-by-second) | Two-thirds of all abandonment happens in the opening sliver. The hook is not one feature among many — it is most of the outcome. |
| **Misconception-first beats clear exposition, effect size 0.79–0.83** (n=364, randomized). Clear exposition can *raise confidence without raising learning* | Muller, Bewes, Sharma, Reimann, *J. Computer Assisted Learning* 24(2) 2008 | **The most important finding for us.** Our planner produces exactly the format proven *least* engaging: clear, correct, frictionless description. Notably the control that added merely *interesting* material produced no gain — interest alone is not the mechanism; confronting a belief is. |
| **Curiosity peaks at *intermediate* knowledge and the gap must be made salient** | Loewenstein, *Psych. Bulletin* 116(1) 1994; Kang et al. *Psych. Science* 2009 (inverted-U, fMRI) | A promise that fully describes the content ("this video shows…") yields *zero* curiosity by construction. Complete knowledge is as inert as none. |
| **Ovsiankina effect: 67% task-resumption rate.** Zeigarnik does *not* replicate (dz=0.15, no memory advantage) | Ghibellini & Meier, *Humanities & Social Sciences Comms* 12:962, 2025 (meta-analysis) | Open loops make people *come back and finish*, not remember better. Cite Ovsiankina. Loops are a completion mechanic — which is exactly what we want. |
| **Degraded audio → speaker rated less intelligent, research less important** (identical content) | Newman & Schwarz, *Science Communication*, 2018 (n=97, n=99) | Our 50% mid-sentence cut rate is not a polish issue. Processing disfluency is charged to the *speaker's credibility*. |
| **Median engagement caps ≈6 min regardless of length**; post-video action falls 56%→31% across length buckets | Guo, Kim, Rubin, *L@S 2014* (6.9M sessions) | Length must be *earned*. Also: informal desk framing beat a multi-million-dollar studio by 2–3×. Authenticity outperforms polish. |
| Real completion cliff is ≈15 min, not 6; interactivity lifted completion 61%→77% | Geri, Winer, Zaks, 2017 | The 6-minute rule is a median, not a cliff. Don't over-index on brevity. |
| **61% of viewer-interaction peaks coincide with a visual transition** | Kim et al. 2014 | The best available evidence for pattern interrupts: viewer behavior concentrates at visual change. |
| **Shot-length series in film converge on 1/f (pink noise)**; post-1980 films much more so | Cutting, DeLong, Nothelfer, *Psych. Science* 21(3) 2010 (150 films) | Correct pacing is **self-similar across timescales** — some long, more medium, many short, correlated with neighbors. It is *not* metronomic, and it is certainly not monotonic deceleration, which is what we ship today. Structural/correlational, not causal — do not read it as "cut every 3 s". |
| **Transcript-based B-roll placement with recommendations → faster editing AND measurably more engaging videos** | Huber, Shin, Russell, Wang, Mysore, *B-Script*, CHI 2019 (n=110, within-subject) | Direct empirical support for a motivated-B-roll recommender driven off the transcript. |
| Film-editing idioms encoded as machine constraints | Leake, Davis, Truong, Agrawala, *Computational Video Editing for Dialogue-driven Scenes*, SIGGRAPH 2017 | The architectural precedent for what Phase 2 is: editorial rules as deterministic constraints, not vibes in a prompt. |

### Craft rules concrete enough to implement deterministically

- **Never cut through a word's initial formant.** The consonant attack is the audible onset;
  removing it makes the word read as truncated even when the vowel survives. Cut *before* the
  attack, never on it.
- **Breaths carry rhythm.** Removing a breath *and the time it occupied* produces unnaturally
  seamless speech. Backfill with room tone; keep breaths before vowel-initial words.
- **Digital silence is not quiet** — it is a step in the noise floor, which the ear flags as a
  splice. This is why room tone exists.
- **2-frame audio crossfades** are the working standard (zero-crossing mismatch between 24 fps
  video and 48 kHz audio causes clicks); equal-power for dissimilar material, equal-gain for
  matched material.
- **J-cut = audio leads picture** (anticipation, accelerates); **L-cut = picture leads audio**
  (reaction, resonance, slows down). Both require *unused source handles* to exist.
- **Split edits and B-roll are the two ways to hide a jump cut**; the axial cut / punch-in is the
  third, converting a positional discontinuity into deliberate emphasis.
- **Radio edit / paper edit:** structure the audio first and ask *"does the story work with your
  eyes closed?"* Beautiful B-roll cannot rescue a broken argument. Assembly-cut discipline
  explicitly bans J/L cuts and music until structure is proven.
- **Paper-edit entries need a *function*** — setup→payoff, a turn, or pacing relief. "Great quote"
  and "beautiful shot" are rejected as non-functions.

### Explicitly NOT used (traced to nothing)

The 8-second-goldfish attention span (fabricated; traced to a 2015 marketing infographic citing a
source that has never produced data), "attention declines after 10–15 minutes" (Wilson & Korn 2007
reviewed the evidence and found none), "open loops = +32% watch time", and every "good retention is
X%" benchmark — **YouTube publishes no numeric retention benchmark at all.** The only defensible
baseline is our own median curve on comparable videos.

---

## 4. Principle → gap ranking

Each principle scored against what Stromation does today, ranked by expected retention impact.

| # | Principle | Status today | Gap | Impact |
|---|---|---|---|---|
| 1 | Hook is *selected*, not chronological | Hook = first footage; 1 hook candidate in 223 | **Total** | ★★★★★ |
| 2 | Curiosity gap must be opened and made salient | Promise describes contents = zero gap | **Total** | ★★★★★ |
| 3 | Dialogue integrity | 50% of cuts sever speech | **Severe** | ★★★★★ |
| 4 | Open loops closed (Ovsiankina) | No loop concept exists | **Total** | ★★★★ |
| 5 | Escalation / tension shape | Flat 0.5 energy; decelerating pace | **Total** | ★★★★ |
| 6 | Payoff placement + setup | `payoff_present` rule exists but is keyword-matched on beat names | **Partial** | ★★★★ |
| 7 | Motivated B-roll | 7 of 91 used, no motivation model | **Severe** | ★★★★ |
| 8 | Pattern interrupts / visual change cadence | 21/21 hard cuts, no cadence model | **Severe** | ★★★ |
| 9 | Audio-first (radio edit) structure | Picture-first; audio is a byproduct | **Total** | ★★★ |
| 10 | Contrast + deliberate silence | No concept | **Total** | ★★★ |
| 11 | Rendered-cut critique | Legacy had it; **V2 removed it** | **Regression** | ★★★ |
| 12 | J/L cuts | In the plan schema, dropped by the renderer | **Partial** | ★★ |
| 13 | Motivated graphics | 0 graphics; no trigger model | **Total** | ★★ |
| 14 | Punch-in / reframe as emphasis | Crops planned blind, dropped in final render | **Broken** | ★★ |
| 15 | Multiple candidates + selection | Tournament exists, unused by customers | **Unused** | ★★ |
| 16 | Learn from human corrections | `editor_operations` recorded, never read | **Total** | ★★ (compounding) |

---

## 5. Proposed subsystems

Every subsystem: flag-gated, additive to the schema, and **incapable of weakening the truth gate**.

---

### S1 — Narrative Substrate (analysis enrichment)

**Why it exists.** Every other subsystem is impossible without it. The planner cannot cut on a
breath, a beat or a motion peak because it has never been shown one. Today the catalog throws away
data we have already paid for: **1,698 word-level timings and 253 sentence boundaries currently sit
in `asset_analysis` and are discarded at catalog merge**, along with Gemini's `composition`,
`continuity` and `natural_sound_value`, and motion's `peak_moments` / `stationary_ranges`.

**Pipeline location.** `pipeline/catalog.py` (merge stage) + `pipeline/schemas.py` (`Segment`
`SCHEMA_VERSION` 2 → 3). Additive only.

**Inputs.** Existing artifacts: transcript (words + sentences), motion (peaks, stationary),
semantic (composition, continuity, natural sound), audio, mechanical.

**Outputs.** `Segment` v3 gains: `speechSpans[]` (start/end/text, sentence-aligned),
`wordTimings[]`, `speechFreeRanges[]`, `audioEnergyEnvelope[]` (per-segment RMS, replacing a single
whole-file LUFS), `motionPeaks[]`, `stationaryRanges[]`, `composition`, `continuity`,
`naturalSoundValue`, and a **normalized** `shotSize` enum (fixing the 8-spellings bug) with the raw
string preserved.

**Deterministic validation.** Every new field derived, never invented; a field absent from the
source artifact is `None`, never guessed. Round-trip test: v2 catalogs load unchanged under v3.

**Interactions.** Planner: `_catalog_json()` projection widens (behind flag). Picture Edit V2:
`catalog_hash()` must include the new fields, so **ENGINE_VERSION bumps** — existing idempotency
keys change once, by design. Product Editor: unaffected.

**Retention impact.** Indirect but gates S2, S3, S5, S6, S7. **Complexity:** Low-Medium.
**Risks.** Catalog-hash churn invalidates reuse once (acceptable, one-time); prompt size grows
(mitigate by projecting only fields the planner is allowed to act on).

---

### S2 — Dialogue Integrity Layer

**Why it exists.** 50% of our cuts sever speech, and Newman & Schwarz show that audio disfluency is
charged to the *speaker's credibility*, not to the edit. This is the cheapest large perceived-quality
win available, and it is fully deterministic — no AI involved.

**Pipeline location.** Three places: (a) planner prompt gets speech-safe cut points; (b)
`_normalize_timeline_arithmetic()` **snaps** violating cut points to the nearest safe boundary; (c)
a new hard gate rule `dialogue_integrity` refuses what cannot be snapped.

**Inputs.** S1 `speechSpans`, `wordTimings`, `speechFreeRanges`.

**Outputs.** Cut points guaranteed to fall in a speech-free range or at a sentence boundary, with a
configurable pre-attack guard (~80–120 ms) so no consonant onset is clipped; `dialogueAdjustments[]`
recording every snap (auditable, like `trimAdjustments`).

**Deterministic validation.** Hard rule: no cut lands strictly inside a word. Soft rules: prefer
sentence boundaries; preserve a breath's duration rather than deleting it; require handles for any
transition. **Never** silently extend a clip past its source bounds — snap or reject.

**Interactions.** Planner (normalize + gate). Picture Edit V2 gains a matching assertion so a
hand-authored plan cannot bypass it. Product Editor unaffected — though customer trims should get
the same snapping later.

**Measured feasibility (important — snapping alone is not sufficient).** Of the 11 violating cuts
in the real POV plan, the distance to the nearest sentence end averages **1.84 s**: only **4 can be
snapped within 1 s** and **7 within 2 s**. The remaining 4 would require moving a cut by more than
two seconds, which materially changes the edit. So S2's correct scope is a **three-tier remedy**:
snap where it is cheap (≤ ~1 s), let the audio finish under the next picture (**L-cut**) where it is
not, and reject only when neither is possible. This means S2 is *complete* only once S6's split
edits exist — until then it fixes roughly two thirds of the defect and honestly reports the rest.

**Retention impact.** ★★★★★ on *perceived* quality; the most visible single fix.
**Complexity:** Low (tier 1) / Medium (tier 2, needs S6). **Risks.** Dense speech reduces legal cut
points → slightly longer clips; mitigate with the tolerance window above.

---

### S3 — Hook Engine

**Why it exists.** 36.6% of viewers leave in the first 3%. We currently open on the chronological
first frame and the catalog identifies exactly **one** hook candidate out of 223 segments.

**Pipeline location.** A distinct stage *inside* the planner, before timeline construction:
`propose_hook_candidates(segments, constraints) → ranked candidates` handed to the model as a
**shortlist it must choose from and justify**, not a free choice.

**Inputs.** S1 substrate. Ranking signals: speech intelligibility, a *complete* opening sentence,
audio-energy onset, motion outlier vs the video's own distribution (not the flat global 0.83),
composition/face presence, `storyUses` containing hook/peak, technical quality, and — critically —
**whether the moment implies a question**.

**Outputs.** `hookCandidates[]` with scores and reasons; the chosen hook carries `hookType`
(in-media-res, contradiction, stakes, visual anomaly, cold-open tease) and an explicit
`opensLoop` reference into S4.

**Deterministic validation.** Hook must be timeline[0] at t=0 (exists). **New hard rules:** the hook
must open a registered loop; the hook may not be the chronologically-first segment *unless it also
wins the ranking* (defeats "start where the camera started"); its opening speech span must be
complete (S2); its `viewerPromise` must **not** be a contents description — enforced by a
structural check that the promise contains an unresolved element rather than an enumeration.

**Interactions.** Planner-internal. Picture Edit V2 unchanged (still transcribes). Gate gains
`hook_selected` and `hook_opens_loop` rules.

**Hook archetypes, and what each needs from the footage.** A hook is *a promise plus a reason the
promise cannot be resolved yet*, so the detector's job is to find **unresolved states**, not exciting
ones. Eight archetypes, split by whether we can detect them **today**:

| Archetype | Primary detection signal | Buildable with S1 alone? |
|---|---|---|
| **Contradiction / myth-bust** | belief-attribution verb + generic subject, followed by an adversative within ~15 tokens | ✅ transcript only |
| **Question** | leading wh-word / auxiliary inversion + terminal pitch rise; reject rhetorical fillers ("right?") | ✅ transcript + audio |
| **Negative / warning** | imperative-positioned prohibition + second person in one clause | ✅ transcript only |
| **Stakes** | numeric token bound to a consequence verb, confirmed by prosodic stress **on the number** | ✅ transcript + audio envelope |
| **In-media-res** | **unresolved anaphora in the opening sentence** + energy already at peak with no ramp-in; discourse-openers ("so", "hi", "today") hard-veto | ✅ transcript + motion/audio |
| Payoff-tease / flash-forward | joint motion+audio peak, reaction evidence, and a position constraint (clip must come from the last ~40% of source) | ⚠️ partial — needs audio-event + face |
| Direct address | single frontal *speaking* face + second-person pronoun density + SNR floor | ❌ needs a real face detector |
| Visual anomaly | per-shot embedding distance from the video's own centroid, gated on sharpness | ❌ needs frame embeddings |

**Five of eight are buildable from the transcript plus S1 signals** — and those five are also the
safest for 9:16, because they survive sound-off viewing where burned-in text carries the hook. Ship
those first; the other three wait on new capabilities (§7).

The most reusable single feature is the **anaphora test** — "does the opening sentence reference
something not present?" It powers in-media-res, the imposed-question variant, and the tease, and it
is cheap with a dependency parse or even a pronoun-without-antecedent heuristic. Note it is also
exactly what our current hook fails: *"Made it to the last stop of the day"* opens with a discourse
marker and resolves everything it raises.

**Retention impact.** ★★★★★ — the single highest-leverage subsystem. **Complexity:** Medium.
**Risks.** Loewenstein's inverted U — a hook about something the viewer knows *nothing* about
generates less curiosity, not more. Mitigate by requiring the hook to be followed within ~5 s by
orienting context, and by keeping a *human-legible reason* on every candidate. Second risk: the
claim-family archetypes (contradiction / stakes / warning) overlap heavily and will double-count if
scored independently — take an argmax or collapse them into one family with a sub-label.

---

### S4 — Curiosity Loop Ledger

**Why it exists.** Loops are the mechanic that converts a hook into a finish (Ovsiankina, 67%).
Today we have no concept of a promise, so nothing is ever paid off — and an unclosed loop is worse
than none.

**Pipeline location.** Plan schema addition + validator + gate.

**Inputs.** Plan text (hook, promise, captions, beats) and the timeline.

**Outputs.** `loops[]`: `{id, openedAtSeconds, openedBy, question, closedAtSeconds, closedBy,
evidence}` — each closure grounded exactly like every other factual claim.

**Deterministic validation.** **Hard:** every opened loop closes before the end; no loop stays open
longer than a configured share of runtime; at most N concurrent loops (cognitive load); the final
beat must close the *primary* loop. **Soft:** loops distributed rather than clustered.

**Interactions.** Extends the existing grounding machinery — loop closures are `GroundedText`, so
Phase 1's evidence rules apply unchanged. Picture Edit V2: none. Gate gains `loops_closed`.

**Retention impact.** ★★★★. **Complexity:** Medium. **Risks.** The model may open trivial loops to
satisfy the rule; counter by scoring loop *quality* in S10 and requiring the closure to carry real
evidence.

---

### S5 — Tension & Escalation Model

**Why it exists.** Our energy curve is flat (0.5 across every beat including the payoff) and our
pace *decelerates* — an 11.73 s shot at t=68 s, the worst possible place for the longest shot in the
video. Cutting's 1/f result says correct pacing is self-similar, not monotonic.

**Pipeline location.** Planner pacing schema + validator + gate; metrics already computed by
Picture Edit V2 (`actualEnergy`, `energyDeviation`) get *teeth*.

**Inputs.** Plan pacing, timeline shot lengths, S1 motion/audio energy.

**Outputs.** `tensionCurve` (required shape), `contrastEvents[]` (deliberate slow/quiet moments),
and per-beat escalation targets.

**Deterministic validation.** **Hard:** energy variance above a floor (kills the flat 0.5 curve);
the payoff beat's energy must exceed the mean of preceding beats; the longest shot may not fall in
the final quartile unless it *is* the payoff. **Soft:** shot-length distribution scored for
self-similarity (a 1/f-ness statistic) rather than a fixed target ASL; at least one contrast event
(silence/stillness) before the payoff.

**The two craft findings that shape this subsystem.** First, Pearlman's central claim: **pace is
differential, not absolute** — rhythm works through *cycles of tension and release*, and a fast
passage only reads as fast against something slower. So S5 must score **variance and contrast**, never
an absolute cutting rate. Second, and best-attested across independent practitioner sources: **the
quiet beat belongs immediately before the payoff, not after it.** A payoff arriving at the top of a
continuous ramp has nothing to be measured against and lands softer than the same material preceded
by silence. This resolves the apparent contradiction between "escalate everything" and "let it
breathe": the correct shape is **rising peaks with troughs between them, and the deepest trough
adjacent to the highest peak** — not a monotonic ramp.

Note this also means our current output fails in *both* directions at once: flat energy (no peaks)
*and* monotonic deceleration (no rising envelope).

**Interactions.** Picture Edit V2 already *measures* `actualEnergy` and reports `energyDeviation`
without acting on it — S5 makes that a gate input. No engine change beyond surfacing.

**Retention impact.** ★★★★. **Complexity:** Medium. **Risks.** Over-constraining pacing produces
mechanical edits; keep these as scored rules with a floor, never a fixed template.

---

### S6 — Motivated B-roll & Split-Edit Layer

**Why it exists.** 91 B-roll segments available, 7 used. B-Script (CHI 2019, n=110) is direct
evidence that transcript-driven B-roll recommendation makes videos measurably *more engaging*.
Critically, B-roll is also the primary mechanism for hiding jump cuts — which is how S2's snapping
gets solved when speech is too dense to cut cleanly.

**Pipeline location.** A planner stage after the story spine is fixed: `propose_broll(spine,
segments)` producing *motivated* insertions. Optionally a deterministic post-pass.

**Inputs.** S1 substrate + the chosen spine. **Motivation rules** (a B-roll cut must satisfy at
least one, and record which): illustrate a claim spoken at that moment · cover a jump cut ·
compress time · show what is described but not visible · provide pacing relief.

**Outputs.** `brollInsertions[]` each carrying `motivation`, the transcript span it serves, and
evidence; plus `splitEdits[]` (J/L) with the handle math proving they are renderable.

**Deterministic validation.** **Hard:** every insertion names a motivation from the closed set and
references a real transcript span; no insertion without motivation (kills decorative filler); J/L
cuts require real unused handles. **Soft:** B-roll share of runtime within a band; no two
consecutive B-roll inserts without an A-roll anchor.

**The legitimacy test, and one concrete timing rule.** The craft literature enumerates B-roll
*functions* but never states a legitimacy test; the usable formulation is: **a cutaway is motivated
when removing it would cost the viewer something specific** — an inference, an orientation, a time
compression, a beat of rhythm. It is decorative when its only contribution is that the screen
changed. Two implementable corollaries: B-roll that lands **slightly before** the word it illustrates
reads as motivated (the image answers a question the viewer has just formed), while B-roll landing
*after* reads as redundant — so insertion timing should lead the referent, not trail it. And generic,
non-specific footage that could illustrate any sentence is decorative *by construction*, because it
cannot be the answer to a particular question. This is Dmytryk's Rule 1 — "never make a cut without a
positive reason" — made checkable.

**Interactions.** Planner produces them; **Picture Edit V2 must learn to place B-roll against a
speech bed** — the first real engine change in Phase 2. Renderer: J/L cuts need audio crossfades
(2-frame standard), which `picture_render_v2` does not do yet (audio is hard-concat today).

**Retention impact.** ★★★★. **Complexity:** Medium-High — this is the first subsystem that changes
the timeline model (audio no longer strictly follows picture). **Risks.** Audio/picture desync bugs;
mitigate by keeping split edits behind their own sub-flag and shipping motivated B-roll (picture
only) first.

---

### S7 — Motivated Graphics Layer

**Why it exists.** Zero graphics today, and our captions actively hurt (they narrate the visible).
A graphic is earned when it does a job the picture cannot: quantify, locate, label, compare, orient
in time, or show what cannot be filmed.

**Pipeline location.** Planner, after B-roll; renderer support in `renderer2` / `visual_finishing`.

**Inputs.** Transcript (numbers, place names, comparisons, time references), plan structure.

**Outputs.** `graphics[]` with a required `trigger` (spoken quantity, named place, explicit
comparison, time jump, chapter boundary) and existing grounded text/evidence.

**This subsystem turned out to be the best-evidenced in the whole proposal**, and it splits into two
pieces with very different cost and priority.

**S7a — the Coherence Rule (cheap, strong evidence, belongs early).** Mayer's meta-analysed
multimedia principles give effect sizes that settle the argument about our captions:

| Principle | Mayer's wording | Effect size |
|---|---|---|
| **Temporal contiguity** | present spoken words at the same time as the corresponding graphic | **d = 1.31** (8/8 tests) |
| **Multimedia** | words + graphics beat words alone | **d = 1.35** (13 comparisons) |
| **Modality** | use spoken words rather than printed words | **d = 1.00** (18/19) |
| **Coherence** | **delete extraneous material** | **d = 0.86** (18/19) |
| **Spatial contiguity** | put printed words next to the part of the graphic they describe | **d = 0.82** (9/9) |
| **Signaling** | highlight essential material | **d = 0.70** (26/28) |
| Redundancy | don't add on-screen captions to narrated graphics | **d = 0.10** (8/12) |

Two consequences, and the second is counter-intuitive:

1. **The case against our captions is coherence, not redundancy.** *"Ringing the doorbell"* is
   extraneous material — and coherence (**d = 0.86**, 18 of 19 tests) is roughly **eight times better
   evidenced** than the redundancy principle everyone quotes (d = 0.10, and it failed in 4 of 12
   tests). Mayer's own example is directly on point: removing decorative video clips of lightning
   strikes from a lesson on lightning *improved* learning. **A decorative graphic is a seductive
   detail with a keyframe** — and the effect is worst exactly in our conditions, because coherence
   effects are strongest when the material is system-paced (video is) and when the extraneous
   material is highly interesting (a slick graphic is).
2. **"No text on screen" is NOT what the evidence says.** Redundancy is weak and reverses for short,
   reworded text. Captions independently benefit everyone (Gernsbacher, 100+ studies), and verbatim
   captions work as well as elaborated ones. The defensible rule is therefore *not* "fewer captions"
   but **"text that says something the audio doesn't, or says it in fewer words, placed on the thing
   it refers to."**

**The single highest-value implementable rule is temporal contiguity (d = 1.31)** — bind every
caption and graphic in-point to the **word-level timestamp** of its trigger token rather than to a
shot boundary. We already have 1,698 word timings sitting unused. This is nearly free and it is the
largest effect in the table.

**S7b — motivated graphics generation** (the trigger taxonomy: quantify, locate, label, compare,
orient in time, show-what-cannot-be-filmed, emphasize — each detected from transcript signals such as
numeric tokens, named entities, comparatives and temporal expressions). Larger job, later.

**Deterministic validation.** **Hard:** every graphic names a trigger detectable in the transcript;
**captions may not restate what the shot already shows** (the coherence rule — this would have
rejected "Ringing the doorbell"); reading speed ≤ **17 cps** (Netflix allows 20 for adults, BBC's
160–180 wpm works out to ~15–17; 17 is the defensible middle); **≤ 42 characters per line, max 2
lines, one preferred**; duration between **5/6 s and 7 s**; contrast **≥ 4.5:1** measured against the
actual worst-case pixels behind the text for its whole duration (WCAG 1.4.3 — a stroke or scrim is
the reliable fix); text inside the **90% action-safe / 80% title-safe** box (SMPTE ST 2046-1).
**Soft:** signaling has a dosage ceiling — Mayer is explicit that it works "when visual and verbal
signals are used sparingly", so highlighting everything signals nothing.

**Retention impact.** S7a ★★★★ (upgraded — the evidence is far stronger than assumed), S7b ★★.
**Complexity:** S7a Low, S7b Medium. **Risks.** Lower-thirds are the *convenient* position, not the
effective one — spatial contiguity says labels belong next to their referent, which our renderer
cannot currently do. ⚠️ **Kinetic/word-level captions are evidence-free:** every "40% longer watch
time" style number traces to caption-software vendor marketing, with no controlled study. They remain
a defensible platform-convention aesthetic — just never encode them as a comprehension optimization.

---

### S8 — Rendered-Cut Critic (restore the V2 feedback loop)

**Why it exists.** Legacy autoedit genuinely rendered → critiqued → revised → re-rendered. **V2
traded that away** for a text-level gate. Nothing in the V2 path ever looks at the video. A plan can
be perfectly legal and score 100 — as ours did — and nobody notices it is inert.

**Pipeline location.** `handle_autoedit_v2`, after preview render: preview → critic → structured
revision requests → bounded deterministic re-plan (reuse the planner's existing repair loop).

**Inputs.** Rendered preview + plan + the nine existing `editorial_intelligence` critic dimensions
(`hook_quality`, `pacing`, `emotional_payoff`, clarity, …), all evidence-backed already.

**Outputs.** `critic_verdict`, `revision_ops`, and a `draft_evaluations` row — **all three of which
V2 currently leaves null**, so runs are not comparable today.

**Deterministic validation.** Revisions may only *reorder, trim or swap within the approved plan's
grounded material* — a critic can never introduce ungrounded content. Loop capped (reuse
`AUTOEDIT_MAX_REVISIONS`). Known quirk to respect: the critic's `overallScore` can contradict its own
booleans — **treat the requests as signal, never the score.**

**Retention impact.** ★★★. **Complexity:** Medium (mostly rewiring existing parts).
**Risks.** Latency and cost per edit; gate behind its own flag and cap passes.

---

### S9 — Editorial Interest Gate  *(the centerpiece)*

**Why it exists.** Everything above is unenforceable without a second scored axis. This is what
makes "good" a *gateable property* rather than a hope, and it is **deterministic** — computed from
measurable proxies, not from an LLM's opinion of itself.

**Pipeline location.** Beside `deterministic_gate()`, as a separate function with its own rule set
and threshold. Truth gate remains a hard veto and is evaluated first.

**Inputs.** The plan + S1 substrate + S5 metrics.

**Outputs.** `interestScore` (0–100) with a per-rule breakdown, mirroring the existing gate's shape
so it is auditable the same way.

**Candidate rules** (weights to be tuned against real footage, not guessed):
hook selected from a ranked shortlist · hook opens a loop · all loops closed · dialogue integrity
100% · energy variance above floor · payoff energy above preceding mean · shot-length
self-similarity · B-roll motivation rate · captions add information (not narration) · at least one
contrast event · longest shot not in the final quartile · catalog utilization above a floor (we use
9.9% today).

**Deterministic validation.** The gate itself *is* validation. Two hard invariants: it can only
**reject**, never approve something the truth gate rejected; and no rule may be satisfiable by
fabricating content.

**⚠ The design trap this gate must avoid — Murch's measurement inversion.** Murch's Rule of Six
weights what makes a cut work: **emotion 51%, story 23%, rhythm 10%, eye-trace 7%, 2D plane 5%, 3D
space 4%**. Emotion and story together are **74%** of the decision. But those are exactly the two we
can measure *worst* — a machine scoring "emotion" is really scoring vocal arousal — while rhythm,
eye-trace and screen direction (the bottom 26%) are cleanly computable. **A naive weighted sum of
measurable proxies would therefore be dominated by the least important criteria — the precise
inversion of what Murch is saying.**

The design consequence is explicit: the computable metrics belong in this gate as **veto and penalty
checks** (a floor a plan must clear), while story and emotion selection stays with the model reasoning
over the transcript and is checked by S8's rendered critique. **Do not build S9 as a score to
maximize.** It is a floor to clear.

**Retention impact.** ★★★★★ *indirectly* — it is what forces every other subsystem to actually
land. **Complexity:** Medium. **Risks.** Goodhart's law — the model optimizes the proxy. Counter
with S8's rendered critique as an independent check, and by treating the score as a *floor for
shipping*, not a target to maximize.

---

### S10 — Plan Tournament for customers

**Why it exists.** An experienced editor considers several structures and picks one. We generate one
plan and ship it. The scoring tournament **already exists** (`editorial_intelligence`,
`tournament_runs`) and is unused in the customer path.

**Pipeline location.** `handle_editorial_plan`: generate N plans (varying story option / hook
candidate), score each with S9 + S8, keep the winner, retain runners-up for the version picker the
UI already has.

**Retention impact.** ★★. **Complexity:** Medium. **Risks.** N× cost and latency — flag-gated,
default N=1 (current behavior), raise deliberately.

---

### S11 — Preview/Final parity + subject-aware reframing  *(prerequisite, carried from Phase 1)*

**Why it exists.** Craft that does not survive to the exported file is worthless. Today
`timeline_json` carries no transitions or crops, and `renderer2` supports neither — so **every
transition and reframe the preview shows is silently dropped from the customer's MP4**, which is
also rendered at `int(29.97) = 29` fps. And 16:9 → 9:16 is pillarboxed with black bars because
nothing knows where the subject is.

**Pipeline location.** `renderer2` / `picture_render_v2` convergence; reframing driven by S1 face
and composition data.

**Retention impact.** ★★ directly, but it **multiplies S5, S6 and S7** — without it their output
never reaches the viewer. **Complexity:** Medium. **Risks.** Render regressions; guard with the
existing per-aspect render tests.

---

### S12 — Correction Learning Loop

**Why it exists.** `editor_operations` durably records every trim, delete and reorder a customer
makes against a known plan — the highest-signal labeled data in the system — and nothing reads it.

**Pipeline location.** Offline aggregation → planner context + selector weights (the conservative
±0.05 rules-based mechanism already exists in `preferences.py`).

**Retention impact.** ★★ immediately, **compounding** over time. **Complexity:** Medium.
**Risks.** Overfitting to one customer; require a volume floor before any weight moves.

---

## 6. Implementation order

Ordered by *expected retention gain per unit of risk*, with each step usable on its own.

| Order | Subsystem | Why here | Flag |
|---|---|---|---|
| **1** | **S1 Narrative Substrate** | Nothing else is possible without it; pure data recovery, no behavior change | `PHASE2_SUBSTRATE` |
| **2** | **S2 Dialogue Integrity** | Biggest perceived-quality win per line of code; fully deterministic; fixes a measured 50% defect | `PHASE2_DIALOGUE` |
| **3** | **S3 Hook Engine + S4 Loop Ledger** | Strongest evidence base (36.6% abandon in first 3%); ship together — a hook without a closed loop is worse than none | `PHASE2_HOOK` |
| **4** | **S9 Editorial Interest Gate** | Makes 2–3 enforceable and everything after it measurable | `PHASE2_INTEREST_GATE` |
| **5** | **S7a Coherence + temporal contiguity** | **Promoted after research.** Temporal contiguity is the largest single effect found anywhere in this document (d = 1.31) and needs only the word timings we already have; the coherence rule (d = 0.86) is what actually kills "Ringing the doorbell" | `PHASE2_COHERENCE` |
| **6** | **S5 Tension & Escalation** | Cheap once S1+S9 exist; kills the flat curve and the decelerating pace | `PHASE2_TENSION` |
| **7** | **S8 Rendered-Cut Critic** | Independent check on S9's proxies before we trust them further | `PHASE2_CRITIC` |
| **8** | **S11 Preview/Final parity** | Must land before S6/S7b or their output never reaches the file | `PHASE2_RENDER_PARITY` |
| **9** | **S6 Motivated B-roll** (split edits behind a sub-flag) | First timeline-model change; needs 1,2,11 in place. Also completes S2 — L-cuts fix the 4 of 11 bad cuts snapping cannot | `PHASE2_BROLL` |
| **10** | **S7b Motivated Graphics generation** | Larger job than the coherence rule; benefits from S11 | `PHASE2_GRAPHICS` |
| **11** | **S10 Tournament** | Multiplies cost; only worth it once quality is measurable | `PHASE2_TOURNAMENT` |
| **12** | **S12 Learning Loop** | Needs volume | `PHASE2_LEARNING` |

**Backward-compatibility contract, enforced at every step**

1. All new `Segment` fields optional; v2 catalogs load unchanged.
2. All new plan sections optional with defaults; a Phase-1 plan still validates.
3. The truth gate is never weakened, never reordered, never made overridable.
4. Every flag defaults **off**; with all flags off, behavior is byte-identical to today.
5. `ENGINE_VERSION` bumps whenever the payload changes (existing rule), and the interest gate is a
   *separate* score — `quality_score` keeps its current meaning.
6. No production migration ships without the established order: migrate → deploy backend → deploy
   frontend → smoke test.

---

## 7. Risks, and what this proposal does not know

**Known risks.** Goodhart's law on S9 (mitigated by S8 + a floor rather than a target) · latency and
cost growth from S8/S10 (flag-gated, capped) · over-constraint producing mechanically "correct" but
lifeless edits (keep soft rules soft) · audio/picture desync from S6 (sub-flag, picture-only first)
· catalog-hash churn from S1 invalidating idempotency once (one-time, by design).

### New capabilities Phase 2 would require (verified absent from the codebase today)

| Capability | Needed by | Status |
|---|---|---|
| **Real face detection** (bbox, scale, frontality, speaking) | S3 direct-address, S11 subject-aware reframing | **Absent.** `composition.py` has `faceBox`/`faceVisibility` fields, but production never supplies measurements — it falls back to `estimate_composition_from_shot_type`, which hard-codes `faceVisibility = 0.35 if "close" in shot else 0`. The fields exist; the detector does not. |
| **Frame embeddings** (CLIP-like, per shot) | S3 visual-anomaly hook, duplicate/variety scoring | **Absent.** Anomaly is definitionally *distance from this video's own normal*, which needs a per-shot vector. |
| **Audio event classifier** (laughter, gasp, impact, applause, speech/music/SFX) | S3 payoff-tease, S6 motivation, sound design later | **Absent.** We have only `silencedetect` + `ebur128`. Note `speech_like` is declared in `AudioArtifact` and never populated. |
| **OCR of burned-in text** | S7 graphic triggers, dedupe against existing on-screen text | **Absent.** Lowest priority. |

None of these block the first four implementation steps. S1, S2, S3-tier-1, S9 and S5 are all
reachable with data we already have.

### Honest gaps in the evidence

**What the research could not establish, and should not be asserted in product copy:**

- **Pattern-interrupt cadence is folklore.** Every quoted interval — 3 s, 5 s, 7 s — traces to
  creator-education blogs, never to data. No study manipulates interrupt interval and measures
  retention. Combined with Cutting's 1/f result, the defensible target is a **shot-length
  distribution** with clustered, heavy-tailed variation — never a fixed interval. Any N-seconds rule
  we ship is a tunable default with no evidentiary backing.
  **And the real literature points the opposite way.** Annie Lang's Limited Capacity Model (LC4MP,
  *J. Communication* 2000, ~1,588 citations) establishes that structural features — cuts, onsets,
  audio transients — *automatically* capture cognitive resources and elicit orienting responses. That
  makes an interrupt an involuntary attention reset, but also a **finite budget**: orienting responses
  habituate, and over-triggering spends capacity on the effect rather than the content. So the
  evidence supports a **ceiling** on interrupt density, which is the inverse of the folk advice to
  interrupt as often as possible.
- **Mid-video re-engagement technique efficacy is unsourced.** The one structural point worth
  keeping: the strongest re-engagement is not novelty (a sting, a graphic) but an *unresolved item*.
  Weight loop-opening events above novelty events.
- **No verified safe-zone pixel numbers for any platform.** TikTok's official templates exist and are
  downloadable (and there is more than one safe zone — anchored ads and RTL layouts differ, so any
  single-number "TikTok safe zone" is wrong by construction). Every Meta/Reels URL 404'd. Working
  heuristic until measured: keep text within the centre 80% horizontally, ~15% from top and ~30% from
  bottom — the bottom is by far the most contested region on all three platforms.
- **No shot-length or optimal-duration data for short-form** beyond platform ceilings (Shorts 3 min,
  TikTok 10 min non-Spark). Our retention evidence is drawn from MOOC and film corpora, not TikTok.
- **"Rewatches are rewarded" is unconfirmed.** TikTok states *completion* is a strong signal; its
  documentation does not mention replays. YouTube Shorts runs on "engaged views" without defining
  them. Loop construction remains a craft technique with no platform confirmation.
- **Kuleshov replicates only qualifiedly** — positive within-subject (Mobbs 2006, Barratt 2016),
  null in the one large between-subject attempt (Prince & Hensley 1992). The effect is real but
  modest and design-sensitive; it licenses *context shifting an ambiguous face*, not the strong claim
  that any juxtaposition manufactures meaning. One genuinely useful corollary: **music changes what
  viewers believe a face is feeling**, so the audio bed is an input to picture meaning, not decoration.
- **Kinetic / word-level captions are evidence-free.** Every quantitative claim ("40% longer watch
  time", "retained viewers 38% longer") traces to caption-software vendor marketing. No controlled
  study isolates caption format. Keep them as a platform-convention aesthetic if desired; never as a
  comprehension claim.
- **Ducking depth numbers are folklore** — but they turn out to be unnecessary. Speech intelligibility
  has a real target: **~12 dB signal-to-noise** for full intelligibility. Duck depth is therefore
  *computed per segment* to hit that ratio, not set to a fixed magic number.
- **No music-in-journalism credibility study appears to exist.** "Music harms documentary
  credibility" is unsupported. What *is* established: music biases interpretation of identical
  footage (Marshall & Cohen 1988; Boltz 2001), and silence is a distinct third condition rather than
  neutral — so **adding music is taking a position**, which is a claim we can make honestly.
- **No published minimum text size as a share of frame height** exists in any standard. Anyone
  quoting one is citing folklore; derive it (CEA-708's 15-row grid implies ~5–6.7% per line) and
  label it derived.

### Sound: two findings that constrain the architecture, for the phase after this one

Sound design is not in Phase 2's scope, but two results should be recorded now because they shape how
it must eventually be built:

1. **Risers and silences are defined *backward* from their payoff** — a riser is only a riser because
   of what lands after it. They cannot be placed by a forward-only, left-to-right pass, which is
   exactly what our pipeline is today. Impacts and whooshes *can* be placed forward. Any future sound
   stage needs a two-pass structure.
2. **Attention transients are a finite budget.** LC4MP says every audio onset triggers an involuntary
   orienting response — and that responses habituate. Sound design has a density ceiling, and the
   ceiling is a real cognitive constraint rather than taste.

Also worth recording while it is fresh: **EBU R 128 explicitly states LRA is not valid below 60
seconds**, which rules it out as a short-form loudness metric. Targets: EBU **−23 LUFS** (streaming
distribution −20 to −16), ATSC **−24 LKFS** with short-form never exceeding the long-form it
accompanies, Spotify −14. **YouTube and TikTok publish no official target** — the ubiquitous "−14
LUFS" for both is folklore and would have to be measured, not cited.

### One structural principle worth adopting wholesale

Documentary practice offers a rule that fits our grounding philosophy exactly: **inventory what the
footage can prove, then pick the smallest structure that fits.** Each structure has material
requirements — a question→answer needs an on-camera resolution; a transformation arc needs before,
after *and* the mechanism between them; a list-with-escalation needs only comparable items and no
causal chain. Choosing a structure the footage cannot support is how nonfiction editing slides into
implying causality that was never filmed. That makes structure selection an **honesty** constraint,
not just a craft one — which is precisely the axis this codebase already defends well.

**Two unresolved questions worth deciding before build starts.**
1. Does the grounding *vocabulary* rule need loosening? It is what makes literal description the
   path of least resistance. My recommendation: leave the **evidence** requirement absolutely
   intact, and test whether allowing a wider *editorial* (non-factual) vocabulary raises interest
   without weakening truth. Measure before changing.
2. Should the customer brief get richer (audience, reference, must-include moments)? Most of the
   planner's binding-policy machinery is dormant because tone/style/must-include arrive as `None`.

---

*Proposal only. No code written, nothing merged, nothing deployed. Awaiting approval before
implementation begins.*
