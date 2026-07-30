# Decision Log

Major decisions for the agency overhaul, with reasoning. Newest first.

## 2026-07-30 — Agency overhaul

1. **Pivot to a done-for-you agency for North Texas home-service businesses.**
   Per the internal business directive and technical specification. DIY templates
   remain as the secondary Stromation Library division and lead source.

2. **Founder identity is public.** Real name and verifiable credentials on the About
   page. Reverses the earlier anonymous-brand rule; an agency selling trust to local
   businesses requires a real, accountable human. Confirmed by the owner.

3. **Adopted the specification's light professional palette** (navy/blue/cyan on light
   surfaces) over the previous dark neon theme. The buyer is an HVAC/plumbing owner,
   not a developer; the spec explicitly warns against overly dark layouts for this
   audience. Node-flow logo retained for brand continuity.

4. **MVP ships four workflows (W-001..W-004), not fourteen.** The specification's own
   controlled-backlog rule (§5.2): estimate follow-up, review requests, reactivation,
   voice AI, customer portal and multi-tenant tooling are deferred until paying
   customers trigger them. Building speculative automation before customers is the
   failure mode the spec forbids.

5. **Lead storage with graceful fallback.** The Supabase management token on file was
   expired, so the `leads` table DDL could not run programmatically. Rather than block
   launch, the intake workflow stores to the existing `subscribers` table
   (`source='lead_audit'`) and upgrades automatically once `docs/leads-table.sql` is
   run. No lead can be lost either way.

6. **On-page simulated demo instead of a video.** Video production is paused by owner
   instruction; the specification permits a clearly-labeled prototype demonstration.
   The interactive simulation is honestly labeled and spends nothing.

7. **Booking via the founder's existing Cal.com link**, referenced in exactly one page
   (book-confirmed.html) plus the confirmation email, so it can be swapped for a
   branded link later without touching the funnel.

8. **W-002 ships in DRY_RUN safe mode.** US A2P 10DLC registration is a hard
   prerequisite for business texting; the template cannot send until a human completes
   registration and deliberately flips the flag.

9. **Old-Stromation workflow purge (owner-directed).** Thirty workflows from the
   consultancy / AI-Daily / solopreneur-studio eras were backed up and deleted.
   ATSHack and all other product families untouched. Backup:
   `.stromation-secrets/backup-old-stromation-workflows-2026-07-30.json`.

10. **Internal business documents are gitignored** (`docs-internal/`, `new direction/`).
    The repository is public; sales plans, HQ roadmap, hiring plans and prospect
    research must never be published.

11. **No URL breakage.** Every pre-existing public URL still resolves; the DIY pages
    keep their dark styling as the Library sub-brand rather than forcing a restyle of
    twenty product pages at launch.
