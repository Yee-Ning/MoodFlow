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
from app.rag.retrieval import (
    HybridRetrievalService,
    RetrievalConfig,
    RetrievalEvaluationCase,
    RetrievalIndexBinding,
    RetrievalResult,
    RetrievalStatus,
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


def _default_retrieval_factory(
    binding: RetrievalIndexBinding,
    config: RetrievalConfig,
) -> CandidatePoolRetriever:
    return HybridRetrievalService(binding, config=config)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate the pre-rerank Top-40 retrieval candidate pool."
    )
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--index-manifest", type=Path, required=True)
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Use deterministic rules only for query-type analysis",
    )
    return parser


async def _run(
    args: argparse.Namespace,
    project_root: Path,
    retrieval_factory: RetrievalFactory,
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
    rankings: dict[str, list[str]] = {}
    case_statuses: dict[str, str] = {}
    for case in cases:
        ranking, status = await _retrieve_case(case, graph, retriever)
        rankings[case.case_id] = ranking
        case_statuses[case.case_id] = status
    report = evaluate_candidate_pool(cases, rankings)
    return json.dumps(
        {
            "pipeline_stage": "candidate_pool_evaluation",
            "candidate_pool_limit": understanding_config.candidate_top_k,
            "rerank_applied": False,
            "index_profile": binding.profile.model_dump(mode="json"),
            "case_statuses": case_statuses,
            "metrics": report.model_dump(mode="json"),
        },
        ensure_ascii=False,
        indent=2,
    )


async def _retrieve_case(
    case: RetrievalEvaluationCase,
    graph,
    retriever: CandidatePoolRetriever,
) -> tuple[list[str], str]:
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
        return [], "query_understanding_needs_input"
    plan = understanding.search_plan
    assert plan is not None
    result = await retriever.retrieve(plan)
    if result.status is RetrievalStatus.UNAVAILABLE:
        return [], result.status.value
    return [item.parent_id for item in result.candidates], result.status.value


def main(
    argv: Sequence[str] | None = None,
    *,
    project_root: Path = PROJECT_ROOT,
    retrieval_factory: RetrievalFactory = _default_retrieval_factory,
) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = asyncio.run(_run(args, project_root.resolve(), retrieval_factory))
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
