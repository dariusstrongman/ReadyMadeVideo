"""Editorial Planner v1 — binding constraints, evidence grounding, execution-
ready schema, deterministic quality gate, job handler and customer endpoints.
The model call is always a stub: no network, no fabrication.
"""
import copy
import os
from uuid import uuid4

import pytest

os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "fake-service-key")
os.environ["WORKER_ENABLED"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from app import jobs, supa  # noqa: E402
from app.main import app  # noqa: E402
from app.pipeline import editorial_planner as ep  # noqa: E402
from app.pipeline.schemas import Segment  # noqa: E402
from tests.fake_supa import FakeSupabase, install  # noqa: E402


def _auth(t):
    return {"Authorization": f"Bearer {t}"}


def _segments(asset_id="asset-1"):
    shots = {1: "wide", 2: "medium", 3: "close"}
    return [Segment(segmentId=f"seg-{i}", assetId=asset_id,
                    sourceStart=0.0, sourceEnd=10.0,
                    action=f"crew works on step {i}", shotType=shots[i],
                    location="Dallas backyard" if i == 1 else "",
                    transcript="we finished the job" if i == 3 else None)
            for i in (1, 2, 3)]


def _option():
    scores = {k: 80 for k in ("hookStrength", "storyClarity", "payoffStrength",
                              "footageSupport", "emotionalInterest",
                              "visualVariety", "platformFit", "durationFit",
                              "originality")}
    return {"premise": "a real repair job", "viewerPromise": "see the result",
            "hook": "result first", "structure": "results_first",
            "payoff": "the finished work", "idealDurationSeconds": 12,
            "strengths": ["s"], "weaknesses": ["w"], "footageCoverage": "full",
            "retentionRisks": ["r"], "scores": scores}


def _self_assessment(**over):
    base = {"hook": 12, "storyClarity": 12, "flowContinuity": 8, "pacing": 8,
            "clipSelection": 8, "payoff": 8, "visualVariety": 4,
            "creativeTreatment": 8, "soundDesign": 4, "platformFit": 4,
            "durationCompliance": 4, "hardFailures": []}
    base.update(over)
    return base


def _valid_plan(music_available=False):
    """A grounded plan over _segments(): three contiguous cuts, 12 s total."""
    tl = []
    for i, seg in enumerate(("seg-1", "seg-2", "seg-3")):
        tl.append({"segmentId": seg, "assetId": "asset-1",
                   "sourceIn": 0.0, "sourceOut": 4.0,
                   "timelineIn": i * 4.0, "timelineOut": (i + 1) * 4.0,
                   "beat": ["hook", "process", "payoff"][i],
                   "reason": "advances the story", "addsNew": "new info",
                   "expectedViewerEffect": "curiosity"})
    return {
        "schemaVersion": 1,
        "storySentence": "This is a story about a repair job, where the crew "
                         "works, leading to a finished result.",
        "footageSummary": "three usable segments", "intendedAudience": "local",
        "viewerPromise": "see the result",
        "options": [_option(), _option(), _option()], "chosenOption": 0,
        "hook": {"segmentId": "seg-1", "sourceIn": 0.0, "sourceOut": 2.0,
                 "firstFrame": "result reveal", "audioCue": "natural sound",
                 "durationSeconds": 2.0, "transitionOut": "cut",
                 "curiosityCreated": "how did it get here",
                 "promiseToFulfill": "show the process"},
        "beats": [{"key": "hook", "purpose": "stop the scroll"},
                  {"key": "process", "purpose": "advance"},
                  {"key": "payoff", "purpose": "deliver"}],
        "timeline": tl,
        "pacing": [{"beat": "hook", "targetDurationSeconds": 4, "energy": 0.9},
                   {"beat": "process", "targetDurationSeconds": 4, "energy": 0.6},
                   {"beat": "payoff", "targetDurationSeconds": 4, "energy": 0.8}],
        "pacingRationale": "fast hook, steady middle, held payoff",
        "transitions": [{"fromSegmentId": "seg-1", "toSegmentId": "seg-2",
                         "type": "cut", "durationSeconds": 0,
                         "purpose": "action match"},
                        {"fromSegmentId": "seg-2", "toSegmentId": "seg-3",
                         "type": "cut", "durationSeconds": 0,
                         "purpose": "into the payoff"}],
        "transitionsRationale": "hard cuts on action",
        "speedRamps": [], "reframes": [], "captions": [], "graphics": [],
        "audio": {"musicAvailable": music_available,
                  "musicPlan": "one supplied cue under the montage"
                  if music_available else None,
                  "naturalSoundSegmentIds": ["seg-3"]},
        "audioTreatments": [{"segmentId": "seg-3", "gainDb": 0,
                             "preserveNaturalSound": True}],
        "colorStabilization": [{"segmentId": "seg-1"}],
        "plannedDurationSeconds": 12.0, "achievableDurationSeconds": None,
        "technicalWarnings": [], "retentionReview": [
            {"position": "mid", "risk": "repetition",
             "mitigation": "vary shot size"}],
        "modelSelfAssessment": _self_assessment(),
        "missingFootage": [],
        "render": {"width": 1080, "height": 1920, "fps": 30, "aspect": "9:16"},
        "status": "approved",
    }


def _gen(*plans):
    seq = list(plans)
    calls = {"n": 0, "parts": []}

    def generate(parts, schema):
        calls["parts"].append(parts)
        plan = seq[min(calls["n"], len(seq) - 1)]
        calls["n"] += 1
        return copy.deepcopy(plan)
    generate.calls = calls
    return generate


def _reject(plan, constraints=None, music=False, needle=None):
    with pytest.raises(ep.PlanRejected) as exc:
        ep.plan_editorial(_segments(), constraints or {}, music, _gen(plan),
                          max_attempts=1)
    flat = " | ".join(exc.value.violations_history[0])
    if needle:
        assert needle in flat, flat
    return flat


# ------------------------------------------------------------------ happy path
def test_valid_plan_approved_by_deterministic_gate():
    out = ep.plan_editorial(_segments(), {}, False, _gen(_valid_plan()))
    assert out["attempts"] == 1 and out["status"] == "approved"
    assert out["qualityScore"] == 100                 # deterministic, not model
    assert out["deterministicGate"]["passed"] is True
    assert out["deterministicGate"]["hardFailures"] == []
    assert out["plan"]["schemaVersion"] == 1


def test_schema_version_is_enforced():
    plan = _valid_plan()
    plan["schemaVersion"] = 2
    _reject(plan, needle="schema:")


# --------------------------------------------- blocker 2: binding constraints
def test_12s_plan_rejected_when_request_requires_45_to_60():
    flat = _reject(_valid_plan(),
                   constraints={"durationMin": 45, "durationMax": 60},
                   needle="REQUESTED duration range")
    assert "45" in flat and "60" in flat


def test_honest_shortfall_needs_achievable_duration_and_missing_shots():
    short = _valid_plan()
    short["status"] = "insufficient_footage"
    short["missingFootage"] = [{"description": "closing reaction shot",
                                "purpose": "payoff"}]
    _reject(short, constraints={"durationMin": 45, "durationMax": 60},
            needle="achievableDurationSeconds")

    short["achievableDurationSeconds"] = 12.0
    out = ep.plan_editorial(_segments(), {"durationMin": 45, "durationMax": 60},
                            False, _gen(short))
    assert out["status"] == "insufficient_footage"

    no_shots = copy.deepcopy(short)
    no_shots["missingFootage"] = []
    _reject(no_shots, constraints={"durationMin": 45, "durationMax": 60},
            needle="exact missing shots")


def test_model_cannot_redefine_platform_or_aspect():
    plan = _valid_plan()      # renders 9:16
    plan["render"] = {"width": 1920, "height": 1080, "fps": 30, "aspect": "16:9"}
    _reject(plan, constraints={"platform": "vertical"},
            needle="violates the requested platform/aspect")
    _reject(plan, constraints={"aspectRatio": "9:16"},
            needle="violates the requested platform/aspect")


def test_must_include_and_exclude_are_binding():
    _reject(_valid_plan(), constraints={"mustInclude": ["drone shot"]},
            needle="required moment 'drone shot' is not represented")
    _reject(_valid_plan(), constraints={"mustExclude": ["crew"]},
            needle="excluded moment 'crew' appears")


# --------------------------------------------- blocker 3: evidence grounding
def test_hook_source_out_of_range_rejected():
    plan = _valid_plan()
    plan["hook"].update(sourceIn=8.0, sourceOut=13.0, durationSeconds=5.0)
    _reject(plan, needle="hook")


def test_hook_must_link_to_first_timeline_cut():
    plan = _valid_plan()
    plan["hook"].update(sourceIn=1.5, sourceOut=3.5)   # first cut starts at 0.0
    _reject(plan, needle="not linked to the first timeline cut")


def test_fabricated_factual_caption_rejected_and_honest_one_accepted():
    plan = _valid_plan()
    plan["captions"] = [{"type": "factual", "text": "Customer Marcus paid 500",
                         "sourceSegmentId": "seg-3", "timelineStart": 8.0,
                         "timelineEnd": 11.0, "evidence": "we finished the job"}]
    flat = _reject(plan, needle="unsupported factual tokens")
    assert "Marcus" in flat and "500" in flat

    missing_src = _valid_plan()
    missing_src["captions"] = [{"type": "factual", "text": "we finished the job",
                                "sourceSegmentId": None, "timelineStart": 8.0,
                                "timelineEnd": 11.0,
                                "evidence": "we finished the job"}]
    _reject(missing_src, needle="no sourceSegmentId")

    bad_evidence = _valid_plan()
    bad_evidence["captions"] = [{"type": "factual", "text": "we finished the job",
                                 "sourceSegmentId": "seg-3", "timelineStart": 8.0,
                                 "timelineEnd": 11.0,
                                 "evidence": "customer was thrilled"}]
    _reject(bad_evidence, needle="evidence is not found")

    honest = _valid_plan()
    honest["captions"] = [{"type": "factual", "text": "we finished the job",
                           "sourceSegmentId": "seg-3", "timelineStart": 8.0,
                           "timelineEnd": 11.0, "evidence": "we finished the job"}]
    out = ep.plan_editorial(_segments(), {}, False, _gen(honest))
    assert out["status"] == "approved"


def test_editorial_label_may_be_creative_but_not_factual():
    cta = _valid_plan()
    cta["captions"] = [{"type": "cta", "text": "follow for the next job",
                        "sourceSegmentId": None, "timelineStart": 10.0,
                        "timelineEnd": 12.0, "evidence": "editorial"}]
    assert ep.plan_editorial(_segments(), {}, False,
                             _gen(cta))["status"] == "approved"

    implied_fact = _valid_plan()
    implied_fact["captions"] = [{"type": "editorial_label",
                                 "text": "Rated 5 stars in Plano",
                                 "sourceSegmentId": None, "timelineStart": 1.0,
                                 "timelineEnd": 3.0, "evidence": "editorial"}]
    _reject(implied_fact, needle="unsupported factual tokens")


def test_invented_audio_segment_ids_rejected():
    for field in ("naturalSoundSegmentIds", "jCutSegmentIds", "lCutSegmentIds"):
        plan = _valid_plan()
        plan["audio"][field] = ["seg-INVENTED"]
        _reject(plan, needle=f"audio.{field} references invented segment")
    unplanned = _valid_plan()
    unplanned["timeline"] = unplanned["timeline"][:2]   # seg-3 no longer planned
    unplanned["plannedDurationSeconds"] = 8.0
    unplanned["transitions"] = unplanned["transitions"][:1]
    unplanned["audioTreatments"] = []
    _reject(unplanned, needle="references unplanned segment 'seg-3'")


def test_musicless_plan_may_not_describe_music():
    wrong_flag = _valid_plan()
    wrong_flag["audio"]["musicAvailable"] = True
    wrong_flag["audio"]["musicPlan"] = "an upbeat cue"
    _reject(wrong_flag, music=False, needle="licensed-music availability")

    fabricated = _valid_plan()
    fabricated["audio"]["musicPlan"] = "energetic track at 120 BPM, licensed"
    _reject(fabricated, music=False, needle="musicPlan must be null")

    with_music = _valid_plan(music_available=True)
    out = ep.plan_editorial(_segments(), {}, True, _gen(with_music))
    assert out["status"] == "approved"
    licensed = _valid_plan(music_available=True)
    licensed["audio"]["musicPlan"] = "track licensed under ID 12345"
    _reject(licensed, music=True, needle="licensing metadata")


def test_story_claims_need_sources():
    plan = _valid_plan()
    plan["storySentence"] = ("This is a story about a job for Contoso, where "
                             "3 crews rebuild a deck, leading to a result.")
    flat = _reject(plan, needle="storySentence carries unsupported")
    assert "Contoso" in flat
    # a location that IS in the catalog (Dallas) is a supported claim
    grounded = _valid_plan()
    grounded["storySentence"] = ("This is a story about a repair in Dallas, "
                                 "where the crew works, leading to a result.")
    assert ep.plan_editorial(_segments(), {}, False,
                             _gen(grounded))["status"] == "approved"


# --------------------------------------- blocker 4: execution-ready structure
def test_transitions_must_join_adjacent_cuts():
    plan = _valid_plan()
    plan["transitions"] = [{"fromSegmentId": "seg-1", "toSegmentId": "seg-3",
                            "type": "whip", "durationSeconds": 0.3,
                            "purpose": "energy"}]
    _reject(plan, needle="does not join two adjacent timeline segments")


def test_speed_ramp_must_fit_inside_its_planned_cut():
    plan = _valid_plan()
    plan["speedRamps"] = [{"segmentId": "seg-2", "sourceStart": 0.0,
                           "sourceEnd": 8.0, "entrySpeed": 1, "peakSpeed": 3,
                           "exitSpeed": 1, "easing": "ease",
                           "narrativePurpose": "compress travel"}]
    _reject(plan, needle="outside the planned cut")
    plan["speedRamps"][0]["sourceEnd"] = 4.0
    assert ep.plan_editorial(_segments(), {}, False,
                             _gen(plan))["status"] == "approved"


def test_reframe_aspect_must_match_render_target():
    plan = _valid_plan()
    plan["reframes"] = [{"segmentId": "seg-1", "outputAspectRatio": "16:9",
                         "subjectTarget": "crew",
                         "startCrop": {"x": 0, "y": 0, "width": 1, "height": 1},
                         "endCrop": {"x": 0, "y": 0, "width": 1, "height": 1}}]
    _reject(plan, needle="does not match the render target")


def test_graphic_duration_must_match_its_timing():
    plan = _valid_plan()
    plan["graphics"] = [{"graphicType": "label", "text": "step one",
                         "timelineStart": 4.0, "timelineEnd": 6.0,
                         "durationSeconds": 5.0, "evidence": "editorial"}]
    _reject(plan, needle="durationSeconds does not match its timing")


def test_pacing_must_reference_real_beats():
    plan = _valid_plan()
    plan["pacing"].append({"beat": "imaginary", "targetDurationSeconds": 3,
                           "energy": 0.5})
    _reject(plan, needle="unknown beat 'imaginary'")


# --------------------------------------- blocker 5: deterministic quality gate
def test_model_perfect_self_score_cannot_rescue_a_broken_plan():
    plan = _valid_plan()
    plan["modelSelfAssessment"] = _self_assessment(
        hook=15, storyClarity=15, flowContinuity=10, pacing=10,
        clipSelection=10, payoff=10, visualVariety=5, creativeTreatment=10,
        soundDesign=5, platformFit=5, durationCompliance=5)   # claims 100
    plan["timeline"][1]["timelineIn"] = 5.5                    # broken contiguity
    flat = _reject(plan, needle="deterministic gate")
    assert "timeline_contiguous" in flat


def test_model_low_self_score_cannot_block_a_grounded_plan():
    plan = _valid_plan()
    plan["modelSelfAssessment"] = _self_assessment(
        hook=1, storyClarity=1, flowContinuity=1, pacing=1, clipSelection=1,
        payoff=1, visualVariety=1, creativeTreatment=1, soundDesign=1,
        platformFit=1, durationCompliance=1,
        hardFailures=["the model doubts itself"])              # claims ~11
    out = ep.plan_editorial(_segments(), {}, False, _gen(plan))
    assert out["status"] == "approved"                         # gate decides
    assert out["qualityScore"] == 100


def test_gate_produces_rule_level_results():
    plan = ep.EditorialPlan(**_valid_plan())
    gate = ep.deterministic_gate(plan, _segments(), {}, [])
    names = {r["rule"] for r in gate["rules"]}
    assert {"hook_grounded", "timeline_contiguous", "claims_grounded",
            "duration_compliant", "story_structure", "payoff_present",
            "music_grounded", "no_redundant_reuse", "visual_variety",
            "requested_treatments", "technical_warnings_surfaced"} <= names
    assert sum(r["weight"] for r in gate["rules"]) == 100


def test_gate_requires_surfaced_technical_warnings():
    segs = _segments()
    segs[1] = segs[1].model_copy(update={"problems": ["shaky footage"]})
    plan = ep.EditorialPlan(**_valid_plan())
    gate = ep.deterministic_gate(plan, segs, {}, [])
    rule = next(r for r in gate["rules"]
                if r["rule"] == "technical_warnings_surfaced")
    assert rule["passed"] is False                    # used seg-2, warned nothing


def test_revision_loop_feeds_deterministic_violations_back():
    bad = _valid_plan()
    bad["timeline"][1]["segmentId"] = "seg-INVENTED"
    gen = _gen(bad, _valid_plan())
    out = ep.plan_editorial(_segments(), {}, False, gen)
    assert out["attempts"] == 2
    assert "REJECTED" in gen.calls["parts"][1][-1]["text"]


def test_rejected_after_attempt_budget():
    bad = _valid_plan()
    bad["timeline"][0]["segmentId"] = "seg-NOPE"
    with pytest.raises(ep.PlanRejected) as exc:
        ep.plan_editorial(_segments(), {}, False, _gen(bad), max_attempts=3)
    assert len(exc.value.violations_history) == 3


# ---------------------------------------------------------------- job handler
def _setup_project(fake, status="draft_ready"):
    uid, token = fake.add_user("planner@example.com")
    project = fake.add_project(uid, "Plan Test", status=status)
    for s in _segments():
        fake.insert("segments", {"project_id": project["id"], "user_id": uid,
                                 "asset_id": "asset-1",
                                 "segment_key": s.segmentId,
                                 "source_start": s.sourceStart,
                                 "source_end": s.sourceEnd,
                                 "data": s.model_dump()})
    return uid, token, project


def test_invalid_job_kind_is_rejected_by_the_store(monkeypatch):
    fake = FakeSupabase()
    install(monkeypatch, fake)
    uid, _, project = _setup_project(fake)
    with pytest.raises(RuntimeError, match="kind_check"):
        jobs.enqueue_job(project["id"], uid, "made_up_kind", {})
    assert jobs.enqueue_job(project["id"], uid, "editorial_plan", {})["kind"] \
        == "editorial_plan"


def test_handler_persists_deterministic_result(monkeypatch):
    fake = FakeSupabase()
    install(monkeypatch, fake)
    uid, token, project = _setup_project(fake)
    monkeypatch.setattr(ep, "gemini_generate", _gen(_valid_plan()))
    jobs.enqueue_job(project["id"], uid, "editorial_plan", {"brief": "b"})
    jobs._run_job(jobs._claim_next())
    row = fake.select("pipeline_jobs", f"project_id=eq.{project['id']}")[0]
    assert row["status"] == "completed"
    assert row["artifacts"]["qualityScore"] == 100        # deterministic score
    plans = fake.select("editorial_plans", f"project_id=eq.{project['id']}")
    assert len(plans) == 1 and plans[0]["version"] == 1
    assert plans[0]["quality_score"] == 100
    assert plans[0]["validation"]["deterministicGate"]["passed"] is True
    assert plans[0]["plan"]["modelSelfAssessment"]["hook"] == 12   # advisory only


@pytest.mark.parametrize("prior", ["ready", "draft_ready"])
def test_status_preserved_on_success_failure_and_cancellation(monkeypatch, prior):
    fake = FakeSupabase()
    install(monkeypatch, fake)
    uid, token, project = _setup_project(fake, status=prior)

    # success
    monkeypatch.setattr(ep, "gemini_generate", _gen(_valid_plan()))
    jobs.enqueue_job(project["id"], uid, "editorial_plan", {})
    jobs._run_job(jobs._claim_next())
    assert fake.select("projects", f"id=eq.{project['id']}")[0]["status"] == prior

    # failure
    def explode(parts, schema):
        raise RuntimeError("model unavailable")
    monkeypatch.setattr(ep, "gemini_generate", explode)
    jobs.enqueue_job(project["id"], uid, "editorial_plan", {})
    jobs._run_job(jobs._claim_next())
    assert fake.select("projects", f"id=eq.{project['id']}")[0]["status"] == prior

    # cancellation at the before_plan_persist checkpoint
    plans_before = len(fake.select("editorial_plans",
                                   f"project_id=eq.{project['id']}"))
    monkeypatch.setattr(ep, "gemini_generate", _gen(_valid_plan()))
    job = jobs.enqueue_job(project["id"], uid, "editorial_plan", {})
    claimed = jobs._claim_next()
    fake.patch("pipeline_jobs", f"id=eq.{claimed['id']}",
               {"status": "cancel_requested"})
    claimed["status"] = "processing"
    jobs._run_job(claimed)
    row = fake.select("pipeline_jobs", f"id=eq.{job['id']}")[0]
    assert row["status"] == "cancelled"
    assert len(fake.select("editorial_plans",
                           f"project_id=eq.{project['id']}")) == plans_before
    assert fake.select("projects", f"id=eq.{project['id']}")[0]["status"] == prior


def test_handler_grounds_music_availability(monkeypatch):
    fake = FakeSupabase()
    install(monkeypatch, fake)
    uid, token, project = _setup_project(fake)
    fake.insert("licensed_music_assets", {
        "project_id": project["id"], "user_id": uid,
        "music_sound_run_id": str(uuid4()), "version": 1})
    seen = {}

    def generate(parts, schema):
        seen["constraints"] = parts[1]["text"]
        return _valid_plan(music_available=True)
    monkeypatch.setattr(ep, "gemini_generate", generate)
    jobs.enqueue_job(project["id"], uid, "editorial_plan", {})
    jobs._run_job(jobs._claim_next())
    assert "licensed music available for this project: True" in seen["constraints"]
    plans = fake.select("editorial_plans", f"project_id=eq.{project['id']}")
    assert plans[0]["plan"]["audio"]["musicAvailable"] is True


def test_binding_constraints_reach_the_validator_not_just_the_prompt(monkeypatch):
    """The handler passes the REQUEST constraints into validation: a 12 s plan
    against a requested 45-60 s fails even though the model 'approved' it."""
    fake = FakeSupabase()
    install(monkeypatch, fake)
    uid, token, project = _setup_project(fake)
    monkeypatch.setattr(ep, "gemini_generate", _gen(_valid_plan()))
    jobs.enqueue_job(project["id"], uid, "editorial_plan",
                     {"durationMin": 45, "durationMax": 60})
    jobs._run_job(jobs._claim_next())
    row = fake.select("pipeline_jobs", f"project_id=eq.{project['id']}")[0]
    assert row["status"] == "failed"
    assert "REQUESTED duration range" in row["error_message"]
    assert fake.select("editorial_plans", f"project_id=eq.{project['id']}") == []


# ------------------------------------------------------------------ endpoints
def test_endpoint_requires_analysis_then_enqueues(monkeypatch):
    from app import main
    main._rate.clear()
    fake = FakeSupabase()
    install(monkeypatch, fake)
    uid, token = fake.add_user("api@example.com")
    project = fake.add_project(uid, "API Test", status="draft_ready")
    client = TestClient(app, raise_server_exceptions=False)
    r = client.post(f"/projects/{project['id']}/editorial-plan",
                    headers=_auth(token), json={"brief": "make it pop"})
    assert r.status_code == 409                         # no segments yet
    fake.insert("segments", {"project_id": project["id"], "user_id": uid,
                             "data": _segments()[0].model_dump()})
    r = client.post(f"/projects/{project['id']}/editorial-plan",
                    headers=_auth(token),
                    json={"brief": "make it pop", "durationMin": 30,
                          "durationMax": 60, "platform": "vertical",
                          "aspectRatio": "9:16"})
    assert r.status_code == 200
    job = r.json()
    assert job["kind"] == "editorial_plan"
    assert job["params"]["aspectRatio"] == "9:16"
    again = client.post(f"/projects/{project['id']}/editorial-plan",
                        headers=_auth(token), json={})
    assert again.json()["id"] == job["id"]              # idempotent while active
    bad = client.post(f"/projects/{project['id']}/editorial-plan",
                      headers=_auth(token),
                      json={"durationMin": 90, "durationMax": 30})
    assert bad.status_code == 422


def test_endpoint_rejects_deleted_and_foreign(monkeypatch):
    from app import main
    main._rate.clear()
    fake = FakeSupabase()
    install(monkeypatch, fake)
    uid, token, project = _setup_project(fake)
    _, intruder = fake.add_user("intruder@example.com")
    client = TestClient(app, raise_server_exceptions=False)
    assert client.post(f"/projects/{project['id']}/editorial-plan",
                       headers=_auth(intruder), json={}).status_code == 403
    assert client.get(f"/projects/{project['id']}/editorial-plan",
                      headers=_auth(intruder)).status_code == 403
    assert client.delete(f"/projects/{project['id']}",
                         headers=_auth(token)).status_code == 200
    assert client.post(f"/projects/{project['id']}/editorial-plan",
                       headers=_auth(token), json={}).status_code == 404
    assert client.get(f"/projects/{project['id']}/editorial-plan",
                      headers=_auth(token)).status_code == 404


def test_get_returns_latest_plan(monkeypatch):
    from app import main
    main._rate.clear()
    fake = FakeSupabase()
    install(monkeypatch, fake)
    uid, token, project = _setup_project(fake)
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get(f"/projects/{project['id']}/editorial-plan",
                      headers=_auth(token)).status_code == 404
    monkeypatch.setattr(ep, "gemini_generate", _gen(_valid_plan()))
    jobs.enqueue_job(project["id"], uid, "editorial_plan", {})
    jobs._run_job(jobs._claim_next())
    r = client.get(f"/projects/{project['id']}/editorial-plan", headers=_auth(token))
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "approved" and body["quality_score"] == 100
    assert body["plan"]["render"]["aspect"] == "9:16"
    assert "ffmpeg" not in str(body["plan"]).lower()
