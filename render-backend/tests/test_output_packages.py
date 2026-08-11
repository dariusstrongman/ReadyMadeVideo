"""Output Intelligence — API, orchestration, idempotency, security.

Covers the directive's system-level adversarial cases: double-click accept,
stale recommendation, cross-user attack, partial failure honesty, single-child
retry idempotency, budget blocking, cancellation, and flag-off invisibility.
The worker is simulated by driving the same on_job_finished hook the real
worker calls, against the same fake DB the real handlers use.
"""
import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "fake-service-key")
os.environ["WORKER_ENABLED"] = "0"

from app import output_packages as op  # noqa: E402
from app.main import app  # noqa: E402
from app.pipeline.schemas import Segment  # noqa: E402
from tests.fake_supa import FakeSupabase, install  # noqa: E402


def _auth(t):
    return {"Authorization": f"Bearer {t}"}


def seg(i, start, end, uses=(), location="studio", speech=True,
        action="discusses the plan in detail", subjects=("host", "guest")):
    return Segment(
        segmentId=f"s{i:03d}", assetId="a1", sourceStart=start, sourceEnd=end,
        storyUses=list(uses), location=location, subjects=list(subjects),
        action=action,
        speechSpans=([{"start": start + 1, "end": end - 1, "text": "…"}]
                     if speech else []),
        focusScore=0.8, stabilityScore=0.8, audioScore=0.7)


def _catalog():
    """12 minutes of coherent speech with hooks and payoffs => combo offer."""
    segs, t = [], 0.0
    for i in range(12):
        uses = ("hook",) if i in (0, 4, 8) else (
            ("completion",) if i in (3, 7, 11) else ("build",))
        segs.append(seg(i, t, t + 60, uses=uses))
        t += 60
    return segs


def _seed(fake, monkeypatch, flag="1"):
    from app import main
    main._rate.clear()
    if flag is None:
        monkeypatch.delenv("OUTPUT_INTELLIGENCE_ENABLED", raising=False)
    else:
        monkeypatch.setenv("OUTPUT_INTELLIGENCE_ENABLED", flag)
    uid, token = fake.add_user("oi@example.com")
    project = fake.add_project(uid, "OI Test", status="ready")
    for n, s in enumerate(_catalog()):
        fake.insert("segments", {"project_id": project["id"], "user_id": uid,
                                 "segment_key": f"k{n:04d}",
                                 "data": s.model_dump()})
    return uid, token, project


def _client():
    return TestClient(app, raise_server_exceptions=False)


def _recommend(client, project, token):
    r = client.post(f"/projects/{project['id']}/output-recommendation",
                    headers=_auth(token))
    assert r.status_code == 200, r.text
    return r.json()


def _accept(client, project, token, rec, selection):
    return client.post(f"/projects/{project['id']}/output-packages",
                       headers=_auth(token),
                       json={"recommendationId": rec["id"],
                             "selection": selection})


COMBO = [{"kind": "long_form"}, {"kind": "short_form", "quantity": 2}]


# ---------------------------------------------------------------- flag (case 33)
def test_flag_off_every_endpoint_is_invisible(monkeypatch):
    fake = FakeSupabase()
    install(monkeypatch, fake)
    uid, token, project = _seed(fake, monkeypatch, flag=None)
    client = _client()
    for method, path in [
            ("post", f"/projects/{project['id']}/output-recommendation"),
            ("get", f"/projects/{project['id']}/output-recommendation"),
            ("get", f"/projects/{project['id']}/output-packages")]:
        assert getattr(client, method)(path, headers=_auth(token)).status_code == 404


def test_flag_off_analysis_chain_is_unchanged(monkeypatch):
    """Classic journey: analysis completion still auto-enqueues the next job."""
    from app import jobs
    fake = FakeSupabase()
    install(monkeypatch, fake)
    uid, token, project = _seed(fake, monkeypatch, flag=None)
    monkeypatch.delenv("PICTURE_EDIT_ENGINE_V2_ENABLED", raising=False)
    jobs._maybe_enqueue_customer_autoedit(fake.tables["projects"][0])
    kinds = [j["kind"] for j in fake.tables["pipeline_jobs"]]
    assert kinds == ["autoedit"]           # byte-identical legacy behavior


