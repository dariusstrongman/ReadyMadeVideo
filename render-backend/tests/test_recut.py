"""Tests for POST /projects/{id}/recut — re-cut an analysed project at a new shape.

A re-cut exists so changing the output shape does not force a full re-analysis:
the segment catalog is expensive and independent of the frame. Unlike
/generate-draft (operator-only) this is customer-owned, so ownership, rate
limiting, validation and idempotency all have to hold.
"""
import os
import uuid

import pytest

os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "fake-service-key")
os.environ["WORKER_ENABLED"] = "0"

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
from tests.fake_supa import FakeSupabase, install  # noqa: E402


@pytest.fixture()
def env(monkeypatch):
    fake = FakeSupabase()
    install(monkeypatch, fake)
    from app import main as m
    m._rate.clear()

    owner_id, owner_tok = fake.add_user("owner@example.com")
    other_id, other_tok = fake.add_user("other@example.com")
    project = fake.add_project(owner_id, "Recut Project", status="draft_ready")
    # add_project hands back a copy, so seed the shape on the stored row.
    for row in fake.tables["projects"]:
        if row["id"] == project["id"]:
            row["aspect_ratio"] = "16:9"

    # An analysed project: the catalog a re-cut reuses.
    fake.tables["segments"].append({
        "id": str(uuid.uuid4()), "project_id": project["id"], "user_id": owner_id,
        "data": {}, "created_at": "2026-08-05T12:00:00+00:00",
    })

    class Env:
        pass

    e = Env()
    e.fake, e.client = fake, TestClient(app, raise_server_exceptions=False)
    e.project, e.owner_id, e.owner_tok = project, owner_id, owner_tok
    e.other_id, e.other_tok = other_id, other_tok
    e.h = lambda tok: {"Authorization": f"Bearer {tok}"}
    e.url = f"/projects/{project['id']}/recut"
    return e


def _autoedit_jobs(env):
    return [j for j in env.fake.tables["pipeline_jobs"] if j["kind"] == "autoedit"]


def _project(env):
    """Re-read from the store: the fixture's reference goes stale on update."""
    return next(p for p in env.fake.tables["projects"] if p["id"] == env.project["id"])


class TestRecutEnqueues:
    def test_recut_queues_one_autoedit_job(self, env):
        r = env.client.post(env.url, json={"aspectRatio": "9:16"},
                            headers=env.h(env.owner_tok))
        assert r.status_code == 200, r.text
        assert len(_autoedit_jobs(env)) == 1

    def test_new_shape_is_persisted_and_passed_to_the_job(self, env):
        env.client.post(env.url, json={"aspectRatio": "9:16"},
                        headers=env.h(env.owner_tok))
        assert _project(env)["aspect_ratio"] == "9:16"
        # The job must carry the same value the request decided on, so the
        # worker cannot read a stale shape.
        assert _autoedit_jobs(env)[0]["params"]["aspect_ratio"] == "9:16"

    def test_recut_without_a_shape_keeps_the_current_one(self, env):
        r = env.client.post(env.url, json={}, headers=env.h(env.owner_tok))
        assert r.status_code == 200, r.text
        assert _project(env)["aspect_ratio"] == "16:9"
        assert _autoedit_jobs(env)[0]["params"]["aspect_ratio"] == "16:9"

    def test_project_moves_back_into_processing(self, env):
        env.client.post(env.url, json={"aspectRatio": "1:1"},
                        headers=env.h(env.owner_tok))
        assert _project(env)["status"] == "analyzing"


class TestRecutRefuses:
    def test_rejects_an_unknown_shape(self, env):
        r = env.client.post(env.url, json={"aspectRatio": "21:9"},
                            headers=env.h(env.owner_tok))
        assert r.status_code == 422
        assert not _autoedit_jobs(env)

    def test_rejects_a_non_owner(self, env):
        r = env.client.post(env.url, json={"aspectRatio": "9:16"},
                            headers=env.h(env.other_tok))
        assert r.status_code in (403, 404)
        assert not _autoedit_jobs(env)
        assert _project(env)["aspect_ratio"] == "16:9"   # unchanged

    def test_rejects_anonymous(self, env):
        r = env.client.post(env.url, json={"aspectRatio": "9:16"})
        assert r.status_code in (401, 403)
        assert not _autoedit_jobs(env)

    def test_refuses_when_the_project_has_no_catalog(self, env):
        env.fake.tables["segments"].clear()
        r = env.client.post(env.url, json={"aspectRatio": "9:16"},
                            headers=env.h(env.owner_tok))
        assert r.status_code == 409
        assert not _autoedit_jobs(env)


class TestRecutIdempotency:
    def test_second_call_returns_the_in_flight_job(self, env):
        first = env.client.post(env.url, json={"aspectRatio": "9:16"},
                                headers=env.h(env.owner_tok)).json()
        second = env.client.post(env.url, json={"aspectRatio": "9:16"},
                                 headers=env.h(env.owner_tok)).json()
        assert first["id"] == second["id"]
        assert len(_autoedit_jobs(env)) == 1

    def test_in_flight_recut_blocks_a_shape_change(self, env):
        env.client.post(env.url, json={"aspectRatio": "9:16"},
                        headers=env.h(env.owner_tok))
        env.client.post(env.url, json={"aspectRatio": "1:1"},
                        headers=env.h(env.owner_tok))
        # The queued job was built for 9:16; silently rewriting the project to
        # 1:1 would leave the render and the record disagreeing.
        assert _project(env)["aspect_ratio"] == "9:16"
        assert len(_autoedit_jobs(env)) == 1
