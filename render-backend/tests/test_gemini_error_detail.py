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


# ── schema ladder ────────────────────────────────────────────────────────────
def _deep_schema():
    leaf = {"type": "STRING"}
    obj = {"type": "OBJECT", "properties": {"a": leaf, "b": leaf},
           "required": ["a"]}
    arr = {"type": "ARRAY", "items": obj}
    return {"type": "OBJECT",
            "properties": {"one": obj, "many": arr,
                           "nested": {"type": "OBJECT",
                                      "properties": {"inner": arr}}}}


def test_prune_keeps_shape_above_cut_and_frees_below():
    pruned = gemini_common.prune_schema(_deep_schema(), 2)
    # depth 0-1 keeps structure
    assert set(pruned["properties"]) == {"one", "many", "nested"}
    # depth-1 object keeps its properties; its depth-2 leaves keep their types
    assert pruned["properties"]["one"]["properties"]["a"] == {"type": "STRING"}
    # below the cut, arrays become arrays of free-form OBJECT
    deep = pruned["properties"]["nested"]["properties"]["inner"]
    assert deep == {"type": "ARRAY", "items": {"type": "OBJECT"}}


def test_prune_zero_is_fully_free_form():
    assert gemini_common.prune_schema(_deep_schema(), 0) == {"type": "OBJECT"}


def test_ladder_falls_back_only_on_too_many_states(monkeypatch):
    calls = []

    def fake_http(method, url, headers=None, body=None, raw=None, timeout=300):
        calls.append(body["generationConfig"]["response_schema"])
        if len(calls) < 3:
            raise RuntimeError("Gemini API 400 on x: The specified schema "
                               "produces a constraint that has too many states")
        return 200, {"candidates": [{"content": {"parts": [
            {"text": "{\"ok\": true}"}]}}]}, {}

    monkeypatch.setattr(gemini_common, "http", fake_http)
    out = gemini_common.generate_json("m", [{"text": "hi"}], _deep_schema(), "k")
    assert out == {"ok": True}
    assert len(calls) == 3                      # full -> prune(4) -> prune(2)
    assert calls[0] == _deep_schema()           # first try is the full schema


def test_other_errors_do_not_walk_the_ladder(monkeypatch):
    def fake_http(method, url, headers=None, body=None, raw=None, timeout=300):
        raise RuntimeError("Gemini API 400 on x: API key not valid")
    monkeypatch.setattr(gemini_common, "http", fake_http)
    with pytest.raises(RuntimeError, match="API key not valid"):
        gemini_common.generate_json("m", [{"text": "hi"}], _deep_schema(), "k")


def test_pruned_rungs_carry_the_full_contract_in_the_prompt(monkeypatch):
    seen_parts = []

    def fake_http(method, url, headers=None, body=None, raw=None, timeout=300):
        seen_parts.append(body["contents"][0]["parts"])
        if len(seen_parts) == 1:
            raise RuntimeError("400: schema has too many states for serving")
        return 200, {"candidates": [{"content": {"parts": [
            {"text": "{\"ok\": true}"}]}}]}, {}

    monkeypatch.setattr(gemini_common, "http", fake_http)
    gemini_common.generate_json("m", [{"text": "prompt"}], _deep_schema(), "k")
    # rung 0: prompt untouched (schema is on the wire in full)
    assert seen_parts[0] == [{"text": "prompt"}]
    # rung 1: pruned wire schema, so the FULL contract rides in the prompt
    assert len(seen_parts[1]) == 2
    assert "STRICT OUTPUT CONTRACT" in seen_parts[1][1]["text"]
    assert '"required"' in seen_parts[1][1]["text"]
