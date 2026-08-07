#!/usr/bin/env python3
"""CI coverage gate: per-critical-module thresholds (fails the build if unmet).
Run after: pytest --cov=app --cov-report=json:coverage.json"""
import json
import sys

THRESHOLDS = {          # module filename -> minimum percent
    "jobs.py": 75.0,
    "main.py": 70.0,     # operator API logic
    "autoedit.py": 70.0,
    "telemetry.py": 75.0,
    "editorial_intelligence.py": 80.0,
    "editorial_planner.py": 85.0,
    "editorial_phase2.py": 85.0,
    "creative_phase3.py": 85.0,
    "picture_edit_v2.py": 85.0,
    "product_editor.py": 85.0,
    "ingestion.py": 90.0,
    "integration.py": 90.0,
    "licenses.py": 80.0,
    "media.py": 85.0,
    "providers.py": 70.0,
    "store.py": 85.0,
}
TOTAL_MIN = 70.0

d = json.load(open(sys.argv[1] if len(sys.argv) > 1 else "coverage.json"))
failed = False
seen = set()
for path, v in sorted(d["files"].items()):
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    if name in THRESHOLDS and name not in seen:
        seen.add(name)
        pct = v["summary"]["percent_covered"]
        ok = pct >= THRESHOLDS[name]
        print(f"{'OK  ' if ok else 'FAIL'} {name:14} {pct:5.1f}% "
              f"(min {THRESHOLDS[name]:.0f}%)")
        failed |= not ok
missing = set(THRESHOLDS) - seen
if missing:
    print(f"FAIL missing modules in coverage report: {sorted(missing)}")
    failed = True
total = d["totals"]["percent_covered"]
ok = total >= TOTAL_MIN
print(f"{'OK  ' if ok else 'FAIL'} {'TOTAL':14} {total:5.1f}% (min {TOTAL_MIN:.0f}%)")
failed |= not ok
sys.exit(1 if failed else 0)
