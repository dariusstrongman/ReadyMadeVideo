"""Product Editor Phase 1 unit and customer API tests."""
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest

os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "fake-service-key")
os.environ["WORKER_ENABLED"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import _editor_existing_document_filter, app  # noqa: E402
from app.product_editor import (  # noqa: E402
    DeleteClip,
    EditorError,
    ReorderClip,
    RestoreClip,
    SplitClip,
    TrimClip,
    apply_batch,
    document_from_candidate,
    translate_revision,
)
from tests.fake_supa import FakeSupabase, install  # noqa: E402


def candidate(project_id, user_id, asset_id):
    return {
        "id": str(uuid4()), "project_id": project_id, "user_id": user_id,
        "candidate_key": "winner", "manifest": {
            "schemaVersion": 1, "sourceAssetIds": [asset_id],
            "pictureTimeline": {
                "version": 1, "width": 1080, "height": 1920, "fps": 30,
                "duration": 8, "tracks": [{"id": "video-1", "type": "video", "clips": [
                    {"id": "clip-a", "assetId": asset_id, "sourceStart": 0,
                     "sourceEnd": 4, "timelineStart": 0, "timelineEnd": 4,
                     "speed": 1, "volume": 1},
                    {"id": "clip-b", "assetId": asset_id, "sourceStart": 4,
                     "sourceEnd": 8, "timelineStart": 4, "timelineEnd": 8,
                     "speed": 1, "volume": 1},
                ]}],
            },
            "captions": {"groups": [{"id": "cap-a", "displayText": "Original",
                                        "startSeconds": 0, "endSeconds": 2}]},
            "graphics": {"events": [{"id": "graphic-a", "kind": "title"}]},
            "attribution": [], "fabricatedFootage": False,
        },
    }


def make_document():
    asset_id = str(uuid4())
    row = candidate(str(uuid4()), str(uuid4()), asset_id)
    return document_from_candidate(row["project_id"], row, {asset_id: 10})


def test_reorder_trim_split_delete_and_reflow():
    document = make_document()
    operations = [
        ReorderClip(type="reorder_clip", actor="user", targetId="clip-b",
                    baseVersion=1, toIndex=0),
        TrimClip(type="trim_clip", actor="user", targetId="clip-b", baseVersion=1,
                 sourceStart=5, sourceEnd=8),
        SplitClip(type="split_clip", actor="user", targetId="clip-a", baseVersion=1,
                  sourceTime=2),
        DeleteClip(type="delete_clip", actor="user", targetId="clip-a", baseVersion=1),
    ]
    result = apply_batch(document, operations)
    clips = result["tracks"][0]["items"]
    assert [item["id"] for item in clips][0] == "clip-b"
    assert clips[0]["timelineStart"] == 0
    assert result["duration"] == 5


def test_restore_clip_is_typed_ancestry_bounded_and_reflows():
    document = make_document()
    clip = document["tracks"][0]["items"][0]
    deleted = apply_batch(document, [
        DeleteClip(type="delete_clip", actor="user", targetId=clip["id"], baseVersion=1),
    ])
    restored = apply_batch(deleted, [
        RestoreClip(type="restore_clip", actor="user", targetId=clip["id"], baseVersion=2,
                    clip=clip, toIndex=0),
    ])
    assert [item["id"] for item in restored["tracks"][0]["items"]] == ["clip-a", "clip-b"]
    assert restored["duration"] == document["duration"]
    foreign = {**clip, "id": "foreign", "assetId": str(uuid4())}
    with pytest.raises(EditorError, match="ancestry"):
        apply_batch(deleted, [
            RestoreClip(type="restore_clip", actor="user", targetId="foreign",
                        baseVersion=2, clip=foreign, toIndex=0),
        ])


@pytest.mark.parametrize("start,end", [(5, 5), (0, 11)])
def test_trim_bounds_rejected(start, end):
    document = make_document()
    operation = TrimClip(type="trim_clip", actor="user", targetId="clip-a",
                         baseVersion=1, sourceStart=start, sourceEnd=end)
    with pytest.raises(EditorError):
        apply_batch(document, [operation])


def test_conversational_revision_is_typed_and_provider_free():
    document = make_document()
    operations = translate_revision("caption: Better opening", document, 3)
    assert operations[0]["type"] == "update_caption"
    assert operations[0]["actor"] == "ai"
    assert operations[0]["baseVersion"] == 3
    with pytest.raises(EditorError):
        translate_revision("invent a new clip", document, 3)


def test_final_render_dispatches_saved_editor_version(monkeypatch, tmp_path):
    from app import jobs
    captured = {}

    def product_handler(job, project, tmp, ctx):
        captured.update(job["params"])
        return {"output": "bound.mp4"}

    monkeypatch.setattr(jobs, "handle_product_editor_render", product_handler)
    result = jobs.handle_final_render(
        {"params": {"editor_document_id": "doc-1", "editor_document_version": 4}},
        {"id": "project-1"}, str(tmp_path), object(),
    )
    assert result == {"output": "bound.mp4"}
    assert captured == {"editor_document_id": "doc-1", "editor_document_version": 4}


@pytest.fixture()
def env(monkeypatch):
    fake = FakeSupabase()
    install(monkeypatch, fake)
    from app import main
    main._rate.clear()
    owner_id, owner_token = fake.add_user("owner@example.com")
    other_id, other_token = fake.add_user("other@example.com")
    project = fake.add_project(owner_id, "Product editor", status="ready")
    asset_id = str(uuid4())
    fake.insert("media_assets", {"id": asset_id, "project_id": project["id"],
                "user_id": owner_id, "filename": "clip.mp4",
                "storage_path": f"users/{owner_id}/projects/{project['id']}/raw/clip.mp4",
                "duration_seconds": 10})
    candidate_row = candidate(project["id"], owner_id, asset_id)
    fake.insert("candidate_runs", candidate_row)
    return {"fake": fake, "client": TestClient(app, raise_server_exceptions=False),
            "owner": (owner_id, owner_token), "other": (other_id, other_token),
            "project": project, "candidate": candidate_row}


def headers(token):
    return {"Authorization": f"Bearer {token}"}


def start(env):
    return env["client"].post(
        f"/projects/{env['project']['id']}/editor/start",
        json={"candidateRunId": env["candidate"]["id"]},
        headers=headers(env["owner"][1]),
    )


def test_customer_auth_and_candidate_ancestry(env):
    path = f"/projects/{env['project']['id']}/editor/start"
    assert env["client"].post(path, json={"candidateRunId": env["candidate"]["id"]}).status_code == 401
    assert env["client"].post(path, json={"candidateRunId": env["candidate"]["id"]},
                              headers=headers(env["other"][1])).status_code == 403
    response = start(env)
    assert response.status_code == 200, response.text
    assert response.json()["version"] == 1
    assert response.json()["document"]["tracks"][0]["type"] == "picture"


def test_editor_start_reopens_existing_document_with_exact_project_filter(env, monkeypatch):
    """Assert raw PostgREST generation; FakeSupabase parsing is intentionally not trusted."""
    from app import main

    created = start(env)
    assert created.status_code == 200, created.text
    original_select = main.supa.db_select
    captured = []

    def capture(table, filters=""):
        if table == "editor_documents":
            captured.append(filters)
        return original_select(table, filters)

    monkeypatch.setattr(main.supa, "db_select", capture)
    reopened = start(env)
    expected = _editor_existing_document_filter(
        env["candidate"]["id"], env["project"]["id"],
    )
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["id"] == created.json()["id"]
    assert len(env["fake"].tables["editor_documents"]) == 1
    assert captured[0] == expected
    assert f"project_id=eq.{env['project']['id']}" in captured[0]
    assert "{project_id}" not in captured[0]


def test_all_customer_editor_mutations_require_project_owner(env):
    current = start(env).json()
    cases = [
        (f"/projects/{env['project']['id']}/editor/{current['id']}/operations",
         {"expectedVersion": 1, "operations": [{"operationId": str(uuid4()),
          "type": "update_caption", "actor": "user", "targetId": "cap-a",
          "baseVersion": 1, "text": "Owner only"}]}),
        (f"/projects/{env['project']['id']}/editor/revisions/propose",
         {"documentId": current["id"], "expectedVersion": 1, "prompt": "mute music"}),
        (f"/projects/{env['project']['id']}/editor/render", {"documentId": current["id"]}),
    ]
    for path, body in cases:
        assert env["client"].post(path, json=body).status_code == 401
        assert env["client"].post(path, json=body,
                                  headers=headers(env["other"][1])).status_code == 403


def test_append_only_operation_and_conflict(env):
    current = start(env).json()
    operation = {"operationId": str(uuid4()), "type": "reorder_clip", "actor": "user",
                 "targetId": "clip-b", "baseVersion": 1, "toIndex": 0}
    path = f"/projects/{env['project']['id']}/editor/{current['id']}/operations"
    response = env["client"].post(path, json={"expectedVersion": 1,
                                              "operations": [operation]},
                                  headers=headers(env["owner"][1]))
    assert response.status_code == 200, response.text
    assert response.json()["version"] == 2
    assert len(env["fake"].tables["editor_documents"]) == 2
    assert len(env["fake"].tables["editor_operations"]) == 1
    conflict = env["client"].post(path, json={"expectedVersion": 1,
                                               "operations": [operation]},
                                   headers=headers(env["owner"][1]))
    assert conflict.status_code == 409


def test_revision_proposal_and_exact_version_render_binding(env):
    current = start(env).json()
    proposed = env["client"].post(
        f"/projects/{env['project']['id']}/editor/revisions/propose",
        json={"documentId": current["id"], "expectedVersion": 1,
              "prompt": "caption: Ship the strongest cut"},
        headers=headers(env["owner"][1]),
    )
    assert proposed.status_code == 200
    assert proposed.json()["providerCalled"] is False
    applied = env["client"].post(
        f"/projects/{env['project']['id']}/editor/{current['id']}/operations",
        json={"expectedVersion": 1, "operations": proposed.json()["operations"]},
        headers=headers(env["owner"][1]),
    )
    assert applied.status_code == 200, applied.text
    current = applied.json()
    rendered = env["client"].post(
        f"/projects/{env['project']['id']}/editor/render",
        json={"documentId": current["id"]}, headers=headers(env["owner"][1]),
    )
    assert rendered.status_code == 200, rendered.text
    job = rendered.json()
    assert job["params"]["editor_document_id"] == current["id"]
    request = env["fake"].tables["editor_render_requests"][0]
    assert request["editor_document_version"] == 2


def test_ai_actor_cannot_be_spoofed_without_proposal(env):
    current = start(env).json()
    operation = {"operationId": str(uuid4()), "type": "update_caption", "actor": "ai",
                 "targetId": "cap-a", "baseVersion": 1, "text": "Spoofed"}
    response = env["client"].post(
        f"/projects/{env['project']['id']}/editor/{current['id']}/operations",
        json={"expectedVersion": 1, "operations": [operation]},
        headers=headers(env["owner"][1]),
    )
    assert response.status_code == 422
    assert "proposal" in response.text.lower()


def test_cc_by_selection_propagates_attribution_and_blocks_export(env):
    env["candidate"]["manifest"]["musicAssetSelection"] = {
        "assetId": "music-cc-by", "attributionRequired": True,
        "attributionStatus": "requires_attribution",
        "attribution": {
            "sourceUrl": "https://provider.example/tracks/1", "creator": "A Creator",
            "title": "Licensed pulse", "license": "CC BY 4.0",
            "licenseUrl": "https://creativecommons.org/licenses/by/4.0/",
            "text": "Licensed pulse by A Creator, CC BY 4.0",
        },
    }
    # A client-shaped top-level claim must not satisfy the server-derived gate.
    env["candidate"]["manifest"]["attribution"] = [{
        "required": True, "rendered": True,
    }]
    current = start(env).json()
    evidence = current["document"]["attribution"][0]
    assert evidence == {
        "assetId": "music-cc-by", "required": True, "rendered": False,
        "status": "requires_attribution",
        "sourceUrl": "https://provider.example/tracks/1", "creator": "A Creator",
        "title": "Licensed pulse", "license": "CC BY 4.0",
        "licenseUrl": "https://creativecommons.org/licenses/by/4.0/",
        "attributionText": "Licensed pulse by A Creator, CC BY 4.0",
    }
    response = env["client"].post(
        f"/projects/{env['project']['id']}/editor/render",
        json={"documentId": current["id"]}, headers=headers(env["owner"][1]),
    )
    assert response.status_code == 409
    assert "attribution" in response.text


def test_cc0_selection_is_not_blocked_by_attribution_gate(env):
    env["candidate"]["manifest"]["musicAssetSelection"] = {
        "assetId": "music-cc0", "attributionRequired": False,
        "attributionStatus": "not_required",
        "attribution": {
            "sourceUrl": "https://provider.example/tracks/cc0", "creator": "Creator",
            "title": "Public-domain pulse", "license": "CC0 1.0",
            "text": "Public-domain pulse by Creator, CC0 1.0",
        },
    }
    current = start(env).json()
    assert current["document"]["attribution"][0]["required"] is False
    response = env["client"].post(
        f"/projects/{env['project']['id']}/editor/render",
        json={"documentId": current["id"]}, headers=headers(env["owner"][1]),
    )
    assert response.status_code == 200, response.text


def test_failed_export_retry_preserves_saved_version(env):
    current = start(env).json()
    rendered = env["client"].post(
        f"/projects/{env['project']['id']}/editor/render",
        json={"documentId": current["id"]}, headers=headers(env["owner"][1]),
    ).json()
    env["fake"].patch("pipeline_jobs", f"id=eq.{rendered['id']}",
                      {"status": "failed", "attempt_count": 1})
    response = env["client"].post(
        f"/projects/{env['project']['id']}/editor/renders/{rendered['id']}/retry",
        headers=headers(env["owner"][1]),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "queued"
    assert response.json()["params"]["editor_document_version"] == 1


def test_completed_export_signing_is_owned_and_path_bounded(env):
    current = start(env).json()
    rendered = env["client"].post(
        f"/projects/{env['project']['id']}/editor/render",
        json={"documentId": current["id"]}, headers=headers(env["owner"][1]),
    ).json()
    path = (f"users/{env['owner'][0]}/projects/{env['project']['id']}"
            f"/renders/{rendered['id']}.mp4")
    env["fake"].storage[f"exports/{path}"] = b"mp4"
    env["fake"].patch("pipeline_jobs", f"id=eq.{rendered['id']}", {
        "status": "completed", "artifacts": {"output": path},
    })
    response = env["client"].post(
        f"/projects/{env['project']['id']}/editor/renders/{rendered['id']}/sign",
        headers=headers(env["owner"][1]),
    )
    assert response.status_code == 200, response.text
    assert response.json()["url"].startswith("https://fake.supabase.co/storage/v1/signed/")


def test_saved_editor_handler_resolves_full_ancestry_and_uploads(env, monkeypatch, tmp_path):
    from app import jobs
    from app.pipeline import (audio_rendering, editorial_intelligence,
                              music_supervisor, picture_editor, visual_finishing)

    current = start(env).json()
    candidate_row = env["fake"].tables["candidate_runs"][0]
    candidate_row.update({"audio_mix_run_id": "mix-1", "music_sound_run_id": "music-1"})
    env["fake"].insert("audio_mix_runs", {
        "id": "mix-1", "project_id": env["project"]["id"],
        "mix_instructions": {"targetVsActual": {}},
    })
    env["fake"].insert("music_sound_runs", {
        "id": "music-1", "project_id": env["project"]["id"], "version": 1,
        "music_plan": {},
    })
    music_path = (f"users/{env['owner'][0]}/projects/{env['project']['id']}"
                  "/licensed-music/track.wav")
    env["fake"].insert("licensed_music_assets", {
        "music_sound_run_id": "music-1", "version": 1,
        "storage_bucket": "raw-footage", "storage_path": music_path,
        "filename": "track.wav",
    })
    env["fake"].storage[f"raw-footage/{music_path}"] = b"music"

    class Dump:
        def __init__(self, payload): self.payload = payload
        def model_dump(self, mode=None): return self.payload

    manifest = SimpleNamespace(
        captions=Dump({"groups": [], "pictureTimingChanged": False}),
        graphics=Dump({"events": [], "pictureTimingChanged": False,
                       "audioChanged": False}),
        color=Dump({"instructions": [], "nonDestructive": True}),
    )
    monkeypatch.setattr(editorial_intelligence, "CompleteCandidateManifest",
                        lambda **_kw: manifest)
    monkeypatch.setattr(audio_rendering, "CompletedAudioMix",
                        lambda **_kw: SimpleNamespace(targetVsActual=object()))
    monkeypatch.setattr(music_supervisor, "MusicPlan", lambda **_kw: object())
    monkeypatch.setattr(picture_editor, "PictureCandidateSummary", lambda **kw: kw)
    monkeypatch.setattr(visual_finishing, "CaptionPackage", lambda **kw: kw)
    monkeypatch.setattr(visual_finishing, "GraphicsPackage", lambda **kw: kw)
    monkeypatch.setattr(visual_finishing, "ColorPackage", lambda **kw: kw)

    def mix(_summary, _sources, _music, _plan, _match, output, _workdir,
            music_gain_db=-8):
        open(output, "wb").write(b"mixed")
        assert music_gain_db == -12

    def finish(input_path, output, graphics, captions, color):
        assert os.path.exists(input_path) and graphics is not None
        assert captions is not None and color is not None
        open(output, "wb").write(b"finished-video")
        return {"durationSeconds": 8, "width": 1080, "height": 1920,
                "graphicsEvents": 1, "captionGroups": 1}

    monkeypatch.setattr(audio_rendering, "render_completed_mix", mix)
    monkeypatch.setattr(visual_finishing, "render_finishing_preview", finish)

    class Context:
        def checkpoint(self, _stage): pass
        def rec(self, *args, **kwargs): self.recorded = (args, kwargs)

    context = Context()
    result = jobs.handle_product_editor_render(
        {"id": str(uuid4()), "params": {"editor_document_id": current["id"],
         "editor_document_version": 1}}, env["project"], str(tmp_path), context,
    )
    assert result["editor_document_id"] == current["id"]
    assert result["music_gain_db"] == -12
    assert f"exports/{result['output']}" in env["fake"].storage
    assert context.recorded[0][0] == "product_editor_render"
