# Incident Response

## Severity model
- SEV-1: cross-tenant exposure, unauthorized messaging, widespread failure ->
  disable the affected workflow IMMEDIATELY, preserve evidence, notify owner, open a record.
- SEV-2: lead processing down or suppression failure -> acknowledge fast, stop unsafe
  actions, manual workaround (leads to a manual queue), fix.
- SEV-3: partial degradation with a safe fallback -> fix within the support window.
- SEV-4: cosmetic -> backlog.

## On alert (from W-004 or monitoring)
1. Classify severity.
2. Contain: deactivate the workflow or pause the campaign. Never delete.
3. Manual fallback: confirm leads are landing somewhere a human sees them.
4. Fix, test with safe data, reactivate one workflow at a time.
5. Record: incident ID, tenant, severity, timeline, root cause, corrective action,
   verification that suppression, credentials and data boundaries remain correct.
6. Customer notification decision: SEV-1/2 involving their data means yes; get legal
   review wherever data exposure is possible.

## Standing rules
- Failures alert Stromation, never customers first.
- No silent continuation after a critical action fails.
- Post-incident: add the failure mode to TEST-PLAN.md.
