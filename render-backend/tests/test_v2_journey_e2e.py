"""Flag-ON end-to-end: analysis hand-off -> editorial_plan -> Picture Edit V2
-> persisted timeline -> bridged candidate -> Product Editor -> final_render.

Runs the REAL handlers through the real worker loop (_claim_next/_run_job) and
the REAL Picture Edit V2 engine against the persisted plan; only the model call
and the ffmpeg render are stubbed (fixture-render fidelity is covered by
test_picture_edit_v2 / test_render). This is the local equivalent of the
production smoke test the wiring must pass after deploy.
"""
import os
os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "fake-service-key")
os.environ["WORKER_ENABLED"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from app import autoedit_bridge, jobs  # noqa: E402
from app.main import app  # noqa: E402
from app.pipeline import editorial_planner as ep  # noqa: E402
from app.pipeline import picture_edit_v2 as pe2  # noqa: E402
from app.pipeline import picture_render_v2 as pr2  # noqa: E402
from tests.fake_supa import FakeSupabase, install  # noqa: E402
from tests.test_editorial_planner import (  # noqa: E402
    _gen, _segments, _valid_plan)

# The Product Editor requires sourceAssetIds to be REAL media_assets UUIDs, so
# this journey uses a UUID asset id end-to-end (segments, plan, media_assets).
ASSET_ID = "a7a7a7a7-0000-4000-8000-000000000001"


def _plan_for_asset():
    plan = _valid_plan()
    for seg in plan["timeline"]:
        seg["assetId"] = ASSET_ID
    return plan


def _auth(t):
    return {"Authorization": f"Bearer {t}"}


def test_v2_journey_end_to_end(monkeypatch, tmp_path):
    fake = FakeSupabase()
    install(monkeypatch, fake)
    from app import main as m
    m._rate.clear()
    monkeypatch.setenv("PICTURE_EDIT_ENGINE_V2_ENABLED", "true")

    uid, token = fake.add_user("journey@example.com")
    project = fake.add_project(uid, "V2 Journey", status="ready")
    for seg in _segments(asset_id=ASSET_ID):
        fake.insert("segments", {"project_id": project["id"], "user_id": uid,
                                 "asset_id": ASSET_ID,
                                 "segment_key": seg.segmentId,
                                 "source_start": seg.sourceStart,
                                 "source_end": seg.sourceEnd,
                                 "data": seg.model_dump()})
    row = fake.select("projects", f"id=eq.{project['id']}")[0]
    row["aspect_ratio"] = "9:16"

    # Real raw-footage plumbing for the worker's ownership-validated download.
    # The media_asset id MUST equal the segments' assetId ("asset-1"): the
    # Product Editor refuses candidates whose clips reference asset ids that
    # are not real owned media_assets.
    key = f"users/{uid}/projects/{project['id']}/raw/clip.mp4"
    fake.insert("media_assets", {
        "id": ASSET_ID, "project_id": project["id"], "user_id": uid,
        "filename": "clip.mp4", "storage_path": key,
        "duration_seconds": 10.0})
    fake.storage[f"raw-footage/{key}"] = b"VIDEO-BYTES"

    # Legacy engine is forbidden for the whole journey.
    import app.pipeline.autoedit as legacy
    monkeypatch.setattr(
        legacy, "autoedit",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("legacy autoedit invoked under V2 flag")))
    # Model stubbed; render stubbed (writes the preview the bridge uploads).
    monkeypatch.setattr(ep, "gemini_generate", _gen(_plan_for_asset()))
    monkeypatch.setattr(
        pr2, "render_picture_edit",
        lambda result, sources, out, cancel_check=None:
            open(out, "wb").write(b"PREVIEW"))

    # 1) analysis completion starts the chain: editorial_plan, never autoedit.
    jobs._maybe_enqueue_customer_autoedit(row)
    queued = [j for j in fake.tables["pipeline_jobs"]]
    assert [j["kind"] for j in queued] == ["editorial_plan"]

    # 2) the worker runs the plan job; approval chains into V2 autoedit.
    jobs._run_job(jobs._claim_next())
    plans = fake.select("editorial_plans", f"project_id=eq.{project['id']}")
    assert len(plans) == 1
    plan = plans[0]
    assert plan["status"] == "approved"
    assert plan["validation"]["deterministicGate"]["passed"] is True
    # hook + requested duration are visible on the persisted plan
    assert plan["plan"]["hook"]["segmentId"]
    assert plan["plan"]["hook"]["durationSeconds"] > 0
    assert plan["plan"]["plannedDurationSeconds"] > 0
    kinds = [j["kind"] for j in fake.tables["pipeline_jobs"]]
    assert kinds == ["editorial_plan", "autoedit"]

    # 3) the worker runs autoedit; the REAL V2 engine consumes the exact plan.
    jobs._run_job(jobs._claim_next())
    auto = [j for j in fake.tables["pipeline_jobs"] if j["kind"] == "autoedit"][0]
    assert auto["status"] == "completed", auto.get("error_message")
    arts = auto["artifacts"]
    assert arts["engine"] == "picture_edit_v2"
    assert arts["engineVersion"] == pe2.ENGINE_VERSION      # 2.1.0
    assert arts["editorialPlanId"] == plan["id"]
    assert arts["editorialPlanVersion"] == plan["version"]
    assert arts["deterministicHash"]
    assert arts["timelineId"] and arts["bridgedCandidateRunId"]

    # 4) persisted timeline follows the plan's order exactly. V2 clips encode
    # their segment as `pe2-{order:03d}-{segmentId}`.
    tl = fake.select("timelines", f"id=eq.{arts['timelineId']}")[0]
    clips = [c for t in tl["timeline_json"]["tracks"]
             if t["type"] == "video" for c in t["clips"]]
    planned_order = [b["segmentId"] for b in plan["plan"]["timeline"]]
    assert [c["id"].split("-", 2)[2] for c in clips] == planned_order
    # and at the shape the plan requested (9:16 vertical)
    assert (tl["timeline_json"]["width"], tl["timeline_json"]["height"]) \
        == (1080, 1920)

    # 5) candidate descends from THAT exact timeline (deterministic ancestry).
    cand = fake.select("candidate_runs", f"id=eq.{arts['bridgedCandidateRunId']}")[0]
    expected_per = autoedit_bridge.bridged_picture_id(
        project["id"], str(arts["timelineId"]))
    assert cand["picture_edit_run_id"] == expected_per
    assert fake.select("projects", f"id=eq.{project['id']}")[0]["status"] \
        == "draft_ready"

    # 6) Product Editor opens that candidate; export creates a final_render
    # bound to the editor document — never the preview.
    client = TestClient(app, raise_server_exceptions=False)
    started = client.post(f"/projects/{project['id']}/editor/start",
                          json={"candidateRunId": cand["id"]},
                          headers=_auth(token))
    assert started.status_code == 200, started.text
    doc = started.json()
    r = client.post(f"/projects/{project['id']}/editor/render",
                    json={"documentId": doc["id"]}, headers=_auth(token))
    assert r.status_code == 200, r.text
    renders = [j for j in fake.tables["pipeline_jobs"]
               if j["kind"] == "final_render"]
    assert len(renders) == 1
    assert renders[0]["params"]["editor_document_id"] == doc["id"]

    # 7) the full journey never touched the legacy engine (the stub would have
    # raised) and never used the legacy fitness template anywhere.
    assert "fitness_v1" not in str(tl["timeline_json"])
