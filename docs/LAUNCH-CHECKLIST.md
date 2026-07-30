# Launch Checklist (agency overhaul, 2026-07-30)

## Completed before deploy
- [x] Homepage states buyer, problem, outcome and primary CTA (audit) clearly
- [x] DIY Library secondary in navigation and hierarchy
- [x] All demonstrations labeled; zero fabricated client proof, staff, offices or stats
- [x] Founder information accurate (name, credentials); photo = marked placeholder
- [x] Pricing separates setup, monthly service and third-party costs
- [x] Privacy / Terms / Refund / License drafted and marked for attorney review
- [x] Audit form validates server-side and stores traceable lead records (tested: valid,
      honeypot, invalid, duplicate)
- [x] Confirmation email sent without implying acceptance of work
- [x] W-001..W-004 templates import clean and inactive; fixtures included
- [x] No credentials in code, workflow JSON, docs or Git (scanned)
- [x] Duplicate protection, suppression design and human handoff documented
- [x] Sitemap, robots, canonicals, OG, JSON-LD validated
- [x] Mobile + desktop rendering reviewed (homepage, lead-recovery, form)
- [x] Money paths regression-tested (Pro Pack checkout, gated download 403 on forgery,
      Starter capture ok:true)
- [x] Rollback documented (tag pre-agency-overhaul + backup zip)

## Owner actions after launch
- [ ] Run `docs/leads-table.sql` in the Supabase SQL editor (2 min)
- [ ] Confirm/replace business phone; add to footer
- [ ] Supply founder photo for about.html
- [ ] Attorney review of the four legal pages
- [ ] Google Search Console: submit updated sitemap
- [ ] Begin the 90-day plan (docs-internal/90-DAY-SALES-PLAN.md)
