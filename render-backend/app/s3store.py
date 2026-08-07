"""AWS S3 storage abstraction for customer raw footage (and, when explicitly
enabled, exports).

Design / security model:
- boto3 is imported LAZILY inside client() so this module — and the whole
  backend — stays importable and bootable when S3 is not configured or boto3 is
  not installed. Nothing here is required at startup.
- The browser never receives AWS credentials. It talks to S3 only through the
  short-lived presigned URLs minted here (server-side, from a least-privilege
  IAM identity). Object keys are always built server-side from the verified
  user/project — the client never chooses a bucket or an arbitrary path.
- `_CLIENT` is a dependency-injection seam: tests set it to a fake S3 client so
  no AWS calls (and no boto3 install) are needed in CI.
"""
from __future__ import annotations

import os

RAW_BUCKET_ENV = "AWS_S3_BUCKET"
REGION_ENV = "AWS_REGION"

# expiries (seconds) for presigned URLs
PART_URL_EXPIRE_S = int(os.environ.get("S3_PART_URL_EXPIRE_S", "3600"))
GET_URL_EXPIRE_S = int(os.environ.get("S3_GET_URL_EXPIRE_S", "3600"))
SSE_ALGO = os.environ.get("S3_SSE_ALGORITHM", "AES256")

_CLIENT = None  # DI/test seam; when set, used instead of a real boto3 client


class S3NotConfigured(RuntimeError):
    pass


def enabled() -> bool:
    """True when a raw-footage bucket is configured (or a fake client injected)."""
    return bool(_CLIENT is not None or os.environ.get(RAW_BUCKET_ENV))


def bucket() -> str:
    name = os.environ.get(RAW_BUCKET_ENV)
    if not name:
        raise S3NotConfigured(f"{RAW_BUCKET_ENV} is not set")
    return name


def region() -> str:
    return os.environ.get(REGION_ENV, "us-east-1")


def client():
    """Return the cached boto3 S3 client (or the injected fake)."""
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT
    if not os.environ.get(RAW_BUCKET_ENV):
        raise S3NotConfigured("S3 is not configured")
    import boto3  # lazy — only needed when actually talking to AWS
    from botocore.config import Config
    kwargs = {
        "region_name": region(),
        "config": Config(signature_version="s3v4",
                         retries={"max_attempts": 3, "mode": "standard"}),
    }
    endpoint = os.environ.get("AWS_S3_ENDPOINT_URL")  # local dev / moto only
    if endpoint:
        kwargs["endpoint_url"] = endpoint
    _CLIENT = boto3.client("s3", **kwargs)
    return _CLIENT


# ---------------- multipart lifecycle ----------------
def create_multipart(key: str, content_type: str) -> str:
    params = {"Bucket": bucket(), "Key": key, "ContentType": content_type}
    if SSE_ALGO:
        params["ServerSideEncryption"] = SSE_ALGO
    return client().create_multipart_upload(**params)["UploadId"]


def presign_part(key: str, upload_id: str, part_number: int,
                 expires: int = PART_URL_EXPIRE_S) -> str:
    return client().generate_presigned_url(
        "upload_part",
        Params={"Bucket": bucket(), "Key": key, "UploadId": upload_id,
                "PartNumber": int(part_number)},
        ExpiresIn=expires)


def complete_multipart(key: str, upload_id: str, parts: list[dict]) -> dict:
    """parts: [{"PartNumber": n, "ETag": etag}, ...] (any order)."""
    ordered = sorted(parts, key=lambda p: int(p["PartNumber"]))
    return client().complete_multipart_upload(
        Bucket=bucket(), Key=key, UploadId=upload_id,
        MultipartUpload={"Parts": ordered})


def abort_multipart(key: str, upload_id: str) -> None:
    client().abort_multipart_upload(Bucket=bucket(), Key=key, UploadId=upload_id)


# ---------------- object ops ----------------
def head_object(key: str) -> dict:
    r = client().head_object(Bucket=bucket(), Key=key)
    return {
        "size": int(r["ContentLength"]),
        "content_type": r.get("ContentType"),
        "etag": (r.get("ETag") or "").strip('"'),
    }


