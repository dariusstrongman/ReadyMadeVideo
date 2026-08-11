"""Output Intelligence engine — deterministic adversarial coverage.

Every case here is one of the directive's mandatory adversarial scenarios,
run against the pure engine with synthetic catalogs. No network, no DB.
"""
import pytest

from app.pipeline import output_intelligence as oi
from app.pipeline.picture_edit_v2 import catalog_hash as pe2_catalog_hash
from app.pipeline.schemas import Segment


def seg(i, asset="a1", start=0.0, end=10.0, uses=(), problems=(),
        dup=None, location="gym", subjects=("athlete",), action="lifts weights",
        speech=(), motion_peaks=(), focus=0.8, stability=0.8, audio=0.6):
    return Segment(
        segmentId=f"s{i}", assetId=asset, sourceStart=start, sourceEnd=end,
        storyUses=list(uses), problems=list(problems), duplicateGroupId=dup,
        location=location, subjects=list(subjects), action=action,
        speechSpans=[{"start": a, "end": b, "text": t} for a, b, t in speech],
        motionPeaks=list(motion_peaks), focusScore=focus,
        stabilityScore=stability, audioScore=audio)


def contiguous(n, asset="a1", each=10.0, start=0.0, **kw):
    """n back-to-back usable segments on one asset."""
    out, t = [], start
    for i in range(n):
        out.append(seg(f"{asset}-{i}", asset=asset, start=t, end=t + each, **kw))
        t += each
    return out


# ---------------------------------------------------------------- identity
def test_catalog_hash_matches_picture_edit_engine():
    """One project, ONE catalog identity: the recommendation must go stale
    exactly when the picture-edit engine would consider the catalog changed."""
    segs = contiguous(5)
    assert oi.catalog_hash(segs) == pe2_catalog_hash(segs)


# ---------------------------------------------------------------- inventory
def test_case5_usable_not_raw_60min_with_8_usable():
    """60 raw minutes, only 8 usable — feasibility runs off the 8."""
    bad = contiguous(52, each=60.0, problems=("mostly black frame",))
    good = [seg(f"g{i}", start=4000 + i * 60, end=4060 + i * 60,
                uses=("hook",) if i == 0 else ("build",)) for i in range(8)]
    inv = oi.build_inventory(bad + good)
    assert inv.raw_seconds == pytest.approx(3600, abs=1)
    assert inv.usable_seconds == pytest.approx(480, abs=1)
    r = oi.check_feasibility({"kind": "long_form", "durationTargetS": 600},
                             bad + good, inv)
    assert r.verdict == oi.IMPOSSIBLE
    assert r.reasons[0]["code"] == "duration_exceeds_usable"


def test_case11_duplicates_do_not_inflate_usable_minutes():
    a = contiguous(4, each=30.0)
    dupes = [seg(f"d{i}", start=200 + i * 30, end=230 + i * 30, dup="grp-1")
             for i in range(3)]
    inv = oi.build_inventory(a + dupes)
    # three copies count once: 4*30 + 30
    assert inv.usable_seconds == pytest.approx(150, abs=0.1)
    assert inv.duplicate_groups == 1
    assert sum("duplicate of" in e["reason"] for e in inv.excluded) == 2


def test_case12_mostly_black_material_is_excluded_with_reasons():
    segs = contiguous(3, problems=("mostly black, unusable",))
    inv = oi.build_inventory(segs)
    assert inv.usable_seconds == 0
    assert len(inv.excluded) == 3
    r = oi.check_feasibility({"kind": "short_form", "quantity": 1}, segs, inv)
    assert r.verdict == oi.IMPOSSIBLE
    assert r.reasons[0]["code"] == "no_usable_footage"


def test_case7_independent_stories_are_separated():
    """Two locations, disjoint subjects, big gaps => two stories."""
    a = contiguous(8, each=30.0, location="kitchen", subjects=("chef",))
    b = contiguous(8, asset="a2", each=30.0, location="garage",
                   subjects=("mechanic",), action="repairs engine")
    inv = oi.build_inventory(a + b)
    assert len(inv.stories) == 2
    longs = oi.assess_long_form(a + b, inv)
    # neither story dominates => separate long-form per story, never one forced
    assert len(longs) == 2
    assert {o.storyId for o in longs} == {"story-1", "story-2"}


