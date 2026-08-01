# Project Two capture plan — designed to test the engine improvements

Purpose: shoot footage that (a) breaks the Project One footage ceiling and (b)
specifically exercises each fix in doc 06, so the next run isolates
engine-improvement from footage-improvement. Same pipeline command, no code
changes required to run it.

## Design principle
Project One confounded two variables (bad footage AND fixable engine behavior).
Project Two must **hold the engine test honest** by deliberately providing the
shot types the engine was blamed for lacking — so if the score still lags, the
cause is unambiguously the engine, not capture.

## Required coverage (fixes the doc-03 ceiling)
Shoot ONE workout, but capture each of these deliberately (the coverage validator
categories, with the P1 gaps in bold):

1. **Establishing** — 2 wide shots of the location, held 6s, no action.
2. **Preparation** — gearing up, chalk, gloves, approaching the equipment.
3. **CLOSE-UP DETAILS** *(missing in P1)* — hands gripping, shoes planting,
   equipment, sweat. 4–6 clips, subject fills the frame, hold 6s+.
4. **Wide action** — the exercise full-body (P1 had these).
5. **Medium action** — waist-up, subject clearly large in frame.
6. **CLOSE-UP EFFORT** *(missing in P1)* — face strain, breathing, grip, at the
   hardest moment. 3–5 clips.
7. **TRACKING / MOVING** *(missing in P1)* — walk/gimbal alongside 2–3 reps.
8. **Peak effort** — the single hardest rep, shot tight AND wide (two angles).
9. **Completion** — finishing, racking, hands-on-knees.
10. **Recovery/exhaustion** — the payoff moment, shot on purpose.
11. **CLEAN natural audio** — 10–20s with only breathing/impacts, NO talking.
12. **Reflection** — a calm closing shot (walk-off, water, looking back).
13. Optional: a second **activity** (so template-diversity logic has something
    real to arc through), and optional drone/environment.

## Per-fix validation matrix
| doc 06 fix | what P2 footage must contain to test it | pass signal |
|---|---|---|
| P1 revision-never-subtracts | at least one unsatisfiable-by-footage critic ask is unlikely now → instead verify revision only trims when it *improves* | v-final ≥ v1 score; never shorter on a "too short" note |
| P2 peak = motion peak | a clearly hardest rep shot tight | peak beat selects the tight hardest-rep clip, cut on exertion |
| P3 activity continuity | two activities + stretches | no cross-activity cut mid-section; stretch lands in cooldown |
| P4 global redundancy | multiple similar wides + distinct closes | no shot family used twice across the timeline |
| P5 adaptive template | genuine arc (warmup→peak→cooldown) | full 7-beat template fills with DISTINCT content, no holes |
| P6 coverage diversity | mix of angles/shot sizes | coverage reports higher band honestly; not a false STRONG |
| P7 rhythm | reps with clear onsets | cuts land on motion onsets/peaks, varied fades |
| P8 motion comparability | 2+ activities of different intensity | motionIntensity 0.8 means the same across both activities |

## Shot discipline (so the test is clean)
- **Get close.** The #1 P1 failure was distance. Fill the frame.
- **Move the camera** on at least 3 shots (tracking/gimbal/handheld follow).
- **Two angles on the peak** — one tight, one wide — so shot-variety is real.
- **Hold every shot ≥6s** (the editor trims; it can't extend).
- **One 15s block of silent effort audio** (no chatter) for natural-sound.
- Keep a few intentional wides for establishing — but they must be the minority.
- Aim for **25–40 usable distinct clips**, more variety than volume.

## How Project Two proves the work
Run the SAME command on P2 footage after doc 06 fixes ship, then compare
scorecards P1 vs P2:
- If **selection/pacing/variety** scores jump (doc 04's editor-fixable lines)
  → the engine fixes worked.
- If **action-visibility/hook/emotional-intensity** jump → the capture fixes
  worked.
- Isolating the two is the entire point: P1 could not, P2 can.

Target: a P2 autonomous first draft at **6–7/10**, with the founder rating again
recorded independently, and the `codex/human-ceiling-evaluation` human benchmark
on the SAME P2 footage to measure the remaining autonomous-vs-human gap.
