"""In-memory boto3-S3-client fake for CI-independent tests (no AWS, no boto3).

Implements the subset of the boto3 S3 client API that app/s3store.py calls.
`put_part()` is a test helper that simulates the browser PUTting a part directly
to S3 via the presigned URL — the backend never sees those bytes.
"""
from __future__ import annotations


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


class FakeS3:
    def __init__(self):
        self.multipart: dict[str, dict] = {}   # upload_id -> {key, content_type, parts}
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
        return (f"https://s3.fake/{Params['Bucket']}/{Params['Key']}"
                f"?op={operation}&exp={ExpiresIn}")

    def complete_multipart_upload(self, Bucket, Key, UploadId, MultipartUpload):
        mp = self.multipart.get(UploadId)
        if not mp:
            raise RuntimeError("NoSuchUpload")
        body = b""
        for part in MultipartUpload["Parts"]:
            number = part["PartNumber"]
            if number not in mp["parts"]:
                raise RuntimeError(f"InvalidPart: {number} was never uploaded")
            body += mp["parts"][number]
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
            raise RuntimeError("NoSuchKey")
        return {"ContentLength": obj.get("force_size", obj["size"]),
                "ContentType": obj["content_type"], "ETag": f'"{obj["etag"]}"'}

    def get_object(self, Bucket, Key):
        obj = self.objects.get(Key)
        if not obj:
            raise RuntimeError("NoSuchKey")
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
    def put_part(self, upload_id: str, part_number: int, data: bytes):
        self.multipart[upload_id]["parts"][part_number] = data
