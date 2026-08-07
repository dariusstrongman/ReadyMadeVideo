"""Multi-clip timeline compiler + FFmpeg renderer (Milestone 10).

Compiles VALIDATED timeline JSON into a deterministic ffmpeg filter graph.
Raw command strings from an LLM are never accepted anywhere.

Supported: leading title card, N sequential video clips (trim/speed/volume),
caption overlays, optional looped music with constant duck under clip audio,
fade in/out, preview (360p) and final (up to 1080p) profiles. Sources with no
audio stream get silence injected so concat stays valid.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass

from .renderer import FFMPEG, ProbeInfo, RenderError, _default_font, _ff_escape, probe

PREVIEW = {"w_land": 640, "h_land": 360, "crf": 30, "preset": "veryfast"}
FINAL = {"w_land": 1920, "h_land": 1080, "crf": 21, "preset": "medium"}


@dataclass
class CompiledRender:
    cmd: list[str]
    duration: float


def _dims(timeline: dict, profile: dict) -> tuple[int, int]:
    """Map the timeline's shape onto this profile's rendering size.

    The profile only carries a long and a short edge, so the timeline's own
    width/height are read for ORIENTATION, not as literal pixels. Square is its
    own case: without it a 1080x1080 timeline fell through to the landscape
    branch and rendered 1920x1080.
    """
    w, h = timeline.get("width", 1920), timeline.get("height", 1080)
    long_edge, short_edge = profile["w_land"], profile["h_land"]
    if h > w:
        return short_edge, long_edge          # vertical, e.g. 1080x1920
    if h == w:
        return short_edge, short_edge         # square, e.g. 1080x1080
    return long_edge, short_edge              # landscape, e.g. 1920x1080


def _atempo_chain(speed: float) -> str:
    """atempo only supports 0.5-2.0 per instance; chain as needed."""
    chain = []
    s = speed
    while s > 2.0:
        chain.append("atempo=2.0")
        s /= 2.0
    while s < 0.5:
        chain.append("atempo=0.5")
        s /= 0.5
    chain.append(f"atempo={s:.4f}")
    return ",".join(chain)


def compile_timeline(timeline: dict, sources: dict[str, str], out_path: str,
                     profile: str = "preview",
                     music_path: str | None = None) -> CompiledRender:
    """timeline: validated timeline dict. sources: assetId -> local file path."""
    prof = PREVIEW if profile == "preview" else FINAL
    W, H = _dims(timeline, prof)
    # never int(): truncating 23.976 -> 23 runs 4% slow. ffmpeg's fps/r
    # filters take decimals directly.
    fps = round(float(timeline.get("fps", 30)), 3)

    vclips = [c for t in timeline["tracks"] if t["type"] == "video"
              for c in t["clips"]]
    if not vclips:
        raise RenderError("timeline has no video clips")
    tclips = [c for t in timeline["tracks"] if t["type"] == "text"
              for c in t["clips"]]
    title = next((c for c in tclips if c.get("role") == "title_card"
                  or (c.get("timelineStart", 1) == 0 and len(tclips) == 1)), None)
    captions = [c for c in tclips if c is not title]

    # input list: unique source files (+ music last)
    input_files: list[str] = []
    input_idx: dict[str, int] = {}
    probes: dict[str, ProbeInfo] = {}
    def _register(aid: str) -> None:
        if aid not in input_idx:
            if aid not in sources:
                raise RenderError(f"no source file for asset {aid}")
            input_idx[aid] = len(input_files)
            input_files.append(sources[aid])
            probes[aid] = probe(sources[aid])

    for c in vclips:
        _register(c["assetId"])
        # audio-under b-roll: the donor asset must be an ffmpeg input even
        # when no remaining picture clip references it — otherwise the
        # audioFrom branch below silently falls back and the speech is lost
        donor_aid = (c.get("audioFrom") or {}).get("assetId")
        if donor_aid:
            _register(donor_aid)

    parts: list[str] = []
    seg_labels: list[tuple[str, str]] = []
    total = 0.0

    title_dur = 0.0
    if title:
        title_dur = float(title["timelineEnd"]) - float(title.get("timelineStart", 0))
        y = {"center": "(h-text_h)/2", "top": "h*0.15", "bottom": "h*0.78"}[
            title.get("position", "center")]
        fs = int(title.get("fontSize", 72) * (H / 1080))
        parts.append(
            f"color=c=0x0a0a0f:s={W}x{H}:r={fps}:d={title_dur:.3f},"
            f"drawtext=fontfile='{_ff_escape(_default_font())}'"
            f":text='{_ff_escape(title['text'])}':fontcolor=0xf0f0f5:fontsize={fs}"
            f":x=(w-text_w)/2:y={y},format=yuv420p,setsar=1[tv]")
        parts.append(f"anullsrc=channel_layout=stereo:sample_rate=48000,"
                     f"atrim=duration={title_dur:.3f}[ta]")
        seg_labels.append(("[tv]", "[ta]"))
        total += title_dur

    for k, c in enumerate(vclips):
        aid = c["assetId"]
        idx = input_idx[aid]
        info = probes[aid]
        s = float(c["sourceStart"])
        e = min(float(c["sourceEnd"]), info.duration)
        if e <= s:
            raise RenderError(f"clip {c['id']}: range {s}-{c['sourceEnd']}s outside "
                              f"{info.duration:.2f}s source")
        speed = float(c.get("speed", 1.0))
        vol = float(c.get("volume", 1.0))
        out_dur = (e - s) / speed
        total += out_dur

        vf = (f"[{idx}:v]trim=start={s:.3f}:end={e:.3f},setpts=(PTS-STARTPTS)/{speed:.4f},"
              f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
              f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,fps={fps},format=yuv420p,setsar=1[v{k}]")
        parts.append(vf)
        # Phase 3 audio-under b-roll: the clip's PICTURE comes from this
        # asset while the audio continues from the DONOR clip (audioFrom) —
        # preview and final render identically, by design (no divergence).
        donor = c.get("audioFrom")
        if donor and donor.get("assetId") in input_idx:
            d_idx = input_idx[donor["assetId"]]
            d_info = probes.get(donor["assetId"])
            d_speed = float(donor.get("speed") or 1.0)
            d_s = float(donor["sourceStart"])
            # clamp to the donor's real length, then pad/trim to EXACTLY the
            # clip's output duration — the a/v concat pairs each segment, so
            # any donor-window drift would push every later clip out of sync
            d_e = min(float(donor["sourceEnd"]),
                      getattr(d_info, "duration", None) or 1e18)
            if getattr(d_info, "has_audio", False) and d_e > d_s:
                parts.append(
                    f"[{d_idx}:a]atrim=start={d_s:.3f}:end={d_e:.3f},"
                    f"asetpts=PTS-STARTPTS,{_atempo_chain(d_speed)},"
                    f"aresample=48000,aformat=channel_layouts=stereo,"
                    f"apad,atrim=duration={out_dur:.3f}[a{k}]")
            else:
                parts.append(f"anullsrc=channel_layout=stereo:sample_rate=48000,"
                             f"atrim=duration={out_dur:.3f}[a{k}]")
        elif info.has_audio and vol > 0:
            parts.append(f"[{idx}:a]atrim=start={s:.3f}:end={e:.3f},asetpts=PTS-STARTPTS,"
                         f"{_atempo_chain(speed)},volume={vol:.2f},"
                         f"aresample=48000,aformat=channel_layouts=stereo[a{k}]")
        else:
            parts.append(f"anullsrc=channel_layout=stereo:sample_rate=48000,"
                         f"atrim=duration={out_dur:.3f}[a{k}]")
        seg_labels.append((f"[v{k}]", f"[a{k}]"))

    concat_in = "".join(v + a for v, a in seg_labels)
    parts.append(f"{concat_in}concat=n={len(seg_labels)}:v=1:a=1[cv][ca]")

    # captions overlay
    vout = "[cv]"
    if captions:
        draw = []
        for cap in captions:
            y = {"center": "(h-text_h)/2", "top": "h*0.12", "bottom": "h*0.82"}[
                cap.get("position", "bottom")]
            fs = int(cap.get("fontSize", 48) * (H / 1080))
            draw.append(
                f"drawtext=fontfile='{_ff_escape(_default_font())}'"
                f":text='{_ff_escape(cap['text'])}':fontcolor=0xf0f0f5"
                f":borderw=2:bordercolor=0x0a0a0f:fontsize={fs}"
                f":x=(w-text_w)/2:y={y}"
                f":enable='between(t,{float(cap['timelineStart']):.2f},"
                f"{float(cap['timelineEnd']):.2f})'")
        parts.append(f"[cv]{','.join(draw)}[cvc]")
        vout = "[cvc]"

    # fades
    fade = min(0.5, total / 8)
    parts.append(f"{vout}fade=t=in:st=0:d={fade:.2f},"
                 f"fade=t=out:st={max(0, total - fade):.2f}:d={fade:.2f}[vfade]")

    aout = "[ca]"
    if music_path:
        midx = len(input_files)
        input_files.append(music_path)
        duck_db = float(timeline.get("music", {}).get("duckDb", -10))
        parts.append(f"[{midx}:a]aloop=loop=-1:size=2e9,atrim=duration={total:.3f},"
                     f"volume={duck_db}dB,aresample=48000,"
                     f"aformat=channel_layouts=stereo[mus]")
        parts.append("[ca][mus]amix=inputs=2:duration=first:normalize=0[amix]")
        aout = "[amix]"
    parts.append(f"{aout}afade=t=out:st={max(0, total - fade):.2f}:d={fade:.2f},"
                 f"loudnorm=I=-16:TP=-1.5:LRA=11[afinal]")

    cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error"]
    for f in input_files:
        cmd += ["-i", f]
    cmd += ["-filter_complex", ";".join(parts),
            "-map", "[vfade]", "-map", "[afinal]",
            "-map_metadata", "-1",
            "-c:v", "libx264", "-preset", prof["preset"], "-crf", str(prof["crf"]),
            "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", out_path]
    return CompiledRender(cmd=cmd, duration=round(total, 3))


class RenderCancelled(Exception):
    pass


def _run_interruptible(cmd: list[str], timeout: int, cancel_check=None,
                       out_path: str | None = None, tick=None,
                       tick_every: float = 20.0):
    """Run ffmpeg with polling so an operator cancellation terminates the
    subprocess promptly (checked every 0.5 s) and partial output is deleted.
    Fixed argument lists only — never shell=True."""
    import time as _time
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    t0 = _time.time()
    last_tick = t0
    while True:
        # A long encode is ONE opaque step: without a periodic tick the job
        # emits no heartbeat for its whole duration, the UI freezes on a stale
        # percentage, and the stale-job watchdog can requeue a render that is
        # actually healthy.
        if tick and _time.time() - last_tick >= tick_every:
            last_tick = _time.time()
            try:
                tick(_time.time() - t0)
            except Exception:  # noqa: BLE001 — telemetry never breaks a render
                pass
        rc = proc.poll()
        if rc is not None:
            _, stderr = proc.communicate()
            return rc, stderr
        if cancel_check and cancel_check():
            proc.kill()
            proc.communicate()
            if out_path and os.path.exists(out_path):
                try:
                    os.remove(out_path)          # delete partial output
                except OSError:
                    pass
            raise RenderCancelled("ffmpeg terminated by cancellation request")
        if _time.time() - t0 > timeout:
            proc.kill()
            proc.communicate()
            raise RenderError(f"render exceeded {timeout}s")
        _time.sleep(0.5)


def render_timeline(timeline: dict, sources: dict[str, str], out_path: str,
                    profile: str = "preview", music_path: str | None = None,
                    timeout: int = 900, cancel_check=None, tick=None):
    compiled = compile_timeline(timeline, sources, out_path, profile, music_path)
    rc, stderr = _run_interruptible(compiled.cmd, timeout, cancel_check,
                                    out_path, tick=tick)
    if rc != 0:
        raise RenderError(f"ffmpeg failed: {stderr.decode(errors='replace')[-600:]}")
    if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        raise RenderError("no output produced")
    info = probe(out_path)
    return {"path": out_path, "duration": info.duration, "width": info.width,
            "height": info.height, "size_bytes": os.path.getsize(out_path),
            "planned_duration": compiled.duration}
