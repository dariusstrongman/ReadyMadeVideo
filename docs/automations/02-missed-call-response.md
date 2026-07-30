# W-002 Missed-Call Lead Response

**Purpose:** verified missed-call event -> consent-aware, branded text-back with human
escalation. SHIPS IN SAFE MODE (DRY_RUN=true): cannot send until A2P registration is
approved, a provider credential is wired, and the flag is deliberately flipped.

**Trigger:** POST webhook from telephony provider (Twilio-shaped: CallSid, From, To,
CallStatus). **Outputs:** outbound SMS request (via the provider node you wire),
event log, human alert on escalation keywords or failures.

**Flow:** signature presence check (implement full HMAC per provider at install) ->
tenant map by called number -> only no-answer/busy/failed/missed qualify -> idempotency
key from the provider event ID (workflow static data, 48h prune) -> suppression check
that FAILS CLOSED (store unreachable = no send) -> quiet-hours gate -> approved
template only (AI does not write first-touch messages) with STOP language appended.

**Compliance (see docs/research/RESEARCH.md):** one message per missed call; zero
marketing content in the auto-text; business name + STOP in the message; 9am-9pm
recipient-local send window; A2P 10DLC brand + campaign registered per client BEFORE
launch; opt-outs propagate via W-004.

**Tests:** fixtures w-002-missed-call.json (prepares a send) and
w-002-answered-call.json (must NOT trigger) plus duplicate, suppressed and
quiet-hour cases.
