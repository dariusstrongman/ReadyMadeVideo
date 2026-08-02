"""Stage 4b: transcription with word-level timestamps behind a provider interface.

Primary provider: OpenAI Whisper API (whisper-1, verbose_json + word timestamps)
— zero local install risk, ~$0.006/min. Optional local providers (faster-whisper,
WhisperX alignment, pyannote diarization, DeepFilterNet enhancement) are declared
as interfaces and can be added without touching callers.
"""
from __future__ import annotations

import json
import os
import urllib.request
from abc import ABC, abstractmethod

from .schemas import Sentence, TranscriptArtifact, Word


class TranscriptionProvider(ABC):
    name: str = "abstract"

    @abstractmethod
    def transcribe(self, wav_path: str) -> TranscriptArtifact: ...


class OpenAIWhisperProvider(TranscriptionProvider):
    name = "openai-whisper-1"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY not configured")

    def transcribe(self, wav_path: str) -> TranscriptArtifact:
        boundary = "----stromation-pipeline"
        with open(wav_path, "rb") as f:
            blob = f.read()
        parts = []
        for k, v in (("model", "whisper-1"), ("response_format", "verbose_json"),
                     ("timestamp_granularities[]", "word"),
                     ("timestamp_granularities[]", "segment")):
            parts.append(f"--{boundary}\r\nContent-Disposition: form-data; "
                         f"name=\"{k}\"\r\n\r\n{v}\r\n".encode())
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; "
                     f"name=\"file\"; filename=\"audio.wav\"\r\n"
                     f"Content-Type: audio/wav\r\n\r\n".encode() + blob + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        body = b"".join(parts)
        req = urllib.request.Request(
            "https://api.openai.com/v1/audio/transcriptions", data=body,
            headers={"Authorization": f"Bearer {self.api_key}",
                     "Content-Type": f"multipart/form-data; boundary={boundary}"})
        with urllib.request.urlopen(req, timeout=300) as r:
            data = json.loads(r.read().decode())

        words = [Word(word=w["word"], start=w["start"], end=w["end"])
                 for w in data.get("words", [])]
        sentences = [Sentence(text=s["text"].strip(), start=s["start"], end=s["end"])
                     for s in data.get("segments", []) if s.get("text", "").strip()]
        return TranscriptArtifact(provider=self.name,
                                  language=data.get("language"),
                                  text=data.get("text", "").strip(),
                                  words=words, sentences=sentences)


def get_transcription_provider() -> TranscriptionProvider | None:
    """Return the configured provider, or None (stage records 'skipped')."""
    if os.environ.get("OPENAI_API_KEY"):
        return OpenAIWhisperProvider()
    return None
