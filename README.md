# ReadyMadeVideo

**AI-powered lead-response and business-automation systems for North Texas service companies.**
Live site: [www.readymadevideo.com](https://www.readymadevideo.com)

Every missed call answered. Every lead followed up.

## What this repository is

The public website and workflow-template library for ReadyMadeVideo:

- **Website** — static HTML/CSS/JS served by GitHub Pages (no build step). Agency pages
  (home, solutions, lead-recovery, pricing, book-a-call) plus the DIY Library
  (starter pack, pro pack, guides).
- **`automations/n8n/`** — importable n8n workflow templates (W-001..W-004) behind the
  Lead Recovery System, with fixtures and setup docs. They import inactive and ship in
  safe mode; see the folder README.
- **`docs/`** — operating documentation: website operations, n8n implementation,
  security notes and known limitations, launch checklist, SEO/analytics/redirect specs.

## What is deliberately NOT here

- Secrets. No API keys, tokens or credentials exist in this repository. Integrations
  reference n8n credential storage; `.env.example` lists variable names only.
- Internal business planning (sales plans, roadmaps, prospect research) — kept local,
  never committed.

## Editing and deploying

1. Edit HTML directly (each page is self-contained with inline styles).
2. Commit to `main`, push. GitHub Pages deploys in about a minute.
3. Verify live, then ping IndexNow after content changes.
4. Rollback: revert the commit and push. See `docs/WEBSITE-OPERATIONS.md`.

## License

Website content and brand: (c) ReadyMadeVideo. Workflow templates in `automations/n8n/`:
free to use in your own business and client work; do not resell or redistribute the
files themselves as a product (see `product-license.html`).
