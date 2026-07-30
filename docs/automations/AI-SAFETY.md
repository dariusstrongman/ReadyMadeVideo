# AI Safety Rules

Binding rules for any AI step inside Stromation-built workflows.

## Authority boundaries

| AI may | AI may not |
|---|---|
| Classify intent and urgency | Make or imply binding price quotes |
| Extract structured contact/service details | Give emergency or safety instructions |
| Summarize a conversation for a human | Commit to schedules not confirmed by the calendar |
| Select among pre-approved response templates | Invent refund, warranty or legal terms |
| Recommend human escalation | Delete data or disable systems autonomously |
| Draft internal notes | Send unlimited follow-ups outside deterministic policy |

## Required controls on every AI step
1. **Structured output** against the schema in `automations/n8n/README.md` context
   (intent / serviceCategory / location / urgency / summary / confidence / requiresHuman
   / reasonCodes). Schema validation happens BEFORE the output influences routing.
2. **Deterministic safety override**: `reasonCodes` containing `SAFETY` (or urgency
   `emergency_or_safety`) routes to a human regardless of confidence.
3. **Confidence threshold** (default 0.7) with human fallback below it.
4. **Fallback response** if the model times out or returns malformed JSON: route to
   human task, never retry into a customer-visible loop.
5. **Prompt-injection posture**: customer text is data, not instructions. Extraction
   prompts wrap untrusted input in delimiters; the model has no tool authority; nothing
   a caller writes can trigger system actions directly.
6. **Input limits**: inbound text truncated to sane lengths before the model sees it.
7. **Logging**: decisions logged with correlation IDs; raw customer text is available to
   the human handler but not copied into alerts or third-party logs. No secrets in prompts.
8. **First-touch messages are templates**, not generations. AI drafts internal material
   and structured data; approved templates talk to customers.

## Review cadence
Any new AI step requires: purpose, schema, threshold, fallback, escalation path and a
test case for malformed output — reviewed before activation (see TEST-PLAN.md).
