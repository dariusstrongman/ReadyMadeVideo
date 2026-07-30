# Known Limitations

Honest list of what is not done, not tested, or constrained. Companion to
`docs/SECURITY-AND-LIMITATIONS.md` (security detail lives there).

## Platform constraints
1. **GitHub Pages**: no server-side redirects, custom headers, or host-level rate
   limiting. Rate limiting and validation happen at the n8n webhook layer.
2. **Static site**: shared header/footer are duplicated per page by the generator;
   editing navigation means regenerating or editing each agency page.

## Launch-state gaps (each has a documented path)
3. **`leads` table not yet created** — management token expired. Intake uses the
   subscribers fallback until `docs/leads-table.sql` is run. No data loss either way.
4. **Founder photo** is a styled placeholder; **business phone** not yet displayed
   (TODO markers in about.html and the footer).
5. **Legal pages are AI-prepared drafts** awaiting attorney review (flagged on-page).
6. **Case studies are demonstrations only** until pilot customers approve real data.
7. **Demo is an on-page simulation**, clearly labeled; no recorded video yet (video
   production paused by owner).
8. **W-002 signature verification** is a presence check in the template; full HMAC
   validation is implemented per customer at install time.
9. **Booking link** is the founder's personal Cal.com URL (single swap point).

## Untested conditions (tracked in docs/automations/TEST-PLAN.md)
10. Provider-outage and rate-limit branches of the live intake workflow are code-reviewed
    but not fault-injected against real outages.
11. Calendar race conditions (double booking) are handled by Cal.com, not tested by us.
12. Email deliverability tested to Gmail; not yet to Outlook/Yahoo corporate filters.

## Operational
13. **Texts to the legacy business number get no auto-reply** since the old SMS handler
    was retired with the consultancy stack; calls still receive the voicemail
    text-redirect. Replaced properly by a W-002-based install when the first pilot signs.
14. Old admin pages (admin-*.html) reference retired workflows; removal pending owner
    decision.
