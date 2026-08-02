from __future__ import annotations

import json
import subprocess
from pathlib import Path

import httpx
import pytest

from app.audio_library.cli import main as cli_main
from app.audio_library.ingestion import AudioIngestor
from app.audio_library.integration import AudioLibraryAdapter
from app.audio_library.licenses import LicensePolicy, ProprietaryLicenseApproval
from app.audio_library.media import (
    MAX_AUDIO_BYTES,
    normalize_audio,
    probe_audio,
    sha256_file,
    validate_container,
)
from app.audio_library.models import ProviderAsset, ProviderTermsEvidence, SearchQuery
from app.audio_library.providers import DownloadResult, FreesoundProvider, ManualImportProvider
from app.audio_library.store import AudioLibraryPaths, ManifestStore


FIXED_TIME = "2026-08-02T12:00:00Z"


def terms(**overrides):
    data = {
        "termsUrl": "https://provider.example/terms",
        "reviewedAt": "2026-08-02",
        "ingestionMethodAllowed": True,
        "commercialApiUseAllowed": True,
        "approvalReference": "legal-review-1",
    }
    data.update(overrides)
    return ProviderTermsEvidence(**data)


def asset(asset_id: str = "one", **overrides):
    data = {
        "assetType": "sfx",
        "category": "impacts",
        "sourceProvider": "manual",
        "sourceUrl": f"https://provider.example/assets/{asset_id}",
        "sourceAssetId": asset_id,
        "filename": f"{asset_id}.wav",
        "creatorName": "Creator",
        "creatorUrl": "https://provider.example/creator",
        "licenseName": "CC0 1.0",
        "licenseUrl": "https://creativecommons.org/publicdomain/zero/1.0/",
        "attributionText": None,
        "declaredCommercialUseAllowed": True,
        "declaredModificationAllowed": True,
        "tags": ["Impact", " impact "],
        "mood": ["Energetic"],
        "energy": 0.8,
        "instrumental": True,
        "vocal": False,
        "providerTerms": terms(),
    }
    data.update(overrides)
    return ProviderAsset(**data)


def make_tone(path: Path, *, codec: str | None = None, frequency: int = 440) -> None:
    command = [
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i",
        f"sine=frequency={frequency}:duration=0.25", "-ar", "48000", "-ac", "1",
    ]
    if codec:
        command.extend(["-c:a", codec])
    command.extend(["-y", str(path)])
    subprocess.run(command, check=True, shell=False)


class FileProvider:
    name = "manual"

    def __init__(self, sources: dict[str, Path], content_type: str = "audio/wav"):
        self.sources = sources
        self.content_type = content_type
        self.downloads = 0

    def search(self, query):
        return []

    def download(self, item, destination):
        self.downloads += 1
        destination.write_bytes(self.sources[item.sourceAssetId].read_bytes())
        return DownloadResult(destination, self.content_type, destination.stat().st_size, str(item.sourceUrl))


@pytest.fixture
def store(tmp_path):
    return ManifestStore(AudioLibraryPaths(tmp_path / "audio"))


def test_accepts_cc0():
    decision = LicensePolicy().evaluate(asset())
    assert decision.accepted is True
    assert decision.reasonCode == "accepted_cc0"
    assert decision.attributionRequired is False


def test_accepts_cc_by_with_complete_attribution():
    item = asset(
        licenseName="CC BY 4.0",
        licenseUrl="http://creativecommons.org/licenses/by/4.0/",
        attributionText='"Sound" by Creator (CC BY 4.0)',
    )
    decision = LicensePolicy().evaluate(item)
    assert decision.accepted is True
    assert decision.attributionRequired is True


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"licenseName": "CC BY-NC 4.0", "licenseUrl": "https://creativecommons.org/licenses/by-nc/4.0/"}, "noncommercial_license"),
        ({"licenseName": "CC BY-ND 4.0", "licenseUrl": "https://creativecommons.org/licenses/by-nd/4.0/"}, "no_derivatives_license"),
        ({"licenseName": "Unknown", "licenseUrl": "https://provider.example/license"}, "unknown_license"),
        ({"licenseUrl": None}, "missing_license_metadata"),
        ({"creatorName": None}, "missing_creator_metadata"),
    ],
)
def test_rejects_ineligible_license_metadata(overrides, reason):
    assert LicensePolicy().evaluate(asset(**overrides)).reasonCode == reason


def test_rejects_cc_by_missing_attribution():
    item = asset(licenseName="CC BY", licenseUrl="https://creativecommons.org/licenses/by/4.0/", attributionText=None)
    assert LicensePolicy().evaluate(item).reasonCode == "missing_attribution_metadata"


