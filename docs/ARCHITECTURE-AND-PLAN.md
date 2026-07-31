# Stromation: AI-First Video Production Platform
## Architecture and Implementation Plan

### 1. Current State Audit
- **Hosting:** Static HTML on GitHub Pages (`www.stromation.com`).
- **Tech Stack:** Pure HTML/CSS/JS without a build step or modern framework.
- **Integrations:** Supabase (leads table), n8n webhooks, GA4, Stripe.
- **Data Risk:** The `leads` table in Supabase contains real customer inquiries. This must not be dropped.

### 2. Migration and Backup Plan
- **Backup:** Created Git tag `pre-video-platform-overhaul` and pushed to origin. A local zip backup should also be maintained.
- **Data Migration:** Existing Supabase tables (`leads`, `projects`, `invoices`, `proposals`) are retained but will be supplemented by new video-platform-specific tables.
- **Routing Migration:** Old agency pages (e.g., `/solutions.html`, `/lead-recovery.html`) will be removed from navigation but can be retained as orphaned files temporarily or redirected to `/` to avoid 404s for indexed pages.

### 3. Proposed Information Architecture
- `/` - Homepage (Marketing)
- `/pricing` - Public Pricing (Waitlist/Beta phase)
- `/showcase` - Product Demonstration / Examples
- `/dashboard` - User Project Dashboard (Auth required)
- `/projects/new` - New Project Setup (Auth required)
- `/editor/[project-id]` - Advanced Editing Studio (Auth required)
- `/review/[project-id]` - Review and Feedback (Auth required, shareable)

### 4. Product Architecture Proposal
- **Frontend:** Given the existing GitHub Pages deployment, we will continue with a static/SPA approach but structure it cleanly. For a production app of this scale, migrating to React/Vite (or Next.js/Remix) is recommended long-term. For Phase 1 (prototype on GitHub Pages), we will use modern vanilla JS or lightweight libraries (e.g., Alpine.js/Tailwind via CDN) to maintain the existing deployment pipeline while building the SPA feel for the editor.
- **Authentication:** Supabase Auth.
- **Database:** Supabase PostgreSQL.
- **Storage:** Supabase Storage (S3-compatible) with signed URLs for direct multipart uploads.
- **Media Processing (Future):** Background workers (Python/FFmpeg/AI models) listening to Supabase webhooks or a dedicated queue (e.g., Redis/Celery).

### 5. Database Schema Proposal (Phase 1)
- `users` (managed by Supabase Auth)
- `profiles` (id, user_id, full_name, created_at)
- `projects` (id, user_id, name, status, raw_duration, target_duration, created_at)
- `media_assets` (id, project_id, filename, storage_path, type, duration, size)
- `timelines` (id, project_id, version, data_json, created_at)

### 6. Media-Processing Workflow Proposal
1. Client requests a signed upload URL from the backend.
2. Client uploads large raw video directly to Object Storage.
3. Storage trigger enqueues a `process_media` job.
4. Worker downloads media, generates a low-res proxy, and runs transcription (e.g., Whisper).
5. Worker runs visual analysis (scene detection, quality scoring).
6. Worker generates a structured `timeline` JSON based on the user's prompt.
7. Client UI polls or receives WebSocket updates and loads the proxy media and timeline JSON into the browser editor.

### 7. Functional vs Mocked Feature Matrix (Phase 1)
| Feature | Phase 1 Status |
|---|---|
| Account Creation | Functional (Supabase Auth) |
| Project Dashboard | Functional |
| Large File Upload | Functional Prototype (UI + basic storage) |
| AI Video Analysis | Mocked |
| Auto-Editing | Mocked (generates a static sample timeline) |
| Browser Editing Studio | Functional Prototype (UI, playback, basic trim) |
| Conversational Editing | Mocked (UI accepts prompt, simulates processing, updates timeline) |
| Video Export | Mocked (UI shows progress, downloads a sample file) |

### 8. Security Review
- **Storage:** RLS (Row Level Security) on Supabase Storage ensures users can only read/write their own project folders.
- **Database:** RLS on all tables (`projects`, `media_assets`, etc.) restricted by `auth.uid()`.
- **API Keys:** No secret keys in frontend code. Only `NEXT_PUBLIC_SUPABASE_URL` and `NEXT_PUBLIC_SUPABASE_ANON_KEY` (or vanilla equivalents) are exposed.

### 9. Infrastructure Cost Estimates
- **10 users:** ~$25/mo (Supabase Pro, basic storage/bandwidth).
- **100 users:** ~$150/mo (Increased storage for raw video, basic GPU worker costs).
- **1,000 users:** ~$1,500+/mo (Significant object storage, dedicated GPU instances for rendering and AI analysis).

### 10. Implementation Plan
1. **Scaffold:** Clean out old agency HTML. Create the new `index.html` (Homepage).
2. **Design System:** Implement the dark/neutral premium creative-software aesthetic.
3. **Marketing Pages:** Build Homepage, Pricing, Showcase.
4. **App Shell:** Build the authenticated layout (`/dashboard.html`).
5. **Upload Flow:** Build the project creation and drag-and-drop upload UI (`/new-project.html`).
6. **Editor Prototype:** Build the complex timeline and preview UI (`/editor.html`).
7. **Integration:** Connect Supabase Auth for login/signup.

### 11. Deployment Plan
- Commit changes to `main` branch.
- Push to GitHub.
- GitHub Pages automatically serves the new static files.
- (Long-term: Migrate repository to Vercel/Netlify for proper SPA routing and serverless functions).
