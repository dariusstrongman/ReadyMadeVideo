"""Ownership-validating, provider-aware raw-footage download dispatcher.

EVERY raw-footage read (worker jobs, legacy render, M4/M6 operator renders, the
pipeline runner) MUST go through download_media_asset(). This is the single choke
point that:
  1. confirms the asset actually belongs to the given project + owner, and
  2. re-validates the S3 bucket/key ancestry at download time.

Defense in depth: even though media_assets writes are now service-role-only, the
worker never blindly trusts a stored storage_key/bucket — a mismatch raises before
any AWS credential is used, so a forged key can never make the worker fetch
another user's object.
"""
from __future__ import annotations

import os

from . import raw_uploads, s3store, supa

RAW_MAX_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", str(2 * 1024 * 1024 * 1024)))


class MediaOwnershipError(RuntimeError):
    pass


def assert_owned(asset: dict, project: dict) -> None:
    if (str(asset.get("user_id")) != str(project.get("user_id"))
            or str(asset.get("project_id")) != str(project.get("id"))):
        raise MediaOwnershipError(
            f"asset {asset.get('id')} does not belong to project {project.get('id')}")


def download_media_asset(asset: dict, project: dict, dest: str) -> str:
    """Validate ownership + provider ancestry, then download to `dest`."""
    assert_owned(asset, project)
    uid, pid = str(project["user_id"]), str(project["id"])
    provider = asset.get("storage_provider") or "supabase"
    if provider == "s3":
        key = asset.get("storage_key") or asset.get("storage_path")
        if asset.get("storage_bucket") != s3store.bucket():
            raise MediaOwnershipError(
                f"asset {asset.get('id')} bucket does not match the configured bucket")
        if not raw_uploads.key_belongs_to(key, uid, pid):
            raise MediaOwnershipError(
                f"s3 key ancestry check failed for asset {asset.get('id')}")
        s3store.download_to_file(key, dest, max_bytes=RAW_MAX_BYTES)
    elif provider == "supabase":
        path = asset.get("storage_path")
        if not raw_uploads.supabase_path_belongs_to(path, uid, pid):
            raise MediaOwnershipError(
                f"supabase path ancestry check failed for asset {asset.get('id')}")
        supa.storage_download("raw-footage", path, dest)
    else:
        raise MediaOwnershipError(f"unknown storage_provider {provider!r}")
    return dest
