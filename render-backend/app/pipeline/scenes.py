"""Stage 3a: shot/scene boundary detection via PySceneDetect (ContentDetector)."""
from __future__ import annotations

from scenedetect import ContentDetector, SceneManager, open_video

from .schemas import SceneRange, ScenesArtifact

THRESHOLD = 27.0
MIN_SCENE_LEN_S = 0.6
# Continuous phone footage often has ZERO hard cuts (Project One smoke-test
# finding: a 73 s handheld clip produced one giant unusable segment). Scenes
# longer than MAX_SCENE_S are subdivided into ~TARGET_SUB_S logical windows so
# the catalog/selector work with editable ranges.
MAX_SCENE_S = 12.0
TARGET_SUB_S = 8.0


def _subdivide(scenes: list[SceneRange]) -> list[SceneRange]:
    out: list[SceneRange] = []
    for s in scenes:
        dur = s.end - s.start
        if dur <= MAX_SCENE_S:
            out.append(s)
            continue
        n = max(2, round(dur / TARGET_SUB_S))
        step = dur / n
        for k in range(n):
            out.append(SceneRange(index=0,
                                  start=round(s.start + k * step, 3),
                                  end=round(s.start + (k + 1) * step, 3)))
    for i, s in enumerate(out):
        s.index = i
    return out


def scenes_stage(proxy_path: str, duration: float) -> ScenesArtifact:
    video = open_video(proxy_path)
    manager = SceneManager()
    manager.add_detector(ContentDetector(
        threshold=THRESHOLD,
        min_scene_len=max(1, int(MIN_SCENE_LEN_S * (video.frame_rate or 30)))))
    manager.detect_scenes(video)
    ranges = manager.get_scene_list()

    scenes: list[SceneRange] = []
    if not ranges:  # single continuous shot
        scenes.append(SceneRange(index=0, start=0.0, end=round(duration, 3)))
    else:
        for i, (s, e) in enumerate(ranges):
            scenes.append(SceneRange(index=i, start=round(s.seconds, 3),
                                     end=round(e.seconds, 3)))
    scenes = _subdivide(scenes)
    return ScenesArtifact(detector="pyscenedetect.ContentDetector+subdivide",
                          threshold=THRESHOLD, scenes=scenes)
