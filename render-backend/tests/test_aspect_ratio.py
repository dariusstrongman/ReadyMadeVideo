"""Output frame shape: a project's aspect_ratio must reach the rendered frame.

Regression cover for vertical footage being letterboxed into 1920x1080: the
New Project wizard collected a destination (TikTok/Reels) and discarded it, so
autoedit always ran with platform="horizontal".
"""
import subprocess

import pytest

from app.pipeline.autoedit import ASPECTS, dims_for_aspect
from app.renderer2 import FFMPEG, FINAL, PREVIEW, _dims, compile_timeline, render_timeline


class TestDimsForAspect:
    def test_vertical_is_portrait(self):
        w, h, platform = dims_for_aspect("9:16")
        assert (w, h) == (1080, 1920)
        assert h > w
        assert platform == "vertical"

    def test_widescreen_is_landscape(self):
        w, h, platform = dims_for_aspect("16:9")
        assert (w, h) == (1920, 1080)
        assert w > h
        assert platform == "horizontal"

    def test_square_is_equal_sided(self):
        w, h, _ = dims_for_aspect("1:1")
        assert w == h == 1080

    def test_unknown_and_missing_fall_back_to_widescreen(self):
        # Existing projects have no aspect_ratio; they were rendered 16:9, so
        # that is the only safe default.
        assert dims_for_aspect(None) == ASPECTS["16:9"]
        assert dims_for_aspect("") == ASPECTS["16:9"]
        assert dims_for_aspect("21:9") == ASPECTS["16:9"]

    def test_square_plans_as_vertical(self):
        assert dims_for_aspect("1:1")[2] == "vertical"


class TestRendererHonoursShape:
    """_dims maps timeline orientation onto a profile's long/short edges."""

    def _tl(self, aspect):
        w, h, _ = dims_for_aspect(aspect)
        return {"width": w, "height": h}

    def test_vertical_timeline_renders_portrait(self):
        assert _dims(self._tl("9:16"), FINAL) == (1080, 1920)
        assert _dims(self._tl("9:16"), PREVIEW) == (360, 640)

    def test_widescreen_timeline_renders_landscape(self):
        assert _dims(self._tl("16:9"), FINAL) == (1920, 1080)
        assert _dims(self._tl("16:9"), PREVIEW) == (640, 360)

    def test_square_timeline_renders_square(self):
        # Before square was handled it fell through to the landscape branch and
        # a 1:1 timeline came out 1920x1080.
        assert _dims(self._tl("1:1"), FINAL) == (1080, 1080)
        assert _dims(self._tl("1:1"), PREVIEW) == (360, 360)

    def test_missing_dimensions_default_to_landscape(self):
        assert _dims({}, FINAL) == (1920, 1080)


class TestBuiltTimelineCarriesShape:
    def test_build_timeline_uses_explicit_dimensions(self):
        from app.pipeline.builder import build_timeline

        class _Blueprint:
            platform = "horizontal"      # deliberately contradicts the request
            beats = []
            templateId = "fitness_v1"
            brief = "test"

        class _Selection:
            beats = []

        w, h, _ = dims_for_aspect("9:16")
        tl = build_timeline(_Blueprint(), _Selection(), width=w, height=h)
        # Explicit dimensions must win over the blueprint's platform, which is
        # how a vertical project survives a horizontally-planned blueprint.
        assert (tl["width"], tl["height"]) == (1080, 1920)


def _src(factory, name, size):
    p = factory.mktemp("ar") / name
    subprocess.run(
        [FFMPEG, "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=30:duration=3",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
         "-shortest", str(p)], check=True)
    return str(p)


@pytest.fixture(scope="module")
def land(tmp_path_factory):
    return _src(tmp_path_factory, "land.mp4", "1920x1080")


@pytest.fixture(scope="module")
def vert(tmp_path_factory):
    return _src(tmp_path_factory, "vert.mp4", "1080x1920")


