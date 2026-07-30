# Website Operations

Static HTML/CSS/JS on GitHub Pages (repo `dariusstrongman/Stromation`, branch `main`,
domain www.stromation.com via CNAME). No build step: edit HTML, commit, push, live in ~1 min.

## Page map (agency IA, 2026-07-30)

| Page | Role |
|---|---|
| index.html | Agency homepage. Primary CTA: Book a 20-Minute Lead Leak Audit |
| solutions.html / lead-recovery.html | Service pages (lead-recovery = flagship, has interactive demo) |
| how-it-works.html / case-studies.html / pricing.html / about.html | Trust + qualification path |
| book-a-call.html → book-confirmed.html | Qualification form → POST `/webhook/lead-audit` → Cal.com embed |
| library.html → templates/starter-pack/pro-pack/guides | DIY Library (secondary division, dark theme retained) |
| privacy/terms/refund-policy/product-license | Legal drafts, marked for attorney review |

## Shared design system

New agency pages are generated from `scratchpad` builders (agency_framework.py) with one
token set: navy `#0B132B`, blue `#1C5D99`, cyan `#16C2D5`, amber `#F59E0B`, surface `#F4F7FB`.
To change header/footer/nav on agency pages consistently, edit the framework and regenerate,
or replicate the change across the pages listed above (they share identical header/footer markup).

## Forms

- **Audit form** (book-a-call.html): client-side validation is a usability aid only; the
  n8n workflow re-validates server-side. Honeypot field `website` + 3s time gate.
- **Starter Pack capture** (starter-pack.html#get): posts to `/webhook/ai-daily-subscribe`
  with `page:'studio'`; welcome email delivers the pack + a real workflow file.

## Analytics (GA4 G-B6Z6XV02RT)

Funnel events: `cta_click` (labeled), `demo_play`, `form_start`, `lead_submit`,
`booking_click`, `starter_pack_signup`, `begin_checkout`, `purchase`.

## Deploy / rollback

- Deploy: commit to `main`, push. Verify live with `curl -I`. Ping IndexNow after content changes.
- Rollback: `git revert` the commit (or reset to tag `pre-agency-overhaul` for full rollback)
  and push. Backup zip of the pre-overhaul site: `packs/backup-pre-agency/` (local).

## TODO (owner)

- Business phone on site (footer TODO comment) once confirmed.
- Founder photo on about.html (placeholder block marked TODO).
- Run `docs/leads-table.sql` in Supabase SQL editor to upgrade lead storage from the
  subscribers fallback to the dedicated `leads` table.
- Attorney review of the four legal pages.