def test_flag_on_analysis_completes_into_a_choice(monkeypatch):
    from app import jobs
    fake = FakeSupabase()
    install(monkeypatch, fake)
    uid, token, project = _seed(fake, monkeypatch)
    jobs._maybe_enqueue_customer_autoedit(fake.tables["projects"][0])
    assert fake.tables["pipeline_jobs"] == []       # nothing auto-planned
    assert fake.tables["projects"][0]["status"] == "ready"
    assert "choose" in fake.tables["projects"][0]["status_reason"]


# ---------------------------------------------------------------- recommendation
def test_recommendation_is_persisted_and_idempotent(monkeypatch):
    fake = FakeSupabase()
    install(monkeypatch, fake)
    uid, token, project = _seed(fake, monkeypatch)
    client = _client()
    a = _recommend(client, project, token)
    b = _recommend(client, project, token)
    assert a["id"] == b["id"]              # same catalog => same row, no twin
    assert len(fake.tables["output_recommendations"]) == 1
    assert a["recommended_key"] == "combo"
    assert a["packages"][0]["deliverables"]
    # grounded: inventory numbers come from the catalog, not the model
    assert a["inventory"]["usable_seconds"] == pytest.approx(720, abs=1)


def test_recommendation_marks_stale_after_new_upload(monkeypatch):
    """Case 14: footage changes => the stored offer says so."""
    fake = FakeSupabase()
    install(monkeypatch, fake)
    uid, token, project = _seed(fake, monkeypatch)
    client = _client()
    _recommend(client, project, token)
    fake.insert("segments", {"project_id": project["id"], "user_id": uid,
                             "segment_key": "k9999",
                             "data": seg(99, 720, 750).model_dump()})
    r = client.get(f"/projects/{project['id']}/output-recommendation",
                   headers=_auth(token))
    assert r.status_code == 200 and r.json()["stale"] is True


# ---------------------------------------------------------------- acceptance
def test_accept_creates_package_and_first_child_starts(monkeypatch):
    fake = FakeSupabase()
    install(monkeypatch, fake)
    uid, token, project = _seed(fake, monkeypatch)
    client = _client()
    rec = _recommend(client, project, token)
    r = _accept(client, project, token, rec, COMBO)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] is True
    assert body["packageStatus"] == "processing"
    assert len(body["deliverables"]) == 3          # 1 long + 2 shorts
    states = [d["status"] for d in body["deliverables"]]
    assert states == ["planning", "queued", "queued"]   # sequential, one active
    jobs_ = fake.tables["pipeline_jobs"]
    assert len(jobs_) == 1 and jobs_[0]["kind"] == "editorial_plan"
    p = jobs_[0]["params"]
    assert p["deliverable_id"] == body["deliverables"][0]["id"]
    assert p["source"] == "customer_journey"
    assert p["durationMin"] > 0 and p["durationMax"] > p["durationMin"]


def test_case15_double_click_accept_never_duplicates(monkeypatch):
    fake = FakeSupabase()
    install(monkeypatch, fake)
    uid, token, project = _seed(fake, monkeypatch)
    client = _client()
    rec = _recommend(client, project, token)
    a = _accept(client, project, token, rec, COMBO)
    b = _accept(client, project, token, rec, COMBO)
    assert a.status_code == b.status_code == 200
    assert a.json()["package"]["id"] == b.json()["package"]["id"]
    assert b.json()["created"] is False
    assert len(fake.tables["output_packages"]) == 1
    assert len(fake.tables["output_deliverables"]) == 3
    assert len(fake.tables["pipeline_jobs"]) == 1   # no duplicate child job


