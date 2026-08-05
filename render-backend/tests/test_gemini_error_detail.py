"""Gemini failures must carry Google's actual error message, not an opaque
"HTTP Error 400" — that opacity cost a production debugging round."""
import io
import json
import urllib.error

import pytest

from app.pipeline import gemini_common


def _http_error(code, message):
    body = json.dumps({"error": {"message": message}}).encode()
    return urllib.error.HTTPError(
        "https://generativelanguage.googleapis.com/v1beta/models/x:generateContent",
        code, "Bad Request", {}, io.BytesIO(body))


def test_google_error_message_is_surfaced(monkeypatch):
    def boom(req, timeout):
        raise _http_error(400, "Invalid JSON payload: response_schema too deep")
    monkeypatch.setattr(gemini_common.urllib.request, "urlopen", boom)
    with pytest.raises(RuntimeError, match="response_schema too deep"):
        gemini_common.http("POST", "https://generativelanguage.googleapis.com/v1beta/models/x:generateContent?key=k",
                           body={"a": 1})


def test_key_never_leaks_into_the_error(monkeypatch):
    def boom(req, timeout):
        raise _http_error(400, "bad request")
    monkeypatch.setattr(gemini_common.urllib.request, "urlopen", boom)
    with pytest.raises(RuntimeError) as e:
        gemini_common.http("POST", "https://generativelanguage.googleapis.com/v1beta/models/x:generateContent?key=SECRETKEY",
                           body={"a": 1})
    assert "SECRETKEY" not in str(e.value)


def test_unreadable_error_body_still_reports_status(monkeypatch):
    err = urllib.error.HTTPError("u", 503, "Service Unavailable", {}, None)
    def boom(req, timeout):
        raise err
    monkeypatch.setattr(gemini_common.urllib.request, "urlopen", boom)
    with pytest.raises(RuntimeError, match="503"):
        gemini_common.http("GET", "https://generativelanguage.googleapis.com/v1beta/files/x?key=k")
