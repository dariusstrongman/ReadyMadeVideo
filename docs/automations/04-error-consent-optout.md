# W-004 Error, Consent and Opt-Out Handling

**Purpose:** the central hub every install gets FIRST: STOP/HELP keyword processing
with durable suppression, plus workflow-error triage with redacted alerts.

**Triggers:** the provider inbound-SMS webhook (keyword branch) and the n8n Error
Workflow assignment or posted error events (error branch).

**Keyword branch:** STOP/STOPALL/UNSUBSCRIBE/CANCEL/END/QUIT -> suppression row written
immediately and durably -> single opt-out acknowledgment. A suppression write failure
is a SEV-2 incident with an explicit instruction to halt outbound messaging manually.
HELP/INFO -> approved identification + contact response.

**Error branch:** classify severity (auth/credential/suppression failures -> SEV-2;
transient timeouts and rate limits -> SEV-3 retriable) -> REDACT secrets and tokens
from the message -> alert the owner. Non-idempotent side effects are never auto-retried
without a dedupe key.

**Rules:** suppression is checked by senders (W-002) before EVERY outbound message,
not only at conversation start; opt-outs are honored immediately (the federal outer
bound is 10 business days; the house standard is immediate).

**Tests:** fixtures w-004-stop.json and w-004-error.json plus HELP, duplicate-callback
and suppression-store-down cases.
