"""Stage 3b + 4a: deterministic mechanical analysis. No LLMs — pure OpenCV/FFmpeg
signal processing, each measurement stored independently.

Visual per scene: blur (Laplacian variance), exposure (luma mean + clip
fractions), black frames, frozen frames, motion energy, shake, perceptual hash
for duplicate grouping.
Audio: integrated loudness (EBU R128), true peak, clipping, silence ranges.
"""
from __future__ import annotations

import math
import re
import subprocess

import cv2
import numpy as np

from ..renderer import FFMPEG
from .schemas import (AudioArtifact, MechanicalArtifact, SceneMechanical,
                      ScenesArtifact, SilenceRange)

SAMPLE_FPS = 4.0          # frames per second sampled for analysis
ANALYSIS_WIDTH = 320      # downscale width for motion/exposure math


def _phash(gray_small: np.ndarray) -> str:
    """8x8 average hash -> 16 hex chars."""
    g = cv2.resize(gray_small, (8, 8), interpolation=cv2.INTER_AREA)
    bits = (g > g.mean()).flatten()
    val = 0
    for b in bits:
        val = (val << 1) | int(b)
    return f"{val:016x}"


def _hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def mechanical_stage(proxy_path: str, scenes: ScenesArtifact) -> MechanicalArtifact:
    cap = cv2.VideoCapture(proxy_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    step = max(1, round(fps / SAMPLE_FPS))

    # collect sampled frames grouped by scene
    per_scene: dict[int, list[tuple[float, np.ndarray]]] = {s.index: [] for s in scenes.scenes}
    idx = 0
    while True:
        ok = cap.grab()
        if not ok:
            break
        if idx % step == 0:
            ok, frame = cap.retrieve()
            if not ok:
                break
            t = idx / fps
            h, w = frame.shape[:2]
            scale = ANALYSIS_WIDTH / w
            small = cv2.resize(frame, (ANALYSIS_WIDTH, max(2, int(h * scale))))
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            for s in scenes.scenes:
                if s.start <= t < s.end:
                    per_scene[s.index].append((t, gray))
                    break
        idx += 1
    cap.release()

    results: list[SceneMechanical] = []
    for s in scenes.scenes:
        frames = per_scene.get(s.index, [])
        if not frames:
            results.append(SceneMechanical(
                index=s.index, start=s.start, end=s.end, blur_laplacian_var=0,
                focus_score=0, exposure_mean=0, exposure_clipped_low=1,
                exposure_clipped_high=0, exposure_score=0, black_frame_fraction=1,
                frozen_frame_fraction=0, motion_energy_mean=0, motion_energy_peak=0,
                shake_score=0, phash="0" * 16))
            continue

        grays = [g for _, g in frames]
        lap_vars = [cv2.Laplacian(g, cv2.CV_64F).var() for g in grays]
        lap = float(np.median(lap_vars))
        # log-normalize: ~50 = soft, ~500+ = sharp
        focus = max(0.0, min(1.0, (math.log10(lap + 1) - 1.0) / 1.9))

        means = [float(g.mean()) for g in grays]
        exp_mean = float(np.mean(means))
        clip_low = float(np.mean([np.mean(g < 16) for g in grays]))
        clip_high = float(np.mean([np.mean(g > 239) for g in grays]))
        # score: penalize heavy clipping and extreme means
        exposure = max(0.0, 1.0 - 2.5 * max(0.0, clip_low - 0.10)
                       - 2.5 * max(0.0, clip_high - 0.10)
                       - max(0.0, abs(exp_mean - 118) - 60) / 120)

        black_frac = float(np.mean([g.mean() < 10 for g in grays]))

        diffs = [float(cv2.absdiff(grays[i], grays[i - 1]).mean())
                 for i in range(1, len(grays))] or [0.0]
        frozen_frac = float(np.mean([d < 0.35 for d in diffs]))
        motion_mean = float(np.mean(diffs))
        motion_peak = float(np.max(diffs))
        # shake: high-frequency variability of motion energy (jerkiness), scaled
        jerk = float(np.std(np.diff(diffs))) if len(diffs) > 2 else 0.0
        shake_score = max(0.0, min(1.0, 1.0 - jerk / 12.0))

        results.append(SceneMechanical(
            index=s.index, start=s.start, end=s.end,
            blur_laplacian_var=round(lap, 2), focus_score=round(focus, 3),
            exposure_mean=round(exp_mean, 1),
            exposure_clipped_low=round(clip_low, 4),
            exposure_clipped_high=round(clip_high, 4),
            exposure_score=round(max(0, min(1, exposure)), 3),
            black_frame_fraction=round(black_frac, 3),
            frozen_frame_fraction=round(frozen_frac, 3),
            motion_energy_mean=round(motion_mean, 3),
            motion_energy_peak=round(motion_peak, 3),
            shake_score=round(shake_score, 3),
            phash=_phash(grays[len(grays) // 2])))

    # duplicate grouping: hamming distance <= 6 joins a group
    group_id = 0
    for i, a in enumerate(results):
        if a.duplicate_group is not None:
            continue
        members = [b for b in results[i + 1:]
                   if b.duplicate_group is None and _hamming(a.phash, b.phash) <= 6]
        if members:
            a.duplicate_group = group_id
            for m in members:
                m.duplicate_group = group_id
            group_id += 1

    return MechanicalArtifact(sampled_fps=SAMPLE_FPS, scenes=results)


def audio_stage(src: str, has_audio: bool) -> AudioArtifact:
    if not has_audio:
        return AudioArtifact(has_audio=False)
    p = subprocess.run(
        [FFMPEG, "-hide_banner", "-i", src,
         "-af", "silencedetect=noise=-35dB:d=0.8,ebur128=peak=true",
         "-f", "null", "-"],
        capture_output=True, timeout=600)
    log = p.stderr.decode(errors="replace")

    silences: list[SilenceRange] = []
    for m in re.finditer(r"silence_start: ([\d.]+)", log):
        silences.append(SilenceRange(start=float(m.group(1)), end=-1))
    for i, m in enumerate(re.finditer(r"silence_end: ([\d.]+)", log)):
        if i < len(silences):
            silences[i].end = float(m.group(1))
    silences = [s for s in silences if s.end > s.start >= 0]

    lufs = None
    m = re.search(r"I:\s+(-?[\d.]+) LUFS", log)
    if m:
        lufs = float(m.group(1))
    peak = None
    m = re.search(r"Peak:\s+(-?[\d.]+) dBFS", log)
    if m:
        peak = float(m.group(1))

    return AudioArtifact(
        has_audio=True, integrated_lufs=lufs, true_peak_db=peak,
        clipped=(peak is not None and peak > -0.3),
        silence_ranges=silences)
