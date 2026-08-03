"""H1: worker-side storage-path ancestry validation (defense-in-depth).

media_assets is client-writable, so the service-role worker must reject any
storage_path that is not under the project owner's prefix. These tests pin both
the pure helper and the _download_sources enforcement, and prove existing valid
uploads still work.
"""
import os

import pytest

os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "fake-service-key")

from app import jobs  # noqa: E402
from tests.fake_supa import FakeSupabase, install  # noqa: E402


def test_owned_path_accepts_owner_prefix():
    assert jobs.owned_raw_storage_path(
        "users/u1/projects/p1/raw/1720_abc.mp4", "u1", "p1")


def test_owned_path_rejects_foreign_owner():
    assert not jobs.owned_raw_storage_path(
        "users/VICTIM/projects/VICTIM/raw/secret.mp4", "u1", "p1")


def test_owned_path_rejects_traversal():
    assert not jobs.owned_raw_storage_path(
        "users/u1/projects/p1/../../VICTIM/x.mp4", "u1", "p1")


def test_owned_path_rejects_empty_or_wrong_project():
    assert not jobs.owned_raw_storage_path("", "u1", "p1")
    assert not jobs.owned_raw_storage_path("users/u1/projects/OTHER/raw/x.mp4", "u1", "p1")


def test_download_sources_rejects_tampered_path(monkeypatch, tmp_path):
    fake = FakeSupabase()
    install(monkeypatch, fake)
    uid, _ = fake.add_user("owner@example.com")
    project = fake.add_project(uid)
    victim_key = "users/VICTIM/projects/VICTIM/raw/secret.mp4"
    fake.storage[f"raw-footage/{victim_key}"] = b"secret"
    fake.insert("media_assets", {"project_id": project["id"], "user_id": uid,
                                 "storage_path": victim_key, "filename": "x.mp4"})
    with pytest.raises(RuntimeError, match="ownership check"):
        jobs._download_sources(project, str(tmp_path))


def test_download_sources_accepts_valid_existing_upload(monkeypatch, tmp_path):
    fake = FakeSupabase()
    install(monkeypatch, fake)
    uid, _ = fake.add_user("owner@example.com")
    project = fake.add_project(uid)
    key = f"users/{uid}/projects/{project['id']}/raw/1720_abc.mp4"
    fake.storage[f"raw-footage/{key}"] = b"VIDEO"
    row = fake.insert("media_assets", {"project_id": project["id"], "user_id": uid,
                                       "storage_path": key, "filename": "clip.mp4"}).json()[0]
    sources, assets = jobs._download_sources(project, str(tmp_path))
    assert len(assets) == 1
    with open(sources[row["id"]], "rb") as fh:
        assert fh.read() == b"VIDEO"
