"""V2 customer journey wiring: analysis -> editorial_plan -> Picture Edit V2.

The engine and planner are individually tested elsewhere; this file covers the
CHAIN — who enqueues what, with which exact parameters, and what happens when a
stage fails. Everything runs against the in-memory fake store with the planner
model stubbed; flag state is set per-test.
"""
import os

os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "fake-service-key")
os.environ["WORKER_ENABLED"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from app import jobs  # noqa: E402
from app.main import app  # noqa: E402
from app.pipeline import editorial_planner as ep  # noqa: E402
from app.pipeline import picture_edit_v2 as pe2  # noqa: E402
from tests.fake_supa import FakeSupabase, install  # noqa: E402
from tests.test_editorial_planner import (  # noqa: E402
    _gen, _setup_project, _valid_plan)


def _auth(t):
    return {"Authorization": f"Bearer {t}"}


def _flag(monkeypatch, on: bool):
    monkeypatch.setenv("PICTURE_EDIT_ENGINE_V2_ENABLED", "true" if on else "")


def _jobs_of(fake, project, kind):
    return [j for j in fake.select("pipeline_jobs",
                                   f"project_id=eq.{project['id']}")
            if j["kind"] == kind]


def _project_row(fake, project):
    return fake.select("projects", f"id=eq.{project['id']}")[0]


# ────────────────────────────────────────── analysis-completion chain start
class TestChainStart:
    def test_v2_on_enqueues_editorial_plan_not_autoedit(self, monkeypatch):
        fake = FakeSupabase()
        install(monkeypatch, fake)
        _flag(monkeypatch, True)
        uid, _, project = _setup_project(fake)
        row = _project_row(fake, project)
        row["aspect_ratio"] = "9:16"
        row["target_platform"] = "tiktok"
        jobs._maybe_enqueue_customer_autoedit(row)
        plans = _jobs_of(fake, project, "editorial_plan")
        assert len(plans) == 1
        assert plans[0]["params"]["source"] == "customer_journey"
        # constraints the customer already gave us travel into the plan
        assert plans[0]["params"]["aspectRatio"] == "9:16"
        assert plans[0]["params"]["platform"] == "tiktok"
        assert plans[0]["params"]["brief"] == project["name"]
        assert _jobs_of(fake, project, "autoedit") == []

    def test_v2_off_keeps_legacy_autoedit(self, monkeypatch):
        fake = FakeSupabase()
        install(monkeypatch, fake)
        _flag(monkeypatch, False)
        uid, _, project = _setup_project(fake)
        jobs._maybe_enqueue_customer_autoedit(_project_row(fake, project))
        assert len(_jobs_of(fake, project, "autoedit")) == 1
        assert _jobs_of(fake, project, "editorial_plan") == []

    def test_existing_approved_plan_skips_straight_to_autoedit(self, monkeypatch):
        """A mid-journey retry must not re-plan: reuse the approved plan."""
        fake = FakeSupabase()
        install(monkeypatch, fake)
        _flag(monkeypatch, True)
        uid, _, project = _setup_project(fake)
        fake.insert("editorial_plans", {
            "project_id": project["id"], "user_id": uid, "version": 3,
            "status": "approved", "quality_score": 90, "plan": {}})
        plan_row = fake.select("editorial_plans",
                               f"project_id=eq.{project['id']}")[0]
        jobs._maybe_enqueue_customer_autoedit(_project_row(fake, project))
        auto = _jobs_of(fake, project, "autoedit")
        assert len(auto) == 1
        assert auto[0]["params"]["editorial_plan_id"] == plan_row["id"]
        assert auto[0]["params"]["editorial_plan_version"] == 3
        assert _jobs_of(fake, project, "editorial_plan") == []

    def test_idempotent_while_plan_job_active(self, monkeypatch):
        fake = FakeSupabase()
        install(monkeypatch, fake)
        _flag(monkeypatch, True)
        uid, _, project = _setup_project(fake)
        row = _project_row(fake, project)
        jobs._maybe_enqueue_customer_autoedit(row)
        jobs._maybe_enqueue_customer_autoedit(row)
        assert len(_jobs_of(fake, project, "editorial_plan")) == 1


# ─────────────────────────────────────────── plan completion hands off to V2
class TestPlanHandoff:
    def test_approved_plan_enqueues_autoedit_with_exact_plan(self, monkeypatch):
        fake = FakeSupabase()
        install(monkeypatch, fake)
        _flag(monkeypatch, True)
        uid, _, project = _setup_project(fake)
        monkeypatch.setattr(ep, "gemini_generate", _gen(_valid_plan()))
        jobs.enqueue_job(project["id"], uid, "editorial_plan",
                         {"source": "customer_journey", "aspectRatio": "9:16"})
        jobs._run_job(jobs._claim_next())
        plans = fake.select("editorial_plans", f"project_id=eq.{project['id']}")
        assert len(plans) == 1 and plans[0]["status"] == "approved"
        auto = _jobs_of(fake, project, "autoedit")
        assert len(auto) == 1
        assert auto[0]["params"]["editorial_plan_id"] == plans[0]["id"]
        assert auto[0]["params"]["editorial_plan_version"] == plans[0]["version"]
        assert auto[0]["params"]["aspect_ratio"] == "9:16"
        assert auto[0]["params"]["source"] == "customer_journey"

    def test_operator_plan_without_source_does_not_chain(self, monkeypatch):
        fake = FakeSupabase()
        install(monkeypatch, fake)
        _flag(monkeypatch, True)
        uid, _, project = _setup_project(fake)
        monkeypatch.setattr(ep, "gemini_generate", _gen(_valid_plan()))
        jobs.enqueue_job(project["id"], uid, "editorial_plan", {"brief": "b"})
        jobs._run_job(jobs._claim_next())
        assert _jobs_of(fake, project, "autoedit") == []

    def test_insufficient_footage_stops_and_surfaces(self, monkeypatch):
        fake = FakeSupabase()
        install(monkeypatch, fake)
        _flag(monkeypatch, True)
        uid, _, project = _setup_project(fake, status="ready")

        def shortfall(segments, constraints, music_available, generate):
            return {"status": "insufficient_footage", "qualityScore": 55,
                    "attempts": 1, "violationsHistory": [],
                    "deterministicGate": {"passed": True},
                    "plan": {"achievableDurationSeconds": 9.5,
                             "missingFootage": [
                                 {"beat": "payoff", "shotType": "close",
                                  "recommendedDurationSeconds": 4,
                                  "why": "no finished-result shot"}]}}
        monkeypatch.setattr(ep, "plan_editorial", shortfall)
        jobs.enqueue_job(project["id"], uid, "editorial_plan",
                         {"source": "customer_journey"})
        jobs._run_job(jobs._claim_next())
        assert _jobs_of(fake, project, "autoedit") == []          # no fallback
        row = _project_row(fake, project)
        assert row["status"] == "analysis_failed"
        assert "achievable 9.5s" in row["status_reason"]

    def test_planner_crash_surfaces_for_customer_but_not_operator(self, monkeypatch):
        fake = FakeSupabase()
        install(monkeypatch, fake)
        _flag(monkeypatch, True)
        uid, _, project = _setup_project(fake, status="ready")

        def explode(*a, **k):
            raise RuntimeError("planner exploded")
        monkeypatch.setattr(ep, "plan_editorial", explode)

        # customer journey: failure must surface on the project
        jobs.enqueue_job(project["id"], uid, "editorial_plan",
                         {"source": "customer_journey"})
        jobs._run_job(jobs._claim_next())
        row = _project_row(fake, project)
        assert row["status"] == "analysis_failed"
        assert "planner exploded" in row["status_reason"]
        assert _jobs_of(fake, project, "autoedit") == []          # no fallback

        # operator-requested plan: status stays untouched (optional stage)
        fake.patch("projects", f"id=eq.{project['id']}",
                   {"status": "ready", "status_reason": "reset"})
        jobs.enqueue_job(project["id"], uid, "editorial_plan", {})
        jobs._run_job(jobs._claim_next())
        assert _project_row(fake, project)["status"] == "ready"


# ───────────────────────────────────────────── V2 autoedit consumes the plan
class TestV2Consumption:
    def test_flag_on_without_plan_fails_loudly_never_legacy(self, monkeypatch):
        fake = FakeSupabase()
        install(monkeypatch, fake)
        _flag(monkeypatch, True)
        uid, _, project = _setup_project(fake, status="ready")

        def legacy_forbidden(*a, **k):
            raise AssertionError("legacy autoedit invoked under V2 flag")
        import app.pipeline.autoedit as legacy
        monkeypatch.setattr(legacy, "autoedit", legacy_forbidden)

        jobs.enqueue_job(project["id"], uid, "autoedit",
                         {"source": "customer_journey"})
        jobs._run_job(jobs._claim_next())
        job = _jobs_of(fake, project, "autoedit")[0]
        assert job["status"] == "failed"
        assert "editorial plan" in job["error_message"].lower()
        assert "legacy autoedit invoked" not in job["error_message"]
        assert _project_row(fake, project)["status"] == "analysis_failed"

    def test_exact_plan_is_consumed_not_latest(self, monkeypatch):
        fake = FakeSupabase()
        install(monkeypatch, fake)
        _flag(monkeypatch, True)
        uid, _, project = _setup_project(fake)
        for version in (1, 2):
            fake.insert("editorial_plans", {
                "project_id": project["id"], "user_id": uid,
                "version": version, "status": "approved",
                "quality_score": 90, "plan": {}})
        v1 = [p for p in fake.select("editorial_plans",
                                     f"project_id=eq.{project['id']}")
              if p["version"] == 1][0]

        seen = {}
        def capture(plan_row, segments, now):
            seen["plan"] = plan_row
            raise pe2.PictureEditRejected(["stop here"])
        monkeypatch.setattr(pe2, "build_picture_edit", capture)

        jobs.enqueue_job(project["id"], uid, "autoedit",
                         {"source": "customer_journey",
                          "editorial_plan_id": v1["id"],
                          "editorial_plan_version": 1})
        jobs._run_job(jobs._claim_next())
        assert seen["plan"]["id"] == v1["id"]
        assert seen["plan"]["version"] == 1      # NOT the newer v2 plan

    def test_version_mismatch_refuses_stale_handoff(self, monkeypatch):
        fake = FakeSupabase()
        install(monkeypatch, fake)
        _flag(monkeypatch, True)
        uid, _, project = _setup_project(fake)
        fake.insert("editorial_plans", {
            "project_id": project["id"], "user_id": uid, "version": 2,
            "status": "approved", "quality_score": 90, "plan": {}})
        plan = fake.select("editorial_plans",
                           f"project_id=eq.{project['id']}")[0]
        jobs.enqueue_job(project["id"], uid, "autoedit",
                         {"source": "customer_journey",
                          "editorial_plan_id": plan["id"],
                          "editorial_plan_version": 1})
        jobs._run_job(jobs._claim_next())
        job = _jobs_of(fake, project, "autoedit")[0]
        assert job["status"] == "failed"
        assert "stale" in job["error_message"]

    def test_foreign_project_plan_is_refused(self, monkeypatch):
        fake = FakeSupabase()
        install(monkeypatch, fake)
        _flag(monkeypatch, True)
        uid, _, project = _setup_project(fake)
        other_uid, _, other = _setup_project(fake)
        fake.insert("editorial_plans", {
            "project_id": other["id"], "user_id": other_uid, "version": 1,
            "status": "approved", "quality_score": 90, "plan": {}})
        foreign = fake.select("editorial_plans",
                              f"project_id=eq.{other['id']}")[0]
        jobs.enqueue_job(project["id"], uid, "autoedit",
                         {"source": "customer_journey",
                          "editorial_plan_id": foreign["id"]})
        jobs._run_job(jobs._claim_next())
        job = _jobs_of(fake, project, "autoedit")[0]
        assert job["status"] == "failed"
        assert "does not belong" in job["error_message"]


# ─────────────────────────────────────────────── customer endpoints (chain)
class TestEndpoints:
    def _client(self):
        return TestClient(app, raise_server_exceptions=False)

    def test_recut_v2_replans_instead_of_bare_autoedit(self, monkeypatch):
        fake = FakeSupabase()
        install(monkeypatch, fake)
        from app import main as m
        m._rate.clear()
        _flag(monkeypatch, True)
        uid, token, project = _setup_project(fake, status="draft_ready")
        r = self._client().post(f"/projects/{project['id']}/recut",
                                json={"aspectRatio": "9:16"},
                                headers=_auth(token))
        assert r.status_code == 200, r.text
        assert r.json()["kind"] == "editorial_plan"
        assert r.json()["params"]["aspectRatio"] == "9:16"
        assert r.json()["params"]["source"] == "recut"
        assert _jobs_of(fake, project, "autoedit") == []

    def test_recut_v2_off_keeps_legacy_contract(self, monkeypatch):
        fake = FakeSupabase()
        install(monkeypatch, fake)
        from app import main as m
        m._rate.clear()
        _flag(monkeypatch, False)
        uid, token, project = _setup_project(fake, status="draft_ready")
        r = self._client().post(f"/projects/{project['id']}/recut",
                                json={"aspectRatio": "9:16"},
                                headers=_auth(token))
        assert r.status_code == 200, r.text
        assert r.json()["kind"] == "autoedit"

    def test_request_edit_retry_paths(self, monkeypatch):
        fake = FakeSupabase()
        install(monkeypatch, fake)
        from app import main as m
        m._rate.clear()
        _flag(monkeypatch, True)
        uid, token, project = _setup_project(fake, status="analysis_failed")
        c = self._client()
        # no plan yet -> re-enters planning with the chain source
        r = c.post(f"/projects/{project['id']}/request-edit",
                   headers=_auth(token))
        assert r.status_code == 200, r.text
        assert r.json()["kind"] == "editorial_plan"
        assert r.json()["params"]["source"] == "customer_journey"
        # idempotent while that job is active
        r2 = c.post(f"/projects/{project['id']}/request-edit",
                    headers=_auth(token))
        assert r2.json()["id"] == r.json()["id"]

    def test_request_edit_reuses_approved_plan(self, monkeypatch):
        fake = FakeSupabase()
        install(monkeypatch, fake)
        from app import main as m
        m._rate.clear()
        _flag(monkeypatch, True)
        uid, token, project = _setup_project(fake, status="analysis_failed")
        fake.insert("editorial_plans", {
            "project_id": project["id"], "user_id": uid, "version": 1,
            "status": "approved", "quality_score": 90, "plan": {}})
        plan = fake.select("editorial_plans",
                           f"project_id=eq.{project['id']}")[0]
        r = self._client().post(f"/projects/{project['id']}/request-edit",
                                headers=_auth(token))
        assert r.status_code == 200, r.text
        assert r.json()["kind"] == "autoedit"
        assert r.json()["params"]["editorial_plan_id"] == plan["id"]

    def test_request_edit_requires_analysis(self, monkeypatch):
        fake = FakeSupabase()
        install(monkeypatch, fake)
        from app import main as m
        m._rate.clear()
        _flag(monkeypatch, True)
        uid, token = fake.add_user("bare@example.com")
        project = fake.add_project(uid, "No Catalog", status="draft")
        r = self._client().post(f"/projects/{project['id']}/request-edit",
                                headers=_auth(token))
        assert r.status_code == 409


class TestTargetDuration:
    def test_target_duration_becomes_binding_band(self):
        band = jobs._plan_constraints_for(
            {"name": "w", "target_duration_seconds": 180})
        assert band["durationMin"] == 153 and band["durationMax"] == 207

    def test_no_target_means_model_decides(self):
        assert "durationMin" not in jobs._plan_constraints_for({"name": "w"})

    def test_chain_start_carries_duration(self, monkeypatch):
        fake = FakeSupabase()
        install(monkeypatch, fake)
        _flag(monkeypatch, True)
        uid, _, project = _setup_project(fake)
        row = _project_row(fake, project)
        row["target_duration_seconds"] = 180
        jobs._maybe_enqueue_customer_autoedit(row)
        params = _jobs_of(fake, project, "editorial_plan")[0]["params"]
        assert params["durationMin"] == 153
        assert params["durationMax"] == 207
