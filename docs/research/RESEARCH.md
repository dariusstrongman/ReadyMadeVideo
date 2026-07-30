# RESEARCH.md — Market, Competitor, Cost and Compliance Research

All sources retrieved **2026-07-30**. Labels: **[VERIFIED]** = confirmed on a primary or
official page; **[SECONDARY]** = third-party only; **[INFERENCE]** = our conclusion from
verified facts; **[FOLKLORE]** = untraceable, banned from all Stromation copy.
Regulatory items are research, not legal advice; anything marked **[ATTORNEY REVIEW]**
requires counsel before appearing in customer-facing claims or contracts.

---

## 1. Missed-call and lead-response facts (what we may honestly cite)

**Safe to use, with attribution:**
- Only **52% of callers to home-services businesses reach a live person**; 45% of
  qualified leads convert on the call; 55% of businesses never ask the caller to book.
  [VERIFIED] Invoca Home Services Lead Conversion Benchmarks Report (July 2026, 70M+ calls).
  https://www.invoca.com/reports/the-invoca-home-services-lead-conversion-benchmarks-report-2026
- **27% of home-services calls go unanswered; under 3% of callers routed to voicemail
  leave a message.** [VERIFIED] Invoca platform data (cite as platform data, not "a study").
  https://www.invoca.com/blog/how-much-missed-sales-calls-cost-home-services-businesses
- **Speed to lead:** contacting a web lead within an hour makes a firm ~7x as likely to
  qualify it vs one hour later (60x vs 24 hours); average company response was 42 hours;
  23% never responded. [VERIFIED] Harvard Business Review, Oldroyd et al., 2011 (B2B/B2C
  web leads; "qualify" is not "close"). https://hbr.org/2011/03/the-short-life-of-online-sales-leads
- Calling a lead within 5 minutes vs 30: contact odds ~100x, qualification odds ~21x.
  [VERIFIED] Lead Response Management study (2007, vendor dataset; never call it "Harvard").
  https://www.leadresponsemanagement.org/lrm_study/
- Trades book only **42% of inbound calls** into jobs (HVAC 38%, plumbing 43%; shops
  under 5 techs: 24%). [VERIFIED] ServiceTitan data report, 3,000+ businesses.
  https://www.servicetitan.com/blog/data-call-booking-rates

**Banned from copy [FOLKLORE]:** "85% of callers never call back", "80% will not leave a
voicemail", "$1,200 lost per missed call" — circular attribution, no traceable origin.
This niche is saturated with AI-generated stat laundering; anything found only on
content-farm aggregators is presumed false.

## 2. Competitors

**AI receptionist platforms (self-serve software):** Sameday AI from $449/mo [VERIFIED];
Goodcall $79-249/agent/mo [VERIFIED]; Smith.ai $300-2,100/mo (human+AI) [VERIFIED];
Ruby $250-1,725/mo [VERIFIED]; Avoca (upmarket, ~$1-3k/mo, $1B valuation 2026)
[SECONDARY]; budget crop at $30-199/mo (Rosie, Solvea, NextPhone) [SECONDARY].

**Texting/lead-response platforms:** Podium (~$399-599/mo, annual contract) [SECONDARY];
Chiirp and LeadTruffle (home-services missed-call specialists, call-gated pricing)
[VERIFIED gated]; Emitrr ~$45/mo [SECONDARY].

**Agency/GHL resellers:** the taught model is $297-997/mo, but actually transacted
missed-call-text-back-only offers were found at **$59-125/mo**, and Fiverr sells the
build for $20. [SECONDARY] Standalone MCTB is being commoditized hard.

**DFW-local:** no verified DFW-headquartered agency with published missed-call-recovery
pricing found; competition is national SEO players. [INFERENCE] The local done-for-you
niche is open, and local trust is our differentiation.

## 3. Adjacent platforms — the strategic finding

**Missed-call text-back is a native feature of the major field-service platforms:**
- **Jobber** ($49-699/mo plans; ~100k customers): AI Receptionist add-on listed at
  **$29/mo** (30 conversations), free on Plus. Open GraphQL API on any plan. [VERIFIED]
- **Housecall Pro** ($59-299/mo): missed-call auto-text is a toggle in the Voice add-on;
  API is gated to the $299/mo Max plan. [VERIFIED]
- **Workiz**: phone system is the core product; AI answering priced separately. [VERIFIED]
- **ServiceTitan**: upmarket (25+ techs), rarely relevant to the 2-25 segment. [VERIFIED]
- **ServiceM8** ($0-349/mo): native n8n integration from the $29 plan — cheapest
  agency-friendly target. [VERIFIED]

**Consequences [INFERENCE]:**
1. Never pitch "we add text-back" to a shop already on Jobber/HCP VoIP — that is a
   $29 checkbox for them. Sell the full recovery loop (qualification, booking, follow-up,
   reporting, accountability) or serve shops NOT on those platforms.
