"""Finalize owner-approved Gold review against the frozen Parent release."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.rag.evaluation.gold_finalize import finalize_review
from app.rag.evaluation.io import write_jsonl


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--pending-gold", type=Path, required=True)
    parser.add_argument("--candidates-jsonl", type=Path, required=True)
    parser.add_argument("--release-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    inputs = {
        args.review.resolve(), args.pending_gold.resolve(),
        args.candidates_jsonl.resolve(), args.release_manifest.resolve(),
    }
    if args.output.resolve() in inputs:
        parser.error("output must differ from every input")
    cases, summary = finalize_review(
        args.review, args.pending_gold, args.candidates_jsonl, args.release_manifest,
    )
    write_jsonl(args.output, cases)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
