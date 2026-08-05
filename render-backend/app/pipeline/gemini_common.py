"""Shared Gemini REST helpers (Files API upload + structured generateContent)."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

BASE = "https://generativelanguage.googleapis.com"


def http(method, url, headers=None, body=None, raw=None, timeout=300,
         _retries=3, _backoff=15):
    data = raw if raw is not None else (json.dumps(body).encode() if body else None)
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "{}"), \
                {k.lower(): v for k, v in r.headers.items()}
    except urllib.error.HTTPError as e:
        # Surface Google's actual error message. urllib raises before the body
        # is read, so without this every failure collapses into an opaque
        # "HTTP Error 400: Bad Request" while the response says exactly what
        # was wrong (invalid model, oversized response_schema, bad key, ...).
        detail = ""
        try:
            payload = json.loads(e.read().decode() or "{}")
            detail = (payload.get("error") or {}).get("message", "")[:500]
        except Exception:
            pass
        if e.code in (429, 500, 502, 503, 504) and _retries > 0:
            # Server-side congestion ("high demand") and rate spikes are
            # temporary — a bounded backoff beats burning a repair attempt.
            time.sleep(_backoff)
            return http(method, url, headers=headers, body=body, raw=raw,
                        timeout=timeout, _retries=_retries - 1,
                        _backoff=min(_backoff * 2, 120))
        raise RuntimeError(
            f"Gemini API {e.code} on {url.split('?')[0]}: "
            f"{detail or e.reason}") from e
    except (TimeoutError, OSError):
        # One retry on transport-level stalls (SSL read timeouts, resets):
        # a 10-minute editorial-plan call dying to a flaky socket should not
        # burn a whole repair attempt. HTTPError is handled above — this
        # clause only sees genuine transport failures.
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


def delete_file(file_name: str, api_key: str) -> bool:
    """Delete an uploaded Files API object. Returns False on failure (callers
    log but do not raise — Google auto-expires files after ~48 h regardless)."""
    try:
        http("DELETE", f"{BASE}/v1beta/{file_name}?key={api_key}")
        return True
    except Exception:
        return False


def prune_schema(schema: dict, max_depth: int) -> dict:
    """Structurally prune a response_schema below max_depth.

    Gemini compiles response_schema into a constraint automaton with a hard
    state budget; a large nested schema (the Editorial Planner's is ~240 typed
    nodes) is rejected with "too many states for serving" REGARDLESS of content.
    Below the cut-off, objects become free-form OBJECT and arrays become arrays
    of free-form OBJECT — the model still sees the full contract in the prompt,
    and every consumer of generate_json re-validates with pydantic plus its own
    deterministic gate, so wire-schema looseness never reaches persistence.
    """
    def walk(node, depth):
        if not isinstance(node, dict):
            return node
        t = node.get("type")
        if depth >= max_depth:
            if t == "ARRAY":
                return {"type": "ARRAY", "items": {"type": "OBJECT"}}
            if t == "OBJECT":
                return {"type": "OBJECT"}
            return {"type": t} if t else {}
        out = dict(node)
        if "properties" in out:
            out["properties"] = {k: walk(v, depth + 1)
                                 for k, v in out["properties"].items()}
        if "items" in out:
            out["items"] = walk(out["items"], depth + 1)
        return out
    return walk(schema, 0)


# "too many states" fallback ladder: full schema first, then progressively
# pruned, then fully free-form JSON. Only this specific rejection walks the
# ladder — every other error raises immediately.
_TOO_MANY_STATES = "too many states"


def generate_json(model: str, parts: list[dict], schema: dict, api_key: str,
                  temperature: float = 0.2, timeout: int = 600,
                  ladder: list[dict] | None = None):
    if ladder is None:
        ladder = [schema, prune_schema(schema, 4), prune_schema(schema, 2),
                  {"type": "OBJECT"}]
    last = None
    for i, wire_schema in enumerate(ladder):
        rung_parts = list(parts)
        if i > 0:
            # The wire schema was pruned, so Gemini no longer sees required
            # fields / enums below the cut. Hand it the FULL contract as prompt
            # text instead — first production run on a pruned rung dropped
            # required nested fields and invented enum values without this.
            rung_parts.append({"text": (
                "STRICT OUTPUT CONTRACT — the JSON you return MUST validate "
                "against this exact JSON Schema (every `required` field, exact "
                "property names, exact `enum` values):\n"
                + json.dumps(schema))})
        body = {"contents": [{"parts": rung_parts}],
                "generationConfig": {"response_mime_type": "application/json",
                                     "response_schema": wire_schema,
                                     "temperature": temperature}}
        try:
            st, out, _ = http(
                "POST", f"{BASE}/v1beta/models/{model}:generateContent"
                        f"?key={api_key}",
                headers={"Content-Type": "application/json"}, body=body,
                timeout=timeout)
        except RuntimeError as e:
            if _TOO_MANY_STATES in str(e) and i + 1 < len(ladder):
                last = e
                print(f"[gemini] response_schema rejected (too many states) at "
                      f"ladder rung {i}; retrying with a pruned schema")
                continue
            raise
        text = out["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(text)
    raise last
