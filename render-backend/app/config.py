"""Startup configuration validation — fail fast with clear errors.

Required (server-only): SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
Optional: GEMINI_API_KEY, OPENAI_API_KEY (AI stages degrade gracefully without),
ALLOWED_ORIGINS, RENDER_TIMEOUT_S, FFMPEG_BIN/FFPROBE_BIN, TITLE_FONT_FILE,
AUTOEDIT_MAX_REVISIONS, PRICING_FILE.
"""
from __future__ import annotations

import os
import shutil
import sys

REQUIRED = ["SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"]
OPTIONAL_AI = ["GEMINI_API_KEY", "OPENAI_API_KEY"]


def validate(exit_on_error: bool = True) -> list[str]:
    problems: list[str] = []
    for key in REQUIRED:
        v = os.environ.get(key, "")
        if not v:
            problems.append(f"missing required env var {key}")
        elif key == "SUPABASE_URL" and not v.startswith("https://"):
            problems.append(f"{key} must be an https URL")
    ffmpeg = os.environ.get("FFMPEG_BIN", "ffmpeg")
    if shutil.which(ffmpeg) is None:
        problems.append(f"ffmpeg binary '{ffmpeg}' not found on PATH")
    if shutil.which(os.environ.get("FFPROBE_BIN", "ffprobe")) is None:
        problems.append("ffprobe binary not found on PATH")
    missing_ai = [k for k in OPTIONAL_AI if not os.environ.get(k)]
    if missing_ai:
        print(f"[config] note: {', '.join(missing_ai)} not set — "
              f"AI stages will be skipped/degraded", file=sys.stderr)
    if problems and exit_on_error:
        for p in problems:
            print(f"[config] ERROR: {p}", file=sys.stderr)
        sys.exit(2)
    return problems
