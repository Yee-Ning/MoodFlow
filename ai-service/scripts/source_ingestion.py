"""Operator CLI for Music RAG Source Ingestion V1."""

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

AI_SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(AI_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(AI_SERVICE_ROOT))

from app.rag.source_ingestion.http_client import (
    DEFAULT_USER_AGENT,
    PolicyAwareHttpClient,
)
from app.rag.source_ingestion.manifest import (
    load_candidates,
    update_review_status,
)
from app.rag.source_ingestion.models import ReviewStatus
from app.rag.source_ingestion.service import SourceIngestionService
from app.rag.source_ingestion.snapshot import RawSnapshotStore


DEFAULT_MANIFEST = (
    AI_SERVICE_ROOT
    / "data"
    / "source_ingestion"
    / "jay_chou_corpus_v1"
    / "sources_candidates.json"
)


def _add_manifest_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Review approved sources and create immutable raw snapshots."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate", help="Validate the manifest.")
    _add_manifest_argument(validate_parser)

    list_parser = subparsers.add_parser("list", help="List candidates as JSON.")
    _add_manifest_argument(list_parser)
    list_parser.add_argument("--status", choices=[status.value for status in ReviewStatus])

    review_parser = subparsers.add_parser(
        "review", help="Set one source to approved, rejected, or pending."
    )
    _add_manifest_argument(review_parser)
    review_parser.add_argument("source_id")
    review_parser.add_argument("status", choices=[status.value for status in ReviewStatus])

    fetch_parser = subparsers.add_parser(
        "fetch", help="Fetch approved sources and append raw snapshot records."
    )
    _add_manifest_argument(fetch_parser)
    selection = fetch_parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--source-id")
    selection.add_argument("--all-approved", action="store_true")
    fetch_parser.add_argument(
        "--user-agent",
        default=os.getenv("MOODFLOW_INGESTION_USER_AGENT", DEFAULT_USER_AGENT),
        help="Identify this client; set operator contact details for public APIs.",
    )
    fetch_parser.add_argument("--min-interval", type=float, default=1.1)
    fetch_parser.add_argument("--max-response-mb", type=int, default=50)
    return parser.parse_args()


def _render(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def main() -> None:
    args = parse_args()
    if args.command == "validate":
        candidates = load_candidates(args.manifest)
        _render(
            {
                "valid": True,
                "candidate_count": len(candidates),
                "status_counts": dict(Counter(item.status.value for item in candidates)),
                "tier_counts": dict(Counter(str(item.source_tier) for item in candidates)),
                "file_type_counts": dict(Counter(item.file_type.value for item in candidates)),
            }
        )
        return

    if args.command == "list":
        candidates = load_candidates(args.manifest)
        if args.status is not None:
            candidates = [item for item in candidates if item.status.value == args.status]
        _render([item.model_dump(mode="json") for item in candidates])
        return

    if args.command == "review":
        candidate = update_review_status(
            args.manifest,
            args.source_id,
            ReviewStatus(args.status),
        )
        _render(candidate.model_dump(mode="json"))
        return

    if args.max_response_mb <= 0:
        raise SystemExit("--max-response-mb must be positive")
    with PolicyAwareHttpClient(
        user_agent=args.user_agent,
        min_interval_seconds=args.min_interval,
        max_response_bytes=args.max_response_mb * 1024 * 1024,
    ) as http_client:
        service = SourceIngestionService(
            args.manifest,
            RawSnapshotStore(AI_SERVICE_ROOT),
            http_client,
        )
        records = (
            service.fetch_all_approved()
            if args.all_approved
            else [service.fetch_one(args.source_id)]
        )
    _render([record.model_dump(mode="json") for record in records])


if __name__ == "__main__":
    main()
