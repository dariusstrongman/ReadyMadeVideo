# Test Plan

Evidence or it did not pass. Record results per install and per release.

## Website matrix
Build renders / responsive (390, 768, 1440) / keyboard + focus / contrast / reduced
motion / forms (valid, invalid, duplicate, spam, provider outage, confirmation) /
links + downloads + checkout / metadata + sitemap + structured data / analytics events
fire exactly once / 404 page.

## Workflow matrix (per workflow, safe data only)
- Happy path: a valid event produces the expected outputs
- Validation: missing or malformed fields, oversized payload, wrong content type
- Replay/duplicate: repeated webhook, provider retry, double submit
- Consent: suppressed contact, STOP, HELP, missing consent source
- Provider failure: timeout, rate limit, expired credential, partial success
- AI failure: timeout, malformed JSON, low confidence, injection-shaped input
- Concurrency: simultaneous events for the same contact
- Recovery: safe retry, manual task creation, alert delivery, rollback to prior version

## Status 2026-07-30 (live intake workflow)
PASSED: valid / honeypot / invalid / duplicate (production endpoint, safe data).
NOT YET FAULT-INJECTED: provider outage and rate limit (code-reviewed only). See
KNOWN-LIMITATIONS.md.

## Rules
- Test webhook URLs and sandbox credentials for everything pre-activation.
- No real customer numbers in tests; team phones only, and only after A2P approval.
- A test without recorded evidence (execution ID or screenshot) does not count.