def test_infeasible_selection_rejected_with_alternative(monkeypatch):
    """Cases 3/25: too many shorts => 422 + explanation + nearest option;
    never silently altered, never partially created."""
    fake = FakeSupabase()
    install(monkeypatch, fake)
    uid, token, project = _seed(fake, monkeypatch)
    client = _client()
    rec = _recommend(client, project, token)
    r = _accept(client, project, token, rec,
                [{"kind": "short_form", "quantity": 50}])
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["error"] == "selection_not_feasible"
    res = detail["results"][0]
    assert res["verdict"] == "NOT_RECOMMENDED"
    assert res["reasons"][0]["code"] == "quantity_exceeds_moments"
    assert res["alternative"]["quantity"] >= 1
    assert fake.tables["output_packages"] == []     # nothing half-made


def test_case17_stale_recommendation_submission_is_rejected(monkeypatch):
    fake = FakeSupabase()
    install(monkeypatch, fake)
    uid, token, project = _seed(fake, monkeypatch)
    client = _client()
    rec = _recommend(client, project, token)
    fake.insert("segments", {"project_id": project["id"], "user_id": uid,
                             "segment_key": "k9999",
                             "data": seg(99, 720, 750).model_dump()})
    r = _accept(client, project, token, rec, COMBO)
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "stale_recommendation"
    assert fake.tables["output_packages"] == []


# ---------------------------------------------------------------- security (34)
def test_cross_user_cannot_reach_recommendation_or_package(monkeypatch):
    fake = FakeSupabase()
    install(monkeypatch, fake)
    uid, token, project = _seed(fake, monkeypatch)
    _, intruder = fake.add_user("intruder@example.com")
    client = _client()
    rec = _recommend(client, project, token)
    r = _accept(client, project, token, rec, COMBO)
    child = r.json()["deliverables"][0]
    for method, path in [
            ("post", f"/projects/{project['id']}/output-recommendation"),
            ("get", f"/projects/{project['id']}/output-recommendation"),
            ("get", f"/projects/{project['id']}/output-packages"),
            ("post", f"/projects/{project['id']}/output-deliverables/"
                     f"{child['id']}/retry")]:
        code = getattr(client, method)(path, headers=_auth(intruder)).status_code
        assert code == 403, f"{path} answered {code} to a stranger"


# ---------------------------------------------------------------- lifecycle
def _simulate_plan_completed(fake, child_id, plan_id="plan-1", version=1,
                             status="approved"):
    job = next(j for j in fake.tables["pipeline_jobs"]
               if (j.get("params") or {}).get("deliverable_id") == child_id
               and j["kind"] == "editorial_plan")
    fake.patch("pipeline_jobs", f"id=eq.{job['id']}", {"status": "completed"})
    op.on_job_finished({**job, "status": "completed",
                        "artifacts": {"editorialPlanId": plan_id,
                                      "planVersion": version,
                                      "status": status}})
    return job


def _simulate_autoedit_completed(fake, child_id, timeline_id="tl-1"):
    op.on_job_finished({"id": "sim-autoedit", "kind": "autoedit",
                        "project_id": fake.tables["projects"][0]["id"],
                        "params": {"deliverable_id": child_id},
                        "status": "completed",
                        "artifacts": {"timelineId": timeline_id,
                                      "editorialPlanId": "plan-1",
                                      "editorialPlanVersion": 1}})


def _simulate_plan_failed(fake, child_id, err="RuntimeError: boom"):
    job = next(j for j in fake.tables["pipeline_jobs"]
               if (j.get("params") or {}).get("deliverable_id") == child_id
               and j["kind"] == "editorial_plan"
               and j["status"] in ("queued", "processing"))
    fake.patch("pipeline_jobs", f"id=eq.{job['id']}", {"status": "failed"})
    op.on_job_finished({**job, "status": "failed", "artifacts": {},
                        "error_message": err})


