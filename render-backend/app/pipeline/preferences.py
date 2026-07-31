"""Milestone 15: user-preference history.

Every meaningful correction is recorded as structured data. Personalization v1
is rules + ranking adjustment ONLY (no fine-tuning until there is a large body
of approved projects — per spec).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from pydantic import BaseModel


class Correction(BaseModel):
    projectId: str
    userId: str = ""
    originalTimelineVersion: int
    requestedChange: str                 # NL command or critic issue
    appliedOperations: list[dict]
    accepted: bool
    finalTimelineVersion: int | None = None
    projectStyle: str = ""
    segmentFeatures: dict = {}           # features of segments involved
    createdAt: str = ""


class LocalCorrectionStore:
    """JSONL store for local/dev mode; CloudCorrectionStore mirrors to Supabase."""

    def __init__(self, path: str):
        self.path = path

    def record(self, c: Correction) -> None:
        c.createdAt = c.createdAt or datetime.now(timezone.utc).isoformat()
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(c.model_dump()) + "\n")

    def load(self) -> list[Correction]:
        if not os.path.exists(self.path):
            return []
        return [Correction(**json.loads(line))
                for line in open(self.path, encoding="utf-8") if line.strip()]


class CloudCorrectionStore:
    def __init__(self):
        from .. import supa
        self.supa = supa

    def record(self, c: Correction) -> None:
        import httpx
        c.createdAt = c.createdAt or datetime.now(timezone.utc).isoformat()
        r = httpx.post(f"{self.supa.SUPABASE_URL}/rest/v1/user_corrections",
                       headers={"apikey": self.supa.SERVICE_KEY,
                                "Authorization": f"Bearer {self.supa.SERVICE_KEY}",
                                "Content-Type": "application/json",
                                "Prefer": "return=minimal"},
                       json={"project_id": c.projectId, "user_id": c.userId,
                             "original_timeline_version": c.originalTimelineVersion,
                             "requested_change": c.requestedChange,
                             "applied_operations": c.appliedOperations,
                             "accepted": c.accepted,
                             "final_timeline_version": c.finalTimelineVersion,
                             "project_style": c.projectStyle,
                             "segment_features": c.segmentFeatures},
                       timeout=30)
        r.raise_for_status()

    def load_for_user(self, user_id: str) -> list[Correction]:
        rows = self.supa.db_select("user_corrections", f"user_id=eq.{user_id}")
        return [Correction(
            projectId=r["project_id"], userId=r["user_id"],
            originalTimelineVersion=r["original_timeline_version"],
            requestedChange=r["requested_change"],
            appliedOperations=r["applied_operations"] or [],
            accepted=r["accepted"],
            finalTimelineVersion=r["final_timeline_version"],
            projectStyle=r.get("project_style") or "",
            segmentFeatures=r.get("segment_features") or {},
            createdAt=r["created_at"]) for r in rows]


def weight_adjustments(corrections: list[Correction]) -> dict[str, float]:
    """Rules-based ranking adjustment from accepted corrections.

    v1 heuristics (documented, deliberately conservative — max ±0.05 per key):
    - accepted replace_clip ops citing variety/repetition -> raise variety weight
    - accepted trim ops (tighter cuts) -> raise motion_fit slightly
    - accepted change_speed ops -> no weight change (renderer preference)
    """
    adj = {"variety": 0.0, "motion_fit": 0.0}
    for c in corrections:
        if not c.accepted:
            continue
        for op in c.appliedOperations:
            blob = (op.get("comment", "") + " " + c.requestedChange).lower()
            if op.get("op") == "replace_clip" and any(
                    k in blob for k in ("variet", "repeat", "same shot", "different")):
                adj["variety"] += 0.01
            if op.get("op") == "trim_clip" and any(
                    k in blob for k in ("faster", "tight", "shorter", "pace")):
                adj["motion_fit"] += 0.01
    return {k: max(-0.05, min(0.05, v)) for k, v in adj.items() if v}
