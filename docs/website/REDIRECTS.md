# Redirects and Index Cleanup

## Principle
GitHub Pages cannot issue server-side 301s. Strategy: never break a URL. Every
previously-indexed public URL still resolves at its original path with current content.

## URL disposition map (agency overhaul, 2026-07-30)

| URL | Disposition |
|---|---|
| / | Repurposed in place: agency homepage (was solopreneur studio; before that AI Daily) |
| /about.html, /privacy.html, /terms.html | Rewritten in place |
| /templates.html, /starter-pack.html, /pro-pack.html, /guides.html, /guides/* | Kept as-is (Library division), nav retargeted |
| /thank-you-pro.html | Kept (noindex); upsell now points to the audit |
| /solutions, /lead-recovery, /how-it-works, /case-studies, /pricing, /library, /book-a-call, /book-confirmed, /refund-policy, /product-license | New pages |
| /admin-*.html | noindex + robots-disallowed internal tools (candidates for deletion — owner decision pending) |
| /404.html | Custom 404 (GitHub Pages serves it automatically for unknown paths) |

Historical URLs from earlier eras (blog/*, ai-tools.html, audit.html, services.html, etc.)
were removed in prior overhauls before this one; unknown paths fall through to 404.html.
No redirect chains exist.

## Indexing checklist (post-launch)
- [x] sitemap.xml regenerated (agency + Library + guides + legal)
- [x] robots.txt: admin-, thank-you-pro, book-confirmed disallowed
- [x] Canonicals self-referencing on all indexable pages
- [x] IndexNow pinged with the new URL set
- [ ] Owner: Google Search Console -> resubmit sitemap, then review Coverage report
      after ~1 week; use URL removal only for anything sensitive (none known)
