"""FastAPI integration tests for the operator API (actual HTTP routes)."""
import os
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "fake-service-key")
os.environ["WORKER_ENABLED"] = "0"          # no background worker in API tests

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from tests.fake_supa import FakeSupabase, install  # noqa: E402


@pytest.fixture()
def env(monkeypatch):
    fake = FakeSupabase()
    install(monkeypatch, fake)
    # rate limiter reset between tests
    from app import main as m
    m._rate.clear()
    owner_id, owner_tok = fake.add_user("owner@example.com")
    other_id, other_tok = fake.add_user("other@example.com")
    op_id, op_tok = fake.add_user("operator@example.com", operator=True)
    project = fake.add_project(owner_id, "API Test", status="ready")
    client = TestClient(app, raise_server_exceptions=False)
    return SimpleEnv(fake=fake, client=client, project=project,
                     owner=(owner_id, owner_tok), other=(other_id, other_tok),
                     operator=(op_id, op_tok))


class SimpleEnv:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def h(self, token):
        return {"Authorization": f"Bearer {token}"}


# ---------- authn/z ----------
def test_unauthenticated_401(env):
    r = env.client.post(f"/projects/{env.project['id']}/analyze", json={})
    assert r.status_code == 401


def test_normal_user_403(env):
    r = env.client.post(f"/projects/{env.project['id']}/analyze",
                        json={"params": {}}, headers=env.h(env.owner[1]))
    assert r.status_code == 403


@pytest.mark.parametrize("path,kind", [
    ("analyze", "analysis"), ("generate-draft", "autoedit"),
    ("revise", "revision"), ("render-final", "final_render")])
def test_operator_can_enqueue_each_kind(env, path, kind):
    r = env.client.post(f"/projects/{env.project['id']}/{path}",
                        json={"params": {}}, headers=env.h(env.operator[1]))
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == kind
    # audited BEFORE the action
    audits = env.fake.tables["operator_audit"]
    assert any(a["action"] == f"enqueue_{kind}" for a in audits)


def test_unknown_project_404(env):
    r = env.client.post("/projects/00000000-0000-0000-0000-000000000000/analyze",
                        json={"params": {}}, headers=env.h(env.operator[1]))
    assert r.status_code == 404


# ---------- jobs ----------
def _mk_job(env, status="failed", attempts=1, kind="analysis"):
    from app import jobs
    j = jobs.enqueue_job(env.project["id"], env.project["user_id"], kind)
    env.fake.patch("pipeline_jobs", f"id=eq.{j['id']}",
                   {"status": status, "attempt_count": attempts})
    return env.fake.select("pipeline_jobs", f"id=eq.{j['id']}")[0]


def test_owner_reads_own_job_but_not_others(env):
    job = _mk_job(env, status="queued")
    r = env.client.get(f"/jobs/{job['id']}", headers=env.h(env.owner[1]))
    assert r.status_code == 200
    r = env.client.get(f"/jobs/{job['id']}", headers=env.h(env.other[1]))
    assert r.status_code == 403


def test_operator_retry_failed_job(env):
    job = _mk_job(env, status="failed", attempts=1)
    r = env.client.post(f"/jobs/{job['id']}/retry", headers=env.h(env.operator[1]))
    assert r.status_code == 200 and r.json()["status"] == "queued"


def test_retry_completed_rejected(env):
    job = _mk_job(env, status="completed")
    r = env.client.post(f"/jobs/{job['id']}/retry", headers=env.h(env.operator[1]))
    assert r.status_code == 409


def test_retry_after_max_attempts_rejected(env):
    job = _mk_job(env, status="failed", attempts=3)
    r = env.client.post(f"/jobs/{job['id']}/retry", headers=env.h(env.operator[1]))
    assert r.status_code == 409


def test_cancel_queued_job(env):
    job = _mk_job(env, status="queued")
    r = env.client.post(f"/jobs/{job['id']}/cancel", headers=env.h(env.operator[1]))
    assert r.status_code == 200 and r.json()["status"] == "cancelled"


def test_cancel_processing_job_two_phase(env):
    job = _mk_job(env, status="processing")
    r = env.client.post(f"/jobs/{job['id']}/cancel", headers=env.h(env.operator[1]))
    assert r.status_code == 200 and r.json()["status"] == "cancel_requested"
    row = env.fake.select("pipeline_jobs", f"id=eq.{job['id']}")[0]
    assert row["cancel_requested_by"] == env.operator[0]


def test_cancel_completed_rejected(env):
    job = _mk_job(env, status="completed")
    r = env.client.post(f"/jobs/{job['id']}/cancel", headers=env.h(env.operator[1]))
    assert r.status_code == 409


def test_cancel_already_cancelled_rejected(env):
    job = _mk_job(env, status="cancelled")
    r = env.client.post(f"/jobs/{job['id']}/cancel", headers=env.h(env.operator[1]))
    assert r.status_code == 409


# ---------- cross-project protections ----------
def test_operator_cannot_sign_foreign_path(env):
    other_project = env.fake.add_project(env.other[0], "Other")
    foreign = (f"users/{env.other[0]}/projects/{other_project['id']}"
               f"/raw/x/clip.mp4")
    r = env.client.post(f"/projects/{env.project['id']}/sign",
                        json={"bucket": "raw-footage", "path": foreign},
                        headers=env.h(env.operator[1]))
    assert r.status_code == 403


def test_timeline_ops_reject_foreign_timeline(env):
    other_project = env.fake.add_project(env.other[0], "Other")
    tl = env.fake.insert("timelines", {
        "project_id": other_project["id"], "user_id": env.other[0],
        "version": 1, "timeline_json": {"version": 1, "tracks": []}}).json()[0]
    r = env.client.post(f"/projects/{env.project['id']}/timeline-ops",
                        json={"base_timeline_id": tl["id"], "operations": []},
                        headers=env.h(env.operator[1]))
    assert r.status_code == 404


def test_segment_flag_requires_existing_segment(env):
    r = env.client.post("/segments/00000000-0000-0000-0000-000000000000/flag",
                        json={"unusable": True}, headers=env.h(env.operator[1]))
    assert r.status_code == 404


# ---------- audiovisual preproduction ----------
def _add_preproduction_segments(env):
    base = {
        "schemaVersion": 2, "assetId": "asset-a", "sourceStart": 0,
        "sourceEnd": 5, "subjects": ["athlete"], "shotType": "medium",
        "cameraAngle": "eye level", "cameraMovement": "static",
        "location": "gym", "transcript": None, "emotion": "focused",
        "focusScore": 0.8, "exposureScore": 0.8, "stabilityScore": 0.8,
        "audioScore": 0.8, "semanticRelevance": 0.8,
        "duplicateGroupId": None, "problems": [],
    }
    for index, (uses, motion, action, shot, movement) in enumerate([
        (["hook", "peak"], 0.95, "sprint start", "close", "static"),
        (["location", "broll"], 0.1, "gym establishing", "wide", "static"),
        (["early_effort"], 0.5, "running", "medium", "gimbal follow"),
        (["build"], 0.7, "sled push", "medium", "tracking"),
        (["completion", "reflection"], 0.2, "recovery", "medium", "static"),
    ]):
        sid = f"seg-{index}"
        data = {**base, "segmentId": sid, "storyUses": uses,
                "motionIntensity": motion, "action": action, "shotType": shot,
                "cameraMovement": movement, "searchText": action,
                "sourceStart": index * 5, "sourceEnd": index * 5 + 5}
        env.fake.insert("segments", {
            "project_id": env.project["id"], "user_id": env.project["user_id"],
            "asset_id": "asset-a", "segment_key": sid, "data": data,
        })


