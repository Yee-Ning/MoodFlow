"""Build an AI-assisted source-audited Gold amendment from frozen Parents."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.rag.evaluation.gold_source_adjudication import adjudicate_source_files
from app.rag.evaluation.io import load_evaluation_cases, write_jsonl


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--v0", type=Path, required=True)
    parser.add_argument("--v1", type=Path, required=True)
    parser.add_argument("--proposals", type=Path, required=True)
    parser.add_argument("--decisions", type=Path, required=True)
    parser.add_argument("--release-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    inputs = {
        args.v0.resolve(), args.v1.resolve(), args.proposals.resolve(),
        args.decisions.resolve(), args.release_manifest.resolve(),
    }
    if args.output.resolve() in inputs:
        parser.error("output must differ from every input")
    cases, summary = adjudicate_source_files(
        args.v0, args.v1, args.proposals, args.decisions, args.release_manifest,
    )
    if args.output.exists():
        if load_evaluation_cases(args.output) != cases:
            parser.error("output exists with different contents")
    else:
        write_jsonl(args.output, cases)
    print(json.dumps({**summary, "output": str(args.output)}, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
