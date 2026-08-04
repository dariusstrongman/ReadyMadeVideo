"""Editorial Planner v1 — grounding validator, revision loop, job handler and
customer endpoints. The model call is always a stub: no network, no fabrication.
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
    return [Segment(segmentId=f"seg-{i}", assetId=asset_id,
                    sourceStart=0.0, sourceEnd=10.0,
                    action=f"action {i}", shotType="medium",
                    transcript="we finished the job" if i == 3 else None)
            for i in (1, 2, 3)]


def _option():
    scores = {k: 80 for k in ("hookStrength", "storyClarity", "payoffStrength",
                              "footageSupport", "emotionalInterest",
                              "visualVariety", "platformFit", "durationFit",
                              "originality")}
    return {"premise": "p", "viewerPromise": "vp", "hook": "h",
            "structure": "results_first", "payoff": "po",
            "idealDurationSeconds": 12, "strengths": ["s"], "weaknesses": ["w"],
            "footageCoverage": "full", "retentionRisks": ["r"],
            "scores": scores}


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
    gate = {"hook": 13, "storyClarity": 13, "flowContinuity": 9, "pacing": 9,
            "clipSelection": 9, "payoff": 9, "visualVariety": 4,
            "creativeTreatment": 8, "soundDesign": 4, "platformFit": 4,
            "durationCompliance": 5, "hardFailures": []}          # total 87
    return {
        "schemaVersion": 1,
        "storySentence": "This is a story about a job, where work happens, "
                         "leading to a finished result.",
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
        "timeline": tl, "pacingProfile": "fast hook, steady middle, held payoff",
        "transitionsRationale": "hard cuts on action",
        "captions": [], "audio": {"musicAvailable": music_available,
                                  "musicPlan": "natural sound only"
                                  if not music_available else "licensed track",
                                  "naturalSoundSegmentIds": ["seg-3"]},
        "colorReframeNotes": "identity",
        "requestedDurationMin": None, "requestedDurationMax": None,
        "plannedDurationSeconds": 12.0, "technicalWarnings": [],
        "retentionReview": [{"position": "mid", "risk": "repetition",
                             "mitigation": "vary shot size"}],
        "qualityGate": gate, "missingFootage": [],
        "render": {"width": 1080, "height": 1920, "fps": 30, "aspect": "9:16"},
        "status": "approved",
    }


def _gen(*plans):
    """A generate() stub returning each canned plan in order."""
    seq = list(plans)
    calls = {"n": 0, "parts": []}

    def generate(parts, schema):
        calls["parts"].append(parts)
        plan = seq[min(calls["n"], len(seq) - 1)]
        calls["n"] += 1
        return copy.deepcopy(plan)
    generate.calls = calls
    return generate


# ---------------------------------------------------------------- validator
def test_valid_plan_approved_first_attempt():
    out = ep.plan_editorial(_segments(), {}, False, _gen(_valid_plan()))
    assert out["attempts"] == 1 and out["status"] == "approved"
    assert out["qualityScore"] == 87
    assert out["plan"]["timeline"][0]["segmentId"] == "seg-1"


def test_invented_segment_triggers_revision_then_success():
    bad = _valid_plan()
    bad["timeline"][1]["segmentId"] = "seg-INVENTED"
    gen = _gen(bad, _valid_plan())
    out = ep.plan_editorial(_segments(), {}, False, gen)
    assert out["attempts"] == 2
    assert any("invented segment" in v for v in out["violationsHistory"][0])
    # the revision prompt fed the violations back to the model
    assert "REJECTED" in gen.calls["parts"][1][-1]["text"]


@pytest.mark.parametrize("mutate,needle", [
    (lambda p: p["timeline"][0].update(sourceOut=99.0, timelineOut=99.0),
     "outside the real source range"),
    (lambda p: p["timeline"][1].update(timelineIn=5.5),
     "not contiguous"),
    (lambda p: p.update(plannedDurationSeconds=40.0),
     "does not equal the timeline"),
    (lambda p: p["hook"].update(segmentId="seg-2"),
     "hook segment is not the first"),
    (lambda p: p.update(colorReframeNotes="run ffmpeg -i in.mp4 out.mp4"),
     "forbidden command token"),
    (lambda p: p["audio"].update(musicAvailable=True),
     "licensed-music availability"),
    (lambda p: p["qualityGate"].update(hook=0, storyClarity=0, pacing=0),
     "below the 80 gate"),
    (lambda p: p["timeline"].__setitem__(2, dict(p["timeline"][0],
                                                 timelineIn=8.0, timelineOut=12.0,
                                                 beat="payoff")),
     "repeats an identical range"),
])
def test_grounding_violations_are_rejected(mutate, needle):
    plan = _valid_plan()
    mutate(plan)
    with pytest.raises(ep.PlanRejected) as exc:
        ep.plan_editorial(_segments(), {}, False, _gen(plan), max_attempts=1)
    assert any(needle in v for v in exc.value.violations_history[0])


def test_caption_transcript_claim_requires_real_transcript():
    plan = _valid_plan()
    # a graphic on seg-1 (NO transcript) claiming a transcript source
    plan["timeline"][0]["graphic"] = {"text": "we finished the job",
                                      "purpose": "proof",
                                      "claimSource": "transcript"}
    with pytest.raises(ep.PlanRejected) as exc:
        ep.plan_editorial(_segments(), {}, False, _gen(plan), max_attempts=1)
    assert any("has no transcript" in v for v in exc.value.violations_history[0])
    # the same claim on seg-3 (which HAS that transcript) is honest
    ok = _valid_plan()
    ok["timeline"][2]["graphic"] = {"text": "we finished the job",
                                    "purpose": "proof",
                                    "claimSource": "transcript"}
    out = ep.plan_editorial(_segments(), {}, False, _gen(ok))
    assert out["status"] == "approved"


def test_duration_range_is_a_hard_constraint():
    short = _valid_plan()
    short["requestedDurationMin"], short["requestedDurationMax"] = 45.0, 60.0
    with pytest.raises(ep.PlanRejected) as exc:
        ep.plan_editorial(_segments(), {"durationMin": 45, "durationMax": 60},
                          False, _gen(short), max_attempts=1)
    assert any("requested duration range" in v
               for v in exc.value.violations_history[0])

    honest = _valid_plan()
    honest["requestedDurationMin"], honest["requestedDurationMax"] = 45.0, 60.0
    honest["status"] = "insufficient_footage"
    honest["missingFootage"] = [{"description": "closing reaction shot",
                                 "purpose": "payoff"}]
    out = ep.plan_editorial(_segments(), {"durationMin": 45, "durationMax": 60},
                            False, _gen(honest))
    assert out["status"] == "insufficient_footage"     # honest shortfall accepted


def test_insufficient_footage_requires_missing_shots():
    plan = _valid_plan()
    plan["status"] = "insufficient_footage"
    plan["missingFootage"] = []
    with pytest.raises(ep.PlanRejected) as exc:
        ep.plan_editorial(_segments(), {}, False, _gen(plan), max_attempts=1)
    assert any("exact missing shots" in v for v in exc.value.violations_history[0])


def test_rejected_after_attempt_budget():
    bad = _valid_plan()
    bad["timeline"][0]["segmentId"] = "seg-NOPE"
    with pytest.raises(ep.PlanRejected) as exc:
        ep.plan_editorial(_segments(), {}, False, _gen(bad), max_attempts=3)
    assert len(exc.value.violations_history) == 3


# ---------------------------------------------------------------- job handler
def _setup_project(fake):
    uid, token = fake.add_user("planner@example.com")
    project = fake.add_project(uid, "Plan Test", status="draft_ready")
    for s in _segments():
        fake.insert("segments", {"project_id": project["id"], "user_id": uid,
                                 "asset_id": "asset-1",
                                 "segment_key": s.segmentId,
                                 "source_start": s.sourceStart,
                                 "source_end": s.sourceEnd,
                                 "data": s.model_dump()})
    return uid, token, project


def test_handler_persists_versioned_plan_without_touching_status(monkeypatch):
    fake = FakeSupabase()
    install(monkeypatch, fake)
    uid, token, project = _setup_project(fake)
    monkeypatch.setattr(ep, "gemini_generate", _gen(_valid_plan()))

    job = jobs.enqueue_job(project["id"], uid, "editorial_plan", {"brief": "b"})
    claimed = jobs._claim_next()
    jobs._run_job(claimed)

    row = fake.select("pipeline_jobs", f"id=eq.{job['id']}")[0]
    assert row["status"] == "completed"
    art = row["artifacts"]
    assert art["status"] == "approved" and art["qualityScore"] == 87
    plans = fake.select("editorial_plans", f"project_id=eq.{project['id']}")
    assert len(plans) == 1 and plans[0]["version"] == 1
    assert plans[0]["plan"]["storySentence"].startswith("This is a story")
    # the optional planning stage NEVER moves the project state machine
    assert fake.select("projects", f"id=eq.{project['id']}")[0]["status"] == "draft_ready"

    # a second run versions up (1 -> 2), never overwrites
    monkeypatch.setattr(ep, "gemini_generate", _gen(_valid_plan()))
    jobs._run_job(jobs.enqueue_job(project["id"], uid, "editorial_plan", {}) and
                  jobs._claim_next())
    versions = sorted(r["version"] for r in
                      fake.select("editorial_plans", f"project_id=eq.{project['id']}"))
    assert versions == [1, 2]


def test_failed_plan_never_moves_project_status(monkeypatch):
    fake = FakeSupabase()
    install(monkeypatch, fake)
    uid, token, project = _setup_project(fake)

    def explode(parts, schema):
        raise RuntimeError("model unavailable")
    monkeypatch.setattr(ep, "gemini_generate", explode)

    jobs.enqueue_job(project["id"], uid, "editorial_plan", {})
    jobs._run_job(jobs._claim_next())
    job = fake.select("pipeline_jobs", f"project_id=eq.{project['id']}")[0]
    assert job["status"] == "failed"
    assert fake.select("projects", f"id=eq.{project['id']}")[0]["status"] == "draft_ready"


def test_handler_grounds_music_availability(monkeypatch):
    fake = FakeSupabase()
    install(monkeypatch, fake)
    uid, token, project = _setup_project(fake)
    fake.insert("licensed_music_assets", {          # project HAS licensed music
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


# ---------------------------------------------------------------- endpoints
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
                          "durationMax": 60, "platform": "vertical"})
    assert r.status_code == 200
    job = r.json()
    assert job["kind"] == "editorial_plan"
    assert job["params"]["durationMin"] == 30

    # idempotent while active
    again = client.post(f"/projects/{project['id']}/editorial-plan",
                        headers=_auth(token), json={})
    assert again.json()["id"] == job["id"]

    # invalid range rejected
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
    assert body["status"] == "approved" and body["version"] == 1
    assert body["plan"]["render"]["aspect"] == "9:16"
    # the plan is data for downstream departments — never FFmpeg
    assert "ffmpeg" not in str(body["plan"]).lower()
