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


class TestTimelineArithmeticNormalization:
    """Bookkeeping is code's job; creative choices stay the model's."""

    def _plan(self, entries):
        return {"timeline": entries, "plannedDurationSeconds": 999.0}

    def _segs(self):
        return [Segment(segmentId="seg-1", assetId="a1", sourceStart=10.0,
                        sourceEnd=20.0, action="crew works", shotType="wide",
                        location="Dallas")]

    def test_small_overshoot_is_clamped_and_cursor_rebuilt(self):
        raw = self._plan([
            {"segmentId": "seg-1", "sourceIn": 9.6, "sourceOut": 15.0,
             "timelineIn": 0.3, "timelineOut": 99.0},
            {"segmentId": "seg-1", "sourceIn": 15.0, "sourceOut": 20.5,
             "timelineIn": 7.7, "timelineOut": 8.0}])
        ep._normalize_timeline_arithmetic(raw, self._segs())
        t0, t1 = raw["timeline"]
        assert t0["sourceIn"] == 10.0            # clamped up (0.4s slip)
        assert t1["sourceOut"] == 20.0           # clamped down (0.5s slip)
        assert (t0["timelineIn"], t0["timelineOut"]) == (0.0, 5.0)
        assert (t1["timelineIn"], t1["timelineOut"]) == (5.0, 10.0)
        assert raw["plannedDurationSeconds"] == 10.0

    def test_large_overshoot_clamps_to_real_footage(self):
        """2026-08-05 final: unconditional clamp — the model chose the
        segment; the code fits the trim to footage that exists."""
        raw = self._plan([
            {"segmentId": "seg-1", "sourceIn": 5.0, "sourceOut": 15.0,
             "timelineIn": 0.0, "timelineOut": 10.0}])
        ep._normalize_timeline_arithmetic(raw, self._segs())
        assert raw["timeline"][0]["sourceIn"] == 10.0
        assert raw["timeline"][0]["sourceOut"] == 15.0

    def test_clamp_that_would_gut_the_trim_is_left_alone(self):
        # segment 10-20; trim 19.9-25.0 would clamp to 0.1s — honest reject
        raw = self._plan([
            {"segmentId": "seg-1", "sourceIn": 19.9, "sourceOut": 25.0,
             "timelineIn": 0.0, "timelineOut": 5.1}])
        ep._normalize_timeline_arithmetic(raw, self._segs())
        assert raw["timeline"][0]["sourceOut"] == 25.0

    def test_unknown_segment_still_gets_cursor_math(self):
        raw = self._plan([
            {"segmentId": "ghost", "sourceIn": 1.0, "sourceOut": 3.0,
             "timelineIn": 9.0, "timelineOut": 9.5}])
        ep._normalize_timeline_arithmetic(raw, self._segs())
        assert (raw["timeline"][0]["timelineIn"],
                raw["timeline"][0]["timelineOut"]) == (0.0, 2.0)


class TestQuoteTokenization:
    def test_leading_apostrophe_from_quoted_speech_matches(self):
        # catalog says "push"; caption text quotes "'push through'"
        out = _violations(_fact("crew inspects the water damage 'push through'",
                                quote="crew inspects the water damage"),
                          segs=[_seg(action="crew inspects the water damage",
                                     transcript="push through the pantry")])
        assert out == []


