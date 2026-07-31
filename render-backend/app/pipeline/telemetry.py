"""P6: per-stage cost + timing telemetry. Estimates only, pricing configurable
via pricing.json (PRICING_FILE env overrides). Never used for marketing claims."""
from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager

_PRICING = None


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


def estimate_cost(units: dict) -> float:
    p = pricing()
    cost = 0.0
    cost += units.get("whisper_minutes", 0) * p.get("whisper_per_audio_minute", 0)
    cost += units.get("gemini_video_seconds", 0) * p.get("gemini_flash_per_video_second", 0)
    cost += units.get("gemini_requests", 0) * p.get("gemini_flash_per_request", 0)
    cost += units.get("cpu_hours", 0) * p.get("compute_per_cpu_hour", 0)
    return round(cost, 5)


def record(stage: str, project_id: str | None = None, job_id: str | None = None,
           duration_seconds: float | None = None, bytes_: int | None = None,
           units: dict | None = None) -> None:
    """Insert a stage_metrics row (service role). Failures never break the job."""
    try:
        import httpx

        from .. import supa
        httpx.post(f"{supa.SUPABASE_URL}/rest/v1/stage_metrics",
                   headers={"apikey": supa.SERVICE_KEY,
                            "Authorization": f"Bearer {supa.SERVICE_KEY}",
                            "Content-Type": "application/json",
                            "Prefer": "return=minimal"},
                   json={"project_id": project_id, "job_id": job_id,
                         "stage": stage, "duration_seconds": duration_seconds,
                         "bytes": bytes_, "units": units or {},
                         "estimated_cost_usd": estimate_cost(units or {})},
                   timeout=15)
    except Exception:
        pass


@contextmanager
def timed(stage: str, project_id=None, job_id=None, units: dict | None = None):
    t0 = time.time()
    try:
        yield
    finally:
        record(stage, project_id, job_id, round(time.time() - t0, 2),
               units=units)
