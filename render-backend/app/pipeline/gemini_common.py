"""Shared Gemini REST helpers (Files API upload + structured generateContent)."""
from __future__ import annotations

import json
import os
import time
import urllib.request

BASE = "https://generativelanguage.googleapis.com"


def http(method, url, headers=None, body=None, raw=None, timeout=300):
    data = raw if raw is not None else (json.dumps(body).encode() if body else None)
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read().decode() or "{}"), \
            {k.lower(): v for k, v in r.headers.items()}


def upload_file(path: str, api_key: str, mime: str = "video/mp4") -> dict:
    size = os.path.getsize(path)
    st, meta, hdrs = http(
        "POST", f"{BASE}/upload/v1beta/files?key={api_key}",
        headers={"X-Goog-Upload-Protocol": "resumable",
                 "X-Goog-Upload-Command": "start",
                 "X-Goog-Upload-Header-Content-Length": str(size),
                 "X-Goog-Upload-Header-Content-Type": mime,
                 "Content-Type": "application/json"},
        body={"file": {"display_name": os.path.basename(path)}})
    upload_url = hdrs.get("x-goog-upload-url")
    if not upload_url:
        raise RuntimeError("Gemini upload: no upload URL returned")
    with open(path, "rb") as f:
        blob = f.read()
    st, out, _ = http("POST", upload_url,
                      headers={"X-Goog-Upload-Command": "upload, finalize",
                               "X-Goog-Upload-Offset": "0"}, raw=blob)
    file = out["file"]
    for _ in range(60):
        if file.get("state") == "ACTIVE":
            return file
        time.sleep(3)
        st, file, _ = http("GET", f"{BASE}/v1beta/{file['name']}?key={api_key}")
    raise RuntimeError(f"Gemini file stuck in state {file.get('state')}")


def generate_json(model: str, parts: list[dict], schema: dict, api_key: str,
                  temperature: float = 0.2, timeout: int = 600):
    body = {"contents": [{"parts": parts}],
            "generationConfig": {"response_mime_type": "application/json",
                                 "response_schema": schema,
                                 "temperature": temperature}}
    st, out, _ = http("POST", f"{BASE}/v1beta/models/{model}:generateContent"
                              f"?key={api_key}",
                      headers={"Content-Type": "application/json"}, body=body,
                      timeout=timeout)
    text = out["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(text)
