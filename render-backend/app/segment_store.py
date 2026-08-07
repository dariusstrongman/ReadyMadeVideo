"""THE one way to read the segment catalog (Batch B2).

`segments` is unique per (asset_id, segment_key, schema_version), so a project
analyzed before and after a schema bump holds BOTH generations of every
segment as separate rows. Every consumer must apply the same deterministic
selection — a subsystem reading mixed versions differently would silently
disagree with the planner about what footage exists.

Selection rule (authoritative, documented):
  * group rows by (asset_id, segment_key) — falling back to the values inside
    ``data`` for rows written without the columns
  * the HIGHEST schema_version wins (v3 beats v2); fields are NEVER merged
    across versions — the winning row is used whole
  * same-version duplicates resolve to the LOWEST row id — deterministic
    regardless of database return order
  * output ordering is (asset_id, segment_key): stable across runs, so row
    order can never change any downstream result
  * projects with only v2 rows load exactly as before
"""
from __future__ import annotations

from . import supa
from .pipeline.schemas import Segment


def _identity(row: dict) -> tuple[str, str]:
    data = row.get("data") or {}
    return (str(row.get("asset_id") or data.get("assetId") or ""),
            str(row.get("segment_key") or data.get("segmentId") or ""))


def _version(row: dict) -> int:
    data = row.get("data") or {}
    try:
        return int(row.get("schema_version") or data.get("schemaVersion") or 0)
    except (TypeError, ValueError):
        return 0


def latest_rows(rows: list[dict]) -> list[dict]:
    """Apply the selection rule to raw `segments` rows."""
    best: dict[tuple, dict] = {}
    for r in rows:
        key = _identity(r)
        cur = best.get(key)
        if cur is None:
            best[key] = r
            continue
        v, cur_v = _version(r), _version(cur)
        if v > cur_v or (v == cur_v
                         and str(r.get("id")) < str(cur.get("id"))):
            best[key] = r
    return [r for _, r in sorted(best.items(), key=lambda kv: kv[0])]


def load_rows(project_id: str) -> list[dict]:
    return latest_rows(
        supa.db_select("segments", f"project_id=eq.{project_id}"))


def load_segments(project_id: str) -> list[Segment]:
    return [Segment(**r["data"]) for r in load_rows(project_id)]