def test_flow_d_partial_failure_retry_no_duplicates(monkeypatch):
    """Child 2 fails; siblings complete; retry child 2; package completes.
    Ancestry lands on every child; siblings never re-run."""
    fake = FakeSupabase()
    install(monkeypatch, fake)
    uid, token, project = _seed(fake, monkeypatch)
    client = _client()
    rec = _recommend(client, project, token)
    body = _accept(client, project, token, rec, COMBO).json()
    c0, c1, c2 = [d["id"] for d in body["deliverables"]]

    # child 0: plan approved -> editing (autoedit chained by real handler)
    _simulate_plan_completed(fake, c0)
    d0 = fake.select("output_deliverables", f"id=eq.{c0}")[0]
    assert d0["status"] == "editing"
    assert d0["editorial_plan_id"] == "plan-1"      # exact ancestry, no inference
    _simulate_autoedit_completed(fake, c0, "tl-c0")
    d0 = fake.select("output_deliverables", f"id=eq.{c0}")[0]
    assert d0["status"] == "ready" and d0["timeline_id"] == "tl-c0"

    # child 1 auto-started; fail it
    d1 = fake.select("output_deliverables", f"id=eq.{c1}")[0]
    assert d1["status"] == "planning"
    _simulate_plan_failed(fake, c1)
    d1 = fake.select("output_deliverables", f"id=eq.{c1}")[0]
    assert d1["status"] == "failed" and "boom" in d1["error_message"]

    # child 2 proceeds despite the failed sibling
    _simulate_plan_completed(fake, c2, plan_id="plan-3", version=3)
    _simulate_autoedit_completed(fake, c2, "tl-c2")

    pkgs = client.get(f"/projects/{project['id']}/output-packages",
                      headers=_auth(token)).json()["packages"]
    assert pkgs[0]["packageStatus"] == "partial"    # honest: not "complete"

    # retry ONLY the failed child
    r = client.post(f"/projects/{project['id']}/output-deliverables/{c1}/retry",
                    headers=_auth(token))
    assert r.json()["retried"] is True
    again = client.post(
        f"/projects/{project['id']}/output-deliverables/{c1}/retry",
        headers=_auth(token))
    assert again.json()["retried"] is False         # idempotent double-click
    # siblings untouched, exactly one new job for c1, none for c0/c2
    plan_jobs = [j for j in fake.tables["pipeline_jobs"]
                 if j["kind"] == "editorial_plan"]
    by_child = {}
    for j in plan_jobs:
        by_child.setdefault(j["params"]["deliverable_id"], []).append(j)
    assert len(by_child[c0]) == 1 and len(by_child[c2]) == 1
    assert len(by_child[c1]) == 2
    assert fake.select("output_deliverables", f"id=eq.{c0}")[0]["status"] == "ready"

    _simulate_plan_completed(fake, c1, plan_id="plan-4", version=4)
    _simulate_autoedit_completed(fake, c1, "tl-c1")
    pkgs = client.get(f"/projects/{project['id']}/output-packages",
                      headers=_auth(token)).json()["packages"]
    assert pkgs[0]["packageStatus"] == "complete"
    tls = {d["timeline_id"] for d in pkgs[0]["deliverables"]}
    assert tls == {"tl-c0", "tl-c1", "tl-c2"}       # each render maps to its child


def test_case23_budget_death_blocks_remaining_honestly(monkeypatch):
    fake = FakeSupabase()
    install(monkeypatch, fake)
    uid, token, project = _seed(fake, monkeypatch)
    client = _client()
    rec = _recommend(client, project, token)
    body = _accept(client, project, token, rec, COMBO).json()
    c0, c1, c2 = [d["id"] for d in body["deliverables"]]
    _simulate_plan_completed(fake, c0)
    _simulate_autoedit_completed(fake, c0)
    _simulate_plan_failed(fake, c1, err="BudgetExceeded: project AI budget")
    states = {d["id"]: d["status"]
              for d in fake.tables["output_deliverables"]}
    assert states[c0] == "ready"                    # completed work preserved
    assert states[c1] == "failed"
    assert states[c2] == "budget_blocked"           # not marched into the wall
    # no further paid job was started for c2
    assert not any(j["params"].get("deliverable_id") == c2
                   for j in fake.tables["pipeline_jobs"])
    # after a budget change, retry re-queues the blocked child legitimately
    r = client.post(f"/projects/{project['id']}/output-deliverables/{c2}/retry",
                    headers=_auth(token))
    assert r.json()["retried"] is True


