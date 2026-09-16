"""Build an evidence-bearing Gold review pack from the frozen Parent release."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.rag.evaluation.gold_evidence import (
    build_source_candidates,
    candidate_records,
    load_frozen_parents,
    render_source_review_pack,
)
from app.rag.evaluation.io import load_evaluation_cases


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pending-gold", type=Path, required=True)
    parser.add_argument("--release-manifest", type=Path, required=True)
    parser.add_argument("--review-pack", type=Path, required=True)
    parser.add_argument("--candidates-jsonl", type=Path, required=True)
    args = parser.parse_args()
    inputs = {args.pending_gold.resolve(), args.release_manifest.resolve()}
    outputs = {args.review_pack.resolve(), args.candidates_jsonl.resolve()}
    if len(outputs) != 2 or inputs & outputs:
        parser.error("review outputs must be distinct from each other and from inputs")

    cases = load_evaluation_cases(args.pending_gold)
    release_id, source_sha256, parents = load_frozen_parents(args.release_manifest)
    candidates = build_source_candidates(cases, parents)
    markdown = render_source_review_pack(
        cases, candidates, release_id=release_id, source_sha256=source_sha256,
    )
    records = candidate_records(
        cases, candidates, release_id=release_id, source_sha256=source_sha256,
    )
    args.review_pack.parent.mkdir(parents=True, exist_ok=True)
    args.candidates_jsonl.parent.mkdir(parents=True, exist_ok=True)
    args.review_pack.write_text(markdown, encoding="utf-8")
    args.candidates_jsonl.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in records) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "status": "review_pending",
        "case_count": len(cases),
        "parent_count": len(parents),
        "candidates_per_case": 5,
        "release_id": release_id,
        "source_sha256": source_sha256,
        "gold_finalized": False,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
