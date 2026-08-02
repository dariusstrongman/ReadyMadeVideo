# Milestone 1 - Creative Treatment and capture truth

## Outcome

Milestone 1 adds a preproduction layer that runs before clip selection. It does
not change the current planner, selector, critic, revision agent, timeline
builder, or renderer. Its output is an inspectable planning contract for the
later audiovisual departments.

The supported request boundary is explicit: vertical fitness edits from 15 to
60 seconds. Unsupported duration/orientation requests are rejected instead of
being silently coerced into a misleading result.

## Architecture

`composition.py`

- Validates normalized subject, face, and action bounds.
- Measures subject prominence, action/face visibility, empty space, headroom,
  occlusion, and composition quality separately.
- Calculates a 9:16 crop that must contain the complete subject/action.
- Rejects crops that cut required bounds or fall below the configured source-
  pixels-per-output-pixel floor.
- Marks shot-type-only fallbacks as low-confidence estimates and never claims
  that those crops are safe.

`capture_quality.py`

- Measures angle count, shot-size diversity, close-up/tracking/establishing/
  completion/recovery presence, natural audio, repetition, dead time, crop
  potential, and measured-composition coverage.
- Produces strengths, limitations, missing shots, target duration, style, and
  an estimated edit ceiling.
- The ceiling tops out at 9 from capture evidence alone; editing and finishing
  still have to earn the remaining quality.

`story_editor.py`

- Always produces five materially different directions: action-first,
  build-and-payoff, raw/intense, cinematic, and social-retention optimized.
- Every beat declares duration, footage requirements, energy, audio, graphics,
  transitions, and ending intent.
- A direction is rejected when required coverage has no matching segment. No
  missing beat is invented or filled with semantically unrelated footage.

`creative_director.py`

- Produces the versioned `CreativeTreatment` shared contract.
- Records purpose, audience, target, tone, story arc, visual/music energy
  curves, natural-audio priorities, graphics density, transition grammar,
  color direction, ending intent, capture constraints, and confidence.

`preproduction.py`

- Orchestrates capture truth -> variants -> treatment.
- Does not select clips, create timelines, call a provider, or render media.

## Persistence and operator control

Migration `20260801_0008_audiovisual_milestone1.sql` adds append-only-versioned
`preproduction_runs`. Owners and operators may read; writes remain service-role
only and pass through the authenticated, audited API.

`POST /projects/{project_id}/preproduction` validates the supported brief,
builds the complete package from the project's segment catalog, confirms the
operator audit record before insertion, and creates a new version. The operator
console displays the Creative Treatment, capture report, warnings, all five
directions, and the exact rejection reasons for unsupported directions.

## Schema and evidence rules

- Canonical segment schema is version 2 and now preserves `cameraAngle`, which
  the semantic provider already returned but catalog construction discarded.
- Composition metrics from actual boxes are labeled `detected_bbox`.
- Until the focused inspector supplies boxes, the package uses conservative
  `semantic_shot_type` estimates, sets crop potential to zero, recommends human
  review, and emits a warning.
- The current scoring weights are documented deterministic heuristics. They are
  not trained or calibrated against Project One and must be evaluated on
  additional fitness activities before quality claims are made.

## Project One boundary

The committed repository contains the editorial analysis and workflow guide,
but not Project One's private scorecard/timeline artifact files. This milestone
therefore does not claim a Project One quality result. No baseline media,
timeline, preview, transcript, or report was modified or committed.

Project One is rerun only in Milestone 6, into a new
`full-audiovisual-v1` lineage after the complete audiovisual path exists.

## Known limitations

- No subject detector is introduced yet. Measured composition is ready to
  consume the Adaptive Footage Inspector's observations in Milestone 2.
- The Creative Treatment is stored and visible but does not yet drive the
  legacy picture editor; that wiring belongs to Milestone 2.
- No music, audio cleanup, graphics, captions, color, specialized critics,
  tournament selection, or publishability decision is implemented here.
- No AI provider is called, so a real-provider smoke test is not applicable to
  this milestone. All CI behavior is deterministic.
- The new migration is committed but is not applied or deployed by this branch.

## Acceptance checks

- Creative Treatment rejects invalid curves and unsupported duration/orientation.
- Five story structures are materially distinct.
- Unsupported beats reject the direction.
- Subject prominence is separate from action visibility and composition.
- Crop feasibility includes bounds and output-resolution safety.
- Capture ceiling falls when angle, shot-size, audio, ending, and diversity
  evidence is missing.
- API creation is operator-only, audit-before-action, versioned, and inspectable.
- No existing timeline or Project One artifact is changed.
