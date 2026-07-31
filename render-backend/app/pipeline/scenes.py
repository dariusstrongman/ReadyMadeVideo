"""Stage 3a: shot/scene boundary detection via PySceneDetect (ContentDetector)."""
from __future__ import annotations

from scenedetect import ContentDetector, SceneManager, open_video

from .schemas import SceneRange, ScenesArtifact

THRESHOLD = 27.0
MIN_SCENE_LEN_S = 0.6


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
    return ScenesArtifact(detector="pyscenedetect.ContentDetector",
                          threshold=THRESHOLD, scenes=scenes)