def test_approved_proprietary_license():
    approval = ProprietaryLicenseApproval(
        licenseId="paid-1", licenseName="Paid RF", licenseUrl="https://paid.example/license",
        commercialUseAllowed=True, modificationAllowed=True, attributionRequired=False,
        approvalReference="contract-9", reviewedAt="2026-08-02",
    )
    decision = LicensePolicy([approval]).evaluate(asset(licenseName="Paid RF", licenseUrl="https://paid.example/license"))
    assert decision.accepted is True
    assert "contract-9" in decision.reason


def test_provider_terms_rejection_happens_before_download(store, tmp_path):
    source = tmp_path / "tone.wav"
    make_tone(source)
    provider = FileProvider({"one": source})
    item = asset(providerTerms=terms(ingestionMethodAllowed=False))
    summary = AudioIngestor(store=store, policy=LicensePolicy(), clock=lambda: FIXED_TIME).ingest(provider, [item])
    assert summary.rejectedCount == 1
    assert provider.downloads == 0


def test_dry_run_does_not_download_or_write_runtime_reports(store, tmp_path):
    source = tmp_path / "tone.wav"
    make_tone(source)
    provider = FileProvider({"one": source})
    summary = AudioIngestor(store=store, policy=LicensePolicy(), clock=lambda: FIXED_TIME).ingest(
        provider, [asset()], dry_run=True,
    )
    assert summary.items[0].status == "dry_run"
    assert provider.downloads == 0
    assert not (store.paths.root / "ingestion-summary.json").exists()
    assert not (store.paths.root / "ingestion-state.json").exists()


def test_preexisting_normalized_file_is_not_removed_on_overwrite_refusal(store, tmp_path):
    source = tmp_path / "tone.wav"
    make_tone(source)
    digest = sha256_file(source)
    existing = store.paths.normalized_dir("sfx", "impacts") / f"one-{digest[:12]}.wav"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"operator-owned")
    summary = AudioIngestor(store=store, policy=LicensePolicy(), clock=lambda: FIXED_TIME).ingest(
        FileProvider({"one": source}), [asset()]
    )
    assert summary.rejectedCount == 1
    assert existing.read_bytes() == b"operator-owned"


def test_real_ffmpeg_ingestion_and_normalization(store, tmp_path):
    source = tmp_path / "tone.wav"
    make_tone(source)
    summary = AudioIngestor(store=store, policy=LicensePolicy(), clock=lambda: FIXED_TIME).ingest(
        FileProvider({"one": source}), [asset()]
    )
    manifest = store.load_manifests()[0]
    normalized = store.paths.root / manifest.normalizedPath
    assert summary.acceptedCount == 1
    assert normalized != source
    assert source.exists()
    assert probe_audio(normalized).sampleRate == 48000
    assert manifest.transformation.sourceOverwritten is False
    assert manifest.transformation.commands[0][0] == "ffmpeg"
    assert manifest.licenseApprovalReference == "policy:cc0-1.0"
    assert manifest.originalFilename == "one.wav"
    assert manifest.sourceSampleRate == 48000


def test_source_identity_duplicate_does_not_download_again(store, tmp_path):
    source = tmp_path / "tone.wav"
    make_tone(source)
    ingestor = AudioIngestor(store=store, policy=LicensePolicy(), clock=lambda: FIXED_TIME)
    ingestor.ingest(FileProvider({"one": source}), [asset()])
    provider = FileProvider({"one": source})
    summary = ingestor.ingest(provider, [asset()])
    assert summary.duplicateCount == 1
    assert provider.downloads == 0


def test_sha_duplicate_is_not_normalized_twice(store, tmp_path):
    source = tmp_path / "tone.wav"
    make_tone(source)
    provider = FileProvider({"one": source, "two": source})
    summary = AudioIngestor(store=store, policy=LicensePolicy(), clock=lambda: FIXED_TIME).ingest(
        provider, [asset("one"), asset("two")]
    )
    assert summary.acceptedCount == 1
    assert summary.duplicateCount == 1
    assert len(store.load_manifests()) == 1


def test_pcm_fingerprint_duplicate_across_formats(store, tmp_path):
    wav = tmp_path / "tone.wav"
    flac = tmp_path / "tone.flac"
    make_tone(wav)
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(wav), "-c:a", "flac", "-y", str(flac)], check=True)
    provider = FileProvider({"one": wav, "two": flac})
    summary = AudioIngestor(store=store, policy=LicensePolicy(), clock=lambda: FIXED_TIME).ingest(
        provider, [asset("one"), asset("two", filename="two.flac")]
    )
    assert summary.duplicateCount == 1
    assert json.loads((store.paths.root / "duplicate-report.json").read_text())[0]["duplicateType"] == "pcm_fingerprint"


