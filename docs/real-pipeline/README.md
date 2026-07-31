# Stromation Real Video Pipeline

## Current status — the honest version
| Claim | Status |
|---|---|
| Authenticated upload → single-clip render flow | **Implemented and live-tested** |
| Advanced analysis + autonomous-edit modules (planner/selector/critic/revision) | **Implemented, experimental** — tested on synthetic + demo footage only |
| Advanced engine connected to the web application | **Implemented via the operator job system** (operator-triggered; the CUSTOMER app does not expose these controls yet) |
| Professional quality on real fitness footage | **Unproven** — no real fitness footage has been processed |
| Production deployment | **Not completed** — everything runs locally / on the feature branch |
| Human quality control | **Still required** — the operator console exists precisely for this |

Branch: `feature/real-video-pipeline`. This replaces the simulated app pages with a
**real, working** upload → project data → structured timeline → FFmpeg render → download
pipeline. No fake progress bars, no pre-rendered outputs, no localStorage auth.

```
Browser (app/, React+Vite SPA)
  │  Supabase Auth (email+password, session restore, reset)
  │  PostgREST reads/writes under RLS (publishable key only)
  │  XHR upload → private raw-footage bucket (real progress, path-scoped RLS)
  ▼
Supabase (project "Stromation", iadzcnzgbtuigyodeqas)
  tables: profiles · projects · media_assets · timelines · render_jobs (all RLS)
  storage: raw-footage (private) · exports (private), 50 MB/file plan cap
  ▼
Render backend (render-backend/, FastAPI + FFmpeg, port 8787)
  verifies the user JWT via GoTrue → explicit ownership-chain checks →
  validates timeline JSON → downloads source (service role) →
  ffmpeg title-card + trim + concat → uploads private export →
  updates render_jobs (queued/processing/completed/failed + output metadata)
```

## What is REAL in this slice
- Account creation, sign in/out, password reset, session restoration (Supabase Auth)
- Projects CRUD with RLS; auto-created profile row (DB trigger)
- Real file upload (MP4/MOV, real XHR progress) into private, user-scoped storage
- `media_assets` records that survive refresh
- Timeline JSON stored/versioned in the DB, loaded back into the editor form
- FFmpeg render from the user's actual uploaded footage: 2 s title card
  (drawtext) + trimmed range, H.264/AAC MP4, faststart, handles no-audio sources
- Live job status from the `render_jobs` row (no timers)
- Signed-URL preview + download (1 h expiry); cross-user access provably blocked

## Analysis pipeline (added 2026-07-31 — implementation-order milestones 2–7)

`render-backend/app/pipeline/` — a resumable, idempotent, stage-based analysis
pipeline. Every stage writes an inspectable artifact (`asset_analysis` table in
cloud mode, JSON files in local mode); completed stages are skipped on re-run.

| Stage | Status | What it does |
|---|---|---|
| probe | **implemented** | full ffprobe metadata (codec/res/fps/rotation/audio/creation time) |
| proxy | **implemented** | 720p analysis proxy + thumbnails + 16 kHz wav; originals retained |
| scenes | **implemented** | PySceneDetect ContentDetector shot boundaries |
| mechanical | **implemented** | deterministic per-scene: blur, exposure + clipping, black frames, frozen frames, motion energy, shake, perceptual-hash duplicate groups — each measurement stored independently, no LLMs |
| audio | **implemented** | EBU R128 loudness, true peak, clipping, silence ranges |
| transcript | **implemented** (provider) | `TranscriptionProvider` → OpenAI Whisper API w/ word timestamps; faster-whisper/WhisperX/pyannote/DeepFilterNet = declared optional providers, not yet built |
| semantic | **implemented** (provider) | `VideoUnderstandingProvider` → Gemini 2.5 Flash via Files API, strict responseSchema, pydantic-validated; NOT hard-coded to one provider |
| motion | **implemented** | dense local sampling (~12 fps OpenCV frame-diff): intensity, peak moments, stationary ranges. MediaPipe Pose = optional upgrade (Python 3.14 wheel pending) |
| catalog | **implemented** | merges everything into canonical versioned `Segment` records + full-text-searchable `segments` table (degrades gracefully without AI providers) |

