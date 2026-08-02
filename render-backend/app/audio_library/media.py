from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .models import AssetType, AudioProbe, TransformationRecord


ALLOWED_EXTENSIONS = {".wav", ".flac", ".mp3", ".ogg", ".aif", ".aiff", ".m4a"}
ALLOWED_MIME_PREFIXES = {"audio/", "application/ogg", "application/octet-stream"}
MAX_AUDIO_BYTES = 100 * 1024 * 1024
MAX_DURATION_SECONDS = 600.0


def _run(command: list[str], *, capture: bool = True) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(command, check=True, capture_output=capture, shell=False)


def require_media_tools() -> None:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError("ffmpeg and ffprobe are required")


def validate_container(path: Path, content_type: str) -> None:
    if path.suffix.lower() not in ALLOWED_EXTENSIONS:
        raise ValueError(f"Unsupported audio extension: {path.suffix}")
    if path.stat().st_size > MAX_AUDIO_BYTES:
        raise ValueError("Audio file exceeds maximum size")
    normalized = content_type.split(";", 1)[0].strip().lower()
    if normalized and not any(normalized.startswith(prefix) for prefix in ALLOWED_MIME_PREFIXES):
        raise ValueError(f"Invalid audio MIME type: {normalized}")


def probe_audio(path: Path) -> AudioProbe:
    require_media_tools()
    result = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration,format_name:stream=index,codec_type,codec_name,sample_rate,channels",
            "-of",
            "json",
            str(path),
        ]
    )
    payload = json.loads(result.stdout.decode("utf-8"))
    audio_streams = [stream for stream in payload.get("streams", []) if stream.get("codec_type") == "audio"]
    other_streams = [stream for stream in payload.get("streams", []) if stream.get("codec_type") != "audio"]
    if len(audio_streams) != 1 or other_streams:
        raise ValueError("Media must contain exactly one audio stream and no other streams")
    stream = audio_streams[0]
    duration = float(payload.get("format", {}).get("duration") or 0)
    channels = int(stream.get("channels") or 0)
    sample_rate = int(stream.get("sample_rate") or 0)
    if duration <= 0 or duration > MAX_DURATION_SECONDS:
        raise ValueError("Audio duration is outside the allowed range")
    if channels not in {1, 2}:
        raise ValueError("Only mono or stereo audio is supported")
    if not 32000 <= sample_rate <= 96000:
        raise ValueError("Sample rate is outside the allowed range")
    return AudioProbe(
        durationSeconds=duration,
        channels=channels,
        sampleRate=sample_rate,
        codec=str(stream.get("codec_name") or "unknown"),
        contentType="audio/" + str(stream.get("codec_name") or "unknown"),
        originalFormat=str(payload.get("format", {}).get("format_name") or "unknown"),
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normalized_pcm_fingerprint(path: Path) -> str:
    require_media_tools()
    result = _run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-ac",
            "1",
            "-ar",
            "48000",
            "-f",
            "s16le",
            "pipe:1",
        ]
    )
    return hashlib.sha256(result.stdout).hexdigest()


@dataclass(frozen=True)
class NormalizationResult:
    output: Path
    probe: AudioProbe
    transformation: TransformationRecord


def normalize_audio(source: Path, destination: Path, asset_type: AssetType) -> NormalizationResult:
    require_media_tools()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(f"Refusing to overwrite normalized asset: {destination}")
    filters = "loudnorm=I=-18:TP=-1.5:LRA=11" if asset_type == "music" else "alimiter=limit=0.891"
    command = [
        "ffmpeg",
        "-v",
        "error",
        "-nostdin",
        "-i",
        str(source),
        "-map",
        "0:a:0",
        "-vn",
        "-map_metadata",
        "-1",
        "-af",
        filters,
        "-ar",
        "48000",
        "-c:a",
        "pcm_s24le",
    ]
    if asset_type == "music":
        command.extend(["-ac", "2"])
    command.extend(["-y", str(destination)])
    _run(command)
    probe = probe_audio(destination)
    if probe.sampleRate != 48000:
        destination.unlink(missing_ok=True)
        raise ValueError("Normalized audio did not reach 48 kHz")
    canonical = ["{input}" if part == str(source) else "{output}" if part == str(destination) else part for part in command]
    return NormalizationResult(
        output=destination,
        probe=probe,
        transformation=TransformationRecord(
            tool="ffmpeg",
            commands=[canonical],
            channels=probe.channels,
            loudnessTargetLufs=-18.0 if asset_type == "music" else None,
            truePeakTargetDb=-1.5 if asset_type == "music" else -1.0,
        ),
    )
