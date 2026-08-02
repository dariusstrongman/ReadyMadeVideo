# Audiovisual Editor Milestone 5: Visual Finishing

Milestone 5 adds an immutable visual-finishing lineage on top of a QC-passed
Milestone 4 audio preview. It plans motion graphics and captions, records
bounded non-destructive color instructions, and renders an inspectable MP4.
It does not retime the selected picture or alter the completed audio mix.

## Inputs and ancestry

The operator endpoint consumes one completed `audio_mix_run` and verifies its
complete Project → Preproduction → Picture Edit → Music Plan → Audio Render
ancestry. It reuses the selected picture candidate, Creative Treatment,
composition evidence, canonical segments, word-timestamp transcript artifacts,
Milestone 3 natural-audio classifications, and Milestone 4 phrase map.

One request creates a shared version across immutable `graphics_runs`,
`caption_runs`, and `color_runs`. Database triggers enforce project/user,
selected-candidate, and complete ancestor consistency. Updates and deletes are
rejected. Concurrent builds collide on the graphics version before dependent
caption or color rows are created.

## Graphic evidence rules

Every available graphic template has an evidence rule. Unsupported counters or
labels are not invented.

| Graphic | Evidence rule |
| --- | --- |
| Intro | Brand-template identity at the opening when a safe lane exists. |
| Animated title | Creative Treatment purpose on the opening hook. |
| Lower third | Named segment subject when no higher-priority action label applies. |
| Callout | Supported hook, peak, or completion beat plus its catalog action. |
| Exercise label | Canonical segment action. |
| Section header | Story-beat boundary or conservative section fallback. |
| Progress bar | Locked selected-picture duration. |
| Rep counter | Explicit numeric rep evidence in catalog/transcript text; never an inferred count. |
| Timer | A supported hold/timer/interval/seconds action; duration comes from the locked clip. |
| Outro | Creative Treatment ending intent at the selected picture ending. |

Graphic starts are snapped to actual Milestone 4 phrase boundaries when they
fall within the configured tolerance. Density follows the Creative Treatment.
The progress bar is the only persistent element; other events are kept short
and evidence-dependent to limit clutter.

## Caption inclusion and exclusion

A transcript alone is not permission to publish captions. For each selected
clip, Milestone 5 records an included/excluded evidence decision with the
Milestone 3 classification, audio score, semantic relevance, timing source, and
reason code.

Captions are included when all of the following are true:

- transcript text exists;
- source audio is usable (`audioScore >= 0.5`);
- semantic relevance is supported (`semanticRelevance >= 0.6`); and
- the segment has an explicit intentional dialogue/narration marker that is not
  marked as background/off-camera/incidental context or a non-speech
  effort/impact action.

`background_chatter` is excluded by default. It can only be overridden by the
meaningful-speech evidence above. `unusable` Milestone 3 events, audio scores
below `0.35`, and `unusable_audio`/`operator_unusable` problems are excluded.
Effort, impact, breathing, grunts, and other non-speech natural audio never
become captions; this decision does not mute or otherwise change those sounds
in the Milestone 4 mix.

Rejection codes are `milestone3_background_chatter`, `unusable_audio`,
`non_speech_natural_audio`, and `no_transcript`. Included speech is labeled
`meaningful_dialogue_supported` or `meaningful_narration_supported`.

## Caption timing and layout

When the upstream transcript artifact contains word timestamps, selected
source-word times are mapped exactly into the locked picture clip and labeled
`transcript_word_timestamps`. If only canonical segment transcript text exists,
words are distributed deterministically inside that clip and clearly labeled
`segment_distributed`; fallback timing is never represented as measured timing.

Caption groups contain at most five words and 42 characters. Each word retains
its own highlight interval. Groups are made non-overlapping, use the caption
safe lane, and displace colliding graphics rather than stacking both elements.
The current upstream transcript contract has one default speaker ID unless a
future transcription provider supplies richer speaker evidence.

## Subject occlusion, safe title, and framing

The platform presets are 1080×1920 (`9:16`), 1080×1080 (`1:1`), and 1920×1080
(`16:9`). Each stores normalized safe-title margins. Text layouts stay inside
those margins; the non-text progress rail sits in the adjacent action-safe
area.

For detected composition, the measured safe-crop box is treated as a
conservative protected subject/action region. The planner tries top, bottom,
and side lanes and omits a graphic when the best measured lane exceeds the
occlusion-risk threshold. When direct geometry is unavailable, it reserves the
center and marks the conservative outer-lane risk for operator inspection.

## Brand templates and contrast

Brand templates version the font family, primary, secondary, and accent colors,
caption style, and title casing. Colors must be six-digit hexadecimal values.
Primary text against the secondary surface must meet WCAG AA contrast of at
least 4.5:1. The server rejects invalid or insufficient-contrast templates.

## Color and LUT limits

Color normalization uses the median exposure score of selected clips, then
records per-clip exposure, temperature, contrast, saturation, highlight
compression, shadow lift, confidence, and `nonDestructive: true`. Corrections
are bounded to prevent extreme grades. The preview applies timed per-clip
exposure/contrast/saturation and temperature adjustments without changing
source media.

The allowlist is `none`, `clean_warm`, `cool_contrast`, and `neutral_social`.
Arbitrary LUT names, file paths, and `.cube` uploads are rejected. Highlight
and shadow values are planning instructions based on existing capture evidence;
Milestone 5 does not claim a new pixel-histogram or calibrated color-chart
measurement.

## Preview and preservation guarantees

The renderer starts from the completed Milestone 4 preview, scales/pads it to
the selected platform, applies bounded color filters, and draws the validated
graphics/captions. It maps the existing audio stream with stream copy. The
render QC records video/audio presence, duration, output dimensions, event and
caption counts, `pictureTimingChanged: false`, and `audioChanged: false`.

## Operator workflow

1. Complete Milestones 1–4 and obtain a QC-passed audio mix.
2. Open the Operator console and select the project.
3. Choose `9:16`, `1:1`, or `16:9` and an allowlisted look.
4. Select **Build finishing preview**.
5. Inspect the signed MP4, template swatches, safe-title data, graphics timeline,
   phrase boundaries, caption evidence/timing, color instructions, and render QC.
6. Building again creates a new immutable version; it never replaces prior runs.

## Tests

The Milestone 5 suite covers platform geometry, template coverage and contrast,
phrase timing, clutter and subject occlusion, chatter/dialogue/unusable-audio
caption decisions, exact and fallback word timing, caption overlap safety,
bounded color normalization, fake-database collisions, operator authorization,
UUID and cross-project protections, complete ancestry, and a real FFmpeg render.

PostgreSQL CI applies every migration through `0012`, runs a valid full lineage,
and proves cross-project rejection, overlap/version constraints, storage-path
ownership, and update/delete immutability for all three run tables.

## Known limitations

- Milestone 3's current classifier initially treats any transcript as chatter;
  Milestone 5 only overrides that classification with the explicit combined
  evidence rule above.
- Speaker diarization is not present in the current transcript artifact.
- Stored composition has a measured safe-crop proxy rather than the original
  per-frame subject boxes.
- Numeric rep counters require explicit textual evidence; no pose-based rep
  counting is claimed.
- LUT support is the safe built-in allowlist, not arbitrary `.cube` ingestion.
- Specialized critics and tournament selection remain later milestones.
