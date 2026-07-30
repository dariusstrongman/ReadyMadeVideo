# W-001 Website Lead Intake

**Purpose:** receive a website qualification form, validate, dedupe, store the lead,
notify the owner, confirm to the lead. Live instance: "Stromation - Lead Audit Intake".

**Trigger:** POST webhook (JSON). **Inputs:** businessName, contactName, workEmail, phone,
industry, teamSize, monthlyLeadVolume, currentSystems, primaryProblem, smsConsent,
website (honeypot), source{landingPage, referrer}.

**Outputs:** lead row (leads table; documented fallback: subscribers/source=lead_audit),
owner notification email, confirmation email (no acceptance-of-work implication),
JSON response {ok}.

**Flow:** validate/normalize (size caps, email regex, 10+ phone digits) -> honeypot
silently accepted with no storage -> correlation ID + djb2 submission hash -> 24h
same-email dedupe (idempotent ok, no duplicate email) -> insert -> notify -> confirm.
Email nodes run onError=continue; the insert precedes them so a notification failure
cannot lose the lead.

**Config:** CONFIG block in the Process node (URLs/keys via env or n8n credentials,
never pasted for production), booking URL, business name.

**Tests (fixtures/):** w-001-valid.json, w-001-honeypot.json plus invalid-field and
duplicate cases per TEST-PLAN.md. All four classes passed on the live instance 2026-07-30.

**Failure/recovery:** storage failure surfaces in the owner notification (stored: FAILED...);
the workflow can be deactivated instantly; the form error message directs leads to email.
