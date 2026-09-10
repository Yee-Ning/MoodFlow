"""CLI for Music RAG Parse + Normalize V1."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Sequence


AI_SERVICE_ROOT = Path(__file__).resolve().parent
if str(AI_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(AI_SERVICE_ROOT))

from app.rag.parse_normalize.service import (
    OutcomeStatus,
    ParseOutcome,
    create_default_service,
)


SUCCESS_STATUSES = {
    OutcomeStatus.PARSED,
    OutcomeStatus.WARNING,
    OutcomeStatus.FALLBACK_USED,
    OutcomeStatus.SKIPPED,
}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Parse immutable HTML, Markdown, TXT, JSON, and PDF snapshots into "
            "NormalizedDocument artifacts."
        )
    )
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--source-id", help="Parse one source from the raw ledger.")
    selection.add_argument(
        "--all-approved",
        action="store_true",
        help="Parse every approved candidate that has a raw snapshot.",
    )
    selection.add_argument(
        "--manifest",
        type=Path,
        help="Parse the latest record for every source in this raw JSONL ledger.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignore a matching raw/parser/normalization fingerprint.",
    )
    parser.add_argument(
        "--parser",
        choices=("auto", "pymupdf", "mineru", "docling"),
        default="auto",
        help="PDF backend mode; auto enables the quality-gated cascade.",
    )
    return parser.parse_args(argv)


def _render(outcomes: list[ParseOutcome]) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    counts = Counter(outcome.status.value for outcome in outcomes)
    payload = {
        "counts": dict(sorted(counts.items())),
        "results": [outcome.model_dump(mode="json") for outcome in outcomes],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main(
    argv: Sequence[str] | None = None,
    *,
    project_root: Path = AI_SERVICE_ROOT,
    raw_manifest_path: Path | None = None,
) -> int:
    args = parse_args(argv)
    service = create_default_service(
        project_root,
        raw_manifest_path=raw_manifest_path,
    )
    if args.source_id:
        outcomes = [
            service.parse_source(
                args.source_id,
                force=args.force,
                parser_mode=args.parser,
            )
        ]
    elif args.all_approved:
        outcomes = service.parse_all_approved(
            force=args.force, parser_mode=args.parser
        )
    else:
        outcomes = service.parse_manifest(
            args.manifest, force=args.force, parser_mode=args.parser
        )
    _render(outcomes)
    return 0 if outcomes and all(
        outcome.status in SUCCESS_STATUSES for outcome in outcomes
    ) else 2


if __name__ == "__main__":
    raise SystemExit(main())
