from __future__ import annotations

import json
import os
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .models import ProviderAsset, ProviderTermsEvidence, SearchQuery


MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024
FREESOUND_HOSTS = {"freesound.org", "www.freesound.org", "cdn.freesound.org"}
DOWNLOAD_CHUNK_BYTES = 64 * 1024


def _write_bounded_stream(
    destination: Path, chunks: Iterable[bytes], *, maximum_bytes: int,
) -> int:
    """Write an iterable of bytes atomically while enforcing a running limit."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(f"{destination.name}.part")
    if destination.exists() or partial.exists():
        raise FileExistsError(f"Refusing to overwrite download path: {destination}")
    total = 0
    try:
        with partial.open("xb") as handle:
            for chunk in chunks:
                if not chunk:
                    continue
                total += len(chunk)
                if total > maximum_bytes:
                    raise ValueError("Download exceeds maximum size")
                handle.write(chunk)
        os.replace(partial, destination)
        return total
    except BaseException:
        partial.unlink(missing_ok=True)
        raise


@dataclass(frozen=True)
class DownloadResult:
    path: Path
    content_type: str
    size_bytes: int
    final_url: str


class AudioProvider(ABC):
    name: str

    @abstractmethod
    def search(self, query: SearchQuery) -> list[ProviderAsset]: ...

    @abstractmethod
    def download(self, asset: ProviderAsset, destination: Path) -> DownloadResult: ...


class FreesoundProvider(AudioProvider):
    """Official Freesound API adapter with a commercial-use approval gate."""

    name = "freesound"
    api_origin = "https://freesound.org"

    def __init__(
        self,
        *,
        api_key: str | None,  # noqa: S107 - caller supplies secret through environment
        oauth_token: str | None,  # noqa: S107 - caller supplies secret through environment
        commercial_api_approved: bool,
        approval_reference: str | None,
        terms_reviewed_at: str | None,
        client: httpx.Client | None = None,
        retries: int = 3,
        rate_limit_seconds: float = 0.25,
        max_download_bytes: int = MAX_DOWNLOAD_BYTES,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.api_key = api_key
        self.oauth_token = oauth_token
        self.commercial_api_approved = commercial_api_approved
        self.approval_reference = approval_reference
        self.terms_reviewed_at = terms_reviewed_at
        self.client = client or httpx.Client(timeout=httpx.Timeout(30, read=120))
        self.retries = max(0, min(retries, 5))
        self.rate_limit_seconds = max(0.0, rate_limit_seconds)
        self.max_download_bytes = max(1, min(max_download_bytes, MAX_DOWNLOAD_BYTES))
        self.sleep = sleep

    def _require_search_access(self) -> None:
        if not self.api_key:
            raise RuntimeError("FREESOUND_API_KEY is required")
        if not self.terms_reviewed_at:
            raise RuntimeError("FREESOUND_TERMS_REVIEWED_AT is required")

    def _require_ingestion_access(self) -> None:
        if not self.oauth_token:
            raise RuntimeError("FREESOUND_OAUTH_TOKEN is required for original downloads")
        if not self.commercial_api_approved or not self.approval_reference:
            raise RuntimeError(
                "Freesound ingestion is disabled until commercial API approval and its reference are configured"
            )
        if not self.terms_reviewed_at:
            raise RuntimeError("FREESOUND_TERMS_REVIEWED_AT is required")

    def _request(
        self, method: str, url: str, *, stream: bool = False, **kwargs: object,
    ) -> httpx.Response:
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in FREESOUND_HOSTS:
            raise RuntimeError(f"Unsafe Freesound URL: {url}")
        attempt = 0
        redirects = 0
        while True:
            try:
                request = self.client.build_request(method, url, **kwargs)
                response = self.client.send(
                    request, follow_redirects=False, stream=stream,
                )
            except httpx.TransportError as exc:
                if attempt >= self.retries:
                    raise RuntimeError("Freesound request failed after bounded retries") from exc
                self.sleep(2**attempt)
                attempt += 1
                continue
            if response.status_code in {301, 302, 303, 307, 308}:
                response.close()
                redirects += 1
                if redirects > 3:
                    raise RuntimeError("Freesound redirect limit exceeded")
                target = str(response.headers.get("location", ""))
                resolved = str(httpx.URL(url).join(target))
                target_parsed = urlparse(resolved)
                if target_parsed.scheme != "https" or target_parsed.hostname not in FREESOUND_HOSTS:
                    raise RuntimeError(f"Unsafe redirect blocked: {resolved}")
                url = resolved
                continue
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < self.retries:
                    retry_after = response.headers.get("retry-after")
                    delay = min(float(retry_after), 30.0) if retry_after and retry_after.isdigit() else 2**attempt
                    self.sleep(delay)
                    attempt += 1
                    response.close()
                    continue
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError:
                response.close()
                raise
            if self.rate_limit_seconds:
                self.sleep(self.rate_limit_seconds)
            return response

    def search(self, query: SearchQuery) -> list[ProviderAsset]:
        self._require_search_access()
        filters = []
        if query.durationMinSeconds is not None:
            filters.append(f"duration:[{query.durationMinSeconds} TO *]")
        if query.durationMaxSeconds is not None:
            filters.append(f"duration:[* TO {query.durationMaxSeconds}]")
        response = self._request(
            "GET",
            f"{self.api_origin}/apiv2/search/text/",
            headers={"Authorization": f"Token {self.api_key}"},
            params={
                "query": query.text or " ",
                "filter": " ".join(filters),
                "page_size": query.maxResults,
                "fields": "id,name,url,download,username,license,duration,samplerate,channels,type,tags,description",
            },
        )
        results = []
        for raw in response.json().get("results", []):
            license_url = str(raw.get("license") or "")
            creator = str(raw.get("username") or "")
            source_url = str(raw.get("url") or f"{self.api_origin}/people/{creator}/sounds/{raw['id']}/")
            results.append(
                ProviderAsset(
                    sourceProvider=self.name,
                    sourceAssetId=str(raw["id"]),
                    sourceUrl=source_url,
                    filename=str(raw.get("name") or f"{raw['id']}.audio"),
                    title=str(raw.get("name") or raw["id"]),
                    assetType=query.assetType or "sfx",
                    category=query.categories[0] if query.categories else ("ambient" if query.assetType == "music" else "ambience"),
                    creatorName=creator,
                    creatorUrl=f"{self.api_origin}/people/{creator}/",
                    licenseName=license_url.rstrip("/").split("/")[-2] if license_url else "unknown",
                    licenseUrl=license_url or None,
                    attributionText=(
                        f'"{raw.get("name", raw["id"])}" by {creator} '
                        f"({self.api_origin}/people/{creator}/). Source: {source_url}. "
                        f"License: {license_url}."
                    ),
                    declaredCommercialUseAllowed=None,
                    declaredModificationAllowed=None,
                    tags=[str(tag) for tag in raw.get("tags", [])],
                    durationSeconds=float(raw["duration"]) if raw.get("duration") is not None else None,
                    sampleRate=int(raw["samplerate"]) if raw.get("samplerate") else None,
                    channels=int(raw["channels"]) if raw.get("channels") else None,
                    contentType=None,
                    providerTerms=ProviderTermsEvidence(
                        termsUrl="https://freesound.org/help/tos_api/",
                        reviewedAt=self.terms_reviewed_at or "1970-01-01",
                        ingestionMethodAllowed=self.commercial_api_approved,
                        commercialApiUseAllowed=self.commercial_api_approved,
                        approvalReference=self.approval_reference,
                    ),
                )
            )
        return results

    def download(self, asset: ProviderAsset, destination: Path) -> DownloadResult:
        self._require_ingestion_access()
        if asset.sourceProvider != self.name or not asset.sourceAssetId.isdigit():
            raise ValueError("Invalid Freesound asset identity")
        response = self._request(
            "GET",
            f"{self.api_origin}/apiv2/sounds/{asset.sourceAssetId}/download/",
            stream=True,
            headers={"Authorization": f"Bearer {self.oauth_token}"},
        )
        try:
            length = response.headers.get("content-length")
            if length:
                try:
                    declared_size = int(length)
                except ValueError as exc:
                    raise ValueError("Download has an invalid Content-Length") from exc
                if declared_size < 0 or declared_size > self.max_download_bytes:
                    raise ValueError("Download exceeds maximum size")
            size = _write_bounded_stream(
                destination,
                response.iter_bytes(chunk_size=DOWNLOAD_CHUNK_BYTES),
                maximum_bytes=self.max_download_bytes,
            )
            return DownloadResult(
                destination, response.headers.get("content-type", ""), size,
                str(response.url),
            )
        finally:
            response.close()


class ManualImportProvider(AudioProvider):
    """Imports operator-supplied media only when a complete JSON sidecar exists."""

    name = "manual"

    def __init__(
        self, import_root: Path, *, max_download_bytes: int = MAX_DOWNLOAD_BYTES,
    ) -> None:
        self.import_root = import_root.resolve()
        self.max_download_bytes = max(1, min(max_download_bytes, MAX_DOWNLOAD_BYTES))

    def search(self, query: SearchQuery) -> list[ProviderAsset]:
        results: list[ProviderAsset] = []
        for sidecar in sorted(self.import_root.glob("*.json")):
            data = json.loads(sidecar.read_text(encoding="utf-8"))
            asset = ProviderAsset.model_validate(data)
            if query.assetType and asset.assetType != query.assetType:
                continue
            if query.categories and asset.category not in query.categories:
                continue
            haystack = " ".join([asset.filename, *asset.tags, *asset.mood]).lower()
            if query.text and query.text.lower() not in haystack:
                continue
            results.append(asset)
            if len(results) >= query.maxResults:
                break
        return results

    def download(self, asset: ProviderAsset, destination: Path) -> DownloadResult:
        source = (self.import_root / (asset.localSourcePath or asset.filename)).resolve()
        if self.import_root not in source.parents or not source.is_file():
            raise ValueError("Manual import path is missing or outside the approved import directory")
        if source.stat().st_size > self.max_download_bytes:
            raise ValueError("Download exceeds maximum size")
        with source.open("rb") as handle:
            size = _write_bounded_stream(
                destination,
                iter(lambda: handle.read(DOWNLOAD_CHUNK_BYTES), b""),
                maximum_bytes=self.max_download_bytes,
            )
        return DownloadResult(
            destination, "application/octet-stream", size, source.as_uri(),
        )
