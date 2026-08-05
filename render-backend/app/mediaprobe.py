"""Bounded, strict media validation via ffprobe.

ffprobe accepts an https input, so we point it at a short-lived presigned S3 GET
URL and cap how much it may read (probesize / analyzeduration / rw_timeout) so a
crafted 2 GiB container cannot pull unbounded bytes through Railway. A process-wide
BoundedSemaphore caps concurrent probes regardless of how many finalize calls
arrive.

Strictness (a valid customer video, not an audio file with cover art):
  - at least one video stream whose disposition.attached_pic != 1 (rejects cover
    artwork masquerading as video),
  - real dimensions, and
  - a finite, positive container duration.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading

FFPROBE = os.environ.get("FFPROBE_BIN", "ffprobe")
PROBE_TIMEOUT_S = int(os.environ.get("PROBE_TIMEOUT_S", "120"))
# byte/time caps so a hostile URL can't stream unbounded data through Railway
PROBE_SIZE_BYTES = int(os.environ.get("PROBE_SIZE_BYTES", str(16 * 1024 * 1024)))
ANALYZE_DURATION_US = int(os.environ.get("PROBE_ANALYZE_DURATION_US", str(10_000_000)))
RW_TIMEOUT_US = int(os.environ.get("PROBE_RW_TIMEOUT_US", str(30_000_000)))
MAX_CONCURRENT_PROBES = int(os.environ.get("MAX_CONCURRENT_PROBES", "2"))
MAX_DIMENSION = 16384

_probe_gate = threading.BoundedSemaphore(MAX_CONCURRENT_PROBES)


def probe_video(source: str, timeout: int = PROBE_TIMEOUT_S) -> dict:
    """Probe a local path or http(s) URL under a global concurrency cap and
    resource limits. Returns:
    {"valid": bool, "duration": float|None, "width": int|None, "height": int|None}
    `valid` requires a non-attached, decodable video stream with sane dimensions
    and a finite positive duration."""
    empty = {"valid": False, "duration": None, "width": None, "height": None}
    cmd = [
        FFPROBE, "-v", "error",
        "-probesize", str(PROBE_SIZE_BYTES),
        "-analyzeduration", str(ANALYZE_DURATION_US),
        "-rw_timeout", str(RW_TIMEOUT_US),
        "-select_streams", "v",
        "-show_entries",
        "stream=codec_type,width,height,disposition:format=duration",
        "-of", "json", source,
    ]
    acquired = _probe_gate.acquire(timeout=timeout)
    if not acquired:
        return empty  # too many concurrent probes — treat as unvalidated
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                timeout=timeout, check=False)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return empty
    finally:
        _probe_gate.release()
    if result.returncode != 0:
        return empty
    try:
        data = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return empty

    real_streams = []
    for stream in data.get("streams", []):
        if stream.get("codec_type") != "video":
            continue
        if (stream.get("disposition") or {}).get("attached_pic") == 1:
            continue  # cover artwork, not real video
        width, height = stream.get("width"), stream.get("height")
        if not (isinstance(width, int) and isinstance(height, int)
                and 0 < width <= MAX_DIMENSION and 0 < height <= MAX_DIMENSION):
            continue
        real_streams.append(stream)
    if not real_streams:
        return empty

    try:
        duration = float(data.get("format", {}).get("duration"))
    except (TypeError, ValueError):
        duration = None
    if duration is None or not (duration > 0) or duration != duration:  # >0 and finite
        return empty

    return {"valid": True, "duration": duration,
            "width": real_streams[0]["width"], "height": real_streams[0]["height"]}


def has_video_stream(source: str, timeout: int = PROBE_TIMEOUT_S) -> bool:
    return probe_video(source, timeout=timeout)["valid"]
