# Stromation Real Video Pipeline — v1 vertical slice

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