Run it: `python -m app.pipeline.runner --file video.mp4 --out outdir` (local) or
`--asset <media_asset_id>` (cloud). Live-verified: Gemini correctly described the
repo's real test clip; Whisper transcribed via API; 9 pipeline tests green.

## Editing engine (milestones 8–15 — added same day)

| Component | Status | Notes |
|---|---|---|
| Story planner + fitness template | **implemented** | `templates/fitness_v1.json` (configurable, 7 beats hook→reflection); deterministic planner adapts duration + drops optional beats on scarce footage; LLM-refined planner can implement the same schema later |
| Candidate selector | **implemented** | hard constraints first (unusable footage, range reuse, missing assets), then weighted ranking (semantic/technical/motion-fit/emotion/variety/audio/uniqueness); every candidate's scores + selection reason stored — fully auditable |
| Timeline op engine | **implemented** | constrained op set (insert/replace/move/trim/split/delete/speed/volume/title/caption/duck); AI never touches FFmpeg; versioned, attributable (user/editor_agent/critic/revision_agent/system_rule); protected ranges enforced (ops may not target protected clips) |
| Multi-clip renderer | **implemented** | compiles validated timeline JSON → deterministic FFmpeg graph: N clips, speed (atempo chains), per-clip volume, title card, caption overlays, looped+ducked music, fades, loudness normalization, preview (360p) + final (1080p) profiles |
| Mechanical validator | **implemented** | broken/beyond-source ranges, missing media, unusable-footage overlap (from catalog), duplicates, duration vs target, caption overruns, empty ending, render drift |
| Critic | **implemented** (provider) | Gemini watches the PREVIEW against the brief; fixed question set + timestamped revisionRequests, schema-validated. Live-verified: correctly identified synthetic test-pattern footage as missing the brief. Known quirk: overallScore can contradict the boolean answers — treat requests, not the score, as the signal |
| Revision agent | **implemented** | converts critic requests into constrained ops using the selector's ranked alternatives; touches only named ranges; loop capped (default 2 passes, env `AUTOEDIT_MAX_REVISIONS`) |
| Conversational editing | **implemented** (provider) | NL → intent/scope/ops via structured-output LLM → deterministic validation → proposed timeline; protected ranges honored; nothing mutates without validation (mock-provider tested; Gemini provider wired) |
| Preference history | **implemented (v1 rules)** | `user_corrections` + `edit_runs` tables (migration 0004); local JSONL store for dev; conservative rules-based weight adjustment (±0.05 max); NO fine-tuning by design until enough approved projects exist |
| Autoedit orchestrator | **implemented** | `python -m app.pipeline.autoedit` — catalog → blueprint → selection → timeline v1 → preview → validate → critic → revision pass(es) → final render; every artifact written to the run dir |

**Live proof (synthetic fixture + Gemini):** plan(5 beats) → select(3 filled, 2 honestly unfilled)
→ preview v1 (real 10.8 s MP4) → validator OK → critic pass 1 (5 timestamped requests) → revision
(delete+2 trims) → preview v2 → critic pass 2 → clean stop at pass cap → **final 1920×1080 H.264/AAC**.
38/38 backend tests green.

**Honest gaps:** run on synthetic + demo footage only — the first-autonomous-edit milestone
(20+ REAL fitness clips) still requires real footage; story quality on real content is unproven;
cloud orchestration endpoint (app-triggered autoedit) not yet wired into the FastAPI app;
critic score calibration; MediaPipe pose pending a Python-3.14 wheel.

