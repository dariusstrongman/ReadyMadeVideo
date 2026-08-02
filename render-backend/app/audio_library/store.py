from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import AssetManifest, DuplicateRecord, RejectionRecord, SearchQuery


def _json_text(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


@dataclass(frozen=True)
class AudioLibraryPaths:
    root: Path

    @property
    def manifests(self) -> Path:
        return self.root / "manifests" / "assets"

    @property
    def temp(self) -> Path:
        return self.root / "temp"

    @property
    def manual_import(self) -> Path:
        return self.root / "manual-import"

    @property
    def license_approvals(self) -> Path:
        return self.root / "approved-licenses.json"

    def normalized_dir(self, asset_type: str, category: str) -> Path:
        return self.root / asset_type / category


class ManifestStore:
    def __init__(self, paths: AudioLibraryPaths) -> None:
        self.paths = paths
        self.paths.manifests.mkdir(parents=True, exist_ok=True)
        self.paths.temp.mkdir(parents=True, exist_ok=True)

    def load_manifests(self) -> list[AssetManifest]:
        manifests = []
        for path in sorted(self.paths.manifests.glob("*.json")):
            manifests.append(AssetManifest.model_validate_json(path.read_text(encoding="utf-8")))
        return sorted(manifests, key=lambda item: item.assetId)

    def save_manifest(self, manifest: AssetManifest) -> None:
        target = self.paths.manifests / f"{manifest.assetId}.json"
        if target.exists():
            raise FileExistsError(f"Manifest already exists: {manifest.assetId}")
        _atomic_write(target, _json_text(manifest.model_dump(mode="json")))

    def _load_records(self, filename: str, model: type) -> list:
        path = self.paths.root / filename
        if not path.exists():
            return []
        return [model.model_validate(item) for item in json.loads(path.read_text(encoding="utf-8"))]

    def add_rejection(self, record: RejectionRecord) -> None:
        records = self._load_records("rejected-assets.json", RejectionRecord)
        records.append(record)
        records.sort(key=lambda item: (item.sourceProvider, item.sourceAssetId, item.rejectedAt))
        _atomic_write(self.paths.root / "rejected-assets.json", _json_text([r.model_dump(mode="json") for r in records]))

    def add_duplicate(self, record: DuplicateRecord) -> None:
        records = self._load_records("duplicate-report.json", DuplicateRecord)
        records.append(record)
        records.sort(key=lambda item: (item.sourceProvider, item.sourceAssetId, item.detectedAt))
        _atomic_write(self.paths.root / "duplicate-report.json", _json_text([r.model_dump(mode="json") for r in records]))

    def write_summary(self, summary: Any) -> None:
        _atomic_write(self.paths.root / "ingestion-summary.json", _json_text(summary.model_dump(mode="json")))

    def indexes(self) -> tuple[dict[tuple[str, str], AssetManifest], dict[str, AssetManifest], dict[str, AssetManifest]]:
        manifests = self.load_manifests()
        return (
            {(item.sourceProvider, item.sourceAssetId): item for item in manifests},
            {item.sha256: item for item in manifests},
            {item.pcmFingerprint: item for item in manifests},
        )

    def search(self, query: SearchQuery) -> list[AssetManifest]:
        def matches(item: AssetManifest) -> bool:
            haystack = " ".join([item.filename, *item.tags, *item.mood, item.category]).lower()
            return all((
                not query.text or query.text.lower() in haystack,
                query.assetType is None or item.assetType == query.assetType,
                not query.categories or item.category in query.categories,
                not query.tags or set(query.tags).issubset(item.tags),
                query.durationMinSeconds is None or item.durationSeconds >= query.durationMinSeconds,
                query.durationMaxSeconds is None or item.durationSeconds <= query.durationMaxSeconds,
                not query.licenses or item.licenseName in query.licenses,
                not query.providers or item.sourceProvider in query.providers,
                not query.moods or bool(set(query.moods) & set(item.mood)),
                query.energyMin is None or item.energy is not None and item.energy >= query.energyMin,
                query.energyMax is None or item.energy is not None and item.energy <= query.energyMax,
                query.bpmMin is None or item.bpm is not None and item.bpm >= query.bpmMin,
                query.bpmMax is None or item.bpm is not None and item.bpm <= query.bpmMax,
                query.instrumental is None or item.instrumental == query.instrumental,
                query.vocal is None or item.vocal == query.vocal,
                query.attributionRequired is None or item.attributionRequired == query.attributionRequired,
            ))

        return [item for item in self.load_manifests() if matches(item)][:query.maxResults]

    def report(self) -> dict[str, Any]:
        assets = self.load_manifests()
        report = {
            "schemaVersion": 1,
            "assetCount": len(assets),
            "musicCount": sum(item.assetType == "music" for item in assets),
            "sfxCount": sum(item.assetType == "sfx" for item in assets),
            "assets": [item.model_dump(mode="json") for item in assets],
        }
        _atomic_write(self.paths.root / "audio-library.json", _json_text(report))
        lines = ["# Audio attribution report", "", f"Eligible assets: {len(assets)}", ""]
        for item in assets:
            if item.attributionRequired:
                lines.extend([f"## {item.filename}", "", item.attributionText or "", f"License: {item.licenseUrl}", ""])
        _atomic_write(self.paths.root / "attribution-report.md", "\n".join(lines).rstrip() + "\n")
        return report

    def validate(self) -> list[str]:
        errors: list[str] = []
        seen_paths: set[str] = set()
        for manifest in self.load_manifests():
            target = (self.paths.root / manifest.normalizedPath).resolve()
            if self.paths.root.resolve() not in target.parents:
                errors.append(f"{manifest.assetId}: normalized path escapes library")
            elif not target.is_file():
                errors.append(f"{manifest.assetId}: normalized file missing")
            if manifest.normalizedPath in seen_paths:
                errors.append(f"{manifest.assetId}: duplicate normalized path")
            seen_paths.add(manifest.normalizedPath)
        return errors
