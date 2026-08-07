"""P6: per-stage cost + timing telemetry — trustworthy version.

Telemetry must never crash a job, but failures must not vanish silently:
- record() returns True/False and logs a structured TELEMETRY-FAILURE line
- failed writes are kept in an in-memory pending queue and retried by
  reconcile_pending() (called at job end)
- jobs track expected-vs-recorded counts in artifacts.telemetry_status so
  incompleteness is VISIBLE, and cost totals are labeled estimates
- every row carries the pricing version used for the estimate

Pricing is configurable via pricing.json (PRICING_FILE env overrides); estimates
only — never used for marketing claims.
"""
from __future__ import annotations

import json
import os
import threading
import time
from contextlib import contextmanager

from ..logging_util import log_event

_PRICING = None
_pending: list[dict] = []
_pending_lock = threading.Lock()


def pricing() -> dict:
    global _PRICING
    if _PRICING is None:
        path = os.environ.get("PRICING_FILE") or os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "pricing.json")
        try:
            _PRICING = json.load(open(path, encoding="utf-8"))
        except Exception:
            _PRICING = {}
    return _PRICING


def pricing_version() -> str:
    return str(pricing().get("version", "unversioned"))


def estimate_cost(units: dict) -> float:
    p = pricing()
    cost = 0.0
    cost += units.get("whisper_minutes", 0) * p.get("whisper_per_audio_minute", 0)
    cost += units.get("gemini_video_seconds", 0) * p.get("gemini_flash_per_video_second", 0)
    cost += units.get("gemini_requests", 0) * p.get("gemini_flash_per_request", 0)
    cost += units.get("cpu_hours", 0) * p.get("compute_per_cpu_hour", 0)
    # B9: token-based lines computed from PROVIDER-REPORTED usage — the
    # per-request flat rate above is a legacy estimate for stages that do not
    # yet report tokens; token units always take precedence where recorded.
    for family in ("gemini_pro", "gemini_flash"):
        cost += units.get(f"{family}_input_tokens", 0) / 1e6 \
            * p.get(f"{family}_per_1m_input", 0)
        cost += units.get(f"{family}_output_tokens", 0) / 1e6 \
            * p.get(f"{family}_per_1m_output", 0)
        cost += units.get(f"{family}_cached_tokens", 0) / 1e6 \
            * p.get(f"{family}_per_1m_cached", 0)
    return round(cost, 5)


def _row(stage, project_id, job_id, duration_seconds, bytes_, units):
    u = dict(units or {})
    u["pricing_version"] = pricing_version()
    return {"project_id": project_id, "job_id": job_id, "stage": stage,
            "duration_seconds": duration_seconds, "bytes": bytes_,
            "units": u, "estimated_cost_usd": estimate_cost(u)}


def _write(row: dict) -> bool:
    import httpx

    from .. import supa
    r = httpx.post(f"{supa.SUPABASE_URL}/rest/v1/stage_metrics",
                   headers={"apikey": supa.SERVICE_KEY,
                            "Authorization": f"Bearer {supa.SERVICE_KEY}",
                            "Content-Type": "application/json",
                            "Prefer": "return=minimal"},
                   json=row, timeout=15)
    return r.status_code in (200, 201)


def record(stage: str, project_id: str | None = None, job_id: str | None = None,
           duration_seconds: float | None = None, bytes_: int | None = None,
           units: dict | None = None) -> bool:
    """Insert a stage_metrics row. Returns False (and queues for reconciliation)
    on failure — never raises into the job."""
    row = _row(stage, project_id, job_id, duration_seconds, bytes_, units)
    try:
        if _write(row):
            return True
        raise RuntimeError("non-2xx from stage_metrics insert")
    except Exception as e:
        log_event("TELEMETRY-FAILURE", project_id=project_id, job_id=job_id,
                  stage=stage, error=str(e)[:200])
        with _pending_lock:
            _pending.append(row)
        return False


def reconcile_pending() -> dict:
    """Retry queued failed writes. Returns {retried, recovered, still_pending}."""
    with _pending_lock:
        batch, _pending[:] = list(_pending), []
    recovered = 0
    for row in batch:
        try:
            if _write(row):
                recovered += 1
                continue
        except Exception:
            pass
        with _pending_lock:
            _pending.append(row)
    status = {"retried": len(batch), "recovered": recovered,
              "still_pending": len(batch) - recovered}
    if status["still_pending"]:
        log_event("TELEMETRY-RECONCILE-INCOMPLETE", **status)
    return status


def pending_count() -> int:
    with _pending_lock:
        return len(_pending)


@contextmanager
def timed(stage: str, project_id=None, job_id=None, units: dict | None = None):
    t0 = time.time()
    try:
        yield
    finally:
        record(stage, project_id, job_id, round(time.time() - t0, 2), units=units)