def test_operator_creates_audited_preproduction_contract(env):
    _add_preproduction_segments(env)
    r = env.client.post(
        f"/projects/{env.project['id']}/preproduction",
        json={"purpose": "authentic training recap", "targetDurationSeconds": 24,
              "targetPlatform": "vertical"},
        headers=env.h(env.operator[1]),
    )
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["version"] == 1
    assert payload["creativeTreatment"]["orientation"] == "9:16"
    assert len(payload["storyVariants"]["variants"]) == 5
    saved = env.fake.tables["preproduction_runs"][0]
    assert saved["project_id"] == env.project["id"]
    assert any(a["action"] == "create_preproduction"
               for a in env.fake.tables["operator_audit"])
    second = env.client.post(
        f"/projects/{env.project['id']}/preproduction",
        json={"purpose": "alternate training recap", "targetDurationSeconds": 20},
        headers=env.h(env.operator[1]),
    )
    assert second.status_code == 200 and second.json()["version"] == 2
    assert [row["version"] for row in env.fake.tables["preproduction_runs"]] == [1, 2]


def test_preproduction_requires_operator_and_catalog(env):
    path = f"/projects/{env.project['id']}/preproduction"
    denied = env.client.post(path, json={}, headers=env.h(env.owner[1]))
    assert denied.status_code == 403
    missing = env.client.post(path, json={}, headers=env.h(env.operator[1]))
    assert missing.status_code == 409


def test_preproduction_rejects_out_of_scope_duration(env):
    _add_preproduction_segments(env)
    r = env.client.post(
        f"/projects/{env.project['id']}/preproduction",
        json={"targetDurationSeconds": 90}, headers=env.h(env.operator[1]),
    )
    assert r.status_code == 422


