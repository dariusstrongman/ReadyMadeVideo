"""Stage 6: high-frequency movement analysis.

Gemini's sparse frame sampling misses fast fitness movement, so movement metrics
come from dense LOCAL sampling. Default method: OpenCV frame-difference energy at
~12 fps (works everywhere). MediaPipe Pose is an optional drop-in provider for
per-joint metrics when its wheel is available for the running Python version.
"""
from __future__ import annotations

import cv2
import numpy as np

from .schemas import MotionArtifact, MotionScene, ScenesArtifact

SAMPLE_FPS = 12.0
WIDTH = 240


def motion_stage(proxy_path: str, scenes: ScenesArtifact) -> MotionArtifact:
    cap = cv2.VideoCapture(proxy_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    step = max(1, round(fps / SAMPLE_FPS))
    actual_fps = fps / step

    samples: list[tuple[float, float]] = []  # (t, motion 0-255)
    prev = None
    idx = 0
    while True:
        ok = cap.grab()
        if not ok:
            break
        if idx % step == 0:
            ok, frame = cap.retrieve()
            if not ok:
                break
            h, w = frame.shape[:2]
            small = cv2.resize(frame, (WIDTH, max(2, int(h * WIDTH / w))))
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            if prev is not None:
                d = cv2.absdiff(gray, prev)
                # Foreground-sensitive metric (Project One smoke-test finding:
                # whole-frame mean gave ~0.02 for real tire flips because a
                # small moving subject on a static wide shot barely moves the
                # global mean). Use the mean of the top-decile most-changed
                # pixels: tracks subject movement independent of subject size.
                flat = d.reshape(-1)
                k = max(1, flat.size // 50)          # top 2% most-changed pixels
                top = float(cv2.mean(cv2.sort(flat.reshape(1, -1),
                                              cv2.SORT_EVERY_ROW
                                              | cv2.SORT_DESCENDING)[:, :k])[0])
                samples.append((idx / fps, top))
            prev = gray
        idx += 1
    cap.release()

    # Adaptive per-video normalization (Project One finding: absolute pixel-diff
    # scales are meaningless across phone footage — small subjects on static
    # wide shots compress to ~0). Intensity is RELATIVE within this video:
    # scene p75 against the video's own p95, so the selector ranks correctly
    # regardless of framing. A floor guards against amplifying pure noise.
    all_vals = np.array([m for _, m in samples]) if samples else np.array([0.0])
    global_scale = max(12.0, float(np.percentile(all_vals, 95)))

    out: list[MotionScene] = []
    for s in scenes.scenes:
        pts = [(t, m) for t, m in samples if s.start <= t < s.end]
        if not pts:
            out.append(MotionScene(index=s.index, start=s.start, end=s.end,
                                   motion_intensity=0))
            continue
        vals = np.array([m for _, m in pts])
        times = [t for t, _ in pts]
        intensity = float(min(1.0, max(0.0, np.percentile(vals, 75) / global_scale)))

        # peak moments: local maxima above 80th percentile, min 1 s apart
        thresh = np.percentile(vals, 80)
        peaks: list[float] = []
        for i in range(1, len(vals) - 1):
            if vals[i] >= thresh and vals[i] >= vals[i - 1] and vals[i] >= vals[i + 1]:
                if not peaks or times[i] - peaks[-1] >= 1.0:
                    peaks.append(round(times[i], 2))

        # stationary ranges: runs where motion < 1.5 for >= 1 s
        stationary: list[list[float]] = []
        run_start = None
        for (t, m) in pts:
            if m < 6.0:
                run_start = t if run_start is None else run_start
            else:
                if run_start is not None and t - run_start >= 1.0:
                    stationary.append([round(run_start, 2), round(t, 2)])
                run_start = None
        if run_start is not None and pts[-1][0] - run_start >= 1.0:
            stationary.append([round(run_start, 2), round(pts[-1][0], 2)])

        out.append(MotionScene(index=s.index, start=s.start, end=s.end,
                               motion_intensity=round(intensity, 3),
                               peak_moments=peaks[:10],
                               stationary_ranges=stationary))
    return MotionArtifact(method="opencv-framediff", sampled_fps=round(actual_fps, 2),
                          scenes=out)
