# SEO Specification

## Current implementation (verified 2026-07-30)
- Unique `<title>` + meta description + self-referencing canonical on every indexable page.
- Open Graph + Twitter card on all pages; shared `og.png`.
- `sitemap.xml`: canonical, indexable URLs only (agency pages, Library pages, 15 guides,
  legal). `robots.txt` disallows `/admin-`, `/thank-you-pro.html`, `/book-confirmed.html`.
- JSON-LD: `ProfessionalService` on the homepage with REAL data only (name, url, email,
  founder, areaServed = DFW cities served, knowsAbout). BreadcrumbList on guides.
- **Forbidden by policy:** fake ratings/reviews in structured data, structured data for
  services or locations not actually offered, doorway/city-spam pages.

## Target queries (agency)
Primary: missed call text back [DFW/city], AI receptionist for HVAC/plumbers,
lead response automation home services, automation agency North Texas, n8n consultant DFW.
Content answers real questions (see CONTENT-MANAGEMENT.md editorial plan); no keyword stuffing.

## Local SEO plan (legitimate only)
1. Google Business Profile for Stromation (owner action; service-area business, no fake
   storefront address).
2. Consistent NAP (name + phone) once the business phone is confirmed.
3. City relevance comes from genuinely serving the area and saying so on real pages,
   not from generated city-page grids (explicitly banned by the spec).

## Checklist after any content change
- [ ] Title <= ~60 chars, description <= ~160, both unique
- [ ] Canonical correct; page added to sitemap if indexable
- [ ] JSON-LD (if any) validates and matches visible content
- [ ] IndexNow ping; GSC sitemap current
