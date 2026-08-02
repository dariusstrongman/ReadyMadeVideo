#!/usr/bin/env python3
"""CI artifact generator: renders a real preview + final MP4 through the actual
multi-clip renderer from a generated fixture, so every PR uploads inspectable
video output. No cloud access required."""
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "render-backend"))
from app.renderer import FFMPEG  # noqa: E402
from app.renderer2 import render_timeline  # noqa: E402
from app.pipeline.visual_finishing import (  # noqa: E402
    BrandTemplate, CaptionPackage, ColorInstruction, ColorPackage,
    GraphicEvent, GraphicsPackage, PRESETS, NormalizedRegion,
    render_finishing_preview,
)

OUT = os.environ.get("CI_ARTIFACT_DIR", "ci-artifacts")
os.makedirs(OUT, exist_ok=True)

src = os.path.join(OUT, "fixture.mp4")
subprocess.run([FFMPEG, "-y", "-loglevel", "error",
                "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=10",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=10",
                "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac",
                "-shortest", src], check=True, timeout=180)

timeline = {
    "version": 1, "width": 1920, "height": 1080, "fps": 30, "duration": 8,
    "tracks": [
        {"id": "video-1", "type": "video", "clips": [
            {"id": "c1", "assetId": "fx", "sourceStart": 0.5, "sourceEnd": 3.5,
             "timelineStart": 2, "timelineEnd": 5, "volume": 1, "speed": 1},
            {"id": "c2", "assetId": "fx", "sourceStart": 5.0, "sourceEnd": 8.0,
             "timelineStart": 5, "timelineEnd": 8, "volume": 1, "speed": 1}]},
        {"id": "text-1", "type": "text", "clips": [
            {"id": "title-1", "role": "title_card", "text": "CI RENDER CHECK",
             "timelineStart": 0, "timelineEnd": 2, "fontSize": 72,
             "position": "center"},
            {"id": "cap-1", "role": "caption", "text": "real ffmpeg output",
             "timelineStart": 3, "timelineEnd": 6, "fontSize": 48,
             "position": "bottom"}]},
    ],
}
for profile in ("preview", "final"):
    r = render_timeline(timeline, {"fx": src},
                        os.path.join(OUT, f"{profile}.mp4"), profile=profile)
    print(f"{profile}: {r['width']}x{r['height']} {r['duration']:.1f}s "
          f"{r['size_bytes']} bytes")
graphics = GraphicsPackage(
    platform=PRESETS["9:16"], brandTemplate=BrandTemplate(
        templateId="ci-brand", name="CI Brand"), phraseBoundaries=[0, 4, 8],
    templateCatalog={"animated_title": {"animation": "slide"}},
    events=[GraphicEvent(
        eventId="ci-title", kind="animated_title", text="VISUAL FINISHING",
        startSeconds=.2, endSeconds=2.5,
        region=NormalizedRegion(x=.1, y=.1, width=.8, height=.12),
        animation="slide", phraseAligned=True, subjectOcclusionRisk=0,
    )], excludedDepartments=["specialized_critics", "tournament_selection"],
)
captions = CaptionPackage(groups=[], timingProvenance=[], evidenceDecisions=[])
color = ColorPackage(
    instructions=[ColorInstruction(
        clipId="ci", segmentId="ci", startSeconds=0, endSeconds=8,
        exposureEv=.05, temperatureShift=0,
        contrast=1.04, saturation=1.03, highlightCompression=.05,
        shadowLift=.02, lutPreset="neutral_social", confidence=1,
    )], normalizationTarget={"method": "ci_fixture"}, lutPreset="neutral_social",
)
finished = render_finishing_preview(
    os.path.join(OUT, "final.mp4"), os.path.join(OUT, "visual-finishing.mp4"),
    graphics, captions, color,
)
print(f"visual-finishing: {finished['width']}x{finished['height']} "
      f"{finished['durationSeconds']:.1f}s")
print("ci artifacts ->", OUT)
