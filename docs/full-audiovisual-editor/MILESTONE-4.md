# Audiovisual Editor Milestone 4: Licensed Audio Rendering

Milestone 4 attaches a real operator-provided licensed music file to an
immutable Milestone 3 plan, analyzes its actual waveform, and renders a real
mixed preview without changing the selected picture edit.

## Licensed attachment

The operator uploads WAV, MP3, M4A, AAC, or FLAC (maximum 50 MB) into the
project owner's private `raw-footage` path. The server validates the extension,
declared content type, stored byte count, decodability, duration, mono/stereo
channel count, 32–96 kHz sample rate, project path ownership, and mandatory
license provider/type/reference plus explicit operator confirmation.

Replacing music creates a new immutable licensed-asset and audio-mix version;
it never updates the prior attachment.

## Actual waveform analysis

The track is decoded by FFmpeg to mono PCM. Onset-energy analysis records BPM,
beat locations/strengths, inferred downbeats, bars, four-bar phrase boundaries,
and a half-second normalized energy envelope. These fields are labeled
`analysisSource: actual_waveform`. The persisted comparison retains Milestone
3's separate `treatment_derived_music_brief` target, so targets and measurements
cannot be confused.

## Picture matching and completed mix

The actual phrase map selects a picture-length music window ending on a phrase
boundary when the licensed file supports one. Every locked picture cut is
compared with real beat positions. Instructions edit/align the music against
picture; they never move picture clips.

The real FFmpeg render contains selected-picture video, planned source natural
audio, chatter attenuation/muting, impact gains, bounded overlap-merged music
ducking, ramped duck attack/release, music/source fades, and a clean resolved
ending. FFmpeg measures the premix, then performs the documented second pass to
`-14 LUFS` integrated and `-1 dBTP`, followed by a true-peak limiter.

## Audio QC

The completed MP4 records measured integrated LUFS and true peak, clipping,
silence ranges, abrupt 100 ms gain changes, missing audio/video streams,
duration, and an overall QC decision. The private preview is uploaded to
`exports` and signed for operator playback.

## Persistence

Migration `20260801_0011_audiovisual_audio_rendering.sql` adds immutable,
versioned `licensed_music_assets` and `audio_mix_runs`. Database triggers enforce
project/user, Milestone 1–3, selected-picture, licensed-track, storage-path, and
actual-analysis ancestry. Update/delete is rejected for both tables.

## Boundary

No motion graphics, captions, color grading, specialized critics, tournament
selection, or picture retiming is introduced. A different picture edit must be
requested through the picture-edit workflow rather than hidden in audio sync.
