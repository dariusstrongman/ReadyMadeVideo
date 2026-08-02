# Changes that would OVERFIT to this single project (do NOT make these)

Project One is n=1: one athlete type, one activity (tire flips), one location,
one camera setup, portrait orientation, ~2 people. Any "fix" that only works
because of those specifics is overfitting. Flagging them so they are avoided when
implementing doc 06.

## High overfit risk — avoid
1. **Tuning motion thresholds to tire-flip magnitudes.** The tire-flip pixel
   signatures are specific. Hard-coding "peak = motionIntensity > 0.75" or
   picking the 41.0/12.0 normalization constants to make THESE flips score well
   would break on running, cycling, yoga, lifting. Fix motion comparability
   generically (scene-relative + documented), validated on ≥2 activity types
   before trusting.
2. **Hard-coding "avoid stretching in build".** The real rule is *activity
   continuity between adjacent beats*, not "stretches are bad." A stretch is
   perfect for a cooldown beat. Encode the general constraint, not the specific
   symptom.
3. **Forcing a shorter template because THIS footage was repetitive.** Some
   projects will have real arcs. Make template length **adaptive to measured
   diversity**, don't shrink the default.
4. **Down-weighting `uniqueness` to zero** because it hurt the peak here. It also
   correctly diversified sources elsewhere. Fix the *interaction* (uniqueness
   shouldn't override the climax pick), don't delete the term.
5. **Special-casing single-camera / single-activity** as the norm. Project Two
   will (deliberately) have multiple angles and activities; logic that assumes
   monotony will then underperform.
6. **Assuming portrait 9:16 or ~2 subjects.** Don't bake orientation or
   subject-count assumptions into selection or rendering.
7. **Calibrating the critic to agree with THIS 4/10.** The critic was actually
   *useful* here (found the real defects). Don't tune its prompts/scoring to
   match one human rating; validate calibration across several projects.
8. **Tuning Gemini scene-subdivision (~8s) to these clip lengths.** Keep it a
   function of clip duration, not a constant chosen for 73–318s clips.

## Low overfit risk — safe to generalize now
- Revision loop must never trim when the critic asked for *more* → universal.
- "Keep best-scoring version" convergence guard → universal.
- Peak beat should target the motion-PEAK moment within a segment → universal.
- Global (whole-timeline) redundancy check → universal.
- "Unsatisfiable request → stop and report the footage ceiling" → universal.
- Provider retry / long-clip upload handling (already added) → universal.

## Guardrail for implementation
Any change in doc 06 should be justified by a *general editing principle* and,
where it touches thresholds, be validated against Project Two's DIFFERENT footage
before it is trusted. One project cannot validate a ranking weight.