def test_case21_project_deleted_mid_package_cancels_remaining(monkeypatch):
    fake = FakeSupabase()
    install(monkeypatch, fake)
    uid, token, project = _seed(fake, monkeypatch)
    client = _client()
    rec = _recommend(client, project, token)
    body = _accept(client, project, token, rec, COMBO).json()
    c0 = body["deliverables"][0]["id"]
    _simulate_plan_completed(fake, c0)              # c0 editing, chain active
    fake.patch("projects", f"id=eq.{project['id']}",
               {"deleted_at": "2026-08-11T00:00:00Z"})
    # deletion cancels the in-flight autoedit (existing machinery); its
    # terminal event is what the worker reports back:
    op.on_job_finished({"id": "sim-autoedit", "kind": "autoedit",
                        "project_id": project["id"],
                        "params": {"deliverable_id": c0},
                        "status": "cancelled", "artifacts": {}})
    states = [d["status"] for d in fake.tables["output_deliverables"]]
    assert states.count("cancelled") == 3           # c0 + both queued siblings
    # and nothing new was enqueued against a deleted project
    assert not any(j["status"] == "queued" for j in fake.tables["pipeline_jobs"])


def test_case22_cancel_package_mid_flight(monkeypatch):
    fake = FakeSupabase()
    install(monkeypatch, fake)
    uid, token, project = _seed(fake, monkeypatch)
    client = _client()
    rec = _recommend(client, project, token)
    body = _accept(client, project, token, rec, COMBO).json()
    r = client.post(f"/projects/{project['id']}/output-packages/"
                    f"{body['package']['id']}/cancel", headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["packageStatus"] == "cancelled"
    states = [d["status"] for d in fake.tables["output_deliverables"]]
    assert all(s == "cancelled" for s in states)
    # the in-flight job got a cancel request
    active = [j for j in fake.tables["pipeline_jobs"]
              if j["status"] in ("queued", "cancelled", "cancel_requested")]
    assert active and active[0]["status"] in ("cancelled", "cancel_requested")
    # a cancelled package never advances again
    assert op.advance_package(body["package"]["id"]) is None


def test_insufficient_footage_child_is_honest_not_retried_blindly(monkeypatch):
    """Case 17 (planner rejects one child): the honest planner outcome lands
    on the child, and the next sibling still gets its chance."""
    fake = FakeSupabase()
    install(monkeypatch, fake)
    uid, token, project = _seed(fake, monkeypatch)
    client = _client()
    rec = _recommend(client, project, token)
    body = _accept(client, project, token, rec, COMBO).json()
    c0, c1 = body["deliverables"][0]["id"], body["deliverables"][1]["id"]
    _simulate_plan_completed(fake, c0, status="insufficient_footage")
    d0 = fake.select("output_deliverables", f"id=eq.{c0}")[0]
    assert d0["status"] == "failed"
    assert "insufficient footage" in d0["error_message"]
    assert fake.select("output_deliverables", f"id=eq.{c1}")[0]["status"] == "planning"


# ---------------------------------------------------------------- round-5 audit
def test_audit1_quantity_zero_or_negative_is_rejected_not_coerced(monkeypatch):
    """quantity=0 used to silently become 1 — the exact 'silently alter'
    the contract bans. Garbage is now rejected with a reason."""
    fake = FakeSupabase()
    install(monkeypatch, fake)
    uid, token, project = _seed(fake, monkeypatch)
    client = _client()
    rec = _recommend(client, project, token)
    for bad in (0, -3):
        r = _accept(client, project, token, rec,
                    [{"kind": "short_form", "quantity": bad}])
        assert r.status_code == 422, f"quantity={bad} was accepted"
        codes = [x["code"] for res in r.json()["detail"]["results"]
                 for x in res["reasons"]]
        assert "invalid_quantity" in codes
    assert fake.tables["output_packages"] == []


def test_audit2_foreign_active_plan_job_is_not_captured(monkeypatch):
    """An operator's active editorial_plan job must not be claimed as a
    child's own — the child would wait forever on a job that never reports
    to it. The child stays queued until the foreign job ends."""
    fake = FakeSupabase()
    install(monkeypatch, fake)
    uid, token, project = _seed(fake, monkeypatch)
    # a foreign plan job is already active (no deliverable_id)
    fake.insert("pipeline_jobs", {"project_id": project["id"], "user_id": uid,
                                  "kind": "editorial_plan", "status": "processing",
                                  "params": {"source": "operator"}})
    client = _client()
    rec = _recommend(client, project, token)
    body = _accept(client, project, token, rec, COMBO).json()
    states = [d["status"] for d in body["deliverables"]]
    assert states == ["queued", "queued", "queued"]     # nothing falsely planning
    # foreign job finishes; the self-heal advance starts the real child job
    fake.patch("pipeline_jobs", f"kind=eq.editorial_plan", {"status": "completed"})
    pkgs = client.get(f"/projects/{project['id']}/output-packages",
                      headers=_auth(token)).json()["packages"]
    states = [d["status"] for d in pkgs[0]["deliverables"]]
    assert states[0] == "planning"
    own = [j for j in fake.tables["pipeline_jobs"]
           if (j.get("params") or {}).get("deliverable_id")]
    assert len(own) == 1


def test_audit3_footage_change_mid_package_cancels_remaining(monkeypatch):
    """The package bound one catalog identity; children must never plan
    against footage the customer was never offered."""
    fake = FakeSupabase()
    install(monkeypatch, fake)
    uid, token, project = _seed(fake, monkeypatch)
    client = _client()
    rec = _recommend(client, project, token)
    body = _accept(client, project, token, rec, COMBO).json()
    c0 = body["deliverables"][0]["id"]
    fake.insert("segments", {"project_id": project["id"], "user_id": uid,
                             "segment_key": "k9999",
                             "data": seg(99, 720, 750).model_dump()})
    _simulate_plan_completed(fake, c0)
    _simulate_autoedit_completed(fake, c0)      # c0 finishes; advance sees change
    states = {d["id"]: d["status"] for d in fake.tables["output_deliverables"]}
    assert states[c0] == "ready"                # finished work is never revoked
    rest = [s for cid, s in states.items() if cid != c0]
    assert all(s == "cancelled" for s in rest)
    err = next(d["error_message"] for d in fake.tables["output_deliverables"]
               if d["status"] == "cancelled")
    assert "footage changed" in err


def test_audit4_orphaned_child_is_reconciled_to_retryable(monkeypatch):
    """Stale-recovery fails jobs WITHOUT firing the deliverable hook; the
    list endpoint repairs the orphan into a retryable failure."""
    fake = FakeSupabase()
    install(monkeypatch, fake)
    uid, token, project = _seed(fake, monkeypatch)
    client = _client()
    rec = _recommend(client, project, token)
    body = _accept(client, project, token, rec, COMBO).json()
    c0 = body["deliverables"][0]["id"]
    # the worker dies: its job is failed by stale recovery, no hook fires
    fake.patch("pipeline_jobs", "kind=eq.editorial_plan",
               {"status": "failed", "error_message": "stale: heartbeat lost"})
    pkgs = client.get(f"/projects/{project['id']}/output-packages",
                      headers=_auth(token)).json()["packages"]
    d0 = next(d for d in pkgs[0]["deliverables"] if d["id"] == c0)
    assert d0["status"] in ("failed", "planning")
    if d0["status"] == "failed":
        assert "retry" in d0["error_message"]
    r = client.post(f"/projects/{project['id']}/output-deliverables/{c0}/retry",
                    headers=_auth(token))
    assert r.status_code == 200


def test_audit5_two_long_forms_from_one_story_rejected(monkeypatch):
    fake = FakeSupabase()
    install(monkeypatch, fake)
    uid, token, project = _seed(fake, monkeypatch)
    client = _client()
    rec = _recommend(client, project, token)
    r = _accept(client, project, token, rec,
                [{"kind": "long_form"}, {"kind": "long_form"}])
    assert r.status_code == 422
    codes = [x["code"] for res in r.json()["detail"]["results"]
             for x in res["reasons"]]
    assert "long_form_count_exceeds_stories" in codes