def test_invalid_mime_is_rejected(store, tmp_path):
    source = tmp_path / "tone.wav"
    make_tone(source)
    summary = AudioIngestor(store=store, policy=LicensePolicy(), clock=lambda: FIXED_TIME).ingest(
        FileProvider({"one": source}, "text/html"), [asset()]
    )
    assert summary.rejectedCount == 1
    assert not store.load_manifests()
    assert not list(store.paths.temp.iterdir())


def test_oversized_file_rejected_without_reading(tmp_path):
    path = tmp_path / "huge.wav"
    with path.open("wb") as handle:
        handle.truncate(MAX_AUDIO_BYTES + 1)
    with pytest.raises(ValueError, match="maximum size"):
        validate_container(path, "audio/wav")


def test_invalid_stream_layout_is_rejected(tmp_path):
    path = tmp_path / "video.mp4"
    subprocess.run([
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=size=16x16:duration=0.1",
        "-f", "lavfi", "-i", "sine=duration=0.1", "-shortest", "-y", str(path),
    ], check=True)
    with pytest.raises(ValueError, match="exactly one audio stream"):
        probe_audio(path)


def test_manual_import_sidecar_and_path_containment(tmp_path):
    root = tmp_path / "manual"
    root.mkdir()
    source = root / "tone.wav"
    make_tone(source)
    item = asset(localSourcePath="tone.wav")
    (root / "tone.json").write_text(item.model_dump_json(), encoding="utf-8")
    provider = ManualImportProvider(root)
    assert provider.search(SearchQuery(maxResults=10))[0].sourceAssetId == "one"
    with pytest.raises(ValueError, match="outside"):
        provider.download(asset(localSourcePath="../escape.wav"), tmp_path / "copy.wav")


def test_manual_import_accepts_eligible_and_rejects_noncommercial(tmp_path):
    paths = AudioLibraryPaths(tmp_path / "audio")
    store = ManifestStore(paths)
    paths.manual_import.mkdir(parents=True, exist_ok=True)
    make_tone(paths.manual_import / "eligible.wav")
    make_tone(paths.manual_import / "blocked.wav", frequency=880)
    eligible = asset("eligible", filename="eligible.wav", localSourcePath="eligible.wav")
    blocked = asset(
        "blocked", filename="blocked.wav", localSourcePath="blocked.wav",
        licenseName="CC BY-NC 4.0",
        licenseUrl="https://creativecommons.org/licenses/by-nc/4.0/",
    )
    (paths.manual_import / "eligible.json").write_text(eligible.model_dump_json(), encoding="utf-8")
    (paths.manual_import / "blocked.json").write_text(blocked.model_dump_json(), encoding="utf-8")
    provider = ManualImportProvider(paths.manual_import)
    summary = AudioIngestor(store=store, policy=LicensePolicy(), clock=lambda: FIXED_TIME).ingest(
        provider, provider.search(SearchQuery(maxResults=10)), max_count=10,
    )
    assert summary.acceptedCount == 1
    assert summary.rejectedCount == 1
    assert len(store.load_manifests()) == 1
    rejected = json.loads((paths.root / "rejected-assets.json").read_text())[0]
    assert rejected["copiedToEligibleLibrary"] is False
    assert rejected["downloadAttempted"] is False
    assert rejected["licenseUrl"].endswith("/by-nc/4.0/")


def test_freesound_requires_credentials_and_commercial_approval():
    provider = FreesoundProvider(
        api_key=None, oauth_token=None, commercial_api_approved=False,
        approval_reference=None, terms_reviewed_at="2026-08-02", rate_limit_seconds=0,
    )
    with pytest.raises(RuntimeError, match="API_KEY"):
        provider.search(SearchQuery())
    with pytest.raises(RuntimeError, match="OAUTH_TOKEN"):
        provider.download(asset(sourceProvider="freesound"), Path("unused"))


def test_freesound_rejects_non_numeric_asset_identity_before_request(tmp_path):
    provider = FreesoundProvider(
        api_key="test-api-key", oauth_token="test-oauth-token", commercial_api_approved=True,  # noqa: S106
        approval_reference="contract", terms_reviewed_at="2026-08-02", rate_limit_seconds=0,
    )
    with pytest.raises(ValueError, match="asset identity"):
        provider.download(
            asset(sourceProvider="freesound", sourceAssetId="../account"),
            tmp_path / "unused.wav",
        )


def test_freesound_blocks_unsafe_redirect():
    def handler(request):
        return httpx.Response(302, headers={"location": "https://evil.example/file.wav"}, request=request)

    provider = FreesoundProvider(
        api_key="test-api-key", oauth_token="test-oauth-token", commercial_api_approved=True,  # noqa: S106
        approval_reference="contract", terms_reviewed_at="2026-08-02",
        client=httpx.Client(transport=httpx.MockTransport(handler)), rate_limit_seconds=0,
    )
    with pytest.raises(RuntimeError, match="Unsafe redirect"):
        provider._request("GET", "https://freesound.org/apiv2/sounds/1/download/")


