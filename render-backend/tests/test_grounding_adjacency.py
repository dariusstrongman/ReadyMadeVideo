"""Approved grounding loosening (2026-08-05): evidence-ADJACENT vocabulary.

Morphological variants of catalog words and media meta-language are not
fabrication; invented entities still are. The editorial_label branch and the
verbatim-quote rule are intentionally untouched — these tests pin all three
properties so the loosening can never silently widen.
"""
import os

os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "fake-service-key")
os.environ["WORKER_ENABLED"] = "0"

from app.pipeline import editorial_planner as ep  # noqa: E402
from app.pipeline.schemas import Segment  # noqa: E402


def _seg(action="crew inspects the water damage in the pantry",
         transcript="we finished the inspection"):
    return Segment(segmentId="seg-1", assetId="a1", sourceStart=0.0,
                   sourceEnd=8.0, action=action, shotType="wide",
                   location="Dallas", transcript=transcript)


def _fact(text, quote="crew inspects the water damage", source="segment_metadata"):
    return ep.GroundedText(text=text, claimType="fact", evidence=[
        ep.EvidenceRef(sourceType=source, segmentId="seg-1", quoteOrValue=quote)])


def _violations(item, segs=None):
    segs = segs or [_seg()]
    by_id = {s.segmentId: s for s in segs}
    pool = ep._catalog_pool(segs, {})
    return ep._grounded_text_violations(item, by_id, pool, {}, "test")


class TestAdjacencyPasses:
    def test_morphological_variant_of_catalog_word(self):
        # catalog says "inspects"/"inspection"; claim says "inspecting"
        assert _violations(_fact("crew inspecting the water damage")) == []

    def test_hyphenated_compound_from_catalog_words(self):
        # catalog says "water damage"; claim says "water-damaged"
        assert _violations(_fact("the water-damaged pantry")) == []

    def test_media_meta_language(self):
        assert _violations(_fact("the video shows the crew inspects water damage",
                                 quote="crew inspects the water damage")) == []

    def test_plural_and_possessive(self):
        assert _violations(_fact("inspections of the pantry",
                                 quote="crew inspects the water damage in the pantry")) == []


class TestFabricationStillFails:
    def test_invented_entity_is_rejected(self):
        out = _violations(_fact("crew inspects the flamingo enclosure",
                                quote="crew inspects the water damage"))
        assert any("unsupported factual content" in v and "flamingo" in v
                   for v in out)

    def test_paraphrased_quote_is_still_rejected(self):
        out = _violations(_fact("crew inspects the water damage",
                                quote="the team looked at the moisture issue"))
        assert any("quote is not present" in v for v in out)

    def test_invented_number_is_rejected(self):
        # note: 1-2 char tokens were never checked here (pre-existing
        # _content_tokens length filter); a 3+ digit invention is
        out = _violations(_fact("crew inspects 470 water damages",
                                quote="crew inspects the water damage"))
        assert any("unsupported factual content" in v for v in out)


class TestLabelBranchUntouched:
    def test_catalog_words_still_rejected_in_labels(self):
        item = ep.GroundedText(text="The Water Damage", claimType="editorial_label",
                               evidence=[])
        out = _violations(item)
        assert out          # catalog vocabulary never passes as a label

    def test_structural_label_still_passes(self):
        item = ep.GroundedText(text="The Setup", claimType="editorial_label",
                               evidence=[])
        assert _violations(item) == []
