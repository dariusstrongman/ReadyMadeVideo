"""Auditable local audio-asset ingestion and M3 library selection."""

from .integration import (
    AudioLibraryAdapter,
    LibrarySelectionQuery,
    default_audio_library_adapter,
)
from .licenses import LicensePolicy
from .store import AudioLibraryPaths, ManifestStore

__all__ = [
    "AudioLibraryAdapter", "AudioLibraryPaths", "LibrarySelectionQuery",
    "LicensePolicy", "ManifestStore", "default_audio_library_adapter",
]
