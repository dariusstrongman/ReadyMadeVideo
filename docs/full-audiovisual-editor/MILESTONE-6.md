# Milestone 6: Editorial intelligence

Milestone 6 evaluates complete, rendered candidate videos above the immutable
Milestones 1–5 lineage. It never mutates earlier runs or Project One artifacts.

## Candidate generation

Each batch uses valid Milestone 2 picture candidates, the Milestone 3 sound
plan, the real Milestone 4 completed mix, and Milestone 5 finishing evidence.
Initial candidates vary supported hook choice, picture candidate,
pacing profile, bounded music synchronization (maximum 150 ms), graphics timing,
caption position, and allowlisted non-destructive color preset. Each candidate
is rendered to a private MP4 and stored once as immutable evidence. A manifest
records source asset IDs and `fabricatedFootage: false`.

## Critics and bounded revision

Ten independent deterministic critics cover hook effectiveness, story
structure, pacing/retention, picture quality, music synchronization, audio
quality, motion graphics, captions, color finishing, and publishability. Every
score contains metric, observed value, target, source reference, weight,
contribution, and explanation. A SHA-256 consistency hash makes repeated scoring
against identical evidence detectable.

Critics may request only: hook change, existing-clip reorder, bounded music
timing shift, graphics timing shift, caption layout change, or non-destructive
color instruction change. A revision reuses existing clips, records every
instruction, points to its immutable parent, and is re-rendered and re-criticized.
No trim extension, generated insert, or fabricated footage is permitted.

## Tournament and publishability

The tournament stores all `n × (n-1) / 2` pairwise comparisons before running a
deterministic elimination bracket. Ties are resolved by stable candidate key.
Each comparison records score deltas and decisive evidence. The winner retains
the complete bracket and winner reasoning.

Publishability reports score hook quality, pacing, emotional payoff, clarity,
graphics, captions, music fit, audio, and technical QC. The weighted overall
score is publishable only at 75 or higher with technical QC passing and no
blocking issue.

## Human-ceiling comparison

When an approved human-ceiling session exists, the batch compares autonomous
initial, optional autonomous revised, human approved, and the editorial winner.
It reports score and duration deltas plus authoritative server-measured human
correction minutes. Missing human evidence is labeled unavailable. It is never
invented.

## Persistence and ownership

Migration `0013` adds immutable, RLS-protected `candidate_runs`, `critic_runs`,
`publishability_reports`, and `tournament_runs`. Database triggers validate the
full preproduction → picture → music → audio → graphics → captions → color
ancestry, project/user/batch ownership, parent lineage, private preview path,
winner membership, and no-fabrication constraint. Updates and deletes are
rejected for all four tables.

## Operator workflow

1. Complete a QC-passed Milestone 5 finishing run.
2. Open **Milestone 6 editorial intelligence** in the operator console.
3. Select **Generate, critique + select winner**.
4. Browse candidate previews and variant ancestry.
5. Inspect the ten structured critic reports and publishability dimensions.
6. Inspect the complete pairwise matrix, bracket, winner reasoning, and
   four-way human-ceiling comparison.

## Known limits

- Critics are deterministic evidence rules, not learned audience-retention
  predictors. Platform performance still requires real post-publication data.
- Music shifts are limited to 150 ms and picture timing stays
  within existing selected clips.
- The human comparison is incomplete until an approved session and scorecards
  exist.
- Milestone 6 selects a publishable artifact but does not publish or deploy it.
