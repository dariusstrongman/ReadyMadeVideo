"""Normalized provider, license, manifest, and ingestion contracts."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, model_validator

AssetType = Literal["music", "sfx"]
ValidationStatus = Literal["accepted", "rejected", "duplicate", "dry_run"]

MUSIC_CATEGORIES = {
    "cinematic", "energetic", "emotional", "ambient", "corporate",
    "tension", "uplifting",
}
SFX_CATEGORIES = {
    "whooshes", "impacts", "risers", "transitions", "ambience", "ui",
    "movement", "sports",
}
ALL_CATEGORIES = MUSIC_CATEGORIES | SFX_CATEGORIES


class ProviderTermsEvidence(BaseModel):
    termsUrl: HttpUrl
    reviewedAt: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}(?:T.*)?$")
    ingestionMethodAllowed: bool
    commercialApiUseAllowed: bool
    approvalReference: str | None = None


class ProviderAsset(BaseModel):
    assetType: AssetType
    category: str
    sourceProvider: str = Field(min_length=2)
    sourceUrl: HttpUrl
    sourceAssetId: str = Field(min_length=1, max_length=200)
    filename: str = Field(min_length=1, max_length=255)
    title: str | None = Field(default=None, max_length=255)
    creatorName: str | None = None
    creatorUrl: HttpUrl | None = None
    licenseName: str = Field(min_length=1)
    licenseUrl: HttpUrl | None = None
    attributionText: str | None = None
    declaredCommercialUseAllowed: bool | None = None
    declaredModificationAllowed: bool | None = None
    contentType: str | None = None
    durationSeconds: float | None = Field(default=None, gt=0)
    sampleRate: int | None = Field(default=None, gt=0)
    channels: int | None = Field(default=None, ge=1)
    bitrate: int | None = Field(default=None, ge=0)
    tags: list[str] = Field(default_factory=list)
    mood: list[str] = Field(default_factory=list)
    energy: float | None = Field(default=None, ge=0, le=1)
    bpm: float | None = Field(default=None, ge=30, le=300)
    instrumental: bool | None = None
    vocal: bool | None = None
    usageConstraints: list[str] = Field(default_factory=list)
    providerTerms: ProviderTermsEvidence
    localSourcePath: str | None = None

    @model_validator(mode="after")
    def valid_category(self):
        if self.category not in ALL_CATEGORIES:
            raise ValueError(f"unsupported audio category: {self.category}")
        expected = MUSIC_CATEGORIES if self.assetType == "music" else SFX_CATEGORIES
        if self.category not in expected:
            raise ValueError("category does not match asset type")
        self.tags = sorted({tag.strip().lower() for tag in self.tags if tag.strip()})
        self.mood = sorted({tag.strip().lower() for tag in self.mood if tag.strip()})
        self.usageConstraints = sorted({value.strip() for value in self.usageConstraints if value.strip()})
        return self


class SearchQuery(BaseModel):
    text: str = Field(default="", max_length=200)
    assetType: AssetType | None = None
    categories: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    durationMinSeconds: float | None = Field(default=None, ge=0)
    durationMaxSeconds: float | None = Field(default=None, gt=0)
    licenses: list[str] = Field(default_factory=list)
    providers: list[str] = Field(default_factory=list)
    moods: list[str] = Field(default_factory=list)
    energyMin: float | None = Field(default=None, ge=0, le=1)
    energyMax: float | None = Field(default=None, ge=0, le=1)
    bpmMin: float | None = Field(default=None, ge=30, le=300)
    bpmMax: float | None = Field(default=None, ge=30, le=300)
    instrumental: bool | None = None
    vocal: bool | None = None
    attributionRequired: bool | None = None
    maxResults: int = Field(default=10, ge=1, le=100)

    @model_validator(mode="after")
    def valid_ranges(self):
        if (self.durationMinSeconds is not None and self.durationMaxSeconds is not None
                and self.durationMinSeconds > self.durationMaxSeconds):
            raise ValueError("duration minimum exceeds maximum")
        if (self.energyMin is not None and self.energyMax is not None
                and self.energyMin > self.energyMax):
            raise ValueError("energy minimum exceeds maximum")
        if (self.bpmMin is not None and self.bpmMax is not None
                and self.bpmMin > self.bpmMax):
            raise ValueError("BPM minimum exceeds maximum")
        unknown = set(self.categories) - ALL_CATEGORIES
        if unknown:
            raise ValueError(f"unsupported categories: {', '.join(sorted(unknown))}")
        return self


class LicenseDecision(BaseModel):
    accepted: bool
    reasonCode: str
    reason: str
    normalizedLicenseId: str | None = None
    licenseName: str
    licenseUrl: str | None = None
    attributionRequired: bool = False
    attributionText: str | None = None
    commercialUseAllowed: bool = False
    modificationAllowed: bool = False
    approvalReference: str | None = None
    policyVersion: str


class AudioProbe(BaseModel):
    codec: str
    durationSeconds: float = Field(gt=0)
    sampleRate: int = Field(gt=0)
    channels: int = Field(ge=1, le=2)
    bitrate: int | None = Field(default=None, ge=0)
    contentType: str
    originalFormat: str


class TransformationRecord(BaseModel):
    tool: Literal["ffmpeg"] = "ffmpeg"
    commands: list[list[str]] = Field(min_length=1)
    sampleRate: Literal[48000] = 48000
    channels: int = Field(ge=1, le=2)
    codec: Literal["pcm_s24le"] = "pcm_s24le"
    loudnessTargetLufs: float | None = None
    truePeakTargetDb: float
    metadataStripped: Literal[True] = True
    sourceOverwritten: Literal[False] = False


class AssetManifest(BaseModel):
    assetId: str
    filename: str
    originalFilename: str
    title: str
    assetType: AssetType
    category: str
    sourceProvider: str
    sourceUrl: str
    sourceAssetId: str
    creatorName: str
    creatorUrl: str | None
    licenseName: str
    licenseUrl: str
    attributionRequired: bool
    attributionText: str | None
    providerAttributionText: str | None
    commercialUseAllowed: Literal[True] = True
    modificationAllowed: Literal[True] = True
    providerTermsUrl: str
    providerTermsReviewedAt: str
    providerApprovalReference: str
    licenseApprovalReference: str
    downloadedAt: str
    originalFormat: str
    sourceSampleRate: int
    sourceChannels: int
    sourceBitrate: int | None
    normalizedFormat: Literal["wav"] = "wav"
    durationSeconds: float
    sampleRate: Literal[48000] = 48000
    channels: int
    bitrate: int | None
    tags: list[str]
    mood: list[str]
    energy: float | None
    bpm: float | None
    instrumental: bool | None
    vocal: bool | None
    usageConstraints: list[str]
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    pcmFingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    ingestionVersion: str
    validationStatus: Literal["accepted"] = "accepted"
    normalizedPath: str
    originalPreserved: bool
    transformation: TransformationRecord


class RejectionRecord(BaseModel):
    sourceProvider: str
    sourceAssetId: str
    sourceUrl: str
    filename: str
    rejectedAt: str
    reasonCode: str
    reason: str
    licenseName: str
    licenseUrl: str | None
    providerTermsUrl: str
    downloadAttempted: bool
    copiedToEligibleLibrary: Literal[False] = False


class DuplicateRecord(BaseModel):
    sourceProvider: str
    sourceAssetId: str
    duplicateOfAssetId: str
    duplicateType: Literal["source_identity", "sha256", "pcm_fingerprint"]
    detectedAt: str
    normalizedAgain: Literal[False] = False


class IngestionItemResult(BaseModel):
    sourceProvider: str
    sourceAssetId: str
    status: ValidationStatus
    assetId: str | None = None
    reasonCode: str | None = None
    duplicateOfAssetId: str | None = None


class IngestionSummary(BaseModel):
    schemaVersion: int = 1
    startedAt: str
    completedAt: str
    dryRun: bool
    requestedCount: int
    processedCount: int
    acceptedCount: int
    rejectedCount: int
    duplicateCount: int
    resumedCount: int
    items: list[IngestionItemResult]
