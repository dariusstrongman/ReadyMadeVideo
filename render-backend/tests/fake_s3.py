"""In-memory boto3-S3-client fake for CI-independent tests (no AWS, no boto3).

Implements the subset of the boto3 S3 client API that app/s3store.py calls, and is
deliberately strict so tests prove real bindings:
  - completion is bound to the multipart's stored Key,
  - each completed part's ETag must match what the (simulated) part upload returned,
  - presigned URLs encode the UploadId + PartNumber,
  - completing an unknown/aborted UploadId raises (NoSuchUpload).
`put_part()` simulates the browser PUTting a part straight to S3 and returns its ETag
(the backend never sees these bytes).
"""
from __future__ import annotations

import hashlib


class _BytesBody:
    def __init__(self, data: bytes):
        self._d, self._i = data, 0

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            chunk, self._i = self._d[self._i:], len(self._d)
            return chunk
        chunk = self._d[self._i:self._i + n]
        self._i += n
        return chunk


class FakeS3Error(RuntimeError):
    pass


class FakeS3:
    def __init__(self):
        self.multipart: dict[str, dict] = {}   # upload_id -> {key, content_type, parts:{n:{data,etag}}}
        self.objects: dict[str, dict] = {}     # key -> {body, content_type, etag, size, force_size?}
        self._n = 0

    def _id(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}-{self._n}"

    # ---- boto3-compatible surface ----
    def create_multipart_upload(self, Bucket, Key, ContentType=None, **kw):
        up = self._id("upload")
        self.multipart[up] = {"key": Key, "content_type": ContentType, "parts": {}}
        return {"UploadId": up}

    def generate_presigned_url(self, operation, Params, ExpiresIn=3600):
        base = f"https://s3.fake/{Params['Bucket']}/{Params['Key']}?op={operation}&exp={ExpiresIn}"
        if "UploadId" in Params:
            base += f"&uploadId={Params['UploadId']}"
        if "PartNumber" in Params:
            base += f"&partNumber={Params['PartNumber']}"
        return base

    def complete_multipart_upload(self, Bucket, Key, UploadId, MultipartUpload):
        mp = self.multipart.get(UploadId)
        if not mp:
            raise FakeS3Error("NoSuchUpload")
        if mp["key"] != Key:
            raise FakeS3Error("key does not match this multipart upload")
        body = b""
        for part in MultipartUpload["Parts"]:
            number = part["PartNumber"]
            stored = mp["parts"].get(number)
            if not stored:
                raise FakeS3Error(f"InvalidPart: {number} was never uploaded")
            supplied = str(part["ETag"]).strip().strip('"')
            if supplied != stored["etag"]:
                raise FakeS3Error(f"InvalidPart: ETag mismatch for part {number}")
            body += stored["data"]
        etag = self._id("etag")
        self.objects[Key] = {"body": body, "content_type": mp["content_type"],
                             "etag": etag, "size": len(body)}
        del self.multipart[UploadId]
        return {"ETag": f'"{etag}"', "Key": Key}

    def abort_multipart_upload(self, Bucket, Key, UploadId):
        self.multipart.pop(UploadId, None)
        return {}

    def head_object(self, Bucket, Key):
        obj = self.objects.get(Key)
        if not obj:
            raise FakeS3Error("NoSuchKey")
        return {"ContentLength": obj.get("force_size", obj["size"]),
                "ContentType": obj["content_type"], "ETag": f'"{obj["etag"]}"'}

    def get_object(self, Bucket, Key):
        obj = self.objects.get(Key)
        if not obj:
            raise FakeS3Error("NoSuchKey")
        return {"Body": _BytesBody(obj["body"])}

    def put_object(self, Bucket, Key, Body=None, ContentType=None, **kw):
        data = Body.read() if hasattr(Body, "read") else (Body or b"")
        etag = self._id("etag")
        self.objects[Key] = {"body": data, "content_type": ContentType,
                             "etag": etag, "size": len(data)}
        return {"ETag": f'"{etag}"'}

    def delete_object(self, Bucket, Key):
        self.objects.pop(Key, None)
        return {}

    def head_bucket(self, Bucket):
        return {}

    # ---- test helper: simulate the browser uploading a part to S3 ----
    def put_part(self, upload_id: str, part_number: int, data: bytes) -> str:
        etag = hashlib.md5(data).hexdigest()  # noqa: S324 — S3 part ETag is md5
        self.multipart[upload_id]["parts"][part_number] = {"data": data, "etag": etag}
        return etag
