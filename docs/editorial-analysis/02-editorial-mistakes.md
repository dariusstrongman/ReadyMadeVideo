# Editorial mistakes the SYSTEM made (fixable in the engine)

These are decisions the engine got wrong given the footage it had — i.e. a
better ranking/loop would have chosen differently from the SAME 165 segments.
Ordered by editorial severity.

1. **Revised the video shorter while the critic demanded it be longer.**
   26.1s → 21.5s across two passes; critic asked for 35s both times. The single
   most damaging system behavior. (critic/revision loop)

2. **Shipped a score-regressed version as final.** v1 scored 0.4, v3 scored 0.3;
   the loop still shipped v3. No "keep best" guard. (critic/revision loop)

3. **Placed an unrelated stretch in the `build` beat.** Broke the tire-flip
   through-line; the closest/best-framed shot in the catalog was spent on the
   one moment it hurt continuity. (ranking: motion_fit over activity-continuity)

4. **Chose a "walking away" clip for `peak`.** The literal climax of the edit is
   someone finishing and stepping back, with a real mid-flip available and
   demoted by the uniqueness penalty. (ranking: uniqueness over impact)

5. **Used the same walking shot for both `location` and `reflection`.**
   Near-identical bookends from adjacent segments of one clip. (variety term
   only checks the previous pick, not the whole timeline)

6. **Three of seven beats are near-identical tire-flip wides** (hook,
   early_effort, plus flip-heavy others). No global redundancy check across the
   assembled timeline. (ranking + template)

7. **Under-filled the duration target at selection.** Planned 35s, assembled
   26.1s before any revision. Clip-length allocation doesn't compensate when few
   distinct segments pass hard constraints. (selector)

8. **Trusted critic timestamps blindly.** Pass1's normalized-looking timestamps
   silently misrouted every revision to the hook clip; the agent had no sanity
   check that a `[0.1-0.2]` range on a 26s timeline is implausible. (revision
   agent robustness)

9. **Coverage tool labeled the footage "STRONG" (55s target).** Category-presence
   counting mistook repetition for coverage; following its recommendation would
   have forced an even more padded, repetitive edit. (coverage validator)

10. **No "footage cannot satisfy this" signal.** The system never surfaced, in
    the edit itself or its status, that the critic's core asks (closer shots,
    angle variety) were physically unsatisfiable — so effort was wasted trimming
    instead of reporting the ceiling. (loop + reporting)

Not counted as system mistakes (correct behavior): choosing the hook (a real
action moment first), degrading gracefully when Gemini dropped, stripping GPS
from every output, refusing to fabricate footage.
