"""Picture Edit Engine V2 — preview renderer.

Executes a PictureEditV2 result with real FFmpeg:
  * per-clip trims + constant playback speed (timeline truth)
  * hard_cut boundaries (concat) and dissolve / dip_to_black / dip_to_white
    boundaries (xfade fade/fadeblack/fadewhite) — soft transitions consume the
    validated SOURCE HANDLES beyond each cut, so the timeline duration is
    preserved exactly
  * static and pan-interpolated crops from executable reframe instructions
  * audio: per-clip original audio (or silence) hard-concatenated — audio
    crossfades are a later audio-engine milestone (documented, not hidden)

Additive module: the existing renderer2 path is untouched. Deterministic
filter-graph construction; raw LLM command strings are never accepted.
"""
from __future__ import annotations

import os
import subprocess

from ..renderer import FFMPEG, RenderError, probe
from ..renderer2 import PREVIEW, _atempo_chain

XFADE_NAMES = {"dissolve": "fade", "dip_to_black": "fadeblack",
               "dip_to_white": "fadewhite"}
PREVIEW_LONG_EDGE = 640


def _preview_dims(timeline: dict) -> tuple[int, int]:
    """Preview dimensions that PRESERVE the approved aspect ratio exactly —
    landscape, portrait AND square (renderer2's helper only knows the first
    two). Longest edge scales to the preview budget; both sides stay even."""
    w = int(timeline.get("width", 1920))
    h = int(timeline.get("height", 1080))
    scale = PREVIEW_LONG_EDGE / max(w, h)
    even = lambda v: max(2, int(round(v * scale / 2)) * 2)  # noqa: E731
    return even(w), even(h)


def _fps_str(timeline: dict) -> str:
    """Frame rate WITHOUT integer truncation: 29.97 stays 29.97, 30 stays 30."""
    fps = float(timeline.get("fps", 30))
    text = f"{fps:.3f}".rstrip("0").rstrip(".")
    return text or "30"


def _crop_filter(inst: dict, out_dur: float) -> str:
    sc, ec = inst["startCrop"], inst["endCrop"]
    if inst["mode"] == "static":
        return (f"crop=w=iw*{sc['width']:.4f}:h=ih*{sc['height']:.4f}"
                f":x=iw*{sc['x']:.4f}:y=ih*{sc['y']:.4f}")
    # pan_interpolated: width/height constant, x/y move linearly over the clip
    return (f"crop=w=iw*{sc['width']:.4f}:h=ih*{sc['height']:.4f}"
            f":x='iw*({sc['x']:.4f}+({ec['x'] - sc['x']:.4f})*t/{out_dur:.3f})'"
            f":y='ih*({sc['y']:.4f}+({ec['y'] - sc['y']:.4f})*t/{out_dur:.3f})'")


def render_picture_edit(result: dict, sources: dict[str, str], out_path: str,
                        timeout: int = 900, cancel_check=None) -> float:
    """Render the PictureEditV2 preview. Returns the output duration."""
    timeline = result["timeline"]
    clips = [c for t in timeline["tracks"] if t["type"] == "video"
             for c in t["clips"]]
    if not clips:
        raise RenderError("picture edit has no clips")
    W, H = _preview_dims(timeline)
    fps = _fps_str(timeline)

    # soft executable transitions by boundary index (clip i -> i+1)
    soft: dict[int, dict] = {
        t["boundaryIndex"]: t for t in result.get("transitionInstructions", [])
        if t["status"] == "executable" and t["type"] in XFADE_NAMES}
    crops: dict[str, dict] = {
        r["segmentId"]: r for r in result.get("reframeInstructions", [])
        if r["status"] == "executable"}
    mappings = result.get("segmentMappings", [])

    input_files: list[str] = []
    input_idx: dict[str, int] = {}
    probes: dict[str, object] = {}
    for c in clips:
        aid = c["assetId"]
        if aid not in input_idx:
            if aid not in sources:
                raise RenderError(f"no source file for asset {aid}")
            input_idx[aid] = len(input_files)
            input_files.append(sources[aid])
            probes[aid] = probe(sources[aid])

    parts: list[str] = []
    orig: list[float] = []          # per-clip original output durations
    for k, c in enumerate(clips):
        aid = c["assetId"]
        info = probes[aid]
        speed = float(c.get("speed", 1.0))
        s, e = float(c["sourceStart"]), float(c["sourceEnd"])
        ext = soft[k]["durationSeconds"] if k in soft else 0.0
        e_ext = min(e + ext * speed, info.duration)   # tail handle, source time
        if e_ext <= s:
            raise RenderError(f"clip {c['id']} empty after trim")
        out_dur = (e - s) / speed
        orig.append(out_dur)
        seg_id = mappings[k]["segmentId"] if k < len(mappings) else None
        crop = ""
        if seg_id and seg_id in crops:
            crop = _crop_filter(crops[seg_id], out_dur) + ","
        parts.append(
            f"[{input_idx[aid]}:v]trim=start={s:.3f}:end={e_ext:.3f},"
            f"setpts=(PTS-STARTPTS)/{speed:.4f},{crop}"
            f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
            f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,fps={fps},format=yuv420p,"
            f"setsar=1[v{k}]")
        if getattr(info, "has_audio", False):
            parts.append(f"[{input_idx[aid]}:a]atrim=start={s:.3f}:end={e:.3f},"
                         f"asetpts=PTS-STARTPTS,{_atempo_chain(speed)},"
                         f"aresample=48000,aformat=channel_layouts=stereo[a{k}]")
        else:
            parts.append(f"anullsrc=channel_layout=stereo:sample_rate=48000,"
                         f"atrim=duration={out_dur:.3f}[a{k}]")

    # video: stepwise chain — concat for hard boundaries, xfade for soft ones
    acc = "[v0]"
    acc_orig = orig[0]
    for k in range(1, len(clips)):
        nxt, label = f"[v{k}]", f"[x{k}]"
        boundary = k - 1
        if boundary in soft:
            tr = soft[boundary]
            parts.append(f"{acc}{nxt}xfade=transition="
                         f"{XFADE_NAMES[tr['type']]}"
                         f":duration={tr['durationSeconds']:.3f}"
                         f":offset={acc_orig:.3f}{label}")
        else:
            parts.append(f"{acc}{nxt}concat=n=2:v=1:a=0{label}")
        acc = label
        acc_orig += orig[k]

    parts.append("".join(f"[a{k}]" for k in range(len(clips)))
                 + f"concat=n={len(clips)}:v=0:a=1[ca]")

    cmd = [FFMPEG, "-y", "-loglevel", "error"]
    for f in input_files:
        cmd += ["-i", f]
    cmd += ["-filter_complex", ";".join(parts), "-map", acc, "-map", "[ca]",
            "-r", fps, "-c:v", "libx264",
            "-preset", PREVIEW["preset"], "-crf", str(PREVIEW["crf"]),
            "-c:a", "aac", "-shortest", out_path]
    proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
    if cancel_check and cancel_check():
        raise RenderError("cancelled")
    if proc.returncode != 0:
        raise RenderError("ffmpeg failed: "
                          + proc.stderr.decode(errors="replace")[-600:])
    if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        raise RenderError("no output produced")
    return acc_orig
