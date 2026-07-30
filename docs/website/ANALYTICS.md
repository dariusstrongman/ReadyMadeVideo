# Analytics Event Specification

Property: GA4 `G-B6Z6XV02RT` (gtag on every page). Privacy: no PII in event params;
analytics disclosed in privacy.html.

## Funnel events (implemented)

| Event | Fires when | Where | Funnel stage |
|---|---|---|---|
| `page_view` | automatic | all pages | Awareness |
| `cta_click` (param `label`) | any primary CTA click (hero_audit, lr_hero_audit, final_audit, ...) | index, lead-recovery | Interest |
| `demo_play` | demonstration started | lead-recovery.html | Interest |
| `form_start` | first focus into the audit form | book-a-call.html | Qualification |
| `lead_submit` | audit form successfully posted | book-a-call.html | Conversion |
| `booking_click` | Cal.com embed loaded | book-confirmed.html | Scheduling |
| `starter_pack_signup` | free pack capture succeeded | starter-pack.html | Library capture |
| `free_workflow_download` | free .json downloaded | starter-pack.html | Library engagement |
| `pro_sample_download` | pro sample downloaded | pro-pack.html | Library consideration |
| `begin_checkout` (value 47) | Stripe link clicked | pro-pack.html | Library conversion |
| `purchase` | thank-you page reached | thank-you-pro.html | Library revenue |
| `buyer_to_audit` | pack buyer clicks audit upsell | thank-you-pro.html | Cross-division |

## KPI readout (weekly, 10 minutes)
1. Sessions + source (GA4 Acquisition)
2. `demo_play` / sessions = demo rate
3. `form_start` -> `lead_submit` = form completion rate
4. `lead_submit` -> booked call (from CRM) = booking rate
5. Downstream sales stages live in the CRM (leads table), not GA.

## Rules
- One event per action (no double-fire; verified in QA).
- New events must be added to this table before shipping.
- Do not add user-identifying parameters.
