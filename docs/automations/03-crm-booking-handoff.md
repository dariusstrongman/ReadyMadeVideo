# W-003 CRM and Booking Handoff

**Purpose:** validated structured-lead object -> clean CRM record -> booking link or
human task. The CRM remains the system of record; the AI summary is a convenience,
never the legal record.

**Trigger:** POST webhook with the structured AI output schema (intent, serviceCategory,
location, urgency, preferredTime, summary, confidence, requiresHuman, reasonCodes,
plus contact fields).

**Flow:** schema validation BEFORE routing -> deterministic safety override (a SAFETY
reason code or emergency urgency goes to a human regardless of confidence) ->
confidence threshold (default 0.7) -> normalize identifiers -> SEARCH BEFORE CREATE
(phone + email); an ambiguous multi-match goes to a human -> create contact only if
none found -> opportunity with summary + correlation -> booking route only for
schedule-intent, non-urgent leads -> team notification with full context.

**Fail-safe:** any CRM or API failure routes to a human task with the error attached;
low-confidence extraction never overwrites authoritative CRM fields.

**Tests:** fixtures w-003-lead.json (happy path) and w-003-safety-override.json
(must route human) plus ambiguous-match, CRM-timeout and low-confidence cases.