class TestFrameIsFilledNotLetterboxed:
    """A 16:9 phone clip delivered to TikTok used to arrive with black bars
    over 56% of the screen — every other improvement is invisible next to
    that. Mismatched sources are scaled up and centre-cropped instead.
    """

    def _graph(self, src, tmp_path, clip=None):
        c = {"id": "c1", "assetId": "A", "sourceStart": 0.0, "sourceEnd": 2.0}
        c.update(clip or {})
        tl = {"version": 1, "width": 1080, "height": 1920, "fps": 30,
              "tracks": [{"type": "video", "clips": [c]}]}
        cmd = compile_timeline(tl, {"A": src}, str(tmp_path / "o.mp4"),
                               profile="final").cmd
        return cmd[cmd.index("-filter_complex") + 1]

    def test_landscape_source_fills_vertical_frame(self, land, tmp_path):
        g = self._graph(land, tmp_path)
        assert "force_original_aspect_ratio=increase" in g
        assert "pad=" not in g

    def test_matching_source_is_left_alone(self, vert, tmp_path):
        # Already 9:16: nothing to crop, and cropping anyway would throw away
        # picture for no reason.
        g = self._graph(vert, tmp_path)
        assert "force_original_aspect_ratio=decrease" in g
        assert "pad=" in g

    def test_punch_in_crop_is_emitted_and_animates_position(self, vert, tmp_path):
        g = self._graph(vert, tmp_path, {
            "crop": {"width": 0.6, "height": 0.6, "x": 0.1, "y": 0.1,
                     "endX": 0.3, "endY": 0.2}})
        assert "crop=w=" in g
        # w/h must NOT vary with time — ffmpeg evaluates the window size once
        # at init and errors outright on a time-varying expression there.
        w_expr = g.split("crop=w='")[1].split("'")[0]
        h_expr = g.split(":h='")[1].split("'")[0]
        assert "min(1,max(0,t/" not in w_expr
        assert "min(1,max(0,t/" not in h_expr
        # x/y do drift, which is where the movement comes from.
        x_expr = g.split(":x='")[1].split("'")[0]
        assert "min(1,max(0,t/2.0000))" in x_expr

    def test_crop_window_is_clamped_inside_the_frame(self, vert, tmp_path):
        # x=0.9 with a 0.6-wide window would run off the right edge.
        g = self._graph(vert, tmp_path, {
            "crop": {"width": 0.6, "height": 0.6, "x": 0.9, "y": 0.9}})
        x_expr = g.split("crop=w='")[1]
        assert "iw*0.4000" in x_expr and "ih*0.4000" in x_expr

    def test_hostile_crop_values_cannot_inject_filter_text(self, vert, tmp_path):
        g = self._graph(vert, tmp_path, {
            "crop": {"width": "0.5;drawbox=t=fill", "height": None, "x": "nan"}})
        assert "drawbox" not in g

    def test_landscape_source_renders_without_black_bars(self, land, tmp_path):
        out = tmp_path / "filled.mp4"
        tl = {"version": 1, "width": 1080, "height": 1920, "fps": 30,
              "tracks": [{"type": "video", "clips": [
                  {"id": "c1", "assetId": "A",
                   "sourceStart": 0.0, "sourceEnd": 2.0}]}]}
        render_timeline(tl, {"A": land}, str(out), profile="final")
        # Decode the top strip, where a letterbox bar would be, and check it
        # carries real picture rather than flat black.
        strip = subprocess.run(
            [FFMPEG, "-v", "error", "-ss", "1", "-i", str(out), "-frames:v", "1",
             "-vf", "crop=1080:200:0:0", "-pix_fmt", "gray",
             "-f", "rawvideo", "-"], capture_output=True).stdout
        assert strip, "no frame decoded"
        mean = sum(strip) / len(strip)
        assert mean > 20, f"top of frame is a black bar (mean luma {mean:.1f})"


class TestFractionalFps:
    """23.976/29.97 sources: the editor document and renderer must carry the
    real rate — int typing rejected every NTSC-rate V2 candidate at
    editor/start, and int() truncation ran renders 4% slow."""

    def test_editor_document_accepts_ntsc_rates(self):
        """Mirrors the exact production path that failed: a V2 candidate cut
        from 23.976 fps sources -> document_from_candidate -> EditorDocument."""
        from app.product_editor import EditorDocument, document_from_candidate
        candidate = {
            "id": "746aef3f-4772-4012-832a-438846b084f7",
            "manifest": {"pictureTimeline": {
                "width": 1080, "height": 1920, "fps": 23.976, "duration": 25.0,
                "tracks": [{"id": "v", "type": "video", "clips": [
                    {"id": "c1", "assetId": "a7a7a7a7-0000-4000-8000-000000000001",
                     "sourceStart": 0.0, "sourceEnd": 25.0,
                     "timelineStart": 0.0, "timelineEnd": 25.0,
                     "speed": 1.0, "volume": 1.0}]}]},
                "sourceAssetIds": ["a7a7a7a7-0000-4000-8000-000000000001"]},
        }
        doc = document_from_candidate(
            "018e7930-dddd-4a4c-bd3c-b1a161aeabbe", candidate,
            {"a7a7a7a7-0000-4000-8000-000000000001": 30.0})
        validated = EditorDocument(**doc)
        assert validated.fps == 23.976

    def test_renderer_keeps_fractional_rate(self):
        import inspect
        from app import renderer2
        src = inspect.getsource(renderer2.render_timeline)
        assert "int(timeline.get(\"fps\"" not in src
