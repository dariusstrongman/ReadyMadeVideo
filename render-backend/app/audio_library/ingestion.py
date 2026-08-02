from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path

from .licenses import POLICY_VERSION, LicensePolicy
from .media import normalize_audio, normalized_pcm_fingerprint, probe_audio, sha256_file, validate_container
from .models import (
    AssetManifest,
    DuplicateRecord,
    IngestionItemResult,
    IngestionSummary,
    ProviderAsset,
    RejectionRecord,
)
from .providers import AudioProvider
from .store import ManifestStore, _atomic_write, _json_text


INGESTION_VERSION = "audio-ingestion-v1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_stem(filename: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_-]+", "-", Path(filename).stem).strip("-").lower()
    return value[:80] or "audio"


class AudioIngestor:
    def __init__(
        self,
        *,
        store: ManifestStore,
        policy: LicensePolicy,
        clock: Callable[[], str] = _utc_now,
    ) -> None:
        self.store = store
        self.policy = policy
        self.clock = clock

    @property
    def state_path(self) -> Path:
        return self.store.paths.root / "ingestion-state.json"

    def _load_state(self) -> set[str]:
        if not self.state_path.exists():
            return set()
        return set(json.loads(self.state_path.read_text(encoding="utf-8")).get("processed", []))

    def _write_state(self, processed: set[str]) -> None:
        _atomic_write(self.state_path, _json_text({"schemaVersion": 1, "processed": sorted(processed)}))

    def _reject(
        self,
        asset: ProviderAsset,
        code: str,
        reason: str,
        *,
        persist: bool = True,
        download_attempted: bool = False,
    ) -> IngestionItemResult:
        if persist:
            self.store.add_rejection(RejectionRecord(
                sourceProvider=asset.sourceProvider,
                sourceAssetId=asset.sourceAssetId,
                sourceUrl=str(asset.sourceUrl),
                filename=asset.filename,
                rejectedAt=self.clock(),
                reasonCode=code,
                reason=reason,
                licenseName=asset.licenseName,
                licenseUrl=str(asset.licenseUrl) if asset.licenseUrl else None,
                providerTermsUrl=str(asset.providerTerms.termsUrl),
                downloadAttempted=download_attempted,
            ))
        return IngestionItemResult(
            sourceProvider=asset.sourceProvider,
            sourceAssetId=asset.sourceAssetId,
            status="rejected",
            reasonCode=code,
        )

    def _duplicate(
        self, asset: ProviderAsset, existing: AssetManifest, kind: str, *, persist: bool = True,
    ) -> IngestionItemResult:
        if persist:
            self.store.add_duplicate(DuplicateRecord(
                sourceProvider=asset.sourceProvider,
                sourceAssetId=asset.sourceAssetId,
                duplicateOfAssetId=existing.assetId,
                duplicateType=kind,
                detectedAt=self.clock(),
            ))
        return IngestionItemResult(
            sourceProvider=asset.sourceProvider,
            sourceAssetId=asset.sourceAssetId,
            status="duplicate",
            duplicateOfAssetId=existing.assetId,
            reasonCode=f"duplicate_{kind}",
        )

    def ingest(
        self,
        provider: AudioProvider,
        assets: Iterable[ProviderAsset],
        *,
        dry_run: bool = False,
        max_count: int = 10,
        resume: bool = False,
    ) -> IngestionSummary:
        if not 1 <= max_count <= 100:
            raise ValueError("max_count must be between 1 and 100")
        started = self.clock()
        processed_state = self._load_state() if resume else set()
        items: list[IngestionItemResult] = []
        resumed_count = 0
        candidates = list(assets)[:max_count]
        source_index, sha_index, fingerprint_index = self.store.indexes()

        for asset in candidates:
            identity = f"{asset.sourceProvider}:{asset.sourceAssetId}"
            if identity in processed_state:
                resumed_count += 1
                continue
            decision = self.policy.evaluate(asset)
            if not decision.accepted:
                item = self._reject(asset, decision.reasonCode, decision.reason, persist=not dry_run)
                items.append(item)
                if not dry_run:
                    processed_state.add(identity)
                    self._write_state(processed_state)
                continue
            existing = source_index.get((asset.sourceProvider, asset.sourceAssetId))
            if existing:
                items.append(self._duplicate(asset, existing, "source_identity", persist=not dry_run))
                if not dry_run:
                    processed_state.add(identity)
                    self._write_state(processed_state)
                continue
            if dry_run:
                items.append(IngestionItemResult(
                    sourceProvider=asset.sourceProvider,
                    sourceAssetId=asset.sourceAssetId,
                    status="dry_run",
                ))
                continue

            suffix = Path(asset.filename).suffix.lower()
            temp = self.store.paths.temp / f"{uuid.uuid4().hex}{suffix}"
            normalized: Path | None = None
            normalized_created = False
            try:
                downloaded = provider.download(asset, temp)
                validate_container(downloaded.path, downloaded.content_type or asset.contentType or "")
                source_probe = probe_audio(downloaded.path)
                digest = sha256_file(downloaded.path)
                existing = sha_index.get(digest)
                if existing:
                    items.append(self._duplicate(asset, existing, "sha256"))
                    continue
                fingerprint = normalized_pcm_fingerprint(downloaded.path)
                existing = fingerprint_index.get(fingerprint)
                if existing:
                    items.append(self._duplicate(asset, existing, "pcm_fingerprint"))
                    continue
                asset_id = f"aud_{digest[:24]}"
                filename = f"{_safe_stem(asset.filename)}-{digest[:12]}.wav"
                normalized = self.store.paths.normalized_dir(asset.assetType, asset.category) / filename
                result = normalize_audio(downloaded.path, normalized, asset.assetType)
                normalized_created = True
                relative_path = normalized.relative_to(self.store.paths.root).as_posix()
                terms = asset.providerTerms
                manifest = AssetManifest(
                    assetId=asset_id,
                    filename=filename,
                    originalFilename=asset.filename,
                    assetType=asset.assetType,
                    category=asset.category,
                    sourceProvider=asset.sourceProvider,
                    sourceUrl=str(asset.sourceUrl),
                    sourceAssetId=asset.sourceAssetId,
                    creatorName=asset.creatorName or "",
                    creatorUrl=str(asset.creatorUrl) if asset.creatorUrl else None,
                    licenseName=decision.licenseName,
                    licenseUrl=decision.licenseUrl or "",
                    attributionRequired=decision.attributionRequired,
                    attributionText=decision.attributionText,
                    providerTermsUrl=str(terms.termsUrl),
                    providerTermsReviewedAt=terms.reviewedAt,
                    providerApprovalReference=terms.approvalReference or "",
                    licenseApprovalReference=decision.approvalReference or "",
                    downloadedAt=self.clock(),
                    originalFormat=source_probe.originalFormat,
                    sourceSampleRate=source_probe.sampleRate,
                    sourceChannels=source_probe.channels,
                    sourceBitrate=source_probe.bitrate,
                    durationSeconds=result.probe.durationSeconds,
                    channels=result.probe.channels,
                    bitrate=result.probe.bitrate,
                    tags=asset.tags,
                    mood=asset.mood,
                    energy=asset.energy,
                    bpm=asset.bpm,
                    instrumental=asset.instrumental,
                    vocal=asset.vocal,
                    usageConstraints=sorted({
                        *asset.usageConstraints,
                        *(["attribution_required"] if decision.attributionRequired else []),
                    }),
                    sha256=digest,
                    pcmFingerprint=fingerprint,
                    ingestionVersion=f"{INGESTION_VERSION}/{POLICY_VERSION}",
                    normalizedPath=relative_path,
                    originalPreserved=asset.sourceProvider == "manual",
                    transformation=result.transformation,
                )
                self.store.save_manifest(manifest)
                source_index[(asset.sourceProvider, asset.sourceAssetId)] = manifest
                sha_index[digest] = manifest
                fingerprint_index[fingerprint] = manifest
                items.append(IngestionItemResult(
                    sourceProvider=asset.sourceProvider,
                    sourceAssetId=asset.sourceAssetId,
                    status="accepted",
                    assetId=asset_id,
                ))
            except (OSError, RuntimeError, ValueError) as exc:
                if normalized and normalized_created:
                    normalized.unlink(missing_ok=True)
                items.append(self._reject(
                    asset, "media_validation_failed", str(exc), download_attempted=True,
                ))
            finally:
                temp.unlink(missing_ok=True)
                processed_state.add(identity)
                self._write_state(processed_state)

        summary = IngestionSummary(
            startedAt=started,
            completedAt=self.clock(),
            dryRun=dry_run,
            requestedCount=len(candidates),
            processedCount=len(items),
            acceptedCount=sum(item.status == "accepted" for item in items),
            rejectedCount=sum(item.status == "rejected" for item in items),
            duplicateCount=sum(item.status == "duplicate" for item in items),
            resumedCount=resumed_count,
            items=items,
        )
        if not dry_run:
            self.store.write_summary(summary)
            self.store.report()
        return summary