def presign_get(key: str, expires: int = GET_URL_EXPIRE_S,
                download_name: str | None = None) -> str:
    params = {"Bucket": bucket(), "Key": key}
    if download_name:
        params["ResponseContentDisposition"] = f'attachment; filename="{download_name}"'
    return client().generate_presigned_url("get_object", Params=params,
                                           ExpiresIn=expires)


def download_to_file(key: str, dest_file: str, max_bytes: int | None = None) -> int:
    """Stream an object to disk (never buffering the whole body in memory).
    Enforces max_bytes as a hard cap to protect the worker."""
    r = client().get_object(Bucket=bucket(), Key=key)
    body = r["Body"]
    written = 0
    with open(dest_file, "wb") as fh:
        while True:
            chunk = body.read(1024 * 1024)
            if not chunk:
                break
            written += len(chunk)
            if max_bytes is not None and written > max_bytes:
                raise ValueError(f"object {key} exceeds max_bytes during download")
            fh.write(chunk)
    return written


def upload_file(key: str, src_file: str, content_type: str = "video/mp4") -> dict:
    with open(src_file, "rb") as fh:
        params = {"Bucket": bucket(), "Key": key, "Body": fh,
                  "ContentType": content_type}
        if SSE_ALGO:
            params["ServerSideEncryption"] = SSE_ALGO
        return client().put_object(**params)


def delete_object(key: str) -> None:
    client().delete_object(Bucket=bucket(), Key=key)


def list_keys(prefix: str, max_keys: int = 10_000) -> list[str]:
    """Every object key under ``prefix`` (paginated; bounded so a runaway
    prefix cannot spin forever). Needed for truthful project deletion —
    enumerate-then-delete, never assume."""
    keys: list[str] = []
    token = None
    while len(keys) < max_keys:
        kwargs = {"Bucket": bucket(), "Prefix": prefix, "MaxKeys": 1000}
        if token:
            kwargs["ContinuationToken"] = token
        resp = client().list_objects_v2(**kwargs)
        keys += [o["Key"] for o in resp.get("Contents", [])]
        if not resp.get("IsTruncated"):
            break
        token = resp.get("NextContinuationToken")
    return keys


def check_connectivity() -> dict:
    """Shallow, non-secret readiness probe: can we reach the configured bucket?
    Never raises and never returns credentials."""
    if not enabled():
        return {"enabled": False, "reachable": False}
    try:
        client().head_bucket(Bucket=bucket())
        return {"enabled": True, "reachable": True}
    except Exception:  # noqa: BLE001
        return {"enabled": True, "reachable": False}


def check_canary() -> dict:
    """Operator-only deep probe: exercises multipart create/abort and object
    put/get/delete under a controlled users/_readiness/ prefix, cleaning up in
    finally. Confirms the IAM permissions the upload flow actually needs — which
    a bare HeadBucket cannot."""
    import uuid
    if not enabled():
        return {"enabled": False}
    marker = f"users/_readiness/{uuid.uuid4()}/probe"
    checks: dict = {}
    upload_id = None
    try:
        upload_id = create_multipart(marker, "application/octet-stream")
        checks["multipart_create"] = True
    except Exception as exc:  # noqa: BLE001
        checks["multipart_create"] = False
        checks["error"] = type(exc).__name__
    finally:
        if upload_id:
            try:
                abort_multipart(marker, upload_id)
                checks["multipart_abort"] = True
            except Exception:  # noqa: BLE001
                checks["multipart_abort"] = False
    try:
        params = {"Bucket": bucket(), "Key": marker, "Body": b"ok",
                  "ContentType": "application/octet-stream"}
        if SSE_ALGO:
            params["ServerSideEncryption"] = SSE_ALGO
        client().put_object(**params)
        checks["put"] = True
        client().get_object(Bucket=bucket(), Key=marker)
        checks["get"] = True
    except Exception as exc:  # noqa: BLE001
        checks.setdefault("put", False)
        checks.setdefault("get", False)
        checks["error"] = type(exc).__name__
    finally:
        try:
            delete_object(marker)
            checks["delete"] = True
        except Exception:  # noqa: BLE001
            checks["delete"] = False
    checks["ok"] = all(checks.get(k) for k in
                       ("multipart_create", "multipart_abort", "put", "get", "delete"))
    return {"enabled": True, **checks}
