"""Bounded media validation via ffprobe.

ffprobe accepts an https input, so we point it at a short-lived presigned S3 GET
URL: it range-reads only the container metadata it needs (including seeking to a
tail moov atom) rather than the render worker streaming the whole 2 GB body onto
Railway. Returns validity plus the duration the pipeline needs.
"""
from __future__ import annotations

import json
import os
import subprocess

FFPROBE = os.environ.get("FFPROBE_BIN", "ffprobe")


def probe_video(source: str, timeout: int = 120) -> dict:
    """Probe a local path or http(s) URL. Returns:
    {"valid": bool, "duration": float|None, "width": int|None, "height": int|None}
    `valid` is True iff a decodable video stream is present."""
    empty = {"valid": False, "duration": None, "width": None, "height": None}
    try:
        result = subprocess.run(
            [FFPROBE, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_type,width,height:format=duration",
             "-of", "json", source],
            capture_output=True, text=True, timeout=timeout, check=False)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return empty
    if result.returncode != 0:
        return empty
    try:
        data = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return empty
    streams = [s for s in data.get("streams", [])
               if s.get("codec_type") == "video" and s.get("width")]
    if not streams:
        return empty
    try:
        duration = float(data.get("format", {}).get("duration"))
    except (TypeError, ValueError):
        duration = None
    return {"valid": True, "duration": duration,
            "width": streams[0].get("width"), "height": streams[0].get("height")}


def has_video_stream(source: str, timeout: int = 120) -> bool:
    return probe_video(source, timeout=timeout)["valid"]
