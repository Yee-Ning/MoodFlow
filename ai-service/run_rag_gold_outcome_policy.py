"""Materialize a policy-only Gold revision without modifying the approved V0."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from app.rag.evaluation.gold_outcome_policy import apply_needs_input_outcome_policy
from app.rag.evaluation.io import load_evaluation_cases, write_jsonl


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.baseline.resolve() == args.output.resolve():
        parser.error("output must differ from approved V0")
    revised = apply_needs_input_outcome_policy(load_evaluation_cases(args.baseline))
    if args.output.exists():
        if load_evaluation_cases(args.output) != revised:
            parser.error("output exists with different contents")
    else:
        write_jsonl(args.output, revised)
    print(json.dumps({
        "status": "success",
        "case_count": len(revised),
        "outcomes": dict(Counter(case.expected_outcome.value for case in revised)),
        "output": str(args.output),
    }, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
