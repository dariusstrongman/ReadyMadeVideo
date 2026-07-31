"""FFmpeg rendering: title card + trimmed source -> H.264/AAC MP4.

Safe subprocess wrapper: argument list only (never shell=True), timeout,
temp-dir isolation with guaranteed cleanup by the caller.
Handles sources with or without an audio stream.
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
from dataclasses import dataclass
from typing import Optional

from .timeline import RenderPlan

FFMPEG = os.environ.get("FFMPEG_BIN", "ffmpeg")
FFPROBE = os.environ.get("FFPROBE_BIN", "ffprobe")
RENDER_TIMEOUT_S = int(os.environ.get("RENDER_TIMEOUT_S", "600"))


def _default_font() -> str:
    if p := os.environ.get("TITLE_FONT_FILE"):
        return p
    if platform.system() == "Windows":
        return "C:/Windows/Fonts/arialbd.ttf"
    for cand in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                 "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
                 "/System/Library/Fonts/Helvetica.ttc"):
        if os.path.exists(cand):
            return cand
    return "DejaVuSans-Bold"  # let fontconfig resolve


class RenderError(Exception):
    pass


@dataclass
class ProbeInfo:
    duration: float
    width: int
    height: int
    has_audio: bool


def probe(path: str) -> ProbeInfo:
    """ffprobe a media file; raises RenderError on corrupt/unreadable input."""
    cmd = [FFPROBE, "-v", "error", "-print_format", "json",
           "-show_format", "-show_streams", path]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=60, check=True)
    except subprocess.CalledProcessError as e:
        raise RenderError(f"source file unreadable: {e.stderr.decode(errors='replace')[:300]}")
    except subprocess.TimeoutExpired:
        raise RenderError("ffprobe timed out")
    data = json.loads(out.stdout.decode())
    streams = data.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    if v is None:
        raise RenderError("no video stream in source file")
    dur = float(data.get("format", {}).get("duration") or v.get("duration") or 0)
    if dur <= 0:
        raise RenderError("could not determine source duration")
    return ProbeInfo(
        duration=dur,
        width=int(v.get("width", 0)),
        height=int(v.get("height", 0)),
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
    )


def _ff_escape(text: str) -> str:
    """Escape text for a drawtext filter argument."""
    out = text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "’")
    out = out.replace("%", "\\%").replace(",", "\\,").replace("[", "\\[").replace("]", "\\]")
    return out


def build_ffmpeg_command(plan: RenderPlan, src: str, dst: str, src_info: ProbeInfo) -> list[str]:
    W, H, FPS = plan.width, plan.height, plan.fps
    s, e = plan.source_start, min(plan.source_end, src_info.duration)
    if e <= s:
        raise RenderError(
            f"trim range {plan.source_start}-{plan.source_end}s is outside the "
            f"{src_info.duration:.2f}s source")
    main_dur = e - s
    title_dur = plan.title_duration if plan.title_text else 0.0

    parts: list[str] = []
    if plan.title_text:
        y = {"center": "(h-text_h)/2", "top": "h*0.15", "bottom": "h*0.78"}[plan.title_position]
        parts.append(
            f"color=c=0x0a0a0f:s={W}x{H}:r={FPS}:d={title_dur:.3f},"
            f"drawtext=fontfile='{_ff_escape(_default_font())}':text='{_ff_escape(plan.title_text)}'"
            f":fontcolor=0xf0f0f5:fontsize={plan.title_font_size}"
            f":x=(w-text_w)/2:y={y},format=yuv420p[title_v]")
        parts.append(
            f"anullsrc=channel_layout=stereo:sample_rate=48000,atrim=duration={title_dur:.3f}[title_a]")

    parts.append(
        f"[0:v]trim=start={s:.3f}:end={e:.3f},setpts=PTS-STARTPTS,"
        f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
        f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,fps={FPS},format=yuv420p[main_v]")
    if src_info.has_audio:
        parts.append(
            f"[0:a]atrim=start={s:.3f}:end={e:.3f},asetpts=PTS-STARTPTS,"
            f"aresample=48000,aformat=channel_layouts=stereo[main_a]")
    else:
        parts.append(
            f"anullsrc=channel_layout=stereo:sample_rate=48000,atrim=duration={main_dur:.3f}[main_a]")

    if plan.title_text:
        parts.append("[title_v][title_a][main_v][main_a]concat=n=2:v=1:a=1[outv][outa]")
        maps = ["-map", "[outv]", "-map", "[outa]"]
    else:
        maps = ["-map", "[main_v]", "-map", "[main_a]"]

    return [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
            "-i", src,
            "-filter_complex", ";".join(parts),
            *maps,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            dst]


@dataclass
class RenderResult:
    output_path: str
    size_bytes: int
    duration_seconds: float
    width: int
    height: int


def render(plan: RenderPlan, src: str, dst: str) -> RenderResult:
    """Run the render. Raises RenderError with a useful message on failure."""
    info = probe(src)
    cmd = build_ffmpeg_command(plan, src, dst, info)
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=RENDER_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise RenderError(f"render exceeded {RENDER_TIMEOUT_S}s timeout")
    if proc.returncode != 0:
        raise RenderError(f"ffmpeg failed: {proc.stderr.decode(errors='replace')[-500:]}")
    if not os.path.exists(dst) or os.path.getsize(dst) == 0:
        raise RenderError("ffmpeg produced no output")
    out_info = probe(dst)
    return RenderResult(
        output_path=dst,
        size_bytes=os.path.getsize(dst),
        duration_seconds=out_info.duration,
        width=out_info.width,
        height=out_info.height,
    )
