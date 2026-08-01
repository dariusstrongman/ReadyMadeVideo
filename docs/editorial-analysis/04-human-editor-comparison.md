# What a strong human editor would likely do differently

Purpose: isolate the gap between the autonomous editor and a skilled human on the
*same 10 clips*. Everything here is achievable without new footage — so this list
IS the fixable-editor gap (it maps directly to the engine improvements in doc 06).
This is Claude's estimate of the human ceiling; the `codex/human-ceiling-evaluation`
branch is producing the independent benchmark to compare against.

## Mistakes a good human editor would avoid
1. **Would not put the stretch in the build.** A human keeps the tire-flip
   through-line and either drops the stretch or uses it as a deliberate breath —
   never mid-escalation. (→ engine: activity-continuity constraint)
2. **Would not pick "walking away" as the peak.** A human scrubs to the hardest
   single flip and cuts on the exertion frame. (→ engine: peak = motion-peak
   moment, not segment-average; de-weight uniqueness for the climax)
3. **Would not repeat the same walking wide as both open-adjacent and close.** A
   human uses one walk once. (→ engine: global redundancy check)
4. **Would cut to the beat of the actual flips.** A human trims each flip to
   start just before the heave and cut on the drop, creating rhythm. The engine
   uses whole ~8s windows with fixed fades. (→ engine: action-aligned in/out
   points using motion peaks already computed in motion.json)
5. **Would make it SHORTER on purpose, and own it.** A human seeing only
   repetitive wides would cut a tight 15–20s piece and not apologize — which is
   the opposite of the loop's "trim because I can't add." The difference is
   *intent*: a human trims to strengthen; the engine trimmed as a failure
   fallback. (→ engine: convergence guard + honest short-cut mode)
6. **Would front-load the two or three most dynamic flips** and discard the
   weakest, rather than distributing similar flips across named beats. (→ engine:
   quality-first selection when footage is single-activity)
7. **Would fix the audio** by pulling music under the chatter or muting/ducking
   the talk. The engine used natural audio raw (chatter included) because no
   music was supplied and it won't fabricate. (→ product: supply a licensed music
   bed option; not an autonomy failure, a missing input)

## Where the human and the engine tie (footage ceiling — see doc 03)
- Neither can add close-ups, angles, or camera movement.
- Neither can build a real intensity arc that wasn't performed.
- Both are limited to a session recap; the human's is tighter and better-rhythmed,
  but still not "cinematic."

## Estimated scores on THIS footage
| | autonomous v3 | strong human (est.) | ceiling cause |
|---|---|---|---|
| Overall watchability | 4 | 5–6 | footage |
| Hook | 3 | 5 | footage (wide) |
| Shot selection | 4 | 7 | **editor-fixable** |
| Pacing/rhythm | 5 | 7 | **editor-fixable** |
| Continuity | 7 | 8 | mostly footage-strength |
| Action visibility | 3 | 3 | footage (hard cap) |
| Ending/payoff | 4 | 5 | editor-fixable within limits |

The 1–2 point human advantage is almost entirely **selection quality + rhythm +
not-self-sabotaging** — exactly the engine areas in doc 06. It is NOT close-ups or
angles; those are a shared ceiling. This is the good news: the engine gap is
addressable in logic, and the footage gap is addressable by the capture plan.