class TestPlanNormalizationConsistency:
    """Bookkeeping the code settles so a good plan is not rejected over it."""

    def _segs(self):
        return [Segment(segmentId="seg-1", assetId="a1", sourceStart=0.0,
                        sourceEnd=20.0, action="crew flips a tire",
                        shotType="wide", location="field",
                        transcript="push through it"),
                Segment(segmentId="seg-2", assetId="a1", sourceStart=20.0,
                        sourceEnd=40.0, action="crew drags a tire",
                        shotType="wide", location="field")]

    def _raw(self, **over):
        raw = {
            "timeline": [
                {"segmentId": "seg-1", "sourceIn": 2.0, "sourceOut": 6.0,
                 "timelineIn": 0.0, "timelineOut": 4.0},
                {"segmentId": "seg-2", "sourceIn": 22.0, "sourceOut": 26.0,
                 "timelineIn": 4.0, "timelineOut": 8.0}],
            "hook": {"segmentId": "seg-2", "sourceIn": 22.0, "sourceOut": 24.0,
                     "durationSeconds": 2.0},
            # seg-3 is REAL catalog footage the timeline did not use (dropped);
            # seg-99 is INVENTED and must survive so the validator rejects it.
            "audio": {"naturalSoundSegmentIds": ["seg-1", "seg-3", "seg-99"],
                      "jCutSegmentIds": [], "lCutSegmentIds": []},
            "plannedDurationSeconds": 99.0,
        }
        raw.update(over)
        return raw

    def test_hook_is_bound_to_the_first_planned_cut(self):
        raw = self._raw()
        ep._normalize_timeline_arithmetic(raw, self._segs())
        assert raw["hook"]["segmentId"] == "seg-1"          # was seg-2
        assert raw["hook"]["sourceIn"] == 2.0               # matches cut 1
        assert raw["hook"]["durationSeconds"] == 2.0        # cap preserved
        assert raw["hook"]["sourceOut"] == 4.0

    def test_real_but_unplanned_refs_dropped_invented_kept_fatal(self):
        raw = self._raw()
        segs = self._segs() + [Segment(
            segmentId="seg-3", assetId="a1", sourceStart=40.0, sourceEnd=60.0,
            action="crew rests", shotType="wide", location="field")]
        ep._normalize_timeline_arithmetic(raw, segs)
        refs = raw["audio"]["naturalSoundSegmentIds"]
        assert "seg-3" not in refs      # real but unused -> bookkeeping, dropped
        assert "seg-99" in refs         # invented -> stays, validator rejects
        assert refs[0] == "seg-1"

    def test_ungrounded_caption_dropped_grounded_kept(self):
        raw = self._raw(captions=[
            {"claimType": "fact", "text": "push through it",
             "evidence": [{"sourceType": "transcript", "segmentId": "seg-1",
                           "quoteOrValue": "push through it"}]},
            {"claimType": "fact", "text": "invented line",
             "evidence": [{"sourceType": "transcript", "segmentId": "seg-1",
                           "quoteOrValue": "this was never said"}]}])
        ep._normalize_timeline_arithmetic(raw, self._segs())
        assert len(raw["captions"]) == 1
        assert raw["captions"][0]["text"] == "push through it"


class TestIrregularMorphologyGrounding:
    """Inflection of a catalog word is not fabrication (2026-08-06): the real
    pov run rejected 'finding' against footage that said 'found' — same verb,
    unreachable by suffix-stripping. Inventions must still fail."""

    def _sets(self, text):
        return ep._allowed_token_sets(text)

    def test_irregular_verb_grounds_in_both_directions(self):
        toks, stems = self._sets("we found water damage here")
        assert ep._token_supported("finding", toks, stems)
        assert ep._token_supported("find", toks, stems)
        toks2, stems2 = self._sets("you will find water damage")
        assert ep._token_supported("found", toks2, stems2)

    def test_tion_family_lands_on_one_root(self):
        toks, stems = self._sets("during the inspection we saw rot")
        assert ep._token_supported("inspections", toks, stems)
        toks2, stems2 = self._sets("several inspections happened")
        assert ep._token_supported("inspection", toks2, stems2)

    def test_trailing_e_base_meets_stripped_catalog_stem(self):
        toks, stems = self._sets("the job was completed on time")
        assert ep._token_supported("complete", toks, stems)

    def test_inventions_still_fail(self):
        toks, stems = self._sets("we found water damage during the inspection")
        for bad in ("zorblax", "warranty", "certified"):
            assert not ep._token_supported(bad, toks, stems), bad


class TestWarningNormalization:
    """technicalWarnings are advisory metadata that never reaches the video:
    one with unverifiable evidence is dropped, not fatal (2026-08-06)."""

    _segs = TestPlanNormalizationConsistency._segs
    _raw = TestPlanNormalizationConsistency._raw

    def test_ungrounded_warning_dropped_grounded_and_evidence_free_kept(self):
        raw = self._raw()
        raw["technicalWarnings"] = [
            {"claimType": "fact", "text": "speech says push through it",
             "evidence": [{"sourceType": "transcript", "segmentId": "seg-1",
                           "quoteOrValue": "push through it"}]},
            {"claimType": "fact", "text": "camera reports 4k capture",
             "evidence": [{"sourceType": "segment_metadata",
                           "segmentId": "seg-1",
                           "quoteOrValue": "this metadata does not exist"}]},
            {"claimType": "fact", "text": "no evidence list",
             "evidence": []},
        ]
        ep._normalize_timeline_arithmetic(raw, self._segs())
        texts = [w["text"] for w in raw["technicalWarnings"]]
        assert "speech says push through it" in texts      # verified -> kept
        assert "camera reports 4k capture" not in texts    # fabricated -> gone
        assert "no evidence list" in texts                 # validator's call
