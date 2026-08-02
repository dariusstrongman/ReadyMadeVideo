# Milestone 2 - Deterministic picture editor

## Outcome

Milestone 2 turns Milestone 1 evidence into three inspectable, picture-only
timeline candidates. It consumes the latest Creative Treatment, Capture Quality
Report, per-segment composition evidence, and story variants. It does not alter
the legacy planner/selector/critic/render behavior and does not add any later
department output.

The three candidates are deliberately different:

1. `kinetic_hook` uses the social-retention/action-first direction, short shots,
   front-loaded motion, and strong subject-prominence weighting.
2. `treatment_arc` follows the Creative Treatment's selected valid story
   direction with balanced shot durations.
3. `controlled_payoff` prefers cinematic/build-and-payoff structure, longer
   readable shots, composition, technical quality, and a held ending.

Candidates are persisted as immutable evidence in `picture_edit_runs`. Existing
timeline rows and every Project One baseline artifact remain untouched.

## Visual Rhythm Planner

Each story beat receives an explicit target shot duration:

```text
base shot seconds = 3.2 - (2.3 * beat energy)
candidate shot seconds = clamp(base * profile factor, 0.65, 4.5)
planned shot count = ceil(beat target seconds / candidate shot seconds)
```

Profile factors are `0.72` kinetic, `1.0` balanced, and `1.32` controlled.
The first shot is capped at 1.2s/1.45s depending on profile. An ending with a
declared payoff is held at least 1.35s for kinetic or 1.8s otherwise. Every plan
stores its energy progression, pacing intent, capture ceiling, and repetition
risk.

## Picture selection

Segments must satisfy the story beat's declared footage requirements. Mostly
black, frozen, or operator-unusable footage is excluded. The editor never fills
an unsupported beat with unrelated material.

Ranking combines story support, motion-to-energy fit, technical quality,
measured/estimated subject prominence, composition quality, variety, and role
fitness. Profile-specific weights make the three results materially different.
The hook role strongly favors `hook`/`peak`, motion, and prominence. The payoff
role strongly favors `completion`/`reflection` and readable lower-energy
resolution.

Repetition controls prohibit segment reuse and duplicate-group reuse. The same
action family may appear at most once when capture repetition exceeds 0.65 and
at most twice otherwise. Consecutive same-asset, shot-size, and action choices
receive variety penalties.

## Safe virtual reframing

A clip receives `virtualReframe.mode = safe_crop` only when Milestone 1 has:

- `measurementSource = detected_bbox`;
- a feasible 9:16 crop containing the measured subject/action bounds; and
- at least one source pixel per output pixel.

The timeline stores the normalized crop box, measurement source, pixel ratio,
confidence, and reason. Semantic shot-type estimates never authorize a crop;
they store `mode = none` and preserve the full source frame. The current renderer
ignores this forward-compatible metadata, so render behavior is unchanged.

## Persistence and API

Migration `20260801_0009_audiovisual_picture_editor.sql` creates immutable,
versioned `picture_edit_runs`. Owners and operators may read. Writes are
service-role-only through the audited API, and database triggers enforce project
ownership, preproduction ancestry, immutability, and unique project versions.

`POST /projects/{project_id}/picture-edit` accepts an optional
`preproductionRunId`; otherwise it uses the latest Milestone 1 run. It requires
an operator and segment catalog, records the audit before insertion, and returns
all three candidates plus the deterministic default candidate. It does not
create or overwrite a row in `timelines`.

## Explicit boundary

Milestone 2 contains no music, sound design, audio cleanup, motion graphics,
titles, captions, color treatment, specialized critic, tournament ranking, or
render change. Those remain later milestones. Candidate choice here is a
deterministic default based on the Creative Treatment, not a quality tournament.

## Known limitations

- Until measured bounding boxes exist, virtual reframing remains disabled.
- The editor selects deterministic source ranges from the beginning of each
  catalog segment; focused within-segment peak localization belongs to the
  Adaptive Footage Inspector.
- Coverage checks prove that a supporting segment exists, not that every beat
  can reach its target duration without reuse. Short candidates remain visible
  with coverage and rejection evidence.
- Candidate timelines are stored for later departments but are not preview-
  rendered or promoted into the autonomous baseline lineage in this milestone.
- The heuristics are inspectable but not trained or calibrated across a broad
  fitness dataset.

## Acceptance checks

- Exactly three candidates with unique structural signatures.
- Strong first-frame hook and explicit shot-duration plan.
- Valid candidates end on completion/recovery evidence.
- Duplicate groups and excessive action repetition are controlled.
- Subject prominence changes selection ranking.
- Only measured, resolution-safe crops generate virtual-reframe instructions.
- Candidate timelines contain video only and preserve volume/speed defaults.
- API is operator-only, audited, versioned, and cross-project safe.
- Migration applies after 0008 and protects immutable picture-edit evidence.
- Project One baselines remain unchanged.
