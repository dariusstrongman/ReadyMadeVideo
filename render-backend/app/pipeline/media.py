"""Stage 1-2: probe (full metadata) and normalize (720p proxy, thumbnails, audio wav)."""
from __future__ import annotations

import json
import os
import subprocess

from ..renderer import FFMPEG, FFPROBE
from .schemas import ProbeArtifact, StreamInfo


def _run(cmd: list[str], timeout=600) -> subprocess.CompletedProcess:
    p = subprocess.run(cmd, capture_output=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError(f"{os.path.basename(cmd[0])} failed: "
                           f"{p.stderr.decode(errors='replace')[-400:]}")
    return p


def probe_stage(src: str) -> ProbeArtifact:
    out = _run([FFPROBE, "-v", "error", "-print_format", "json",
                "-show_format", "-show_streams", src], timeout=120)
    data = json.loads(out.stdout.decode())
    fmt = data.get("format", {})
    v = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    a = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)

    def fps_of(s):
        try:
            n, d = s.get("avg_frame_rate", "0/1").split("/")
            return round(int(n) / int(d), 3) if int(d) else None
        except Exception:
            return None

    rotation = 0
    if v:
        for sd in v.get("side_data_list", []) or []:
            if "rotation" in sd:
                rotation = int(sd["rotation"]) % 360
        rotation = rotation or abs(int((v.get("tags") or {}).get("rotate", 0)))

    return ProbeArtifact(
        duration=float(fmt.get("duration") or (v or {}).get("duration") or 0),
        size_bytes=int(fmt.get("size") or os.path.getsize(src)),
        container=fmt.get("format_name"),
        video=StreamInfo(codec=v.get("codec_name"), width=v.get("width"),
                         height=v.get("height"), fps=fps_of(v)) if v else None,
        audio=StreamInfo(codec=a.get("codec_name"),
                         sample_rate=int(a.get("sample_rate") or 0) or None,
                         channels=a.get("channels")) if a else None,
        rotation=rotation,
        creation_time=(fmt.get("tags") or {}).get("creation_time"),
        has_audio=a is not None,
    )


def proxy_stage(src: str, workdir: str, probe: ProbeArtifact) -> dict:
    """720p analysis proxy + thumbnails (1 per 2 s, max 90) + 16 kHz mono wav.

    Returns local file paths; the runner uploads them to private storage.
    Originals are retained untouched for the final render.
    """
    os.makedirs(workdir, exist_ok=True)
    proxy = os.path.join(workdir, "proxy.mp4")
    wav = os.path.join(workdir, "audio.wav")
    thumb_dir = os.path.join(workdir, "thumbs")
    os.makedirs(thumb_dir, exist_ok=True)

    # proxy: scale so the short side is <=720, keep aspect + fps, light crf
    # -map_metadata -1: strip GPS/device metadata from every generated file;
    # only technically required stream parameters survive.
    _run([FFMPEG, "-y", "-loglevel", "error", "-i", src,
          "-vf", "scale='if(gt(iw,ih),-2,720)':'if(gt(iw,ih),720,-2)'",
          "-map_metadata", "-1",
          "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
          "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", proxy])

    # thumbnails
    interval = 2.0
    n_thumbs = min(90, max(1, int(probe.duration / interval)))
    _run([FFMPEG, "-y", "-loglevel", "error", "-i", proxy,
          "-vf", f"fps=1/{interval},scale=320:-2",
          "-frames:v", str(n_thumbs),
          os.path.join(thumb_dir, "thumb_%04d.jpg")])

    # audio for STT / analysis (silent file if no audio stream)
    if probe.has_audio:
        _run([FFMPEG, "-y", "-loglevel", "error", "-i", src,
              "-map_metadata", "-1",
              "-vn", "-ac", "1", "-ar", "16000", wav])
    else:
        _run([FFMPEG, "-y", "-loglevel", "error",
              "-f", "lavfi", "-i", f"anullsrc=r=16000:cl=mono:d={max(probe.duration, 0.1)}",
              wav])

    thumbs = sorted(os.path.join(thumb_dir, f) for f in os.listdir(thumb_dir))
    return {"proxy": proxy, "wav": wav, "thumbs": thumbs}
