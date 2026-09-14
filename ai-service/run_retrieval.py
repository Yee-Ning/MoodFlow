"""Run online retrieval through the Top-40 candidate-pool boundary."""

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
    RerankStatus,
    load_rerank_config,
)
from app.rag.retrieval import (
    HybridRetrievalService,
    RetrievalConfig,
    RetrievalIndexBinding,
    RetrievalResult,
    RetrievalStatus,
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
        description=(
            "Understand one music query, retrieve Top 40, and optionally rerank Top 12."
        )
    )
    parser.add_argument("query", help="Music knowledge query to retrieve")
    parser.add_argument(
        "--index-manifest",
        type=Path,
        required=True,
        help="Ready index-build manifest, relative to the ai-service directory",
    )
    parser.add_argument("--current-song-id", type=int)
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Use deterministic rules only for query-type analysis",
    )
    parser.add_argument(
        "--rerank",
        action="store_true",
        help="Apply Qwen3 reranking and return the Top-12 candidate pool",
    )
    return parser


async def _run(
    args: argparse.Namespace,
    project_root: Path,
    retrieval_factory: RetrievalFactory,
    rerank_factory: RerankFactory,
) -> tuple[int, str]:
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
    service = QueryUnderstandingService(
        config=understanding_config,
        index_profile=binding.profile,
    )
    graph = create_query_understanding_graph(service=service)
    current_song_id = args.current_song_id
    understanding_output = await graph.ainvoke(
        create_query_understanding_state(
            args.query,
            messages=[HumanMessage(content=args.query)],
            routing_context=PlayerContext(
                has_current_song=current_song_id is not None,
                current_song_id=current_song_id,
            ),
        )
    )
    understanding = understanding_output["result"]
    if understanding.status is not QueryUnderstandingStatus.READY:
        return 2, json.dumps(
            {
                "pipeline_stage": "query_understanding",
                "rerank_applied": False,
                "query_understanding": understanding.model_dump(mode="json"),
            },
            ensure_ascii=False,
            indent=2,
        )
    plan = understanding.search_plan
    assert plan is not None
    retrieval = await retrieval_factory(binding, retrieval_config).retrieve(plan)
    if args.rerank:
        rerank_config = load_rerank_config()
        reranked = await rerank_factory(rerank_config).rerank(plan, retrieval)
        if retrieval.status is RetrievalStatus.UNAVAILABLE:
            status = 3
        elif reranked.status is RerankStatus.UNAVAILABLE:
            status = 4
        else:
            status = 0
        return status, json.dumps(
            {
                "pipeline_stage": "reranked_candidate_pool",
                "candidate_pool_limit": plan.candidate_top_k,
                "rerank_top_k": rerank_config.top_k,
                "rerank_applied": reranked.status is RerankStatus.SUCCESS,
                "query_understanding": understanding.model_dump(mode="json"),
                "retrieval": retrieval.model_dump(mode="json"),
                "reranking": reranked.model_dump(mode="json"),
            },
            ensure_ascii=False,
            indent=2,
        )
    status = 3 if retrieval.status is RetrievalStatus.UNAVAILABLE else 0
    return status, json.dumps(
        {
            "pipeline_stage": "candidate_pool",
            "candidate_pool_limit": plan.candidate_top_k,
            "rerank_applied": False,
            "query_understanding": understanding.model_dump(mode="json"),
            "retrieval": retrieval.model_dump(mode="json"),
        },
        ensure_ascii=False,
        indent=2,
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
        status, payload = asyncio.run(
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
    return status


if __name__ == "__main__":
    raise SystemExit(main())