See `LICENSE-AUDIT.md` for the dependency ruling (Crayotter = reference only,
OpenChatCut AGPL = not incorporated).

## Hardening + operations layer (P1–P7, added 2026-08-01)
- **DB boundary (migration 0005):** relational-ownership TRIGGERS (not just RLS) —
  child rows must match their project's owner; render jobs must reference a
  timeline of the same project/user; analysis/segments must reference an asset of
  the same project. Proven by `scripts/test_db_integrity.py` (11 live checks,
  service-role bypass attempts rejected).
- **Project states:** draft/uploading/ready/analyzing/analysis_failed/draft_ready/
  rendering/render_failed/completed + `status_reason` + `project_status_events`
  transition log (DB trigger). `completed` only after a successful final render.
- **Provider hygiene:** Gemini Files API uploads (semantic + critic) are deleted in
  `finally`; failed deletion degrades to Google's ~48 h auto-expiry (documented);
  no separate reconciliation job yet (accepted risk at this scale).
- **Secrets:** all scripts read env only (`SECRETS_ENV_FILE` optional); startup
  config validation (`app/config.py`); frontend secret guard
  (`app/scripts/check-secrets.mjs`) blocks service-role material from src+bundle;
  gitleaks in CI.
- **Jobs (P4):** `pipeline_jobs` (kinds analysis/autoedit/revision/final_render) +
  DB-backed worker thread — survives restarts, stale-recovery, idempotent via a
  partial unique index, attempt caps, heartbeats, temp cleanup, per-stage
  telemetry. Operator-only endpoints: analyze / generate-draft / revise /
  render-final / jobs get-retry-cancel / timeline-ops / sign / coverage /
  evaluation / segment-flag.
- **Operator console (P3):** `/operator` route; access enforced server-side
  (operators table) + operator RLS read policies; every action audited
  (`operator_audit`). Covers projects, owner+brief, assets w/ private previews,
  jobs, all analysis artifacts, segment search, blueprint + per-beat candidate
  ranking with reasons, timeline comparison + constrained edits, approve-final,
  coverage checks, evaluation recording.
- **Evaluation (P5):** `draft_evaluations` — auto metrics filled by the draft job,
  manual correction/rating fields recorded by the operator. No improvement claims
  without these measurements.
- **Telemetry (P6):** `stage_metrics` with configurable `pricing.json` estimates.
- **Capture system (P7):** `CAPTURE-GUIDE.md` + rules-based coverage validator
  reporting likely-missing categories (never inventing footage).
- **Live proof:** `scripts/e2e_operator_flow.py` — 23/23 checks: upload → worker
  analysis → coverage → autoedit draft (critic) → evaluation → operator metrics →
  final render → completed-with-reason → audit trail → cost telemetry, plus
  authorization negatives.

## What is still MOCKED / PLANNED / UNFINISHED
- **AI analysis and auto-editing: not built.** The v1 renderer is title + single trim.
- v1 renderer supports exactly ONE video clip + optional ONE title clip; complex
  `timelineStart` placement, multi-clip, transitions, music: planned.
- The marketing-site pages `dashboard/new-project/upload/editor/processing.html`
  remain the old static demos — the real app lives in `app/` (deploy will move it
  to `app.stromation.com`). They should be relinked/retired at deploy time.
- Render service runs jobs in-process (FastAPI background task) — fine for
  1 user, needs a worker/queue before real concurrency.
- No upload resumability; no proxy generation; no email notifications.
- Upload limit is **50 MB/file** (Supabase free-plan global cap; raise on Pro).
- Project deletion: DB rows cascade immediately; storage objects are removed
  best-effort client-side — a failed storage delete can orphan objects (documented
  tradeoff; cleanup is manual via dashboard).

## Local development

### 0. Prereqs
node ≥ 20, Python ≥ 3.12, ffmpeg + ffprobe on PATH
(Windows: `winget install Gyan.FFmpeg` · Docker optional for the backend)