# ---------- audiovisual picture editor ----------
def _create_preproduction(env):
    _add_preproduction_segments(env)
    response = env.client.post(
        f"/projects/{env.project['id']}/preproduction",
        json={"purpose": "Milestone 2 picture edit", "targetDurationSeconds": 24},
        headers=env.h(env.operator[1]),
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_operator_creates_versioned_audited_picture_candidates(env):
    preproduction = _create_preproduction(env)
    baseline = _project_timeline(env)
    baseline_json = baseline["timeline_json"].copy()
    path = f"/projects/{env.project['id']}/picture-edit"
    response = env.client.post(
        path, json={"preproductionRunId": preproduction["id"]},
        headers=env.h(env.operator[1]),
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["version"] == 1
    assert len(payload["candidates"]) == 3
    assert len(payload["visualRhythmPlans"]) == 3
    assert payload["selectedCandidateId"]
    assert {c["timeline"]["meta"]["milestone"] for c in payload["candidates"]} == {
        "audiovisual_picture_editor_v1",
    }
    assert all([track["type"] for track in c["timeline"]["tracks"]] == ["video"]
               for c in payload["candidates"])
    assert env.fake.select("timelines", f"id=eq.{baseline['id']}")[0][
        "timeline_json"] == baseline_json
    saved = env.fake.tables["picture_edit_runs"][0]
    assert saved["preproduction_run_id"] == preproduction["id"]
    assert any(a["action"] == "create_picture_edit"
               for a in env.fake.tables["operator_audit"])

    second = env.client.post(path, json={}, headers=env.h(env.operator[1]))
    assert second.status_code == 200, second.text
    assert second.json()["version"] == 2
    assert [row["version"] for row in env.fake.tables["picture_edit_runs"]] == [1, 2]


def test_picture_edit_accepts_valid_preproduction_uuid(env):
    preproduction = _create_preproduction(env)
    response = env.client.post(
        f"/projects/{env.project['id']}/picture-edit",
        json={"preproductionRunId": preproduction["id"]},
        headers=env.h(env.operator[1]),
    )
    assert response.status_code == 200, response.text
    assert env.fake.tables["picture_edit_runs"][0]["preproduction_run_id"] == (
        preproduction["id"]
    )


def test_picture_edit_rejects_malformed_preproduction_uuid_with_422(env):
    response = env.client.post(
        f"/projects/{env.project['id']}/picture-edit",
        json={"preproductionRunId": "not-a-uuid"},
        headers=env.h(env.operator[1]),
    )
    assert response.status_code == 422
    assert env.fake.tables["picture_edit_runs"] == []


def test_picture_edit_rejects_query_injection_as_invalid_uuid(env):
    response = env.client.post(
        f"/projects/{env.project['id']}/picture-edit",
        json={"preproductionRunId": "00000000-0000-0000-0000-000000000001&or=(id.neq.x)"},
        headers=env.h(env.operator[1]),
    )
    assert response.status_code == 422
    assert env.fake.tables["picture_edit_runs"] == []


def test_picture_edit_requires_operator_catalog_and_preproduction(env):
    path = f"/projects/{env.project['id']}/picture-edit"
    assert env.client.post(path, json={}).status_code == 401
    assert env.client.post(path, json={}, headers=env.h(env.owner[1])).status_code == 403
    no_catalog = env.client.post(path, json={}, headers=env.h(env.operator[1]))
    assert no_catalog.status_code == 409
    _add_preproduction_segments(env)
    no_preproduction = env.client.post(path, json={}, headers=env.h(env.operator[1]))
    assert no_preproduction.status_code == 409
    assert "preproduction" in no_preproduction.json()["detail"]


def test_picture_edit_rejects_cross_project_preproduction_reference(env):
    preproduction = _create_preproduction(env)
    other_project = env.fake.add_project(env.project["user_id"], "Other", status="ready")
    env.fake.tables["segments"][0]["project_id"] = other_project["id"]
    path = f"/projects/{other_project['id']}/picture-edit"
    response = env.client.post(
        path, json={"preproductionRunId": preproduction["id"]},
        headers=env.h(env.operator[1]),
    )
    assert response.status_code == 409


# ---------- audiovisual music and sound supervisor ----------
def _create_picture_edit(env):
    preproduction = _create_preproduction(env)
    response = env.client.post(
        f"/projects/{env.project['id']}/picture-edit",
        json={"preproductionRunId": preproduction["id"]},
        headers=env.h(env.operator[1]),
    )
    assert response.status_code == 200, response.text
    return preproduction, response.json()


def test_operator_creates_versioned_audited_music_sound_plan(env):
    preproduction, picture = _create_picture_edit(env)
    baseline_candidates = env.fake.tables["picture_edit_runs"][0]["candidates"].copy()
    path = f"/projects/{env.project['id']}/music-sound"
    response = env.client.post(
        path, json={"pictureEditRunId": picture["id"]},
        headers=env.h(env.operator[1]),
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["version"] == 1
    plan = payload["musicPlan"]
    assert plan["preproductionRunId"] == preproduction["id"]
    assert plan["pictureEditRunId"] == picture["id"]
    assert plan["selectedCandidateId"] == picture["selectedCandidateId"]
    assert plan["beatPhraseAnalysis"]["phrases"]
    assert plan["loudnessTargets"]["integratedLufs"] == -14
    saved = env.fake.tables["music_sound_runs"][0]
    assert saved["picture_edit_run_id"] == picture["id"]
    assert env.fake.tables["picture_edit_runs"][0]["candidates"] == baseline_candidates
    assert any(a["action"] == "create_music_sound_plan"
               for a in env.fake.tables["operator_audit"])

    second = env.client.post(path, json={}, headers=env.h(env.operator[1]))
    assert second.status_code == 200, second.text
    assert second.json()["version"] == 2


def test_music_sound_requires_operator_and_picture_edit(env):
    path = f"/projects/{env.project['id']}/music-sound"
    assert env.client.post(path, json={}).status_code == 401
    assert env.client.post(path, json={}, headers=env.h(env.owner[1])).status_code == 403
    missing = env.client.post(path, json={}, headers=env.h(env.operator[1]))
    assert missing.status_code == 409


def test_music_sound_rejects_malformed_picture_run_uuid(env):
    response = env.client.post(
        f"/projects/{env.project['id']}/music-sound",
        json={"pictureEditRunId": "bad&id=neq.injected"},
        headers=env.h(env.operator[1]),
    )
    assert response.status_code == 422


def test_music_sound_rejects_cross_project_picture_reference(env):
    _, picture = _create_picture_edit(env)
    other_project = env.fake.add_project(env.project["user_id"], "Other", status="ready")
    response = env.client.post(
        f"/projects/{other_project['id']}/music-sound",
        json={"pictureEditRunId": picture["id"]},
        headers=env.h(env.operator[1]),
    )
    assert response.status_code == 409


# ---------- audiovisual real licensed-audio rendering ----------
def _create_music_sound(env):
    _, picture = _create_picture_edit(env)
    response = env.client.post(
        f"/projects/{env.project['id']}/music-sound",
        json={"pictureEditRunId": picture["id"]},
        headers=env.h(env.operator[1]),
    )
    assert response.status_code == 200, response.text
    return picture, response.json()


def _install_audio_render_stubs(monkeypatch):
    from pathlib import Path

    from app.pipeline import audio_rendering as ar

    analysis = ar.ActualWaveformAnalysis(
        bpm=120, bpmConfidence=0.95,
        beatLocations=[ar.ActualBeat(
            timeSeconds=index * 0.5, strength=1, beatIndex=index,
            beatInBar=index % 4 + 1, barIndex=index // 4,
            isDownbeat=index % 4 == 0,
        ) for index in range(4)],
        downbeatLocations=[0], barLocations=[0], phraseBoundaries=[24],
        energyEnvelope=[ar.ActualEnergyPoint(timeSeconds=0, energy=0.4),
                        ar.ActualEnergyPoint(timeSeconds=24, energy=1)],
    )
    monkeypatch.setattr(ar, "probe_music_file", lambda *a, **k: ar.TrackMediaInfo(
        codec="pcm_s16le", durationSeconds=30, channels=2, sampleRateHz=48000,
        contentType="audio/wav", filename="licensed.wav",
    ))
    monkeypatch.setattr(ar, "analyze_actual_waveform", lambda path: analysis)
    monkeypatch.setattr(ar, "match_picture_to_actual_track", lambda *a, **k: ar.TrackMatch(
        targetAnalysisSource="treatment_derived_music_brief", targetBpm=120,
        actualBpm=120, bpmDifference=0, musicSourceStartSeconds=0,
        musicSourceEndSeconds=24, endingPhraseBoundarySeconds=24,
        phraseResolvedEnding=True, syncInstructions=[], energyComparison=[],
    ))
    def render_stub(candidate, sources, music, plan, match, output, workdir):
        Path(output).write_bytes(b"real-preview")
        return {"input_i": "-14.10", "input_tp": "-1.10"}, []
    monkeypatch.setattr(ar, "render_completed_mix", render_stub)
    monkeypatch.setattr(ar, "analyze_audio_qc", lambda path: ar.AudioQC(
        integratedLufs=-14.1, truePeakDbtp=-1.1, clippingDetected=False,
        silenceRanges=[], abruptGainChanges=[], missingAudioStream=False,
        missingVideoStream=False, durationSeconds=24, passed=True,
    ))


def _upload_music(env, filename="licensed.wav", content_type="audio/wav", content=b"wave"):
    return env.client.post(
        f"/projects/{env.project['id']}/licensed-music/upload",
        params={"filename": filename, "content_type": content_type},
        content=content, headers={**env.h(env.operator[1]),
                                  "Content-Type": "application/octet-stream"},
    )


def _audio_render_body(music, upload):
    return {
        "musicSoundRunId": music["id"], "storagePath": upload["storagePath"],
        "filename": upload["filename"], "contentType": upload["contentType"],
        "sizeBytes": upload["sizeBytes"],
        "licenseMetadata": {"provider": "Artlist", "licenseType": "commercial",
                            "licenseReference": "license-123",
                            "confirmedByOperator": True},
    }


def test_operator_attaches_replaces_and_renders_licensed_music(env, monkeypatch):
    picture, music = _create_music_sound(env)
    env.fake.insert("media_assets", {
        "id": "asset-a", "project_id": env.project["id"],
        "user_id": env.project["user_id"], "filename": "source.mp4",
        "storage_path": (f"users/{env.project['user_id']}/projects/"
                         f"{env.project['id']}/raw/asset-a/source.mp4"),
        "size_bytes": 10,
    })
    env.fake.storage["raw-footage/" + env.fake.tables["media_assets"][0][
        "storage_path"]] = b"source"
    baseline = env.fake.tables["picture_edit_runs"][0]["candidates"].copy()
    _install_audio_render_stubs(monkeypatch)
    uploaded = _upload_music(env)
    assert uploaded.status_code == 200, uploaded.text
    response = env.client.post(
        f"/projects/{env.project['id']}/audio-render",
        json=_audio_render_body(music, uploaded.json()),
        headers=env.h(env.operator[1]),
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "qc_passed"
    assert payload["completedAudioMix"]["analysis"]["analysisSource"] == "actual_waveform"
    assert payload["completedAudioMix"]["pictureTimingChanged"] is False
    assert env.fake.tables["picture_edit_runs"][0]["candidates"] == baseline
    assert env.fake.tables["licensed_music_assets"][0]["music_sound_run_id"] == music["id"]
    assert env.fake.tables["audio_mix_runs"][0]["picture_edit_run_id"] == picture["id"]
    preview_path = env.fake.tables["audio_mix_runs"][0]["preview_storage_path"]
    assert f"exports/{preview_path}" in env.fake.storage

    replacement = _upload_music(env, content=b"new-wave").json()
    second = env.client.post(
        f"/projects/{env.project['id']}/audio-render",
        json=_audio_render_body(music, replacement), headers=env.h(env.operator[1]),
    )
    assert second.status_code == 200 and second.json()["version"] == 2
    assert [row["version"] for row in env.fake.tables["licensed_music_assets"]] == [1, 2]


def test_audio_upload_and_render_authorization_and_metadata(env):
    path = f"/projects/{env.project['id']}/licensed-music/upload"
    params = {"filename": "licensed.wav", "content_type": "audio/wav"}
    assert env.client.post(path, params=params, content=b"x").status_code == 401
    assert env.client.post(path, params=params, content=b"x",
                           headers=env.h(env.owner[1])).status_code == 403
    assert _upload_music(env, filename="malware.exe",
                         content_type="application/octet-stream").status_code == 422
    _, music = _create_music_sound(env)
    upload = _upload_music(env).json()
    body = _audio_render_body(music, upload)
    body["licenseMetadata"]["confirmedByOperator"] = False
    response = env.client.post(f"/projects/{env.project['id']}/audio-render",
                               json=body, headers=env.h(env.operator[1]))
    assert response.status_code == 422
    del body["licenseMetadata"]["licenseReference"]
    body["licenseMetadata"]["confirmedByOperator"] = True
    missing = env.client.post(f"/projects/{env.project['id']}/audio-render",
                              json=body, headers=env.h(env.operator[1]))
    assert missing.status_code == 422
    body = _audio_render_body(music, upload)
    body["filename"] = "../licensed.wav"
    traversal = env.client.post(f"/projects/{env.project['id']}/audio-render",
                                json=body, headers=env.h(env.operator[1]))
    assert traversal.status_code == 422


def test_audio_render_rejects_cross_project_music_ownership(env):
    _, music = _create_music_sound(env)
    upload = _upload_music(env).json()
    other = env.fake.add_project(env.project["user_id"], "Other", status="ready")
    response = env.client.post(
        f"/projects/{other['id']}/audio-render",
        json=_audio_render_body(music, upload), headers=env.h(env.operator[1]),
    )
    assert response.status_code == 403


# ---------- audiovisual visual finishing ----------
def _create_audio_mix(env, monkeypatch):
    picture, music = _create_music_sound(env)
    env.fake.insert("media_assets", {
        "id": "asset-a", "project_id": env.project["id"],
        "user_id": env.project["user_id"], "filename": "source.mp4",
        "storage_path": (f"users/{env.project['user_id']}/projects/"
                         f"{env.project['id']}/raw/asset-a/source.mp4"),
        "size_bytes": 10,
    })
    source_path = env.fake.tables["media_assets"][0]["storage_path"]
    env.fake.storage["raw-footage/" + source_path] = b"source"
    _install_audio_render_stubs(monkeypatch)
    upload = _upload_music(env).json()
    response = env.client.post(
        f"/projects/{env.project['id']}/audio-render",
        json=_audio_render_body(music, upload), headers=env.h(env.operator[1]),
    )
    assert response.status_code == 200, response.text
    return picture, response.json()


def test_visual_finishing_authorization_uuid_boundary_and_ownership(env):
    path = f"/projects/{env.project['id']}/visual-finishing"
    assert env.client.post(path, json={}).status_code == 401
    assert env.client.post(path, json={}, headers=env.h(env.owner[1])).status_code == 403
    malformed = env.client.post(path, json={"audioMixRunId": "not-a-uuid"},
                                headers=env.h(env.operator[1]))
    assert malformed.status_code == 422
    injected = env.client.post(path, json={
        "audioMixRunId": "00000000-0000-0000-0000-000000000001&or=(id.neq.x)"},
        headers=env.h(env.operator[1]))
    assert injected.status_code == 422
    foreign = env.fake.insert("audio_mix_runs", {
        "project_id": "00000000-0000-0000-0000-000000000099",
        "user_id": env.other[0],
        "version": 1, "status": "qc_passed",
    }).json()[0]
    cross = env.client.post(path, json={"audioMixRunId": foreign["id"]},
                            headers=env.h(env.operator[1]))
    assert cross.status_code == 409


def test_operator_creates_complete_immutable_visual_finishing_lineage(env, monkeypatch):
    from pathlib import Path

    from app.pipeline import visual_finishing as vf

    picture, audio = _create_audio_mix(env, monkeypatch)
    baseline = env.fake.tables["picture_edit_runs"][0]["candidates"].copy()

    def finishing_stub(source, output, graphics, captions, color):
        Path(output).write_bytes(b"visual-finishing-preview")
        return {"durationSeconds": 24, "videoStreamPresent": True,
                "audioStreamPresent": True, "width": 1080, "height": 1920,
                "pictureTimingChanged": False, "audioChanged": False,
                "graphicsEvents": len(graphics.events),
                "captionGroups": len(captions.groups)}

    monkeypatch.setattr(vf, "render_finishing_preview", finishing_stub)
    response = env.client.post(
        f"/projects/{env.project['id']}/visual-finishing",
        json={"audioMixRunId": audio["id"], "aspect": "9:16",
              "lutPreset": "clean_warm"}, headers=env.h(env.operator[1]),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "qc_passed"
    assert body["graphics"]["pictureTimingChanged"] is False
    assert body["graphics"]["audioChanged"] is False
    assert body["color"]["nonDestructive"] is True
    assert len(env.fake.tables["graphics_runs"]) == 1
    assert len(env.fake.tables["caption_runs"]) == 1
    assert len(env.fake.tables["color_runs"]) == 1
    assert env.fake.tables["caption_runs"][0]["graphics_run_id"] == body["graphicsRunId"]
    assert env.fake.tables["color_runs"][0]["caption_run_id"] == body["captionRunId"]
    assert env.fake.tables["graphics_runs"][0]["audio_mix_run_id"] == audio["id"]
    assert env.fake.tables["picture_edit_runs"][0]["candidates"] == baseline
    preview = env.fake.tables["color_runs"][0]["preview_storage_path"]
    assert f"exports/{preview}" in env.fake.storage
    assert any(item["action"] == "create_visual_finishing"
               for item in env.fake.tables["operator_audit"])


# ---------- audiovisual editorial intelligence ----------
def test_editorial_intelligence_authorization_and_uuid_boundary(env):
    path = f"/projects/{env.project['id']}/editorial-intelligence"
    assert env.client.post(path, json={}).status_code == 401
    assert env.client.post(path, json={}, headers=env.h(env.owner[1])).status_code == 403
    malformed = env.client.post(path, json={"colorRunId": "not-a-uuid"},
                                headers=env.h(env.operator[1]))
    assert malformed.status_code == 422
    injected = env.client.post(path, json={
        "colorRunId": "00000000-0000-0000-0000-000000000001&or=(id.neq.x)"},
        headers=env.h(env.operator[1]))
    assert injected.status_code == 422


def test_operator_builds_immutable_editorial_tournament(env, monkeypatch):
    from pathlib import Path

    from app.pipeline import editorial_intelligence as ei
    from app.pipeline import visual_finishing as vf

    _, audio = _create_audio_mix(env, monkeypatch)
    picture_row = env.fake.tables["picture_edit_runs"][0]
    valid = next(item for item in picture_row["candidates"] if item["valid"])
    if len([item for item in picture_row["candidates"] if item["valid"]]) < 3:
        import copy
        picture_row["candidates"] = [valid, *[
            {**copy.deepcopy(valid), "candidateId": f"api-variant-{index}",
             "label": f"API variant {index}"} for index in (2, 3)
        ]]

    def finishing_stub(source, output, graphics, captions, color):
        Path(output).write_bytes(b"visual-preview")
        return {"durationSeconds": 24, "videoStreamPresent": True,
                "audioStreamPresent": True, "width": 1080, "height": 1920,
                "pictureTimingChanged": False, "audioChanged": False}

    monkeypatch.setattr(vf, "render_finishing_preview", finishing_stub)
    visual = env.client.post(
        f"/projects/{env.project['id']}/visual-finishing",
        json={"audioMixRunId": audio["id"]}, headers=env.h(env.operator[1]),
    )
    assert visual.status_code == 200, visual.text

    def candidate_render_stub(manifest, sources, completed, output, workdir):
        Path(output).write_bytes(b"complete-editorial-candidate")
        return {"durationSeconds": manifest.pictureTimeline["duration"],
                "videoStreamPresent": True, "audioStreamPresent": True,
                "width": 1080, "height": 1920, "fabricatedFootage": False}

    monkeypatch.setattr(ei, "render_complete_candidate", candidate_render_stub)
    response = env.client.post(
        f"/projects/{env.project['id']}/editorial-intelligence",
        json={"colorRunId": visual.json()["colorRunId"]},
        headers=env.h(env.operator[1]),
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload["candidates"]) >= 3
    assert len(payload["tournament"]["pairwiseComparisons"]) == (
        len(payload["candidates"]) * (len(payload["candidates"]) - 1) // 2
    )
    assert payload["winnerCandidateRunId"] in {
        item["id"] for item in payload["candidates"]
    }
    assert len(env.fake.tables["critic_runs"]) == 10 * len(payload["candidates"])
    assert len(env.fake.tables["publishability_reports"]) == len(payload["candidates"])
    assert len(env.fake.tables["tournament_runs"]) == 1
    assert all(not row["fabricated_footage"] for row in env.fake.tables["candidate_runs"])
    assert any(row["action"] == "create_editorial_intelligence"
               for row in env.fake.tables["operator_audit"])


# ---------- timeline op validation ----------
def _project_timeline(env):
    tl = {"version": 1, "width": 1920, "height": 1080, "fps": 30, "duration": 6,
          "tracks": [{"id": "video-1", "type": "video", "clips": [
              {"id": "c1", "assetId": "a1", "sourceStart": 0, "sourceEnd": 3,
               "timelineStart": 0, "timelineEnd": 3, "volume": 1, "speed": 1},
              {"id": "c2", "assetId": "a1", "sourceStart": 4, "sourceEnd": 7,
               "timelineStart": 3, "timelineEnd": 6, "volume": 1, "speed": 1}]}]}
    return env.fake.insert("timelines", {
        "project_id": env.project["id"], "user_id": env.project["user_id"],
        "version": 1, "timeline_json": tl}).json()[0]


def test_invalid_operation_422(env):
    tl = _project_timeline(env)
    r = env.client.post(f"/projects/{env.project['id']}/timeline-ops",
                        json={"base_timeline_id": tl["id"],
                              "operations": [{"op": "run_ffmpeg", "cmd": "evil"}]},
                        headers=env.h(env.operator[1]))
    assert r.status_code == 422


def test_protected_range_unchanged(env):
    tl = _project_timeline(env)
    r = env.client.post(f"/projects/{env.project['id']}/timeline-ops",
                        json={"base_timeline_id": tl["id"],
                              "operations": [{"op": "delete_clip",
                                              "clipId": "c2"}],
                              "protected_ranges": [[3, 6]]},
                        headers=env.h(env.operator[1]))
    assert r.status_code == 422
    assert "protected" in r.json()["detail"]
    # base timeline untouched (a new version was never created)
    versions = env.fake.select("timelines",
                               f"project_id=eq.{env.project['id']}")
    assert len(versions) == 1


def test_valid_timeline_op_creates_new_version(env):
    tl = _project_timeline(env)
    r = env.client.post(f"/projects/{env.project['id']}/timeline-ops",
                        json={"base_timeline_id": tl["id"],
                              "operations": [{"op": "trim_clip", "clipId": "c1",
                                              "sourceEnd": 2.0}]},
                        headers=env.h(env.operator[1]))
    assert r.status_code == 200
    assert r.json()["version"] == 2


# ---------- audit reliability ----------
def test_action_aborts_when_audit_store_fails(env):
    env.fake.fail_tables.add("operator_audit")
    r = env.client.post(f"/projects/{env.project['id']}/analyze",
                        json={"params": {}}, headers=env.h(env.operator[1]))
    assert r.status_code == 503
    assert "audit" in r.json()["detail"]
    # the action did NOT happen
    assert len(env.fake.tables["pipeline_jobs"]) == 0


def test_rate_limit_on_enqueue(env, monkeypatch):
    from app import main as m
    monkeypatch.setattr(m, "RATE_LIMIT_PER_MIN", 2)
    for i in range(2):
        env.fake.tables["pipeline_jobs"].clear()
        r = env.client.post(f"/projects/{env.project['id']}/analyze",
                            json={"params": {}}, headers=env.h(env.operator[1]))
        assert r.status_code == 200
    r = env.client.post(f"/projects/{env.project['id']}/analyze",
                        json={"params": {}}, headers=env.h(env.operator[1]))
    assert r.status_code == 429


def test_body_size_limit(env):
    r = env.client.post(f"/projects/{env.project['id']}/analyze",
                        content=b"x" * 10,
                        headers={**env.h(env.operator[1]),
                                 "Content-Length": str(50 * 1024 * 1024),
                                 "Content-Type": "application/json"})
    assert r.status_code == 413


def test_unhandled_error_is_sanitized(env, monkeypatch):
    from app import main as m

    def boom(*a, **k):
        raise RuntimeError(r"C:\secret\path\creds.txt exploded")
    monkeypatch.setattr(m.supa, "db_select", boom)
    r = env.client.get("/jobs/any-id", headers=env.h(env.owner[1]))
    assert r.status_code == 500
    assert r.json() == {"detail": "internal error"}
    assert "secret" not in r.text


# ---------- additional route coverage ----------
def test_readyz_ok(env):
    r = env.client.get("/readyz")
    assert r.status_code == 200 and r.json()["ready"] is True


def test_sign_success_for_project_object(env):
    path = (f"users/{env.project['user_id']}/projects/{env.project['id']}"
            f"/raw/x/clip.mp4")
    env.fake.storage[f"raw-footage/{path}"] = b"data"
    r = env.client.post(f"/projects/{env.project['id']}/sign",
                        json={"bucket": "raw-footage", "path": path},
                        headers=env.h(env.operator[1]))
    assert r.status_code == 200 and "url" in r.json()


def test_sign_unknown_bucket_422(env):
    r = env.client.post(f"/projects/{env.project['id']}/sign",
                        json={"bucket": "weird", "path": "x"},
                        headers=env.h(env.operator[1]))
    assert r.status_code == 422


def test_flag_segment_success_roundtrip(env):
    seg = env.fake.insert("segments", {
        "segment_key": "s1", "asset_id": "a1", "project_id": env.project["id"],
        "user_id": env.project["user_id"], "source_start": 0, "source_end": 2,
        "data": {"problems": []}, "search_text": "x"}).json()[0]
    r = env.client.post(f"/segments/{seg['id']}/flag",
                        json={"unusable": True, "reason": "blurry"},
                        headers=env.h(env.operator[1]))
    assert r.status_code == 200
    assert "operator_unusable" in r.json()["problems"]
    r = env.client.post(f"/segments/{seg['id']}/flag",
                        json={"unusable": False},
                        headers=env.h(env.operator[1]))
    assert "operator_unusable" not in r.json()["problems"]


def test_evaluation_recording_and_missing_row(env):
    r = env.client.post(f"/projects/{env.project['id']}/evaluation",
                        json={"fields": {"first_draft_rating": 7}},
                        headers=env.h(env.operator[1]))
    assert r.status_code == 404          # no evaluation row yet
    env.fake.insert("draft_evaluations", {
        "project_id": env.project["id"], "user_id": env.project["user_id"]})
    r = env.client.post(f"/projects/{env.project['id']}/evaluation",
                        json={"fields": {"first_draft_rating": 7,
                                         "human_correction_minutes": 12,
                                         "bogus_field": 1}},
                        headers=env.h(env.operator[1]))
    assert r.status_code == 200
    assert set(r.json()["updated"]) == {"first_draft_rating",
                                        "human_correction_minutes"}


def test_evaluation_rejects_all_unknown_fields(env):
    r = env.client.post(f"/projects/{env.project['id']}/evaluation",
                        json={"fields": {"hack": 1}},
                        headers=env.h(env.operator[1]))
    assert r.status_code == 422


def test_coverage_endpoint(env):
    env.fake.insert("segments", {
        "segment_key": "s1", "asset_id": "a1", "project_id": env.project["id"],
        "user_id": env.project["user_id"], "source_start": 0, "source_end": 5,
        "data": {"schemaVersion": 1, "segmentId": "s1", "assetId": "a1",
                 "sourceStart": 0, "sourceEnd": 5, "storyUses": ["peak"],
                 "motionIntensity": 0.9, "shotType": "wide"},
        "search_text": "x"})
    r = env.client.get(f"/projects/{env.project['id']}/coverage",
                       headers=env.h(env.operator[1]))
    assert r.status_code == 200
    assert r.json()["segmentCount"] == 1


def test_job_get_404(env):
    r = env.client.get("/jobs/00000000-0000-0000-0000-000000000000",
                       headers=env.h(env.operator[1]))
    assert r.status_code == 404


# ---------- human-ceiling evaluation ----------
def _human_ceiling_baselines(env):
    initial = _project_timeline(env)
    revised_json = initial["timeline_json"] | {"duration": 5.5}
    revised = env.fake.insert("timelines", {
        "project_id": env.project["id"], "user_id": env.project["user_id"],
        "version": 2, "timeline_json": revised_json}).json()[0]
    run = env.fake.insert("edit_runs", {
        "project_id": env.project["id"], "user_id": env.project["user_id"],
        "status": "completed", "timeline_v1_id": initial["id"],
        "timeline_v2_id": revised["id"]}).json()[0]
    env.fake.insert("draft_evaluations", {
        "project_id": env.project["id"], "user_id": env.project["user_id"],
        "edit_run_id": run["id"]})
    return initial, revised, run


def _age_timing_event(env, session_id, event_type, seconds):
    """Move a fake persisted server event into the past for deterministic timing."""
    event = next(
        row for row in reversed(env.fake.tables["human_edit_timing_events"])
        if row["human_edit_session_id"] == session_id
        and row["event_type"] == event_type
    )
    event["occurred_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=seconds)
    ).isoformat()


def test_human_ceiling_records_every_operation_and_builds_report(env):
    import copy
    initial, revised, run = _human_ceiling_baselines(env)
    initial_evidence = copy.deepcopy(initial["timeline_json"])
    revised_evidence = copy.deepcopy(revised["timeline_json"])

    start = env.client.post(
        f"/projects/{env.project['id']}/human-ceiling/start",
        json={"autonomous_initial_timeline_id": initial["id"],
              "autonomous_revised_timeline_id": revised["id"],
              "edit_run_id": run["id"]}, headers=env.h(env.operator[1]))
    assert start.status_code == 200, start.text
    session = start.json()["session"]
    human = start.json()["human_timeline"]
    _age_timing_event(env, session["id"], "start", 150)
    assert human["lineage"] == "human_draft"
    assert human["parent_timeline_id"] == revised["id"]

    initial_after = env.fake.select("timelines", f"id=eq.{initial['id']}")[0]
    revised_after = env.fake.select("timelines", f"id=eq.{revised['id']}")[0]
    assert initial_after["timeline_json"] == initial_evidence
    assert revised_after["timeline_json"] == revised_evidence
    assert initial_after["is_immutable"] is True
    assert revised_after["is_immutable"] is True

    edit = env.client.post(
        f"/projects/{env.project['id']}/timeline-ops",
        json={
            "base_timeline_id": human["id"],
            "human_edit_session_id": session["id"],
            "client_reported_seconds": 999,
            "note": "human ceiling pass",
            "operations": [
                {"op": "replace_clip", "clipId": "c1", "assetId": "a2",
                 "sourceStart": 10, "sourceEnd": 12},
                {"op": "trim_clip", "clipId": "c2", "sourceStart": 4.5,
                 "sourceEnd": 6.5},
                {"op": "move_clip", "clipId": "c2", "newIndex": 0},
                {"op": "change_volume", "clipId": "c2", "volume": 0},
                {"op": "add_title", "text": "PROJECT ONE", "durationSeconds": 1},
            ],
        }, headers=env.h(env.operator[1]))
    assert edit.status_code == 200, edit.text
    assert edit.json()["lineage"] == "human_draft"
    corrections = env.fake.select(
        "user_corrections", f"human_edit_session_id=eq.{session['id']}")
    assert [c["correction_type"] for c in corrections] == [
        "replacement", "trim", "reorder", "audio", "title"]
    assert sum(c["client_reported_seconds"] for c in corrections) == 999
    assert sum(c["server_measured_seconds"] for c in corrections) >= 149
    assert edit.json()["timing"]["authoritative_source"] == "server_timestamps"

    approve = env.client.post(
        f"/projects/{env.project['id']}/human-ceiling/approve",
        json={"session_id": session["id"], "client_reported_seconds": 240},
        headers=env.h(env.operator[1]))
    assert approve.status_code == 200, approve.text
    approved_id = approve.json()["approved_timeline_id"]
    approved = env.fake.select("timelines", f"id=eq.{approved_id}")[0]
    assert approved["lineage"] == "human_approved"
    assert approved["is_immutable"] is True
    assert approve.json()["timing"]["server_measured_seconds"] >= 149
    assert approve.json()["timing"]["client_reported_seconds"] == 240

    for timeline_id, overall, publishable in (
        (initial["id"], 4, False), (revised["id"], 3, False),
        (approved_id, 6, True),
    ):
        score = env.client.post(
            f"/projects/{env.project['id']}/human-ceiling/scorecard",
            json={"session_id": session["id"], "timeline_id": timeline_id,
                  "scores": {"hook": overall, "pacing": overall},
                  "overall_rating": overall, "publishable": publishable,
                  "evaluator_role": "operator"},
            headers=env.h(env.operator[1]))
        assert score.status_code == 200, score.text

    report = env.client.get(
        f"/projects/{env.project['id']}/human-ceiling/report"
        f"?session_id={session['id']}", headers=env.h(env.operator[1]))
    assert report.status_code == 200, report.text
    data = report.json()
    assert set(data["versions"]) == {
        "autonomous_initial", "autonomous_revised", "human_approved"}
    assert data["human_work"]["operation_count"] == 5
    assert data["human_work"]["server_measured_seconds"] >= 149
    assert data["human_work"]["client_reported_seconds"] == 240
    assert data["human_work"]["server_measured_minutes"] < 3
    assert data["deltas"]["human_vs_revised_rating"] == 3
    assert "| Autonomous initial |" in data["markdown"]


def test_human_ceiling_prevents_parallel_sessions_and_stale_edits(env):
    initial, revised, run = _human_ceiling_baselines(env)
    payload = {"autonomous_initial_timeline_id": initial["id"],
               "autonomous_revised_timeline_id": revised["id"],
               "edit_run_id": run["id"]}
    first = env.client.post(f"/projects/{env.project['id']}/human-ceiling/start",
                            json=payload, headers=env.h(env.operator[1]))
    assert first.status_code == 200
    second = env.client.post(f"/projects/{env.project['id']}/human-ceiling/start",
                             json=payload, headers=env.h(env.operator[1]))
    assert second.status_code == 409
    session = first.json()["session"]
    stale = env.client.post(
        f"/projects/{env.project['id']}/timeline-ops",
        json={"base_timeline_id": revised["id"],
              "human_edit_session_id": session["id"],
              "operations": [{"op": "trim_clip", "clipId": "c1",
                              "sourceEnd": 2}]},
        headers=env.h(env.operator[1]))
    assert stale.status_code == 409


def test_human_ceiling_accepts_already_frozen_autonomous_baselines(env):
    initial, revised, run = _human_ceiling_baselines(env)
    env.fake.patch("timelines", f"id=eq.{initial['id']}",
                   {"lineage": "autonomous_initial", "is_immutable": True})
    env.fake.patch("timelines", f"id=eq.{revised['id']}",
                   {"lineage": "autonomous_revised", "is_immutable": True})
    response = env.client.post(
        f"/projects/{env.project['id']}/human-ceiling/start",
        json={"autonomous_initial_timeline_id": initial["id"],
              "autonomous_revised_timeline_id": revised["id"],
              "edit_run_id": run["id"]}, headers=env.h(env.operator[1]))
    assert response.status_code == 200, response.text
    assert response.json()["human_timeline"]["lineage"] == "human_draft"
    blocked = env.client.post(
        f"/projects/{env.project['id']}/timeline-ops",
        json={"base_timeline_id": initial["id"],
              "operations": [{"op": "trim_clip", "clipId": "c1",
                              "sourceEnd": 2}]},
        headers=env.h(env.operator[1]))
    assert blocked.status_code == 409
    assert "immutable" in blocked.json()["detail"]


def test_human_ceiling_rejects_unrelated_baselines(env):
    initial = _project_timeline(env)
    revised = env.fake.insert("timelines", {
        "project_id": env.project["id"], "user_id": env.project["user_id"],
        "version": 2, "timeline_json": initial["timeline_json"]}).json()[0]
    response = env.client.post(
        f"/projects/{env.project['id']}/human-ceiling/start",
        json={"autonomous_initial_timeline_id": initial["id"],
              "autonomous_revised_timeline_id": revised["id"]},
        headers=env.h(env.operator[1]))
    assert response.status_code == 422
    assert "same recorded edit run" in response.json()["detail"]


def test_client_time_cannot_override_server_measured_time(env):
    initial, revised, run = _human_ceiling_baselines(env)
    start = env.client.post(
        f"/projects/{env.project['id']}/human-ceiling/start",
        json={"autonomous_initial_timeline_id": initial["id"],
              "autonomous_revised_timeline_id": revised["id"],
              "edit_run_id": run["id"]}, headers=env.h(env.operator[1])).json()
    session, human = start["session"], start["human_timeline"]
    _age_timing_event(env, session["id"], "start", 2)
    env.client.post(
        f"/projects/{env.project['id']}/timeline-ops",
        json={"base_timeline_id": human["id"],
              "human_edit_session_id": session["id"],
              "client_reported_seconds": 600,
              "operations": [{"op": "trim_clip", "clipId": "c1",
                              "sourceEnd": 2}]},
        headers=env.h(env.operator[1]))
    approve = env.client.post(
        f"/projects/{env.project['id']}/human-ceiling/approve",
        json={"session_id": session["id"], "client_reported_seconds": 0},
        headers=env.h(env.operator[1]))
    assert approve.status_code == 200, approve.text
    timing = approve.json()["timing"]
    assert timing["server_measured_seconds"] >= 1
    assert timing["server_measured_seconds"] < 10
    assert timing["client_reported_seconds"] == 0


def test_zero_server_time_approval_is_rejected_when_operations_exist(env, monkeypatch):
    from app import main as main_module

    initial, revised, run = _human_ceiling_baselines(env)
    root = f"/projects/{env.project['id']}/human-ceiling"
    started = env.client.post(
        root + "/start",
        json={"autonomous_initial_timeline_id": initial["id"],
              "autonomous_revised_timeline_id": revised["id"],
              "edit_run_id": run["id"]}, headers=env.h(env.operator[1])).json()
    operation = env.client.post(
        f"/projects/{env.project['id']}/timeline-ops",
        json={"base_timeline_id": started["human_timeline"]["id"],
              "human_edit_session_id": started["session"]["id"],
              "operations": [{"op": "trim_clip", "clipId": "c1",
                              "sourceEnd": 2}]},
        headers=env.h(env.operator[1]))
    assert operation.status_code == 200, operation.text
    events = [row for row in env.fake.tables["human_edit_timing_events"]
              if row["human_edit_session_id"] == started["session"]["id"]]
    fixed = datetime.now(timezone.utc).isoformat()
    events[0].update({"id": "0-start", "occurred_at": fixed})
    events[1].update({"id": "1-operation", "occurred_at": fixed})
    monkeypatch.setattr(main_module, "_now", lambda: fixed)
    approval = env.client.post(
        root + "/approve", json={"session_id": started["session"]["id"]},
        headers=env.h(env.operator[1]))
    assert approval.status_code == 422
    assert "server-measured correction time is zero" in approval.json()["detail"]


def _assert_auth_regression(env, method, path, payload):
    request = getattr(env.client, method)
    assert request(path, json=payload).status_code == 401
    assert request(path, json=payload, headers=env.h(env.owner[1])).status_code == 403


def test_human_ceiling_authorization_regressions_for_every_action(env):
    initial, revised, run = _human_ceiling_baselines(env)
    root = f"/projects/{env.project['id']}/human-ceiling"
    start_payload = {
        "autonomous_initial_timeline_id": initial["id"],
        "autonomous_revised_timeline_id": revised["id"],
        "edit_run_id": run["id"],
    }
    _assert_auth_regression(env, "post", f"{root}/start", start_payload)
    started = env.client.post(
        f"{root}/start", json=start_payload, headers=env.h(env.operator[1]))
    assert started.status_code == 200, started.text
    session = started.json()["session"]
    draft = started.json()["human_timeline"]
    _age_timing_event(env, session["id"], "start", 2)

    timing_payload = {"session_id": session["id"], "client_reported_seconds": 77}
    _assert_auth_regression(env, "post", f"{root}/pause", timing_payload)
    paused = env.client.post(
        f"{root}/pause", json=timing_payload, headers=env.h(env.operator[1]))
    assert paused.status_code == 200, paused.text
    assert paused.json()["timing_state"] == "paused"

    _assert_auth_regression(env, "post", f"{root}/resume", timing_payload)
    resumed = env.client.post(
        f"{root}/resume", json=timing_payload, headers=env.h(env.operator[1]))
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["timing_state"] == "running"

    operation_payload = {
        "base_timeline_id": draft["id"],
        "human_edit_session_id": session["id"],
        "client_reported_seconds": 88,
        "operations": [{"op": "trim_clip", "clipId": "c1", "sourceEnd": 2}],
    }
    ops_path = f"/projects/{env.project['id']}/timeline-ops"
    _assert_auth_regression(env, "post", ops_path, operation_payload)
    applied = env.client.post(
        ops_path, json=operation_payload, headers=env.h(env.operator[1]))
    assert applied.status_code == 200, applied.text

    approve_payload = {"session_id": session["id"], "client_reported_seconds": 99}
    _assert_auth_regression(env, "post", f"{root}/approve", approve_payload)
    approved = env.client.post(
        f"{root}/approve", json=approve_payload, headers=env.h(env.operator[1]))
    assert approved.status_code == 200, approved.text

    score_payload = {
        "session_id": session["id"], "timeline_id": initial["id"],
        "scores": {"hook": 5}, "overall_rating": 5,
    }
    _assert_auth_regression(env, "post", f"{root}/scorecard", score_payload)
    score = env.client.post(
        f"{root}/scorecard", json=score_payload, headers=env.h(env.operator[1]))
    assert score.status_code == 200, score.text

    report_path = f"{root}/report?session_id={session['id']}"
    assert env.client.get(report_path).status_code == 401
    assert env.client.get(report_path, headers=env.h(env.owner[1])).status_code == 403
    assert env.client.get(report_path, headers=env.h(env.operator[1])).status_code == 200

    second = env.client.post(
        f"{root}/start", json=start_payload, headers=env.h(env.operator[1]))
    assert second.status_code == 200, second.text
    abandon_payload = {
        "session_id": second.json()["session"]["id"],
        "reason": "Authorization regression evidence",
        "client_reported_seconds": 12,
    }
    _assert_auth_regression(env, "post", f"{root}/abandon", abandon_payload)
    abandoned = env.client.post(
        f"{root}/abandon", json=abandon_payload, headers=env.h(env.operator[1]))
    assert abandoned.status_code == 200, abandoned.text
    assert abandoned.json()["status"] == "abandoned"

    audited = {row["action"] for row in env.fake.tables["operator_audit"]}
    assert {
        "start_human_ceiling", "pause_human_ceiling", "resume_human_ceiling",
        "timeline_ops", "approve_human_ceiling",
        "record_human_ceiling_scorecard", "abandon_human_ceiling",
    }.issubset(audited)


def test_abandon_preserves_nonapproved_draft_and_releases_active_slot(env):
    initial, revised, run = _human_ceiling_baselines(env)
    root = f"/projects/{env.project['id']}/human-ceiling"
    payload = {
        "autonomous_initial_timeline_id": initial["id"],
        "autonomous_revised_timeline_id": revised["id"],
        "edit_run_id": run["id"],
    }
    first = env.client.post(root + "/start", json=payload,
                            headers=env.h(env.operator[1])).json()
    session, draft = first["session"], first["human_timeline"]
    abandoned = env.client.post(
        root + "/abandon",
        json={"session_id": session["id"], "reason": "Operator stopped comparison"},
        headers=env.h(env.operator[1]))
    assert abandoned.status_code == 200, abandoned.text
    evidence = env.fake.select("timelines", f"id=eq.{draft['id']}")[0]
    assert evidence["lineage"] == "human_draft"
    assert evidence["is_immutable"] is True
    stored = env.fake.select("human_edit_sessions", f"id=eq.{session['id']}")[0]
    assert stored["status"] == "abandoned"
    assert stored.get("approved_timeline_id") is None
    assert stored["abandoned_timeline_id"] == draft["id"]
    assert stored["abandonment_reason"] == "Operator stopped comparison"
    restarted = env.client.post(root + "/start", json=payload,
                                headers=env.h(env.operator[1]))
    assert restarted.status_code == 200, restarted.text


def test_zero_revision_project_uses_initial_as_draft_parent_without_fake_baseline(env):
    initial = _project_timeline(env)
    run = env.fake.insert("edit_runs", {
        "project_id": env.project["id"], "user_id": env.project["user_id"],
        "status": "completed", "timeline_v1_id": initial["id"],
        "timeline_v2_id": None,
    }).json()[0]
    env.fake.patch("timelines", f"id=eq.{initial['id']}", {"edit_run_id": run["id"]})
    root = f"/projects/{env.project['id']}/human-ceiling"
    started = env.client.post(
        root + "/start",
        json={"autonomous_initial_timeline_id": initial["id"],
              "edit_run_id": run["id"]},
        headers=env.h(env.operator[1]))
    assert started.status_code == 200, started.text
    body = started.json()
    assert body["comparison_mode"] == "initial_vs_human"
    assert body["session"]["autonomous_revised_timeline_id"] is None
    assert body["human_timeline"]["parent_timeline_id"] == initial["id"]
    _age_timing_event(env, body["session"]["id"], "start", 1)
    edit = env.client.post(
        f"/projects/{env.project['id']}/timeline-ops",
        json={"base_timeline_id": body["human_timeline"]["id"],
              "human_edit_session_id": body["session"]["id"],
              "operations": [{"op": "trim_clip", "clipId": "c1",
                              "sourceEnd": 2}]},
        headers=env.h(env.operator[1]))
    assert edit.status_code == 200, edit.text
    approved = env.client.post(
        root + "/approve", json={"session_id": body["session"]["id"]},
        headers=env.h(env.operator[1]))
    assert approved.status_code == 200, approved.text
    report = env.client.get(
        root + f"/report?session_id={body['session']['id']}",
        headers=env.h(env.operator[1]))
    assert report.status_code == 200, report.text
    assert set(report.json()["versions"]) == {
        "autonomous_initial", "human_approved",
    }
    assert report.json()["deltas"]["human_vs_revised_rating"] is None


def test_operation_index_unique_collision_is_retried(env):
    initial, revised, run = _human_ceiling_baselines(env)
    root = f"/projects/{env.project['id']}/human-ceiling"
    started = env.client.post(
        root + "/start",
        json={"autonomous_initial_timeline_id": initial["id"],
              "autonomous_revised_timeline_id": revised["id"],
              "edit_run_id": run["id"]}, headers=env.h(env.operator[1])).json()
    env.fake.conflict_once_tables.add("user_corrections")
    response = env.client.post(
        f"/projects/{env.project['id']}/timeline-ops",
        json={"base_timeline_id": started["human_timeline"]["id"],
              "human_edit_session_id": started["session"]["id"],
              "operations": [{"op": "trim_clip", "clipId": "c1",
                              "sourceEnd": 2}]},
        headers=env.h(env.operator[1]))
    assert response.status_code == 200, response.text
    rows = env.fake.select(
        "user_corrections",
        f"human_edit_session_id=eq.{started['session']['id']}")
    assert [row["operation_index"] for row in rows] == [1]
