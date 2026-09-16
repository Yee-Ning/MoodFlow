"""Create the frozen 30-case blind human-review pack without calling a Judge."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.rag.evaluation.gold_review import (
    audit_silver_provenance,
    blind_review_markdown,
    review_summary,
    select_blind_review_cases,
)
from app.rag.evaluation.io import load_evaluation_cases, load_evaluation_traces, write_jsonl


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--silver", type=Path, required=True)
    parser.add_argument("--pending", type=Path, required=True)
    parser.add_argument("--review-pack", type=Path, required=True)
    parser.add_argument("--baseline-traces", type=Path)
    parser.add_argument("--index-projection", type=Path)
    parser.add_argument("--audit-output", type=Path)
    args = parser.parse_args()
    paths = [args.silver, args.pending, args.review_pack]
    if args.audit_output:
        paths.append(args.audit_output)
    if len({path.resolve() for path in paths}) != len(paths):
        parser.error("input and output paths must be distinct")
    if any((args.baseline_traces, args.index_projection, args.audit_output)) and not all(
        (args.baseline_traces, args.index_projection, args.audit_output)
    ):
        parser.error("audit requires baseline traces, index projection and output together")

    silver = load_evaluation_cases(args.silver)
    cases = select_blind_review_cases(silver)
    summary = review_summary(cases)
    if args.baseline_traces and args.index_projection and args.audit_output:
        summary["silver_audit"] = audit_silver_provenance(
            silver,
            load_evaluation_traces(args.baseline_traces),
            args.index_projection,
        )
        args.audit_output.parent.mkdir(parents=True, exist_ok=True)
        args.audit_output.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    write_jsonl(args.pending, cases)
    args.review_pack.parent.mkdir(parents=True, exist_ok=True)
    args.review_pack.write_text(blind_review_markdown(cases), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
