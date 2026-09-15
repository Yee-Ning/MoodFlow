"""Execute the current online retrieval stages and persist canonical eval traces."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from collections.abc import Callable, Sequence
from pathlib import Path

from app.agent.foundation.subgraphs.worldline.retrieval import WorldLineContextAdapter
from app.rag.evaluation import collect_evaluation_traces, load_evaluation_cases, write_jsonl
from app.rag.evaluation.collector import EvaluationRetriever
from app.rag.query_understanding import (
    QueryUnderstandingService,
    create_query_understanding_graph,
    load_query_understanding_config,
)
from app.rag.reranking import (
    CandidateReranker,
    QwenCandidateReranker,
    RerankConfig,
    load_rerank_config,
)
from app.rag.retrieval import (
    HybridRetrievalService,
    RetrievalConfig,
    RetrievalIndexBinding,
    load_retrieval_config,
    load_retrieval_index_binding,
)

PROJECT_ROOT = Path(__file__).resolve().parent

RetrievalFactory = Callable[
    [RetrievalIndexBinding, RetrievalConfig],
    EvaluationRetriever,
]
RerankFactory = Callable[[RerankConfig], CandidateReranker]


def _default_retrieval_factory(
    binding: RetrievalIndexBinding,
    config: RetrievalConfig,
) -> HybridRetrievalService:
    return HybridRetrievalService(binding, config=config)


def _default_rerank_factory(config: RerankConfig) -> CandidateReranker:
    return QwenCandidateReranker(config)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect Top40, Top12, and final context traces for RAG Eval V0"
    )
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--index-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument("--rerank", action="store_true")
    return parser


async def _run(
    args: argparse.Namespace,
    root: Path,
    retrieval_factory: RetrievalFactory,
    rerank_factory: RerankFactory,
) -> dict[str, object]:
    dataset = _resolve(args.dataset, root)
    output = _resolve(args.output, root)
    cases = load_evaluation_cases(dataset)
    retrieval_config = load_retrieval_config()
    binding = load_retrieval_index_binding(root, args.index_manifest, retrieval_config)
    understanding_config = load_query_understanding_config()
    if args.no_llm:
        understanding_config = understanding_config.model_copy(
            update={"llm_fallback_enabled": False}
        )
    graph = create_query_understanding_graph(
        service=QueryUnderstandingService(
            config=understanding_config,
            index_profile=binding.profile,
        )
    )
    retriever = retrieval_factory(binding, retrieval_config)
    reranker = rerank_factory(load_rerank_config()) if args.rerank else None
    if isinstance(reranker, QwenCandidateReranker):
        await reranker.prepare()
    adapter = WorldLineContextAdapter(retrieval_config)
    traces = await collect_evaluation_traces(
        cases,
        run_id=args.run_id,
        graph=graph,
        retriever=retriever,
        reranker=reranker,
        context_adapter=adapter.adapt,
        retrieval_version=retrieval_config.retrieval_version,
        context_token_budget=retrieval_config.selection.worldline_token_budget,
    )
    write_jsonl(output, traces)
    stage_statuses = {
        stage: Counter(getattr(item.statuses, stage) for item in traces)
        for stage in ("query_understanding", "retrieval", "rerank", "context")
    }
    failed_count = sum(
        any(getattr(item.statuses, stage) == "failed" for stage in stage_statuses)
        for item in traces
    )
    return {
        "status": "partial" if failed_count else "success",
        "run_id": args.run_id,
        "case_count": len(cases),
        "trace_count": len(traces),
        "failed_count": failed_count,
        **{
            f"{stage}_statuses": dict(counts)
            for stage, counts in stage_statuses.items()
        },
        "rerank_enabled": args.rerank,
        "output": str(output),
    }


def _resolve(path: Path, root: Path) -> Path:
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def main(
    argv: Sequence[str] | None = None,
    *,
    project_root: Path = PROJECT_ROOT,
    retrieval_factory: RetrievalFactory = _default_retrieval_factory,
    rerank_factory: RerankFactory = _default_rerank_factory,
) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = asyncio.run(
            _run(args, project_root.resolve(), retrieval_factory, rerank_factory)
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 2 if payload["status"] == "partial" else 0


if __name__ == "__main__":
    raise SystemExit(main())
