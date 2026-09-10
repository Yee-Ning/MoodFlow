"""Batch CLI for producing the Cleaning-ready Normalized Corpus V1."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable, Sequence


AI_SERVICE_ROOT = Path(__file__).resolve().parent
if str(AI_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(AI_SERVICE_ROOT))

from app.rag.parse_normalize.corpus import CorpusParseReporter, CorpusParseRunner


RunnerFactory = Callable[[Path, Path], CorpusParseRunner]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Parse every validated Raw Snapshot and publish the Normalized "
            "Corpus V1 manifest and summary."
        )
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--all", action="store_true", help="Run the full batch.")
    mode.add_argument(
        "--report-only",
        action="store_true",
        help="Regenerate corpus reports without parsing sources.",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        help="Validated raw corpus ledger; defaults to data/raw/raw_corpus_manifest.jsonl.",
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--parser",
        choices=("auto", "pymupdf", "mineru", "docling"),
        default="auto",
        help="PDF backend mode. The default quality-gated cascade is auto.",
    )
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    project_root: Path = AI_SERVICE_ROOT,
    runner_factory: RunnerFactory = CorpusParseRunner,
) -> int:
    args = parse_args(argv)
    root = project_root.resolve()
    manifest = (
        args.manifest.resolve()
        if args.manifest
        else root / "data" / "raw" / "raw_corpus_manifest.jsonl"
    )
    if args.report_only:
        reporter = CorpusParseReporter(root, manifest)
        manifest_rows, report = reporter.build()
        reporter.write(manifest_rows, report)
        report["batch_outcome_counts"] = {}
    else:
        runner = runner_factory(root, manifest)
        _, _, report = runner.run_all(
            force=args.force, parser_mode=args.parser
        )
    payload = {
        key: report[key]
        for key in (
            "total_sources",
            "parse_success",
            "parsed_warning",
            "fallback_used",
            "manual_review",
            "failed",
            "average_quality_score",
            "raw_integrity",
            "batch_outcome_counts",
        )
    }
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not report["failed"] and report["raw_integrity"]["all_hashes_match"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
