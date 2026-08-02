from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import AssetManifest, SearchQuery
from .store import ManifestStore


@dataclass(frozen=True)
class LibrarySelectionQuery:
    duration_seconds: float
    mood: tuple[str, ...] = ()
    energy_min: float | None = None
    energy_max: float | None = None
    bpm_min: float | None = None
    bpm_max: float | None = None
    instrumental: bool | None = True
    max_results: int = 10


class AudioLibraryAdapter:
    """Read-only, deterministic lookup for Milestone 3 music plans."""

    def __init__(self, store: ManifestStore) -> None:
        self.store = store

    def search(self, query: LibrarySelectionQuery) -> list[AssetManifest]:
        return self.store.search(SearchQuery(
            assetType="music",
            durationMinSeconds=query.duration_seconds,
            moods=list(query.mood),
            energyMin=query.energy_min,
            energyMax=query.energy_max,
            bpmMin=query.bpm_min,
            bpmMax=query.bpm_max,
            instrumental=query.instrumental,
            maxResults=query.max_results,
        ))

    def search_for_music_plan(self, music_plan: dict[str, Any], *, max_results: int = 10) -> list[dict[str, Any]]:
        brief = music_plan.get("trackBrief", {})
        tempo = brief.get("tempoBpm")
        tone = brief.get("tone") or []
        if isinstance(tone, str):
            tone = [tone]
        arc = brief.get("energyArc") or []
        energy_values = [float(point.get("energy", 0.5)) for point in arc if isinstance(point, dict)]
        energy = sum(energy_values) / len(energy_values) if energy_values else None
        duration = float(
            music_plan.get("pictureDurationSeconds")
            or music_plan.get("durationSeconds")
            or brief.get("durationSeconds")
            or 0
        )
        query = LibrarySelectionQuery(
            duration_seconds=duration,
            mood=tuple(str(value).lower() for value in tone),
            energy_min=max(0.0, energy - 0.25) if energy is not None else None,
            energy_max=min(1.0, energy + 0.25) if energy is not None else None,
            bpm_min=max(30.0, float(tempo) - 10) if tempo else None,
            bpm_max=min(300.0, float(tempo) + 10) if tempo else None,
            max_results=max_results,
        )
        return [
            {
                "assetId": item.assetId,
                "normalizedPath": item.normalizedPath,
                "sourceProvider": item.sourceProvider,
                "sourceAssetId": item.sourceAssetId,
                "licenseName": item.licenseName,
                "licenseUrl": item.licenseUrl,
                "attributionText": item.attributionText,
                "sha256": item.sha256,
                "pcmFingerprint": item.pcmFingerprint,
                "ingestionVersion": item.ingestionVersion,
            }
            for item in self.search(query)
        ]
