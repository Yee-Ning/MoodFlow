"""Materialize Gold V3 after immutable-source review; no model calls."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.rag.evaluation.gold_evidence import load_frozen_parents
from app.rag.evaluation.gold_v3_resolution import resolve_gold_v3
from app.rag.evaluation.io import load_evaluation_cases, write_jsonl
from app.rag.evaluation.models import RAGEvaluationCase


def write_frozen_v3(path: Path, cases: list[RAGEvaluationCase]) -> str:
    """Create the version once; reject attempts to rewrite a different Gold V3."""
    if path.exists():
        if load_evaluation_cases(path) != cases:
            raise ValueError(f"Gold V3 is already frozen with different content: {path}")
        return "verified_identical"
    write_jsonl(path, cases)
    return "created"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--release-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.baseline.resolve() == args.output.resolve():
        parser.error("Gold V3 output must not overwrite the baseline")
    baseline = load_evaluation_cases(args.baseline)
    candidates = [
        json.loads(line) for line in args.candidates.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    release_id, source_sha256, parents = load_frozen_parents(args.release_manifest)
    revised = resolve_gold_v3(baseline, candidates, parents)
    write_status = write_frozen_v3(args.output, revised)
    print(json.dumps({
        "status": "success",
        "write_status": write_status,
        "baseline_case_count": len(baseline),
        "changed_case_count": sum(a != b for a, b in zip(baseline, revised, strict=True)),
        "release_id": release_id,
        "source_sha256": source_sha256,
        "output": str(args.output),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
