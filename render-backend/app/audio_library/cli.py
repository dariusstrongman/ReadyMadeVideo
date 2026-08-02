from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from .ingestion import AudioIngestor
from .licenses import LicensePolicy
from .models import SearchQuery
from .providers import FreesoundProvider, ManualImportProvider
from .store import AudioLibraryPaths, ManifestStore


def _default_root() -> Path:
    return Path(__file__).resolve().parents[3] / "assets" / "audio"


def _bool_env(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes"}


def _query(args: argparse.Namespace) -> SearchQuery:
    return SearchQuery(
        text=args.text,
        assetType=args.type,
        categories=args.category or [],
        tags=args.tag or [],
        durationMinSeconds=args.duration_min,
        durationMaxSeconds=args.duration_max,
        licenses=args.license or [],
        providers=args.provider_filter or [],
        moods=args.mood or [],
        energyMin=args.energy_min,
        energyMax=args.energy_max,
        bpmMin=args.bpm_min,
        bpmMax=args.bpm_max,
        instrumental=args.instrumental,
        vocal=args.vocal,
        attributionRequired=args.attribution_required,
        maxResults=args.max_count,
    )


def _emit(value: Any, json_output: bool) -> None:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if json_output:
        print(json.dumps(value, indent=2, sort_keys=True))
        return
    if isinstance(value, list):
        for item in value:
            data = item.model_dump(mode="json") if hasattr(item, "model_dump") else item
            print(f"{data.get('assetId', '-')}  {data.get('assetType', '')}/{data.get('category', '')}  {data.get('filename', '')}")
    elif isinstance(value, dict):
        for key, item in value.items():
            if key != "assets":
                print(f"{key}: {item}")
    else:
        print(value)


def _add_filters(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("text", nargs="?", default="")
    parser.add_argument("--type", choices=["music", "sfx"])
    parser.add_argument("--category", action="append")
    parser.add_argument("--tag", action="append")
    parser.add_argument("--license", action="append")
    parser.add_argument("--provider-filter", action="append")
    parser.add_argument("--mood", action="append")
    parser.add_argument("--duration-min", type=float)
    parser.add_argument("--duration-max", type=float)
    parser.add_argument("--energy-min", type=float)
    parser.add_argument("--energy-max", type=float)
    parser.add_argument("--bpm-min", type=float)
    parser.add_argument("--bpm-max", type=float)
    parser.add_argument("--instrumental", action=argparse.BooleanOptionalAction)
    parser.add_argument("--vocal", action=argparse.BooleanOptionalAction)
    parser.add_argument("--attribution-required", action=argparse.BooleanOptionalAction)
    parser.add_argument("--max-count", type=int, default=10)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.audio_library")
    parser.add_argument("--root", type=Path, default=_default_root())
    parser.add_argument("--json", action="store_true", dest="json_output")
    commands = parser.add_subparsers(dest="command", required=True)
    search = commands.add_parser("search", help="Search accepted local manifests")
    _add_filters(search)

    ingest = commands.add_parser("ingest", help="Search and ingest from an approved provider")
    ingest.add_argument("--provider", choices=["freesound"], required=True)
    ingest.add_argument("--dry-run", action="store_true")
    ingest.add_argument("--resume", action="store_true")
    ingest.add_argument("--retries", type=int, default=3)
    ingest.add_argument("--rate-limit-seconds", type=float, default=0.25)
    _add_filters(ingest)

    manual = commands.add_parser("import-local", help="Import sidecar-described files from manual-import")
    manual.add_argument("--dry-run", action="store_true")
    manual.add_argument("--resume", action="store_true")
    _add_filters(manual)

    commands.add_parser("validate", help="Validate manifests and normalized paths")
    commands.add_parser("report", help="Regenerate deterministic aggregate and attribution reports")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    paths = AudioLibraryPaths(args.root.resolve())
    store = ManifestStore(paths)
    policy = LicensePolicy.from_file(paths.license_approvals)
    try:
        if args.command == "search":
            _emit(store.search(_query(args)), args.json_output)
            return 0
        if args.command == "validate":
            errors = store.validate()
            _emit({"valid": not errors, "errors": errors}, args.json_output)
            return 1 if errors else 0
        if args.command == "report":
            _emit(store.report(), args.json_output)
            return 0
        if args.command == "import-local":
            provider = ManualImportProvider(paths.manual_import)
        else:
            provider = FreesoundProvider(
                api_key=os.getenv("FREESOUND_API_KEY"),
                oauth_token=os.getenv("FREESOUND_OAUTH_TOKEN"),
                commercial_api_approved=_bool_env("FREESOUND_COMMERCIAL_API_APPROVED"),
                approval_reference=os.getenv("FREESOUND_COMMERCIAL_API_APPROVAL_REFERENCE"),
                terms_reviewed_at=os.getenv("FREESOUND_TERMS_REVIEWED_AT"),
                retries=args.retries,
                rate_limit_seconds=args.rate_limit_seconds,
            )
        assets = provider.search(_query(args))
        summary = AudioIngestor(store=store, policy=policy).ingest(
            provider,
            assets,
            dry_run=args.dry_run,
            max_count=args.max_count,
            resume=args.resume,
        )
        _emit(summary, args.json_output)
        return 1 if summary.rejectedCount else 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"audio-library error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
