# Audiovisual Editor Milestone 3: Music and Sound Supervisor

Milestone 3 creates an immutable, inspectable `MusicPlan` from the Milestone 1
Creative Treatment and the selected, supported Milestone 2 picture candidate.
It plans audio; it does not select a licensed file or render/mutate a timeline.

## Music Plan

The deterministic supervisor records:

- a treatment-derived tempo, 4/4 beat grid, bar map, and four-bar phrase map;
- music-energy points aligned to picture time and musical anchors;
- per-selected-clip natural-audio classification (`clean_natural`, `effort`,
  `impact`, `background_chatter`, or `unusable`);
- explicit source gain/fade moves, chatter reduction, impact emphasis, and
  music ducking envelopes;
- two-pass `-14 LUFS` integrated / `-1 dBTP` true-peak targets under
  ITU-R BS.1770-4;
- phrase-resolved ending instructions with an intentional natural-audio tail;
- picture-to-music sync instructions that move/edit music around locked picture
  cuts rather than changing Milestone 2 picture timing.

Beat/phrase analysis is labeled `treatment_derived_music_brief`: without an
attached licensed track, it is a target grid for track search and editorial
conformance, not a claim that a waveform was analyzed.

## API and operator workflow

`POST /projects/{project_id}/music-sound` accepts an optional typed UUID
`pictureEditRunId`. Otherwise it uses the latest picture-edit run. The API
requires operator authorization, a selected supported candidate, matching
project ancestry, its originating preproduction treatment, and the segment
catalog. It audits before inserting a new immutable version.

In Operator, run **Build music + sound plan** after Milestone 2. The inspection
panel shows tempo/phrases/loudness, beat markers, natural-audio classifications,
chatter moves, ducking, impacts, fades, ending, and sync instructions.

## Persistence and evidence protection

Migration `20260801_0010_audiovisual_music_sound.sql` adds
`music_sound_runs`. Rows are versioned per project, service-write-only, readable
under owner/operator RLS, ancestry-checked against both input milestones, tied
to the persisted selected candidate, and immutable against update/delete.
Existing timelines, Project One artifacts, and Milestone 1/2 records are never
updated.

## Explicit boundary

This milestone adds no motion graphics, captions, color grade, specialized
critics, final tournament selection, licensed-track acquisition, or audio
rendering. Those remain later work.
