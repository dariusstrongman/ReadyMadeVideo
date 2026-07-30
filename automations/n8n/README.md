# Stromation n8n Workflow Templates

Production-oriented workflow templates behind the Stromation Lead Recovery System.
Each imports into n8n as an **inactive** workflow. Nothing sends or writes until you
configure it and deliberately activate it.

| File | Purpose | Ships safe because |
|---|---|---|
| `W-001-website-lead-intake.json` | Website qualification form → validate → dedupe → store lead → notify owner → confirm to lead | Honeypot, validation, 24h dedupe, idempotent duplicates; notification failure cannot lose the stored lead |
| `W-002-missed-call-response.json` | Verified missed-call event → consent-aware branded text-back | `DRY_RUN=true` by default; suppression check fails closed; quiet hours; idempotency vs provider retries; approved template only |
| `W-003-crm-booking-handoff.json` | Structured lead object → clean CRM record → booking link or human task | Schema validation before routing; safety codes override confidence; search-before-create; CRM failure falls back to a human task |
| `W-004-error-consent-optout.json` | STOP/HELP keyword processing + central workflow-error triage | STOP is durable and immediate; suppression failure raises a SEV-2 incident; secrets redacted from alerts |

## Import

1. n8n → Workflows → Import from File → pick the JSON.
2. Read the sticky note in the canvas; edit the `CONFIG` block at the top of the main Code node.
3. Attach your own credentials (SMTP, provider) where nodes reference `REPLACE_WITH_...`.
4. Test with the payloads in `fixtures/` against the **test** webhook URL.
5. Activate only after tests pass.

## Non-negotiables for production

- **No credentials in Code nodes or exported JSON.** Use n8n credentials or environment
  variables. The `CONFIG` placeholders exist so the template is readable, not as an
  invitation to paste keys there.
- **US business texting requires A2P 10DLC registration** (brand + campaign) with your
  provider before W-002 may send a single message. Keep `DRY_RUN=true` until approved.
- **Suppression checks run before every send**, not once per conversation. If the
  suppression store is unreachable, the workflow does not send.
- **Humans keep authority.** These workflows collect, route and report. Pricing,
  commitments, emergencies and anything sensitive go to a person.

## Fixtures

`fixtures/` contains safe sample payloads for every test class: valid, honeypot,
missed vs answered call, structured lead, safety override, STOP keyword, error event.
POST them to the workflow's test URL with `Content-Type: application/json`.

## Support

These templates are maintained by Stromation (stromation.com). Done-for-you
installation, testing, documentation and monitoring is what our agency service does:
https://www.stromation.com/lead-recovery.html