### 1. Database (already applied to the live project)
Migrations live in `supabase/migrations/`. To (re)apply:
```
python scripts/apply_migrations.py
```
Idempotent; verifies the project is "Stromation" before touching anything.

### 2. Render backend
```
cd render-backend
pip install -r requirements.txt
copy .env.example .env        # fill SUPABASE_SERVICE_ROLE_KEY (server-only!)
set SUPABASE_URL=... && set SUPABASE_SERVICE_ROLE_KEY=...   (or use a dotenv runner)
python -m uvicorn app.main:app --port 8787
```
Docker instead:
```
docker build -t stromation-render render-backend
docker run -p 8787:8787 -e SUPABASE_URL=... -e SUPABASE_SERVICE_ROLE_KEY=... stromation-render
```

### 3. Frontend app
```
cd app
npm install
npm run dev        # http://localhost:5173
```
All frontend env values (`app/.env.example`) are browser-safe: the publishable
key is constrained by RLS. **The service-role key must never appear anywhere in
`app/` — it belongs only in the render backend's server environment.**

### 4. Tests
```
cd render-backend && python -m pytest tests/ -q     # 16 tests: validation + real ffmpeg renders
python scripts/e2e_pipeline.py                      # 27 checks: full live pipeline + authorization
python scripts/e2e_pipeline.py --keep               # same, but leaves data for UI inspection
```

## Manual test checklist
- [ ] Sign up with a fresh email → confirmation behavior noted → sign in
- [ ] Refresh while signed in → session restored, no login bounce
- [ ] Visit `/project/<someone-elses-id>` → "not found or no access"
- [ ] Create project → appears on dashboard → survives refresh
- [ ] Upload a real MP4 (< 50 MB) → progress bar advances → file listed after refresh
- [ ] Upload a .txt / oversized file → clear error, nothing stored
- [ ] Edit title + trim range → Save timeline → refresh → Restore last saved works
- [ ] Render → status goes queued → processing → completed (no timer fakery)
- [ ] Preview plays the actual uploaded content with title card; Download gets an MP4
- [ ] Sign out → `/` redirects to login; deep link to a project redirects to login
- [ ] Second account cannot see the first account's project/footage/export
- [ ] Failed render (e.g. trim beyond source) shows the error and Retry works

## Deployment plan (NOT executed — requires authorization)
GitHub Pages cannot run FFmpeg, hold secrets, or execute server code, so the
authenticated product cannot stay on Pages.

| Piece | Recommendation | Why |
|---|---|---|
| Marketing site | GitHub Pages, unchanged (`stromation.com`) | works today, zero cost |
| App (`app/`) | Cloudflare Pages or Vercel at `app.stromation.com` | static SPA build, free tier, proper routing |
| Render backend | Fly.io / Railway / a small VPS (Docker image provided) | needs CPU + ffmpeg + server-only secrets; scale later with a queue + GPU boxes |
| Database/Auth/Storage | existing Supabase project | already wired; upgrade to Pro ($25/mo) to lift the 50 MB upload cap |

Rough monthly cost (assumptions: ~3 renders/user/mo, 720p–1080p, ≤50 MB uploads):
- **10 users:** Supabase Pro $25 + render host ~$5–10 + app hosting $0 ≈ **$30–35/mo**
- **100 users:** Supabase Pro + storage/egress ~$40–60 + 1–2 render workers ~$20–40 ≈ **$60–100/mo**
(Real GPU/AI analysis later is the big cost driver, not this slice.)

## Next milestone recommendation
Wire the **concierge loop** onto this pipeline: you (the operator) get an
"operator view" of a customer's uploaded footage and hand-build the timeline
JSON for them with better primitives (multi-clip, music track, captions) —
renderer grows clip-concat + audio-mix next, THEN AI analysis starts replacing
the manual timeline authoring. Sell the result while the automation matures.
