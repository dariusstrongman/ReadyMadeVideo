# Content Management

## Page inventory and ownership
- Agency pages (light theme): generated from the local page framework; edit content in
  place or regenerate. Keep the shared header/footer identical across them.
- Library pages (dark theme): templates.html, starter-pack.html, pro-pack.html,
  guides.html + guides/*.html. These retain the Library sub-brand styling.
- New guides: follow the existing guide template (BreadcrumbList JSON-LD, one h1,
  cross-links to two sibling guides, CTA appropriate to intent, added to guides.html
  and sitemap.xml).

## Editorial standards (house rules, enforced)
- Answer a real question a service-business owner actually has.
- No invented statistics; cite current sources for claims or phrase qualitatively.
- No banned words (leverage, seamless, streamline, game-changer...), no em dashes,
  no exclamation marks, sentence-case headings.
- Every demonstration labeled; nothing presented as client results without approval.
- Relevant CTA per intent: informational -> free pack/audit; buyer-intent -> pricing/audit.

## 12-week editorial plan (agency-focused, one piece per week)
| Wk | Working title | Intent |
|---|---|---|
| 1 | What happens to a home-service lead after a missed call | problem awareness |
| 2 | How to calculate what slow lead response costs your business | problem quantification |
| 3 | Missed-call text-back: implementation guide (exists — refresh for agency CTA) | how-to |
| 4 | What home-service companies should automate first | roadmap |
| 5 | n8n vs Zapier for service businesses (exists — refresh angle) | comparison |
| 6 | AI lead qualification with human handoff, explained honestly | trust/education |
| 7 | How to monitor business automations so they never fail silently | trust/education |
| 8 | Questions to ask any automation agency before you sign | sales enablement |
| 9 | Who owns your workflows? Portability and vendor lock-in | differentiation |
| 10 | Automation security basics for small businesses | trust/education |
| 11 | Texting customers legally: consent, STOP and quiet hours (attorney-reviewed) | compliance trust |
| 12 | Anatomy of our Lead Recovery pilot: what we measure and why | proof narrative |

Publish cadence over volume: one good piece beats five thin ones. No AI filler; each
piece needs original analysis or a demonstration.

## Update triggers
- Pricing changes -> pricing.html + any page quoting numbers + this repo's docs.
- New case study -> case-studies.html using the taxonomy badges only with approval.
- New workflow template -> automations/n8n/ + its doc + library mention.
