"""Output frame shape: a project's aspect_ratio must reach the rendered frame.

Regression cover for vertical footage being letterboxed into 1920x1080: the
New Project wizard collected a destination (TikTok/Reels) and discarded it, so
autoedit always ran with platform="horizontal".
"""
from app.pipeline.autoedit import ASPECTS, dims_for_aspect
from app.renderer2 import FINAL, PREVIEW, _dims


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
