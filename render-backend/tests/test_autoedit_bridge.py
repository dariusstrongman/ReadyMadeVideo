"""Slice 2 — Strategy-B bridge: basic autoedit -> Product Editor candidate.

Covers: bridged candidate creation (no fabricated music/license), idempotency, the
analysis->autoedit hand-off, initial/revised still requiring full ancestry, mixed
ancestry rejection, Product Editor open + save/version, and bridged export (original
audio, no licensed-music mix) with exact-version binding.

fake_supa mirrors the migration 0016 ancestry CHECK; true DB trigger/constraint
enforcement is validated separately against disposable PostgreSQL.
"""
import os
from uuid import uuid4

import pytest

os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "fake-service-key")
os.environ["WORKER_ENABLED"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from app import autoedit_bridge, jobs, supa  # noqa: E402
from app.main import app  # noqa: E402
from tests.fake_supa import FakeSupabase, install  # noqa: E402


def _timeline(asset_id):
    return {"version": 1, "width": 1080, "height": 1920, "fps": 30, "duration": 8,
            "tracks": [{"id": "video-1", "type": "video", "clips": [
                {"id": "clip-a", "assetId": asset_id, "sourceStart": 0, "sourceEnd": 4,
                 "timelineStart": 0, "timelineEnd": 4, "speed": 1, "volume": 1},
                {"id": "clip-b", "assetId": asset_id, "sourceStart": 4, "sourceEnd": 8,
                 "timelineStart": 4, "timelineEnd": 8, "speed": 1, "volume": 1}]}]}


def _setup(monkeypatch, tmp_path):
    fake = FakeSupabase()
    install(monkeypatch, fake)
    uid, token = fake.add_user("owner@example.com")
    project = fake.add_project(uid, status="ready")
    asset_id = str(uuid4())
    key = f"users/{uid}/projects/{project['id']}/raw/clip.mp4"
    fake.insert("media_assets", {"id": asset_id, "project_id": project["id"],
                                 "user_id": uid, "filename": "clip.mp4",
                                 "storage_path": key, "duration_seconds": 8.0})
    fake.storage[f"raw-footage/{key}"] = b"VIDEO"
    tl_row = fake.insert("timelines", {"project_id": project["id"], "user_id": uid,
                                       "version": 1, "timeline_json": _timeline(asset_id),
                                       "lineage": "autonomous_revised"}).json()[0]
    preview = tmp_path / "preview.mp4"
    preview.write_bytes(b"PREVIEW")
    return fake, uid, token, project, asset_id, tl_row, str(preview)


def _bridge(fake, project, tl_row, preview):
    return autoedit_bridge.bridge_from_autoedit(
        project, tl_row, preview, insert=jobs._insert, db_select=supa.db_select,
        upload_export=jobs._upload_export, now=jobs._now)


# ---------------- bridge creation ----------------
def test_bridged_candidate_has_no_music_and_no_fabricated_data(monkeypatch, tmp_path):
    fake, uid, _, project, asset_id, tl_row, preview = _setup(monkeypatch, tmp_path)
    cand = _bridge(fake, project, tl_row, preview)
    assert cand["generation_kind"] == "bridged"
    for k in ("music_sound_run_id", "audio_mix_run_id", "graphics_run_id",
              "caption_run_id", "color_run_id", "parent_candidate_run_id"):
        assert cand[k] is None
    assert cand["fabricated_footage"] is False
    m = cand["manifest"]
    assert m["fabricatedFootage"] is False
    assert m["sourceAssetIds"] == [asset_id]
    assert m["captions"] == {"groups": []} and m["graphics"] == {"events": []}
    assert m["color"]["status"] == "identity"
    # no fabricated tempo/beat/energy/license anywhere
    blob = str(m).lower()
    for banned in ("tempo", "beat", "energy", "license", "bpm"):
        assert banned not in blob
    # real preproduction + picture ancestry created
    assert fake.tables["preproduction_runs"] and fake.tables["picture_edit_runs"]
    assert cand["preview_storage_path"].startswith(
        f"users/{uid}/projects/{project['id']}/autoedit/")


def test_bridge_is_idempotent(monkeypatch, tmp_path):
    fake, _, _, project, _, tl_row, preview = _setup(monkeypatch, tmp_path)
    first = _bridge(fake, project, tl_row, preview)
    second = _bridge(fake, project, tl_row, preview)
    assert first["id"] == second["id"]
    assert len([c for c in fake.tables["candidate_runs"]
                if c["generation_kind"] == "bridged"]) == 1
    assert len(fake.tables["preproduction_runs"]) == 1  # no duplicate ancestry


def test_bridge_returns_none_without_clips(monkeypatch, tmp_path):
    fake, _, _, project, _, _, preview = _setup(monkeypatch, tmp_path)
    empty = fake.insert("timelines", {"project_id": project["id"],
                                      "user_id": project["user_id"], "version": 2,
                                      "timeline_json": {"tracks": []},
                                      "lineage": "autonomous_revised"}).json()[0]
    assert _bridge(fake, project, empty, preview) is None


# ---------------- ancestry integrity (mirrors migration 0016 CHECK) ----------------
def test_mixed_bridged_ancestry_rejected(monkeypatch, tmp_path):
    fake, uid, _, project, _, _, _ = _setup(monkeypatch, tmp_path)
    r = fake.insert("candidate_runs", {
        "batch_id": str(uuid4()), "project_id": project["id"], "user_id": uid,
        "generation_kind": "bridged", "candidate_key": "x", "candidate_index": 1,
        "audio_mix_run_id": "mix-1"})   # contradictory lineage
    assert r.status_code == 400


def test_initial_candidate_requires_full_ancestry(monkeypatch, tmp_path):
    fake, uid, _, project, _, _, _ = _setup(monkeypatch, tmp_path)
    r = fake.insert("candidate_runs", {
        "batch_id": str(uuid4()), "project_id": project["id"], "user_id": uid,
        "generation_kind": "initial", "candidate_key": "x", "candidate_index": 1})
    assert r.status_code == 400


def test_revised_candidate_requires_full_ancestry(monkeypatch, tmp_path):
    fake, uid, _, project, _, _, _ = _setup(monkeypatch, tmp_path)
    r = fake.insert("candidate_runs", {
        "batch_id": str(uuid4()), "project_id": project["id"], "user_id": uid,
        "generation_kind": "revised", "candidate_key": "x", "candidate_index": 1,
        "music_sound_run_id": "m", "audio_mix_run_id": "a", "graphics_run_id": "g",
        "caption_run_id": "c"})   # missing color_run_id
    assert r.status_code == 400


# ---------------- analysis -> autoedit hand-off (idempotent) ----------------
def test_analysis_handoff_enqueues_autoedit_once(monkeypatch, tmp_path):
    fake, _, _, project, _, _, _ = _setup(monkeypatch, tmp_path)
    jobs._maybe_enqueue_customer_autoedit(project)
    autoedit_jobs = [j for j in fake.tables["pipeline_jobs"] if j["kind"] == "autoedit"]
    assert len(autoedit_jobs) == 1
    # active autoedit already present -> no duplicate
    jobs._maybe_enqueue_customer_autoedit(project)
    assert len([j for j in fake.tables["pipeline_jobs"] if j["kind"] == "autoedit"]) == 1


def test_handoff_skips_when_bridged_candidate_exists(monkeypatch, tmp_path):
    fake, _, _, project, _, tl_row, preview = _setup(monkeypatch, tmp_path)
    _bridge(fake, project, tl_row, preview)
    jobs._maybe_enqueue_customer_autoedit(project)
    assert not [j for j in fake.tables["pipeline_jobs"] if j["kind"] == "autoedit"]


# ---------------- Product Editor opens bridged candidate + save/version ----------------
def _auth(t):
    return {"Authorization": f"Bearer {t}"}


def test_product_editor_opens_bridged_and_versions(monkeypatch, tmp_path):
    from app import main
    main._rate.clear()
    fake, uid, token, project, asset_id, tl_row, preview = _setup(monkeypatch, tmp_path)
    cand = _bridge(fake, project, tl_row, preview)
    client = TestClient(app, raise_server_exceptions=False)

    start = client.post(f"/projects/{project['id']}/editor/start",
                        headers=_auth(token), json={"candidateRunId": cand["id"]})
    assert start.status_code == 200, start.text
    doc = start.json()
    assert doc["version"] == 1
    assert fake.tables["editor_documents"]                       # document created
    music_track = next(t for t in doc["document"]["tracks"] if t["type"] == "music")
    assert music_track["items"] == []                            # honest empty music

    picture = next(t for t in doc["document"]["tracks"] if t["type"] == "picture")["items"]
    op = {"expectedVersion": 1, "operations": [{
        "type": "reorder_clip", "actor": "user", "targetId": picture[0]["id"],
        "baseVersion": 1, "toIndex": 1}]}
    saved = client.post(f"/projects/{project['id']}/editor/{doc['id']}/operations",
                        headers=_auth(token), json=op)
    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == 2                          # immutable new version


# ---------------- bridged export (original audio, no licensed-music mix) ----------------
def _stub_render(monkeypatch):
    import app.renderer2 as r2

    def fake_render(tl, sources, out, profile="final", cancel_check=None):
        with open(out, "wb") as fh:
            fh.write(b"MP4BYTES")
        return {"duration": 8.0, "width": 1080, "height": 1920, "size_bytes": 8}
    monkeypatch.setattr(r2, "render_timeline", fake_render)

    import app.pipeline.audio_rendering as ar

    def boom(*a, **k):  # bridged must NEVER call the licensed-music mix
        raise AssertionError("render_completed_mix must not run for bridged export")
    monkeypatch.setattr(ar, "render_completed_mix", boom)


def _editor_doc_row(fake, project, uid, cand, asset_id, version=1):
    document = {
        "schemaVersion": 1, "projectId": project["id"], "candidateRunId": cand["id"],
        "width": 1080, "height": 1920, "fps": 30, "duration": 8,
        "tracks": [
            {"id": "picture", "type": "picture", "items": [
                {"id": "clip-a", "assetId": asset_id, "sourceStart": 0, "sourceEnd": 8,
                 "timelineStart": 0, "timelineEnd": 8, "speed": 1, "assetDuration": 8}]},
            {"id": "captions", "type": "captions", "items": []},
            {"id": "music", "type": "music", "items": []},
            {"id": "sfx", "type": "sfx", "items": []},
            {"id": "graphics", "type": "graphics", "items": []},
        ],
        "sourceAssetIds": [asset_id], "attribution": [],
    }
    return fake.insert("editor_documents", {
        "project_id": project["id"], "user_id": uid, "candidate_run_id": cand["id"],
        "version": version, "document": document}).json()[0]


def test_bridged_export_uses_original_audio(monkeypatch, tmp_path):
    fake, uid, _, project, asset_id, tl_row, preview = _setup(monkeypatch, tmp_path)
    _stub_render(monkeypatch)
    cand = _bridge(fake, project, tl_row, preview)
    doc = _editor_doc_row(fake, project, uid, cand, asset_id, version=1)
    job = {"id": str(uuid4()), "project_id": project["id"],
           "params": {"editor_document_id": doc["id"], "editor_document_version": 1}}
    ctx = jobs.JobContext(job)
    result = jobs.handle_product_editor_render(job, project, str(tmp_path), ctx)
    assert result["editor_document_id"] == doc["id"]
    assert result["editor_document_version"] == 1
    assert result["music_gain_db"] is None            # no music mixed
    assert result["size_bytes"] == 8
    assert result["output"].endswith("-editor-v1.mp4")
    assert f"exports/{result['output']}" in fake.storage


def test_bridged_export_rejects_version_mismatch(monkeypatch, tmp_path):
    fake, uid, _, project, asset_id, tl_row, preview = _setup(monkeypatch, tmp_path)
    _stub_render(monkeypatch)
    cand = _bridge(fake, project, tl_row, preview)
    doc = _editor_doc_row(fake, project, uid, cand, asset_id, version=5)
    job = {"id": str(uuid4()), "project_id": project["id"],
           "params": {"editor_document_id": doc["id"], "editor_document_version": 4}}
    with pytest.raises(RuntimeError, match="version ancestry is inconsistent"):
        jobs.handle_product_editor_render(job, project, str(tmp_path), jobs.JobContext(job))
