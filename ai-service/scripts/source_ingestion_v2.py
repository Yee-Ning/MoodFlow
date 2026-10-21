"""Automatic Music RAG source discovery-pool ingestion and corpus reporting."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Sequence


AI_SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(AI_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(AI_SERVICE_ROOT))

from app.rag.source_ingestion.attempts import (
    SourceAttemptLedger,
    backfill_v1_failures,
)
from app.rag.source_ingestion.auto_service import AutoSourceIngestionService
from app.rag.source_ingestion.discovery import load_discovered_candidates
from app.rag.source_ingestion.http_client import (
    DEFAULT_USER_AGENT,
    PolicyAwareHttpClient,
)
from app.rag.source_ingestion.manifest import load_candidates
from app.rag.source_ingestion.quality_filter import AutoSourceQualityFilter
from app.rag.source_ingestion.report import SourceCorpusReporter
from app.rag.source_ingestion.retry import SourceRetryConfig
from app.rag.source_ingestion.snapshot import RawSnapshotStore
from app.rag.source_ingestion.validation import SnapshotValidator


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Filter, deduplicate, fetch, validate, and report Music RAG raw "
            "sources without invoking Parse + Normalize."
        )
    )
    parser.add_argument("command", choices=("run", "validate", "report"))
    parser.add_argument("--project-root", type=Path, default=AI_SERVICE_ROOT)
    parser.add_argument("--candidate-manifest", type=Path)
    parser.add_argument("--discovery-manifest", type=Path)
    parser.add_argument("--failure-manifest", type=Path)
    parser.add_argument("--target-count", type=int, default=62)
    parser.add_argument("--max-replacement-attempts", type=int, default=3)
    parser.add_argument("--auto-retry", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--max-source-attempts", type=int, default=3)
    parser.add_argument(
        "--reopen-failed", action="store_true",
        help="Retry failed candidates and reopen exhausted groups with a fresh per-run attempt cap.",
    )
    parser.add_argument(
        "--user-agent",
        default=os.getenv("MOODFLOW_INGESTION_USER_AGENT", DEFAULT_USER_AGENT),
    )
    parser.add_argument("--min-interval", type=float, default=1.1)
    parser.add_argument("--max-response-mb", type=int, default=50)
    return parser.parse_args(argv)


def _paths(args: argparse.Namespace) -> tuple[Path, Path, Path]:
    root = args.project_root.resolve()
    corpus_directory = root / "data/source_ingestion/jay_chou_corpus_v1"
    return (
        args.candidate_manifest
        or corpus_directory / "sources_candidates.json",
        args.discovery_manifest
        or corpus_directory / "discovered_candidates_v2.json",
        args.failure_manifest or corpus_directory / "failed_sources_v1.json",
    )


def _load_failures(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("failure manifest root must be a JSON array")
    return [dict(item) for item in payload]


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.target_count <= 0:
        raise SystemExit("--target-count must be positive")
    if args.max_replacement_attempts <= 0:
        raise SystemExit("--max-replacement-attempts must be positive")
    if args.max_source_attempts <= 0:
        raise SystemExit("--max-source-attempts must be positive")
    if args.max_response_mb <= 0:
        raise SystemExit("--max-response-mb must be positive")

    root = args.project_root.resolve()
    candidate_path, discovery_path, failure_path = _paths(args)
    existing_candidates = load_candidates(candidate_path)
    if args.command == "run" or discovery_path.exists():
        discovered_candidates = load_discovered_candidates(discovery_path)
    else:
        discovered_candidates = []
    attempt_ledger = SourceAttemptLedger(root / "data/raw/source_attempts.jsonl")
    backfill_v1_failures(
        existing_candidates, _load_failures(failure_path), attempt_ledger
    )

    run_summary = None
    if args.command == "run":
        with PolicyAwareHttpClient(
            user_agent=args.user_agent,
            min_interval_seconds=args.min_interval,
            max_response_bytes=args.max_response_mb * 1024 * 1024,
        ) as client:
            service = AutoSourceIngestionService(
                snapshot_store=RawSnapshotStore(root),
                http_client=client,
                validator=SnapshotValidator(),
                quality_filter=AutoSourceQualityFilter(),
                attempt_ledger=attempt_ledger,
                existing_candidates=existing_candidates,
                retry_config=SourceRetryConfig(
                    enabled=args.auto_retry, max_source_attempts=args.max_source_attempts,
                ),
            )
            run_summary = service.run(
                discovered_candidates,
                target_count=args.target_count,
                max_replacement_attempts=args.max_replacement_attempts,
                reopen_failed=args.reopen_failed,
            )

    report = SourceCorpusReporter(root).write_outputs(
        existing_candidates=existing_candidates,
        discovered_candidates=discovered_candidates,
        attempts=attempt_ledger.load(),
    )
    payload: dict[str, object] = {"report": report}
    if run_summary is not None:
        payload["run_summary"] = run_summary.model_dump(mode="json")
    if args.command in {"validate", "report"}:
        payload = report
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if report["ready_for_parse_normalize"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
