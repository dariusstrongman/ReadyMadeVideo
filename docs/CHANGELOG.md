# Stromation Changelog

## 2026-07-30 — Agency Overhaul (new direction)

Baseline before overhaul: commit `d9a1e8e` (tag `pre-agency-overhaul`, backup zip in local packs/backup-pre-agency/).

Transforming Stromation from a DIY-template site for solopreneurs into a done-for-you AI automation
agency for North Texas home-service businesses, per the internal Technical Design and Operating
Specification v1.0 (2026-07-30). MVP scope only.

### Changed
- New agency positioning sitewide: "Every missed call answered. Every lead followed up."
- New light professional design system (navy/blue/cyan on light surfaces), node-flow logo retained.
- `index.html` rewritten as the agency homepage (audit CTA primary).
- `about.html` rewritten with real founder background.
- `privacy.html` / `terms.html` updated for agency services (marked for attorney review).
- Existing DIY pages (templates, packs, guides) retained at their URLs as the secondary Stromation Library.

### Added
- `solutions.html`, `lead-recovery.html`, `how-it-works.html`, `case-studies.html`, `pricing.html`,
  `library.html`, `book-a-call.html`, `book-confirmed.html`, `refund-policy.html`, `product-license.html`.
- `automations/n8n/` — W-001..W-004 importable workflow templates + fixtures + README.
- Supabase `leads` table + live n8n Lead Audit Intake workflow (webhook `/webhook/lead-audit`).
- Public ops docs in `docs/`.

### Preserved
- Pro Pack checkout + gated delivery, Starter Pack capture, all guide URLs, admin tools.
