# n8n Implementation Guide

## Live workflows (Stromation's own funnel)

| Workflow | ID | Purpose |
|---|---|---|
| Stromation - Lead Audit Intake | dd9VDsV151Lxb5JG | `/webhook/lead-audit`: validate → dedupe → store lead → notify owner → confirm to lead |
| AI Daily - Subscribe | taFNK0KHtnudeeTy | Starter Pack capture (`page:'studio'` branch delivers the pack) |
| Pro Pack - Delivery & Sale Log / Gated Download / Resend | 0HZAFtdXdAs5wo78 / adnjwZyt8DPcBDEt / qEEYFUmD8Z8oGxmi | $47 product delivery (Stripe-verified, tokenized download) |

Lead storage: `leads` table on the main Supabase project (run `docs/leads-table.sql` once).
Until that table exists the intake gracefully falls back to `subscribers` rows with
`source='lead_audit'` and the full payload in `notes`; it upgrades automatically after.

## Customer-facing templates

See `automations/n8n/README.md` for W-001..W-004: import instructions, safe-mode defaults,
fixtures and the production non-negotiables (A2P 10DLC before any SMS, suppression checks
before every send, credentials only in n8n credential storage).

## House rules (hard-won)

- `this.helpers.httpRequest()` — never fetch()/require(); no `Buffer.from()` in Code nodes.
- emailSend v2.1: use `text`/`html` (never `message`); `appendAttribution:false`;
  fromEmail must match the SMTP credential user.
- API-created webhook nodes need a `webhookId` set or they 404.
- n8n API PUT updates a draft: publish by deactivate → activate.
- Code nodes allow top-level await — validate their JS with an async wrapper before `node --check`.
- Test with safe data and the test webhook URL before activating anything.
