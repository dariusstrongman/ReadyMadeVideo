"""B10: configurable AI spend controls — no unlimited-by-default mode.

One cost model, shared with telemetry (``telemetry.estimate_cost`` reads
pricing.json), so the budget guard and the recorded estimates can never
disagree. Costs computed here are ESTIMATES from provider-reported token
counts; billing-level actuals are unknowable at request time and are kept
as a separate (null) field by callers.

Env:
  AI_BUDGET_PER_JOB_USD      default 2.50 — hard ceiling for one job's calls
  AI_BUDGET_PER_PROJECT_USD  default 10.00 — ceiling across a project's jobs
  Value "off"/"unlimited" disables a limit EXPLICITLY (logged loudly);
  garbage or non-positive values fall back to the default — a config typo
  must never mean unlimited spend.
"""
from __future__ import annotations

import math
import os

from ..logging_util import log_event
from . import telemetry

DEFAULT_JOB_LIMIT_USD = 2.50
DEFAULT_PROJECT_LIMIT_USD = 10.00
# planner output is large (full structured plan); input estimated at ~4
# chars/token. Deliberately conservative so the guard errs on stopping.
EST_CHARS_PER_TOKEN = 4.0
EST_OUTPUT_TOKENS_PER_CALL = 12_000


class BudgetExceeded(RuntimeError):
    """Raised BEFORE a paid call that would break the configured budget."""

    def __init__(self, scope: str, spent: float, limit: float,
                 next_est: float):
        self.scope, self.spent, self.limit = scope, spent, limit
        self.next_est = next_est
        super().__init__(
            f"budget_exceeded: {scope} AI budget "
            f"(spent ${spent:.2f} + next call ~${next_est:.2f} > "
            f"${limit:.2f}) — completed analysis and artifacts are "
            "preserved; raise the budget env to retry")


def _limit(env: str, default: float) -> float:
    raw = (os.environ.get(env) or "").strip().lower()
    if raw in ("off", "unlimited"):
        log_event("AI-BUDGET-DISABLED", env=env)
        return math.inf
    try:
        val = float(raw)
        if val > 0:
            return val
    except ValueError:
        pass
    if raw:
        log_event("AI-BUDGET-BAD-VALUE", env=env, value=raw[:40],
                  fallback=default)
    return default


def units_for(records: list[dict]) -> dict:
    """Aggregate usage records into telemetry unit names (model family by
    name: anything with 'pro' bills at Pro rates, else Flash rates)."""
    units: dict[str, float] = {}
    for r in records:
        family = "gemini_pro" if "pro" in str(r.get("model", "")).lower() \
            else "gemini_flash"
        for src, dst in (("inputTokens", "input"), ("outputTokens", "output"),
                         ("cachedTokens", "cached")):
            key = f"{family}_{dst}_tokens"
            units[key] = units.get(key, 0) + int(r.get(src) or 0)
    return units


def cost_of(records: list[dict]) -> float:
    return telemetry.estimate_cost(units_for(records))


def estimate_call_cost(parts: list[dict], model: str) -> float:
    chars = sum(len(p.get("text", "")) for p in parts if isinstance(p, dict))
    est = [{"model": model,
            "inputTokens": int(chars / EST_CHARS_PER_TOKEN),
            "outputTokens": EST_OUTPUT_TOKENS_PER_CALL}]
    return cost_of(est)


class AiBudget:
    """Per-job spend tracker with job + project ceilings."""

    def __init__(self, project_spent_usd: float = 0.0):
        self.job_limit = _limit("AI_BUDGET_PER_JOB_USD",
                                DEFAULT_JOB_LIMIT_USD)
        self.project_limit = _limit("AI_BUDGET_PER_PROJECT_USD",
                                    DEFAULT_PROJECT_LIMIT_USD)
        self.project_spent = float(project_spent_usd)
        self.job_spent = 0.0

    def precheck(self, next_call_est_usd: float) -> None:
        if self.job_spent + next_call_est_usd > self.job_limit:
            raise BudgetExceeded("per-job", self.job_spent, self.job_limit,
                                 next_call_est_usd)
        if self.project_spent + self.job_spent + next_call_est_usd \
                > self.project_limit:
            raise BudgetExceeded("per-project",
                                 self.project_spent + self.job_spent,
                                 self.project_limit, next_call_est_usd)

    def add(self, records: list[dict]) -> None:
        self.job_spent += cost_of(records)
