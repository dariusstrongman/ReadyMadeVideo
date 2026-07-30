# Implementation Guide (master)

How a Stromation customer install proceeds, tying the workflow templates to the
operating process. Per-workflow detail: `01-website-lead-intake.md` .. `04-error-consent-optout.md`.
Platform rules and gotchas: `../N8N-IMPLEMENTATION.md`. Safety: `AI-SAFETY.md`.

## Install sequence (per customer)
1. **Audit + system map** — confirm phone provider, website forms, calendar, CRM and
   what each supports (webhooks? API? event for missed calls?). Output: integration map
   + message templates for approval.
2. **Environment** — customer tenant config (numbers -> tenant map, business hours,
   escalation contacts); credentials created in n8n credential storage with least
   privilege; test vs production credentials separated.
3. **Import W-004 first** (error/consent hub), wire as the n8n Error Workflow, point the
   provider's inbound-SMS webhook at it. Suppression store live before anything can send.
4. **Import W-001** (adapted to their forms) and W-003 (their CRM/calendar). Test with
   fixtures; then W-002 with DRY_RUN=true.
5. **A2P 10DLC registration** (started at kickoff — approval time is outside our
   control). Only after approval: live SMS tests to team phones, then flip DRY_RUN.
6. **Acceptance testing with the customer** — full test matrix (TEST-PLAN.md), every
   template reviewed on a real phone, handoff drill with the on-call person.
7. **Activate one workflow at a time**, manual fallback standing by. Review at 24h / 7d.
8. **Handover** — customer receives workflow exports, docs, and the weekly report
   schedule. Onboarding checklist: CUSTOMER-ONBOARDING.md.

## Standards (non-negotiable, from the spec)
- Validation + correlation ID at the start of every workflow.
- Every side effect idempotent or protected by a dedupe key.
- Suppression/consent checked immediately before every outbound message.
- Bounded timeouts, retry policy and a failure branch on every provider call.
- Terminal status on every run: succeeded / partial / failed-retriable / failed-manual / suppressed.
- Human handoffs carry enough context to act without opening n8n.