2. The natural Stromation customer keeps a long-held number on a personal cell or legacy
   carrier — platform-native text-back cannot help them.
3. Integration priority: Jobber and ServiceM8 first (open APIs), HCP only for Max-plan
   customers.

## 4. Pricing sanity check

Anchors: GHL platform $97-497/mo [VERIFIED]; software-only AI answering $79-789/mo
[VERIFIED]; n8n productized builds $5,000 fixed (Goodspeed) [VERIFIED], aggregated
$3k-10k setup + $500-1,500/mo [SECONDARY]; SMB agency retainers $1,000-3,000/mo
[SECONDARY].

**Verdict [INFERENCE]:** Pilot ($1,000 + $500/mo) is an aggressive foot-in-the-door;
Standard ($2,500 + $750/mo) sits at the LOW end of professional build pricing, with
room toward $3.5k-5k setup once case studies exist. The pricing risk is not being too
high; it is being compared to $29 add-ons — so the offer is framed as done-for-you
outcome plus an accountable human, never as software.

## 5. Third-party costs to disclose (single location)

- **Twilio**: SMS $0.0083/segment + ~$0.004 carrier pass-through; local number $1.15/mo;
  A2P vetting $15 one-time; low-volume brand ~$4-48 one-time; campaign $1.50-10/mo.
  [VERIFIED unit prices; re-check the fee article at install time]
- **n8n**: Cloud Starter ~EUR 20/mo, or self-hosted free + ~$5-12/mo VPS.
- **OpenAI**: mini-model tiers put ~200 AI conversations at **under $1/month**.
- **Estimated monthly pass-through at ~100 missed calls/mo: roughly $15-45/month**,
  plus ~$20 one-time carrier registration. [ESTIMATE from verified unit prices]
This matches the pricing page's "modest for a single location" language.

## 6. Messaging compliance (summary; full detail below) [ATTORNEY REVIEW]

- **A2P 10DLC** registration is mandatory carrier policy for business SMS on local
  numbers: brand + campaign via The Campaign Registry; campaign vetting currently
  running ~10-15 days; fees ~$4-48 brand one-time, $15 campaign vetting, $1.50-10/mo.
  Best fit for missed-call text-back: Low-Volume Standard brand + Low-Volume Mixed
  (or Customer Care) campaign. Each client is registered as its OWN brand. [VERIFIED
  Twilio docs]
- **Consent:** a missed call is NOT automatically consent to text. The defensible shape
  is a single, immediate, non-marketing reply referencing their call, with business name
  and STOP language. FCC "closely related purpose" doctrine supports it but no ruling
  squarely blesses missed-call auto-texts, and McLaughlin v. McKesson (2025) means courts
  no longer defer to FCC interpretations. One message; no drip sequences without separate
  written opt-in; zero promotional content. [ATTORNEY REVIEW]
- **Opt-out:** FCC revocation rule (effective 2025): STOP/QUIT/END/REVOKE/CANCEL/
  UNSUBSCRIBE are per-se revocations, honored within 10 business days (house standard:
  immediate). HELP must return business name + contact. First message includes opt-out
  language. [VERIFIED]
- **Quiet hours:** federal solicitation window 8am-9pm recipient-local; **Texas SB 140
  (effective Sept 2025)** is stricter for solicitation texts: 9am-9pm Mon-Sat,
  noon-9pm Sunday, with a DTPA private right of action. Our auto-texts stay
  non-promotional AND gated to 9am-9pm anyway. Whether reply-only senders need Texas
  telephone-solicitor registration ($200 + $10,000 bond) is a per-client counsel
  question. [ATTORNEY REVIEW]
- **Risk profile:** realistic exposure comes from (a) promo content in auto-texts,
  (b) texting after STOP, (c) drip sequences, (d) no quiet-hours gate, (e) unregistered
  10DLC traffic being carrier-blocked. Product rules in W-002/W-004 exist precisely to
  prevent all five. Install contracts must place consent/compliance responsibility with
  the client and disclaim legal advice. [ATTORNEY REVIEW]

Key compliance sources: Twilio Messaging Policy + A2P 10DLC docs (twilio.com),
CTIA Messaging Principles (2023/2025), FCC revocation rule analyses (BCLP, Wiley),
Butera v. Sugarhouse (D. Utah 2025), Steidinger v. Blackstone (7th Cir. 2026, circuit
split on DNC texts), Texas SB 140 analyses (Kelley Drye, GK Law). Full URLs preserved
in the research transcripts.

## 7. What this changes in the product/site
1. Site copy may cite Invoca 52% / ServiceTitan 42% / HBR speed-to-lead WITH attribution;
   all folklore stats stay banned.
2. Sales positioning: full recovery loop + local accountable human, never bare text-back.
3. Qualify prospects for platform overlap early (Jobber/HCP users get a different pitch).
4. W-002 stays one-message, non-promotional, quiet-hours-gated, per-client A2P.
5. Budget 2-3 weeks for campaign approval in every install timeline (already in docs).