# ---------------------------------------------------------------- feasibility
def test_case1_30s_source_cannot_become_10_minutes():
    segs = [seg(1, end=30.0, uses=("hook", "completion"))]
    r = oi.check_feasibility({"kind": "long_form", "durationTargetS": 600}, segs)
    assert r.verdict == oi.IMPOSSIBLE
    assert r.reasons[0]["code"] == "duration_exceeds_usable"
    assert r.alternative is not None            # nearest honest option offered


def test_case10_one_short_clip_recommends_one_short():
    segs = [seg(1, end=31.0, uses=("hook", "completion"))]
    rec = oi.recommend(segs)
    assert rec.recommendedKey == "shorts_only"
    assert len(rec.packages[0].deliverables) == 1
    assert rec.packages[0].deliverables[0].format == "short_form"


def test_case2_45min_speech_dominant_recommends_long_form_plus_shorts():
    """A coherent podcast: one location, speech everywhere, hooks + payoff."""
    segs = []
    t = 0.0
    for i in range(45):
        uses = ("hook",) if i in (0, 10, 25) else (
            ("completion",) if i in (11, 26, 44) else ("build",))
        segs.append(seg(i, start=t, end=t + 60, uses=uses,
                        location="studio", subjects=("host", "guest"),
                        action="discusses the topic",
                        speech=((t + 1, t + 59, "…"),)))
        t += 60
    rec = oi.recommend(segs)
    assert rec.inventory.speech_fraction > 0.9
    assert rec.recommendedKey == "combo"
    longs = [d for d in rec.packages[0].deliverables if d.format == "long_form"]
    assert len(longs) == 1 and longs[0].purpose == "interview"
    # speech-led compression is gentle: target well above the action formula
    assert longs[0].recommendedDurationS >= 45 * 60 * 0.5


def test_case3_podcast_cannot_yield_20_shorts_alternative_offered():
    segs = []
    t = 0.0
    for i in range(45):
        uses = ("hook",) if i in (0, 10, 25) else ("build",)
        segs.append(seg(i, start=t, end=t + 60, uses=uses, location="studio",
                        speech=((t + 1, t + 59, "…"),)))
        t += 60
    inv = oi.build_inventory(segs)
    found = oi.discover_shorts(segs, inv)
    assert 0 < len(found) < 20
    r = oi.check_feasibility({"kind": "short_form", "quantity": 20},
                             segs, inv, shorts=found)
    assert r.verdict == oi.NOT_RECOMMENDED
    assert r.reasons[0]["code"] == "quantity_exceeds_moments"
    assert r.alternative == {"kind": "short_form", "quantity": len(found)}


def test_case4_only_3_good_moments_never_inflated_to_8():
    base = contiguous(6, each=20.0, uses=())        # no hooks — filler
    hooks = [seg(f"h{i}", start=200 + i * 40, end=220 + i * 40,
                 uses=("hook", "completion"), action=f"distinct trick {i}",
                 location=f"spot-{i}") for i in range(3)]
    segs = base + hooks
    inv = oi.build_inventory(segs)
    found = oi.discover_shorts(segs, inv)
    assert len(found) == 3
    r = oi.check_feasibility({"kind": "short_form", "quantity": 8},
                             segs, inv, shorts=found)
    assert r.verdict == oi.NOT_RECOMMENDED
    assert r.alternative["quantity"] == 3


def test_case25_and_24_user_quantity_bounds():
    """More than supportable => rejected with alternative; fewer => fine."""
    segs = contiguous(4, each=25.0, uses=("hook", "completion"))
    inv = oi.build_inventory(segs)
    found = oi.discover_shorts(segs, inv)
    assert found
    more = oi.check_feasibility({"kind": "short_form",
                                 "quantity": len(found) + 5}, segs, inv,
                                shorts=found)
    assert more.verdict == oi.NOT_RECOMMENDED
    fewer = oi.check_feasibility({"kind": "short_form", "quantity": 1},
                                 segs, inv, shorts=found)
    assert fewer.verdict == oi.SUPPORTED


