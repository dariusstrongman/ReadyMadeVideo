"""Structured JSON logging: one line per event with project/job/stage context.
Kept dependency-free (print to stdout; collected by the process supervisor)."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone


def log_event(event: str, **fields) -> None:
    rec = {"ts": datetime.now(timezone.utc).isoformat(), "event": event}
    rec.update({k: v for k, v in fields.items() if v is not None})
    try:
        print(json.dumps(rec, default=str), flush=True)
    except Exception:  # logging must never break the caller
        print(f'{{"event":"{event}"}}', file=sys.stdout, flush=True)
