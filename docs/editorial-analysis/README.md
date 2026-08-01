# Project One editorial analysis

Rigorous, evidence-based root-cause analysis of the first real-footage autonomous
edit (scored 4/10). Separates failures by cause and turns them into a prioritized
engine backlog and a Project Two capture plan.

Branch `claude/editorial-analysis` · documentation only · Project One artifacts
and pipeline code were **not** modified. Evidence read from `project-one-local/`
run2 (selection/critic/revision/timeline JSON) and frame-verified against the
original footage.

| # | Document | Answers |
|---|---|---|
| 01 | [root-cause-report](01-root-cause-report.md) | Why 4/10 — four-way failure attribution (footage / editor / critic-revision / template-ranking) + decision-by-decision ledger |
| 02 | [editorial-mistakes](02-editorial-mistakes.md) | Mistakes the SYSTEM made (engine-fixable) |
| 03 | [footage-limitations](03-footage-limitations.md) | Limits the FOOTAGE made unavoidable |
| 04 | [human-editor-comparison](04-human-editor-comparison.md) | What a strong human would do differently; estimated human ceiling |
| 05 | [overfitting-risks](05-overfitting-risks.md) | Changes that would overfit to this one project — do NOT make |
| 06 | [engine-improvements-prioritized](06-engine-improvements-prioritized.md) | Ranked fix backlog (P1–P10) by expected impact |
| 07 | [project-two-capture-plan](07-project-two-capture-plan.md) | Footage plan that tests each fix and breaks the ceiling |
| 08 | [human-ceiling-workflow](08-human-ceiling-workflow.md) | Immutable baselines, human lineage, correction ledger, scorecards, and three-way report |

## One-paragraph summary
The 4/10 is ~60% footage ceiling (no close-ups, one angle, one repeated
activity — unrecoverable by any editor) and ~40% fixable engine behavior. The
single worst engine defect: the **revision loop made the video shorter while the
critic demanded it be longer**, shipping a score-regressed version (0.4→0.3).
Ranking logic also chose an anticlimactic "walking away" peak (uniqueness penalty
demoted the real flip) and a continuity-breaking stretch in the build. The
highest-value fixes (P1–P4) are pure logic and should move comparable footage
from ~4 toward the ~5–6 human ceiling; passing 6 requires the Project Two capture
improvements, not code. Every proposed change is flagged for overfitting risk and
must be validated on Project Two's different footage before merging.
