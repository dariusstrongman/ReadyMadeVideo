#!/usr/bin/env python3
"""Phase 7: coverage evaluation over the REAL segment catalog.

Loads every analysis dir's segments.json, runs the coverage validator, and
classifies each category Present / Weak / Missing, plus repetition and
technically-unusable counts. Recommends a final duration from actual coverage.

Usage: python scripts/project_one_coverage.py <project-one-local-root>
"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "render-backend"))

from app.pipeline.coverage import validate_coverage  # noqa: E402
from app.pipeline.schemas import Segment  # noqa: E402


def main():
    root = sys.argv[1]
    analysis = os.path.join(root, "analysis")
    segments = []
    for name in sorted(os.listdir(analysis)):
        p = os.path.join(analysis, name, "segments.json")
        if os.path.exists(p):
            for raw in json.load(open(p, encoding="utf-8")):
                segments.append(Segment(**raw))
    rep = validate_coverage(segments)

    usable = rep.usableSegmentCount
    lines = ["# Project One — real-footage coverage report\n",
             f"segments: **{rep.segmentCount}** total, **{usable}** usable\n"]
    lines.append("| category | status | matches |")
    lines.append("|---|---|---|")
    for item in rep.items:
        n = len(item.matchingSegments)
        if not item.present:
            status = "OPTIONAL-missing" if item.optional else "**MISSING**"
        elif n == 1:
            status = "WEAK (1 segment)"
        else:
            status = f"Present ({n})"
        lines.append(f"| {item.label} | {status} | "
                     f"{', '.join(item.matchingSegments[:4]) or '—'} |")
    for w in rep.warnings:
        lines.append(f"\n⚠ {w}")

    present = sum(1 for i in rep.items if i.present and not i.optional)
    required = sum(1 for i in rep.items if not i.optional)
    if present <= required * 0.4 or usable < 8:
        band, target = "LIMITED", 20
    elif present <= required * 0.7 or usable < 16:
        band, target = "MODERATE", 32
    else:
        band, target = "STRONG", 55
    lines.append(f"\n## Duration recommendation\ncoverage band: **{band}** "
                 f"({present}/{required} required categories present, "
                 f"{usable} usable segments) → target **~{target}s** final. "
                 f"Do not force a longer edit from this footage.")

    out = os.path.join(root, "reports", "coverage-report.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    json.dump({"band": band, "target_seconds": target,
               "report": rep.model_dump()},
              open(os.path.join(root, "reports", "coverage-report.json"), "w",
                   encoding="utf-8"), indent=2)
    print("\n".join(lines))


if __name__ == "__main__":
    main()
