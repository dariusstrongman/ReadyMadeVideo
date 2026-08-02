# Project One — real-footage workflow (NOT yet executed)

Preconditions: `python scripts/project_one_readiness.py` passes 17/17 · real
workout footage shot per `CAPTURE-GUIDE.md` · backend + worker running · founder
account granted the operator role.

## The workflow (execute in order, record everything)
1. Create the founder account in the app (sign up normally).
2. Create **Project One** from the dashboard.
3. Upload 20–40 real fitness clips (≤50 MB each on the current plan).
4. Verify every upload appears with correct duration after a refresh.
5. Operator console → **Run coverage analysis** (coverage check button).
6. Note missing footage categories from the report.
7. Shoot/upload supplemental footage if required categories are missing.
8. Operator console → **Analyze** (full pipeline; watch job progress + cost telemetry).
9. Review the segment catalog: search, inspect scores + semantic descriptions.
10. Flag unusable segments (`Mark unusable`) — the selector will avoid them.
11. **Generate draft** with the real creative brief + target duration/platform.
12. Record pipeline cost + duration from `stage_metrics` (labeled estimates).
13. Watch the full draft **without making any edits** first.
14. Score the first draft on the scorecard below (`first_draft_rating`).
15. Make corrections through constrained timeline ops ONLY (each is recorded);
    log counts + minutes in the evaluation form.
16. Run one **Revise** pass (critic + revision agent).
17. Compare timeline versions side-by-side in the console.
18. Approve the final timeline (`Approve → final` on the chosen version).
19. Final MP4 renders through the worker; download via signed URL.
20. Record total human correction time in the evaluation.
21. Record whether the output is publishable (scorecard).
22. Record exact weaknesses for the next iteration (evaluation notes).

## Project One scorecard (fill after step 21)
| Dimension | Score 1–10 / value |
|---|---|
| Story quality | |
| Hook quality | |
| Shot variety | |
| Pacing | |
| Continuity | |
| Music choice | |
| Music mixing | |
| Natural audio | |
| Caption accuracy | |
| Color consistency | |
| Ending / payoff | |
| Bad clip selections (count) | |
| Missing story beats (count) | |
| Manual correction minutes | |
| Total processing cost (estimate) | |
| Total processing time | |
| Publishable WITHOUT correction | yes / no |
| Publishable AFTER correction | yes / no |

Store the completed scorecard in `draft_evaluations` (ratings/corrections fields)
plus a copy of this table in the evaluation `notes`.

**Standing rule:** no claim of professional autonomous editing is made until this
scorecard — on real footage — says the output was publishable, and even then the
claim is limited to what was measured.
