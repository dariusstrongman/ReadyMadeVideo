"""Pure, network-free helpers for the S3 multipart raw-footage upload flow:
input validation, ownership-scoped key building, and part planning.

Keeping this logic side-effect-free makes the security-critical rules (size cap,
extension/MIME allow-list, no path traversal, server-built keys) unit-testable
without AWS, and lets the FastAPI endpoints stay thin wiring over auth + s3store.
"""
from __future__ import annotations

import math
import os
import re
from posixpath import basename

# One shared ceiling. 2 GiB, mirrored by the frontend (app/src/lib/config.js).
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", str(2 * 1024 * 1024 * 1024)))
MIN_UPLOAD_BYTES = 1

# S3 multipart constraints: parts (except the last) must be >= 5 MiB, <= 10000 parts.
S3_MIN_PART_SIZE = 5 * 1024 * 1024
S3_MAX_PARTS = 10000
DEFAULT_PART_SIZE = max(
    S3_MIN_PART_SIZE,
    int(os.environ.get("RAW_UPLOAD_PART_SIZE", str(16 * 1024 * 1024))))

ALLOWED_EXTENSIONS = {".mp4", ".mov"}
ALLOWED_CONTENT_TYPES = {"video/mp4", "video/quicktime"}


class UploadValidationError(ValueError):
    """Carries a stable machine code alongside a human message."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def safe_filename(name: str) -> str:
    """Collapse any client-supplied name to a bounded, traversal-free basename."""
    base = basename((name or "").replace("\\", "/")).replace("\x00", "")
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._")
    return (base or "footage")[-120:]


def validate_extension(name: str) -> str:
    ext = os.path.splitext(safe_filename(name).lower())[1]
    if ext not in ALLOWED_EXTENSIONS:
        raise UploadValidationError("unsupported_extension",
                                    "file must be MP4 or MOV")
    return ext


def validate_content_type(content_type: str) -> None:
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise UploadValidationError(
            "unsupported_content_type",
            "content type must be video/mp4 or video/quicktime")


def validate_size(size: int) -> None:
    if not isinstance(size, int) or size < MIN_UPLOAD_BYTES:
        raise UploadValidationError("invalid_size",
                                    "declared size must be a positive integer")
    if size > MAX_UPLOAD_BYTES:
        raise UploadValidationError(
            "too_large", f"file exceeds the {MAX_UPLOAD_BYTES}-byte (2 GB) limit")


def object_key(user_id: str, project_id: str, asset_id: str, filename: str) -> str:
    """Server-built, ownership-scoped key. The client cannot influence the
    bucket or the prefix — only the (sanitized) trailing filename."""
    return (f"users/{user_id}/projects/{project_id}/raw-footage/"
            f"{asset_id}/{safe_filename(filename)}")


def key_belongs_to(key: str, user_id: str, project_id: str) -> bool:
    prefix = f"users/{user_id}/projects/{project_id}/raw-footage/"
    return bool(key) and key.startswith(prefix) and ".." not in key


def supabase_path_belongs_to(path: str, user_id: str, project_id: str) -> bool:
    """Legacy Supabase raw-footage paths: users/{uid}/projects/{pid}/..."""
    prefix = f"users/{user_id}/projects/{project_id}/"
    return bool(path) and path.startswith(prefix) and ".." not in path


_ETAG_RE = re.compile(r"^[A-Za-z0-9._\-]+$")


def validate_part_manifest(parts: list[dict], expected_count: int) -> None:
    """Reject malformed completion manifests before calling S3: wrong count,
    duplicates, gaps/out-of-range numbers, malformed ETags."""
    numbers = [p["partNumber"] for p in parts]
    if len(numbers) != expected_count:
        raise UploadValidationError(
            "part_count", f"expected {expected_count} parts, got {len(numbers)}")
    if len(set(numbers)) != len(numbers):
        raise UploadValidationError("duplicate_parts", "duplicate part numbers")
    if sorted(numbers) != list(range(1, expected_count + 1)):
        raise UploadValidationError(
            "non_consecutive", "parts must be a consecutive 1..N manifest with no gaps")
    for part in parts:
        etag = str(part["etag"]).strip().strip('"')
        if not etag or not _ETAG_RE.match(etag):
            raise UploadValidationError(
                "bad_etag", f"malformed ETag for part {part['partNumber']}")


def plan_parts(size: int, part_size: int = DEFAULT_PART_SIZE) -> dict:
    """Choose a part size that satisfies S3 constraints for this file size."""
    part_size = max(int(part_size or DEFAULT_PART_SIZE), S3_MIN_PART_SIZE)
    count = max(1, math.ceil(size / part_size))
    if count > S3_MAX_PARTS:
        part_size = math.ceil(size / S3_MAX_PARTS)
        count = math.ceil(size / part_size)
    return {"part_size": part_size, "part_count": count}
