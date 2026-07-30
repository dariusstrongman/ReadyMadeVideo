# Security Notes and Known Limitations

## Security posture

- **No secrets in this repository.** Credentials live in n8n credential storage and the
  local (untracked) secrets folder. `docs/leads-table.sql` and workflow templates contain
  placeholders only. Every commit is secret-scanned before push.
- **Form endpoints:** honeypot + time gate client-side; server-side validation, size caps,
  and a 24h dedupe window in the intake workflow. Generic error responses to the browser.
- **Lead data:** stored in Supabase with RLS enabled and no public policies (service role
  only). The browser uses no Supabase keys for lead submission — everything goes through
  the n8n webhook.
- **Paid product delivery:** Stripe checkout session is verified server-side before
  delivery; the download link is tokenized and forged tokens are rejected (403).
- **Messaging (future customer installs):** templates ship with DRY_RUN and suppression
  checks that fail closed; A2P 10DLC registration is documented as a prerequisite.

## Known limitations (honest list, 2026-07-30)

1. **GitHub Pages constraints:** no server-side redirects, no custom security headers, no
   server-side rate limiting on the static host. Rate limiting exists at the n8n layer.
2. **`leads` table not yet created** (Supabase management token expired). The intake
   works via the documented fallback; run `docs/leads-table.sql` to upgrade. No data loss
   either way.
3. **Case studies:** demonstrations only until pilot customers approve real data. This is
   by design, not an oversight.
4. **Demo is an on-page simulation**, clearly labeled. A recorded video walkthrough is a
   TODO once the owner records/approves one.
5. **Legal pages are AI-prepared drafts** describing actual practices; attorney review
   pending and flagged on every page.
6. **W-002 signature verification** is a presence check in the template; full HMAC
   validation is implemented per-customer at installation (documented in the template).
7. **Booking uses the founder's existing Cal.com link**; a Stromation-branded scheduling
   link can replace it in one spot (book-confirmed.html + intake confirmation email).
