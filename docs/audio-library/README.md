# Audio asset library ingestion

This package builds a local, searchable music and sound-effects library for the audiovisual editor. It keeps license evidence beside every accepted asset and rejects anything that cant support commercial editing.

No provider downloads run by default. The checked-in sample plan has `downloadAuthorized: false`. Production Supabase is not part of this workflow.

## What is included

- A central license policy for CC0, CC BY 3.0/4.0, and explicitly approved commercial royalty-free licenses
- Official Freesound API discovery and original-file download support
- Operator-controlled local file import with JSON sidecars
- Safe FFmpeg validation and normalization to 48 kHz, 24-bit WAV
- SHA-256 and normalized PCM fingerprint duplicate detection
- Deterministic manifests, attribution reports, rejection reports, and ingestion summaries
- Structured local search and a read-only Milestone 3 lookup adapter
- Resume state, bounded retries, request pacing, download limits, and dry runs

## Provider rules

### Freesound

Freesound access uses only its official API. The free API terms describe noncommercial access, and original file downloads require OAuth 2. For that reason, production ingestion stays disabled unless all of these values exist:

```text
FREESOUND_API_KEY
FREESOUND_OAUTH_TOKEN
FREESOUND_COMMERCIAL_API_APPROVED=true
FREESOUND_COMMERCIAL_API_APPROVAL_REFERENCE
FREESOUND_TERMS_REVIEWED_AT
```

The approval reference should point to written permission, a commercial API agreement, or an internal legal review tied to the current provider terms. An API key alone is not enough.

Official references:

- [Freesound API terms](https://freesound.org/help/tos_api/)
- [Freesound API authentication](https://freesound.org/docs/api/authentication.html)
- [Freesound API resources and download endpoint](https://freesound.org/docs/api/resources_apiv2.html)
- [Freesound license FAQ](https://freesound.org/help/faq/)

### Manual import

Put an operator-provided media file and its `.json` sidecar in `assets/audio/manual-import/`. Start from `example.sidecar.json.example`. The media path must remain inside that directory. The sidecar must include the provider/source identity, creator, license URL, provider terms review, commercial-use decision, modification decision, and an approval reference.

Manual import does not treat an operator upload as proof of rights. The sidecar evidence still has to pass the central policy. A rejected source stays in the operator-controlled staging folder, but it is never copied to an eligible category, normalized, or written to an accepted manifest.

YouTube Audio Library, Mixkit, and Pixabay download automation is not implemented. Their site and account terms need a separate review before an adapter is added.

## License decisions

The license decision runs before the first media request.

Accepted:

- CC0 1.0
- CC BY 3.0 or 4.0 with creator name, creator URL, license URL, and attribution text
- A commercial royalty-free license listed in `assets/audio/approved-licenses.json`, with commercial use and modification explicitly approved

Rejected:

- Creative Commons NonCommercial variants
- Creative Commons NoDerivatives variants
- Sampling+
- Unknown or unclear licenses
- Missing creator or license metadata
- Required attribution with incomplete attribution fields
- A provider access method that is not approved for ingestion and commercial use
- Missing provider approval evidence

Tags such as `royalty-free`, filenames, search text, and provider category labels never determine legal eligibility.

This system records evidence and enforces the configured policy. It does not provide legal advice. A person responsible for rights clearance must approve proprietary licenses and provider access.

## Media processing

Downloads go to a random file under `assets/audio/temp/`. The pipeline then:

1. Checks the extension, MIME type, and 100 MB size cap.
2. Uses FFprobe to require exactly one audio stream, no video stream, mono or stereo, 32 to 96 kHz input, and a duration no longer than 10 minutes.
3. Calculates the source SHA-256.
4. Decodes canonical mono 48 kHz signed 16-bit PCM and hashes it for cross-format duplicate detection.
5. Writes a new 48 kHz PCM 24-bit WAV. Music is stereo with a -18 LUFS and -1.5 dBTP normalization target. SFX keeps mono/stereo shape and uses a -1 dB peak limiter.
6. Strips embedded metadata and records the list-form FFmpeg command with `{input}` and `{output}` placeholders.
7. Deletes the temporary download.

The command runner uses argument lists with `shell=False`. Existing normalized files are never overwritten. Manual source files remain untouched.

## CLI

Run from `render-backend/`:

```powershell
python -m app.audio_library search "impact" --type sfx --category impacts --max-count 10
python -m app.audio_library --json search --type music --mood energetic --bpm-min 110 --bpm-max 130
python -m app.audio_library ingest --provider freesound --type sfx --category impacts --dry-run --max-count 5
python -m app.audio_library import-local --dry-run --max-count 10
python -m app.audio_library import-local --resume --max-count 10
python -m app.audio_library validate
python -m app.audio_library report
```

Global `--root` points the command at another library directory. `--json` produces machine-readable output. Ingestion count is capped at 100 per invocation. Freesound retries are bounded at five and can be configured with `--retries` and `--rate-limit-seconds`.

## Files and reports

```text
assets/audio/
  approved-licenses.json
  sample-ingestion-plan.json
  manual-import/
  music/<category>/
  sfx/<category>/
  manifests/assets/<asset-id>.json
  audio-library.json
  attribution-report.md
  rejected-assets.json
  duplicate-report.json
  ingestion-summary.json
  ingestion-state.json
```

Accepted asset manifests include source identity, original filename, creator and license evidence, usage constraints, provider terms evidence, timestamps, original and normalized media properties, tags, mood, energy, BPM, vocal/instrumental flags, SHA-256, PCM fingerprint, normalized path, ingestion/policy version, and the recorded transformation.

Runtime media and reports are gitignored because licensed material and operator evidence should not be published with the source repository.

## Milestone 3 integration

`AudioLibraryAdapter.search_for_music_plan()` accepts a Milestone 3 music plan. It maps the track brief tone, tempo target, energy arc, and candidate duration into a local manifest query. Results include the asset ID, normalized path, source identity, license fields, hashes, and ingestion version.

The adapter is read-only. It does not change the music plan or claim a match when no eligible asset exists. Milestone 3 remains responsible for the final operator selection and licensed-track attachment.

## Adding a provider

1. Implement `AudioProvider.search()` and `AudioProvider.download()`.
2. Use fixed HTTPS origins and an allowlist for every redirect.
3. Put secrets in authorization headers, not URLs, logs, manifests, or CLI output.
4. Map provider results into `ProviderAsset` without guessing missing license facts.
5. Add provider-terms fields and require an approval reference.
6. Add tests for no credentials, unsafe redirects, rate limits, retry limits, missing rights evidence, and download size.
7. Update this document with official terms and API references.

## Current limits

- The repository contains no licensed audio and the sample plan does not authorize downloads.
- Freesound production ingestion needs written commercial API approval.
- The PCM fingerprint catches identical decoded audio. It is not an acoustic similarity detector for remasters, time shifts, pitch shifts, or edits.
- Music normalization uses FFmpeg loudness normalization during ingestion. Final program loudness and true-peak QC still belong to Milestone 4.
- Provider terms and commercial agreements can change. Their review timestamps and approval references need maintenance.
