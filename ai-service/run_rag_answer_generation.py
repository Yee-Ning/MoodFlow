"""Generate citation-aware answers for canonical RAG evaluation traces."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from app.rag.evaluation import (
    CitationAwareAnswerGenerator,
    generate_evaluation_answers,
    load_evaluation_cases,
    load_evaluation_traces,
    write_jsonl,
)

PROJECT_ROOT = Path(__file__).resolve().parent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate grounded answers and citations for RAG Eval V0"
    )
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--traces", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-concurrent", type=int, default=4)
    return parser


async def _run(
    args: argparse.Namespace,
    root: Path,
    generator: CitationAwareAnswerGenerator | None,
) -> dict[str, object]:
    dataset = _resolve(args.dataset, root)
    trace_path = _resolve(args.traces, root)
    output = _resolve(args.output, root)
    cases = load_evaluation_cases(dataset)
    traces = load_evaluation_traces(trace_path)
    generated = await generate_evaluation_answers(
        cases,
        traces,
        generator=generator,
        max_concurrent=args.max_concurrent,
    )
    write_jsonl(output, generated)
    statuses = Counter(item.statuses.generation for item in generated)
    failed_count = statuses["failed"]
    return {
        "status": "partial" if failed_count else "success",
        "trace_count": len(generated),
        "generation_statuses": dict(statuses),
        "failed_count": failed_count,
        "output": str(output),
    }


def _resolve(path: Path, root: Path) -> Path:
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def main(
    argv: Sequence[str] | None = None,
    *,
    project_root: Path = PROJECT_ROOT,
    generator: CitationAwareAnswerGenerator | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = asyncio.run(_run(args, project_root.resolve(), generator))
    except (OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 2 if payload["status"] == "partial" else 0


if __name__ == "__main__":
    raise SystemExit(main())
