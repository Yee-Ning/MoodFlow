"""Manage and run the framework-neutral MoodFlow RAG evaluation harness."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Sequence

from app.rag.evaluation import (
    AnnotationStatus,
    build_deepeval_payloads,
    build_annotation_review_markdown,
    build_pending_dataset,
    bootstrap_silver_dataset,
    evaluate_traces,
    load_evaluation_cases,
    load_evaluation_config,
    load_evaluation_traces,
    write_jsonl,
)

PROJECT_ROOT = Path(__file__).resolve().parent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MoodFlow offline RAG evaluation harness")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_dataset = subparsers.add_parser(
        "validate-dataset",
        help="Validate a versioned evaluation JSONL dataset",
    )
    validate_dataset.add_argument("--dataset", type=Path, required=True)

    generate = subparsers.add_parser(
        "generate-skeleton",
        help="Generate pending cases from the existing query fixture",
    )
    generate.add_argument("--source", type=Path, required=True)
    generate.add_argument("--output", type=Path, required=True)
    generate.add_argument("--limit", type=int, default=50)

    validate_traces = subparsers.add_parser(
        "validate-traces",
        help="Validate canonical evaluation traces",
    )
    validate_traces.add_argument("--traces", type=Path, required=True)

    bootstrap = subparsers.add_parser(
        "bootstrap-silver",
        help="Bulk-approve a non-Gold Silver set from persisted final contexts",
    )
    bootstrap.add_argument("--dataset", type=Path, required=True)
    bootstrap.add_argument("--traces", type=Path, required=True)
    bootstrap.add_argument("--output", type=Path, required=True)
    bootstrap.add_argument("--max-evidence", type=int, default=3)

    evaluate = subparsers.add_parser(
        "evaluate",
        help="Calculate deterministic retrieval, rerank, context, and citation metrics",
    )
    evaluate.add_argument("--dataset", type=Path, required=True)
    evaluate.add_argument("--traces", type=Path, required=True)
    evaluate.add_argument("--output", type=Path)

    export_deepeval = subparsers.add_parser(
        "export-deepeval",
        help="Export approved generated traces for the isolated DeepEval runner",
    )
    export_deepeval.add_argument("--dataset", type=Path, required=True)
    export_deepeval.add_argument("--traces", type=Path, required=True)
    export_deepeval.add_argument("--output", type=Path, required=True)

    review = subparsers.add_parser(
        "export-review-pack",
        help="Render canonical traces as a human-readable annotation pack",
    )
    review.add_argument("--dataset", type=Path, required=True)
    review.add_argument("--traces", type=Path, required=True)
    review.add_argument("--output", type=Path, required=True)
    return parser


def _resolve(path: Path, root: Path) -> Path:
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _execute(args: argparse.Namespace, root: Path) -> dict[str, object]:
    if args.command == "generate-skeleton":
        source = _resolve(args.source, root)
        output = _resolve(args.output, root)
        cases = build_pending_dataset(source, limit=args.limit)
        write_jsonl(output, cases)
        return {
            "status": "success",
            "command": args.command,
            "case_count": len(cases),
            "output": str(output),
        }
    if args.command == "validate-dataset":
        cases = load_evaluation_cases(_resolve(args.dataset, root))
        config = load_evaluation_config()
        statuses = Counter(item.annotation_status.value for item in cases)
        outcomes = Counter(item.expected_outcome.value for item in cases)
        approved = statuses[AnnotationStatus.APPROVED.value]
        return {
            "status": "success",
            "command": args.command,
            "case_count": len(cases),
            "annotation_statuses": dict(statuses),
            "expected_outcomes": dict(outcomes),
            "expected_case_count": config.expected_case_count,
            "minimum_approved_cases": config.minimum_approved_cases,
            "baseline_ready": (
                len(cases) == config.expected_case_count
                and approved >= config.minimum_approved_cases
            ),
        }
    if args.command == "validate-traces":
        traces = load_evaluation_traces(_resolve(args.traces, root))
        return {
            "status": "success",
            "command": args.command,
            "trace_count": len(traces),
            "run_ids": sorted({item.run_id for item in traces}),
        }
    if args.command == "bootstrap-silver":
        cases = load_evaluation_cases(_resolve(args.dataset, root))
        traces = load_evaluation_traces(_resolve(args.traces, root))
        approved = bootstrap_silver_dataset(
            cases,
            traces,
            max_evidence=args.max_evidence,
        )
        output = _resolve(args.output, root)
        write_jsonl(output, approved)
        outcomes = Counter(item.expected_outcome.value for item in approved)
        return {
            "status": "success",
            "command": args.command,
            "case_count": len(approved),
            "annotation_status": AnnotationStatus.APPROVED.value,
            "label_tier": "silver",
            "expected_outcomes": dict(outcomes),
            "output": str(output),
        }
    if args.command == "export-deepeval":
        cases = load_evaluation_cases(_resolve(args.dataset, root))
        traces = load_evaluation_traces(_resolve(args.traces, root))
        payloads = build_deepeval_payloads(cases, traces)
        output = _resolve(args.output, root)
        write_jsonl(output, payloads)
        return {
            "status": "success",
            "command": args.command,
            "case_count": len(payloads),
            "output": str(output),
        }
    if args.command == "export-review-pack":
        cases = load_evaluation_cases(_resolve(args.dataset, root))
        traces = load_evaluation_traces(_resolve(args.traces, root))
        output = _resolve(args.output, root)
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix(output.suffix + ".tmp")
        try:
            temporary.write_text(
                build_annotation_review_markdown(cases, traces),
                encoding="utf-8",
            )
            temporary.replace(output)
        except OSError:
            temporary.unlink(missing_ok=True)
            raise
        return {
            "status": "success",
            "command": args.command,
            "case_count": len(cases),
            "output": str(output),
        }
    if args.command == "evaluate":
        cases = load_evaluation_cases(_resolve(args.dataset, root))
        traces = load_evaluation_traces(_resolve(args.traces, root))
        report = evaluate_traces(cases, traces)
        payload = {
            "status": "success",
            "command": args.command,
            "report": report.model_dump(mode="json"),
        }
        if args.output is not None:
            output = _resolve(args.output, root)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            payload["output"] = str(output)
        return payload
    raise ValueError(f"unsupported evaluation command:{args.command}")


def main(
    argv: Sequence[str] | None = None,
    *,
    project_root: Path = PROJECT_ROOT,
) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = _execute(args, project_root.resolve())
    except (OSError, RuntimeError, ValueError) as exc:
        print(
            json.dumps(
                {"status": "failed", "error": str(exc)},
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
