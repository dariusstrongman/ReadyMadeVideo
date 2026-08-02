"""Stage 5: multimodal video understanding behind VideoUnderstandingProvider.

First implementation: Gemini video understanding (Files API + generateContent
with a strict responseSchema). Output is schema-validated JSON — never prose.
The product is NOT hard-coded to Gemini: implement the ABC for other providers.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod

from pydantic import ValidationError

from .schemas import ScenesArtifact, SemanticArtifact, SemanticSegment

GEMINI_MODEL = os.environ.get("GEMINI_VIDEO_MODEL", "gemini-2.5-flash")


class VideoUnderstandingProvider(ABC):
    name: str = "abstract"

    @abstractmethod
    def analyze(self, proxy_path: str, scenes: ScenesArtifact,
                context: str = "") -> SemanticArtifact: ...


def _http(method, url, headers=None, body=None, raw=None, timeout=300):
    data = raw if raw is not None else (json.dumps(body).encode() if body else None)
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        # lowercase header keys — Google returns X-Goog-Upload-Url in varying case
        return r.status, json.loads(r.read().decode() or "{}"), \
            {k.lower(): v for k, v in r.headers.items()}


_RESPONSE_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "scene_index": {"type": "INTEGER"},
            "subjects": {"type": "ARRAY", "items": {"type": "STRING"}},
            "action": {"type": "STRING"},
            "shot_type": {"type": "STRING"},
            "camera_angle": {"type": "STRING"},
            "camera_movement": {"type": "STRING"},
            "location": {"type": "STRING"},
            "composition": {"type": "STRING"},
            "emotion": {"type": "STRING"},
            "emotional_intensity": {"type": "NUMBER"},
            "physical_intensity": {"type": "NUMBER"},
            "usable_start": {"type": "NUMBER"},
            "usable_end": {"type": "NUMBER"},
            "story_uses": {"type": "ARRAY", "items": {"type": "STRING", "enum": [
                "hook", "location", "early_effort", "build", "peak",
                "completion", "reflection", "broll", "transition"]}},
            "continuity": {"type": "STRING"},
            "problems": {"type": "ARRAY", "items": {"type": "STRING"}},
            "natural_sound_value": {"type": "NUMBER"},
            "search_description": {"type": "STRING"},
        },
        "required": ["scene_index", "action", "shot_type", "search_description"],
    },
}


class GeminiVideoProvider(VideoUnderstandingProvider):
    name = "gemini"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY not configured")
        self.base = "https://generativelanguage.googleapis.com"

    def _upload(self, path: str) -> dict:
        size = os.path.getsize(path)
        st, meta, hdrs = _http(
            "POST", f"{self.base}/upload/v1beta/files?key={self.api_key}",
            headers={"X-Goog-Upload-Protocol": "resumable",
                     "X-Goog-Upload-Command": "start",
                     "X-Goog-Upload-Header-Content-Length": str(size),
                     "X-Goog-Upload-Header-Content-Type": "video/mp4",
                     "Content-Type": "application/json"},
            body={"file": {"display_name": os.path.basename(path)}})
        upload_url = hdrs.get("x-goog-upload-url")
        if not upload_url:
            raise RuntimeError("Gemini upload: no upload URL returned")
        with open(path, "rb") as f:
            blob = f.read()
        st, out, _ = _http("POST", upload_url,
                           headers={"X-Goog-Upload-Command": "upload, finalize",
                                    "X-Goog-Upload-Offset": "0",
                                    "Content-Length": str(size)},
                           raw=blob)
        file = out["file"]
        # wait for ACTIVE state (processing)
        for _ in range(60):
            if file.get("state") == "ACTIVE":
                return file
            time.sleep(3)
            st, file, _ = _http("GET", f"{self.base}/v1beta/{file['name']}?key={self.api_key}")
        raise RuntimeError(f"Gemini file stuck in state {file.get('state')}")

    def analyze(self, proxy_path: str, scenes: ScenesArtifact,
                context: str = "") -> SemanticArtifact:
        """Full analyze cycle (upload + generate) with retries.

        Project One findings on long phone clips (200-320 s): (a) large
        single-shot uploads can drop mid-flight, (b) generateContent on long
        videos can be closed server-side under load. Mitigations: clips longer
        than LONG_CLIP_S upload a 480p low-bitrate copy from the start (Gemini
        samples ~1 fps — analysis quality unaffected, payload ~4x smaller), and
        the WHOLE cycle retries with backoff."""
        import subprocess
        import tempfile
        LONG_CLIP_S = 150.0
        duration = max((s.end for s in scenes.scenes), default=0)
        upload_src = proxy_path
        if duration > LONG_CLIP_S:
            small = os.path.join(tempfile.gettempdir(),
                                 f"gemini-upload-{os.getpid()}.mp4")
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", proxy_path,
                 "-vf", "scale='if(gt(iw,ih),-2,480)':'if(gt(iw,ih),480,-2)'",
                 "-map_metadata", "-1", "-c:v", "libx264",
                 "-preset", "veryfast", "-crf", "32",
                 "-c:a", "aac", "-b:a", "64k", small],
                check=True, capture_output=True, timeout=900)
            upload_src = small

        from . import gemini_common
        last_err = None
        for attempt in range(3):
            file = None
            try:
                file = self._upload(upload_src)
                return self._analyze_uploaded(file, scenes, context)
            except Exception as e:  # noqa: BLE001 — retry the full cycle
                last_err = e
                time.sleep(5 * (attempt + 1))
            finally:
                # provider hygiene: delete the uploaded file every attempt.
                # Google auto-expires after ~48 h if deletion fails.
                if file is not None:
                    gemini_common.delete_file(file["name"], self.api_key)
        raise RuntimeError(f"Gemini analyze failed after retries: {last_err}")

    def _analyze_uploaded(self, file: dict, scenes: ScenesArtifact,
                          context: str = "") -> SemanticArtifact:
        scene_desc = "\n".join(
            f"scene {s.index}: {s.start:.2f}s to {s.end:.2f}s" for s in scenes.scenes)
        prompt = (
            "You are a video editor's footage analyst for fitness/lifestyle content. "
            "Analyze this footage. The video has been segmented into these scenes:\n"
            f"{scene_desc}\n\n"
            "For EVERY scene index listed, return one object describing: subjects, "
            "the physical action, shot size (wide/medium/close/extreme close), camera "
            "angle, camera movement (static/pan/handheld follow/drone/etc), location, "
            "composition notes, emotion, emotional_intensity 0-1, physical_intensity 0-1, "
            "the most usable sub-range as usable_start/usable_end in SECONDS (absolute "
            "video time within that scene), which story beats it could serve "
            "(hook/location/early_effort/build/peak/completion/reflection/broll/transition), "
            "continuity info (clothing, lighting, environment), visible problems, "
            "natural_sound_value 0-1, and a one-sentence search_description. "
            + (f"Additional context: {context}" if context else ""))
        body = {
            "contents": [{"parts": [
                {"file_data": {"file_uri": file["uri"], "mime_type": "video/mp4"}},
                {"text": prompt}]}],
            "generationConfig": {
                "response_mime_type": "application/json",
                "response_schema": _RESPONSE_SCHEMA,
                "temperature": 0.2,
            },
        }
        st, out, _ = _http("POST",
                           f"{self.base}/v1beta/models/{GEMINI_MODEL}:generateContent"
                           f"?key={self.api_key}",
                           headers={"Content-Type": "application/json"}, body=body,
                           timeout=600)
        try:
            text = out["candidates"][0]["content"]["parts"][0]["text"]
            raw_segments = json.loads(text)
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            raise RuntimeError(f"Gemini returned unusable output: {e}: "
                               f"{json.dumps(out)[:400]}")
        validated: list[SemanticSegment] = []
        errors = []
        for seg in raw_segments:
            try:
                validated.append(SemanticSegment(**seg))
            except ValidationError as e:
                errors.append(f"scene {seg.get('scene_index')}: {e.errors()[:1]}")
        if not validated:
            raise RuntimeError(f"no valid segments from provider: {errors[:3]}")
        return SemanticArtifact(provider=self.name, model=GEMINI_MODEL,
                                segments=validated)


def get_video_understanding_provider() -> VideoUnderstandingProvider | None:
    if os.environ.get("GEMINI_API_KEY"):
        return GeminiVideoProvider()
    return None