def test_case29_no_hook_no_short_is_offered():
    segs = contiguous(5, each=20.0, uses=("build",))
    inv = oi.build_inventory(segs)
    assert oi.discover_shorts(segs, inv) == []
    r = oi.check_feasibility({"kind": "short_form", "quantity": 1}, segs, inv)
    assert r.verdict == oi.IMPOSSIBLE
    assert r.reasons[0]["code"] == "no_standalone_moments"


def test_case30_overlapping_candidates_deduplicate():
    """Two hook seeds inside the same 30s of source => one short, not two."""
    segs = [
        seg(1, start=0, end=15, uses=("hook",)),
        seg(2, start=15, end=30, uses=("hook", "completion")),
        seg(3, start=100, end=130, uses=("hook", "completion"),
            action="different moment", location="street"),
    ]
    inv = oi.build_inventory(segs)
    found = oi.discover_shorts(segs, inv)
    ranges = [list(o.sourceRange.values())[0] for o in found]
    for i, a in enumerate(ranges):
        for b in ranges[i + 1:]:
            inter = min(a[1], b[1]) - max(a[0], b[0])
            assert inter <= 0.5 * min(a[1] - a[0], b[1] - b[0])


def test_case9_no_speech_montage_still_offers_highlights():
    segs = contiguous(10, each=15.0, uses=("peak",), motion_peaks=(5.0,),
                      speech=())
    rec = oi.recommend(segs)
    assert rec.packages, "montage footage must still yield offers"
    kinds = {d.format for p in rec.packages for d in p.deliverables}
    assert "short_form" in kinds
    assert rec.inventory.speech_fraction == 0.0


def test_case6_several_unrelated_clips_do_not_force_one_long_form():
    segs = []
    for i in range(4):
        segs += contiguous(2, asset=f"clip{i}", each=15.0,
                           location=f"place-{i}", subjects=(f"person-{i}",),
                           start=0.0)
    inv = oi.build_inventory(segs)
    longs = oi.assess_long_form(segs, inv)
    assert longs == []                     # 30s pockets are not long-form
    r = oi.check_feasibility({"kind": "long_form", "durationTargetS": 100}, segs, inv)
    assert r.verdict == oi.NOT_RECOMMENDED


def test_dialogue_safe_edges_penalized():
    """A hook window that starts mid-sentence scores below one that doesn't."""
    clean = [seg(1, start=0, end=20, uses=("hook", "completion"),
                 speech=((2.0, 18.0, "full sentence"),))]
    cut = [seg(2, start=0, end=20, uses=("hook", "completion"),
               speech=((-5.0, 10.0, "sentence started earlier"),))]
    inv_c = oi.build_inventory(clean)
    inv_x = oi.build_inventory(cut)
    a = oi.discover_shorts(clean, inv_c)
    b = oi.discover_shorts(cut, inv_x)
    assert a and b
    assert a[0].confidence > b[0].confidence
    assert any("mid-sentence" in lim for lim in b[0].limitations)


def test_selection_counts_shorts_across_items():
    """Five one-short items compete for the same moments."""
    segs = contiguous(3, each=25.0, uses=("hook", "completion"))
    inv = oi.build_inventory(segs)
    found = oi.discover_shorts(segs, inv)
    sel = [{"kind": "short_form", "quantity": 1}] * (len(found) + 2)
    results = oi.check_selection(sel, segs)
    assert all(r.verdict == oi.NOT_RECOMMENDED for r in results)


def test_recommendation_is_deterministic_and_versioned():
    segs = contiguous(12, each=30.0, uses=("hook",))
    a, b = oi.recommend(segs), oi.recommend(segs)
    assert a.to_json() == b.to_json()
    assert a.engineVersion == oi.ENGINE_VERSION
    assert a.catalogHash == b.catalogHash
