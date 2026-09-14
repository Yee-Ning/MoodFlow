"""Evaluate the live pre-rerank candidate pool against a labeled JSONL set."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Callable, Protocol, Sequence

from langchain_core.messages import HumanMessage

from app.rag.query_understanding import (
    QueryUnderstandingService,
    QueryUnderstandingStatus,
    create_query_understanding_graph,
    create_query_understanding_state,
    load_query_understanding_config,
)
from app.rag.query_understanding.models import SearchPlan
from app.rag.reranking import (
    CandidateReranker,
    QwenCandidateReranker,
    RerankConfig,
    evaluate_reranking,
    load_rerank_config,
)
from app.rag.retrieval import (
    HybridRetrievalService,
    RetrievalConfig,
    RetrievalEvaluationCase,
    RetrievalIndexBinding,
    RetrievalResult,
    evaluate_candidate_pool,
    load_evaluation_cases,
    load_retrieval_config,
    load_retrieval_index_binding,
)
from app.schemas.player import PlayerContext


PROJECT_ROOT = Path(__file__).resolve().parent


class CandidatePoolRetriever(Protocol):
    async def retrieve(self, plan: SearchPlan) -> RetrievalResult: ...


RetrievalFactory = Callable[
    [RetrievalIndexBinding, RetrievalConfig], CandidatePoolRetriever
]
RerankFactory = Callable[[RerankConfig], CandidateReranker]


def _default_retrieval_factory(
    binding: RetrievalIndexBinding,
    config: RetrievalConfig,
) -> CandidatePoolRetriever:
    return HybridRetrievalService(binding, config=config)


def _default_rerank_factory(config: RerankConfig) -> CandidateReranker:
    return QwenCandidateReranker(config)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate Top-40 retrieval and optional Top-12 reranking."
    )
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--index-manifest", type=Path, required=True)
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Use deterministic rules only for query-type analysis",
    )
    parser.add_argument(
        "--rerank",
        action="store_true",
        help="Evaluate Qwen3 Top-12 reranking against the same labels",
    )
    return parser


async def _run(
    args: argparse.Namespace,
    project_root: Path,
    retrieval_factory: RetrievalFactory,
    rerank_factory: RerankFactory,
) -> str:
    dataset_path = (
        args.dataset
        if args.dataset.is_absolute()
        else project_root / args.dataset
    ).resolve()
    cases = load_evaluation_cases(dataset_path)
    retrieval_config = load_retrieval_config()
    binding = load_retrieval_index_binding(
        project_root,
        args.index_manifest,
        retrieval_config,
    )
    understanding_config = load_query_understanding_config()
    if args.no_llm:
        understanding_config = understanding_config.model_copy(
            update={"llm_fallback_enabled": False}
        )
    understanding_service = QueryUnderstandingService(
        config=understanding_config,
        index_profile=binding.profile,
    )
    graph = create_query_understanding_graph(service=understanding_service)
    retriever = retrieval_factory(binding, retrieval_config)
    reranker = (
        rerank_factory(load_rerank_config())
        if args.rerank
        else None
    )
    rankings: dict[str, list[str]] = {}
    reranked_rankings: dict[str, list[str]] = {}
    fallback_by_case: dict[str, bool] = {}
    latency_ms_by_case: dict[str, float] = {}
    case_statuses: dict[str, str | dict[str, str]] = {}
    for case in cases:
        ranking, retrieval_status, reranked, rerank_status, fallback, latency_ms = (
            await _retrieve_case(case, graph, retriever, reranker)
        )
        rankings[case.case_id] = ranking
        if reranker is None:
            case_statuses[case.case_id] = retrieval_status
            continue
        reranked_rankings[case.case_id] = reranked
        fallback_by_case[case.case_id] = fallback
        latency_ms_by_case[case.case_id] = latency_ms
        case_statuses[case.case_id] = {
            "retrieval": retrieval_status,
            "rerank": rerank_status,
        }
    report = evaluate_candidate_pool(cases, rankings)
    payload = {
        "pipeline_stage": (
            "rerank_evaluation" if reranker is not None else "candidate_pool_evaluation"
        ),
        "candidate_pool_limit": understanding_config.candidate_top_k,
        "rerank_applied": reranker is not None,
        "index_profile": binding.profile.model_dump(mode="json"),
        "case_statuses": case_statuses,
        "metrics": report.model_dump(mode="json"),
    }
    if reranker is not None:
        rerank_config = load_rerank_config()
        payload["rerank_top_k"] = rerank_config.top_k
        payload["rerank_metrics"] = evaluate_reranking(
            cases,
            rankings,
            reranked_rankings,
            fallback_by_case=fallback_by_case,
            latency_ms_by_case=latency_ms_by_case,
        ).model_dump(mode="json")
    return json.dumps(payload, ensure_ascii=False, indent=2)


async def _retrieve_case(
    case: RetrievalEvaluationCase,
    graph,
    retriever: CandidatePoolRetriever,
    reranker: CandidateReranker | None,
) -> tuple[list[str], str, list[str], str, bool, float]:
    output = await graph.ainvoke(
        create_query_understanding_state(
            case.query,
            messages=[HumanMessage(content=case.query)],
            routing_context=PlayerContext(
                has_current_song=case.current_song_id is not None,
                current_song_id=case.current_song_id,
            ),
        )
    )
    understanding = output["result"]
    if understanding.status is not QueryUnderstandingStatus.READY:
        return (
            [],
            "query_understanding_needs_input",
            [],
            "not_run",
            False,
            0,
        )
    plan = understanding.search_plan
    assert plan is not None
    result = await retriever.retrieve(plan)
    ranking = [item.parent_id for item in result.candidates]
    if reranker is None:
        return ranking, result.status.value, [], "not_run", False, 0
    reranked = await reranker.rerank(plan, result)
    return (
        ranking,
        result.status.value,
        [item.candidate.parent_id for item in reranked.candidates],
        reranked.status.value,
        reranked.fallback_used,
        reranked.timings.total_ms,
    )


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
            _run(
                args,
                project_root.resolve(),
                retrieval_factory,
                rerank_factory,
            )
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(
            json.dumps(
                {"status": "failed", "error": str(exc)},
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        return 1
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