def test_freesound_retries_429():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429 if len(calls) == 1 else 200, json={"results": []}, request=request)

    provider = FreesoundProvider(
        api_key="test-api-key", oauth_token="test-oauth-token", commercial_api_approved=True,  # noqa: S106
        approval_reference="contract", terms_reviewed_at="2026-08-02",
        client=httpx.Client(transport=httpx.MockTransport(handler)), retries=1,
        rate_limit_seconds=0, sleep=lambda _: None,
    )
    assert provider.search(SearchQuery()) == []
    assert len(calls) == 2
    assert calls[0].headers["authorization"] == "Token test-api-key"
    assert "test-api-key" not in str(calls[0].url)


def test_freesound_retries_transport_error():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("temporary", request=request)
        return httpx.Response(200, json={"results": []}, request=request)

    provider = FreesoundProvider(
        api_key="test-api-key", oauth_token="test-oauth-token", commercial_api_approved=True,  # noqa: S106
        approval_reference="contract", terms_reviewed_at="2026-08-02",
        client=httpx.Client(transport=httpx.MockTransport(handler)), retries=1,
        rate_limit_seconds=0, sleep=lambda _: None,
    )
    assert provider.search(SearchQuery()) == []
    assert calls == 2


def test_resume_skips_completed_identity(store, tmp_path):
    source = tmp_path / "tone.wav"
    make_tone(source)
    ingestor = AudioIngestor(store=store, policy=LicensePolicy(), clock=lambda: FIXED_TIME)
    ingestor.ingest(FileProvider({"one": source}), [asset(providerTerms=terms(ingestionMethodAllowed=False))])
    provider = FileProvider({"one": source})
    summary = ingestor.ingest(provider, [asset()], resume=True)
    assert summary.resumedCount == 1
    assert provider.downloads == 0


def test_reports_are_deterministic(store, tmp_path):
    source = tmp_path / "tone.wav"
    make_tone(source)
    AudioIngestor(store=store, policy=LicensePolicy(), clock=lambda: FIXED_TIME).ingest(
        FileProvider({"one": source}), [asset()]
    )
    first = (store.paths.root / "audio-library.json").read_bytes()
    store.report()
    assert (store.paths.root / "audio-library.json").read_bytes() == first
    assert store.validate() == []


def test_milestone_three_adapter_returns_license_provenance(store, tmp_path):
    source = tmp_path / "music.wav"
    make_tone(source)
    music = asset(
        assetType="music", category="energetic", durationSeconds=0.2,
        mood=["energetic"], bpm=120, filename="music.wav",
    )
    AudioIngestor(store=store, policy=LicensePolicy(), clock=lambda: FIXED_TIME).ingest(
        FileProvider({"one": source}), [music]
    )
    results = AudioLibraryAdapter(store).search_for_music_plan({
        "pictureDurationSeconds": 0.2,
        "trackBrief": {"tone": ["energetic"], "tempoBpm": 120, "energyArc": [{"energy": 0.8}]},
    })
    assert results[0]["assetId"].startswith("aud_")
    assert results[0]["licenseUrl"].endswith("/zero/1.0/")
    assert len(results[0]["sha256"]) == 64


def test_normalizer_refuses_overwrite(tmp_path):
    source = tmp_path / "tone.wav"
    output = tmp_path / "normalized.wav"
    make_tone(source)
    output.write_bytes(b"owned")
    with pytest.raises(FileExistsError):
        normalize_audio(source, output, "sfx")


def test_cli_search_validate_report_and_no_credentials(tmp_path, capsys, monkeypatch):
    root = tmp_path / "library"
    assert cli_main(["--root", str(root), "--json", "search", "impact"]) == 0
    assert json.loads(capsys.readouterr().out) == []
    assert cli_main(["--root", str(root), "--json", "validate"]) == 0
    assert json.loads(capsys.readouterr().out)["valid"] is True
    assert cli_main(["--root", str(root), "--json", "report"]) == 0
    assert json.loads(capsys.readouterr().out)["assetCount"] == 0
    monkeypatch.delenv("FREESOUND_API_KEY", raising=False)
    assert cli_main([
        "--root", str(root), "ingest", "--provider", "freesound",
        "--type", "sfx", "--category", "impacts", "--dry-run",
    ]) == 2
    error = capsys.readouterr().err
    assert "FREESOUND_API_KEY is required" in error
    assert "OAUTH" not in error
