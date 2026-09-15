"""Collect canonical traces by executing the current online retrieval stages."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from langchain_core.messages import HumanMessage

from app.rag.evaluation.models import (
    EvaluationContext,
    RAGEvaluationCase,
    RAGEvaluationTrace,
    TraceStageStatuses,
)
from app.rag.evaluation.trace_builder import build_evaluation_trace
from app.rag.query_understanding import (
    QueryUnderstandingResult,
    QueryUnderstandingStatus,
    create_query_understanding_state,
)
from app.rag.query_understanding.models import SearchPlan
from app.rag.reranking import CandidateReranker, RerankResult
from app.rag.retrieval import RetrievalResult
from app.schemas.player import PlayerContext


class EvaluationQueryGraph(Protocol):
    async def ainvoke(self, state: dict[str, Any]) -> dict[str, Any]: ...


class EvaluationRetriever(Protocol):
    async def retrieve(self, plan: SearchPlan) -> RetrievalResult: ...


class EvaluationContextDocument(Protocol):
    id: str
    title: str
    content: str
    source: str
    url: Any
    metadata: dict[str, Any]


ContextAdapter = Callable[
    [RetrievalResult | RerankResult, SearchPlan],
    Sequence[EvaluationContextDocument],
]


@dataclass(frozen=True, slots=True)
class _TraceStageFailure(Exception):
    stage: str
    statuses: TraceStageStatuses
    cause: Exception


async def collect_evaluation_traces(
    cases: Sequence[RAGEvaluationCase],
    *,
    run_id: str,
    graph: EvaluationQueryGraph,
    retriever: EvaluationRetriever,
    context_adapter: ContextAdapter,
    reranker: CandidateReranker | None = None,
    retrieval_version: str | None = None,
    context_version: str = "context-pack-v1",
    context_token_budget: int,
) -> list[RAGEvaluationTrace]:
    """Run every case independently so one failure does not erase the batch."""
    traces: list[RAGEvaluationTrace] = []
    for case in cases:
        try:
            traces.append(
                await _collect_case(
                    case,
                    run_id=run_id,
                    graph=graph,
                    retriever=retriever,
                    reranker=reranker,
                    context_adapter=context_adapter,
                    retrieval_version=retrieval_version,
                    context_version=context_version,
                    context_token_budget=context_token_budget,
                )
            )
        except Exception as exc:  # noqa: BLE001 - batch artifact must retain failures
            failure = (
                exc
                if isinstance(exc, _TraceStageFailure)
                else _TraceStageFailure(
                    stage="unknown",
                    statuses=TraceStageStatuses(query_understanding="failed"),
                    cause=exc,
                )
            )
            traces.append(
                RAGEvaluationTrace(
                    run_id=run_id,
                    case_id=case.case_id,
                    query=case.query,
                    statuses=failure.statuses,
                    warnings=[
                        "trace_collection_failed:"
                        f"{failure.stage}:{type(failure.cause).__name__}"
                    ],
                )
            )
    return traces


async def _collect_case(
    case: RAGEvaluationCase,
    *,
    run_id: str,
    graph: EvaluationQueryGraph,
    retriever: EvaluationRetriever,
    reranker: CandidateReranker | None,
    context_adapter: ContextAdapter,
    retrieval_version: str | None,
    context_version: str,
    context_token_budget: int,
) -> RAGEvaluationTrace:
    try:
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
        understanding = output.get("result")
        if not isinstance(understanding, QueryUnderstandingResult):
            raise ValueError("query understanding graph returned an invalid result")
    except Exception as exc:  # noqa: BLE001 - annotate the failing pipeline stage
        raise _TraceStageFailure(
            stage="query_understanding",
            statuses=TraceStageStatuses(query_understanding="failed"),
            cause=exc,
        ) from exc
    if understanding.status is QueryUnderstandingStatus.NEEDS_INPUT:
        return RAGEvaluationTrace(
            run_id=run_id,
            case_id=case.case_id,
            query=case.query,
            statuses=TraceStageStatuses(query_understanding="needs_input"),
        )
    plan = understanding.search_plan
    if plan is None:
        raise _TraceStageFailure(
            stage="query_understanding",
            statuses=TraceStageStatuses(query_understanding="failed"),
            cause=ValueError("ready query understanding omitted its search plan"),
        )
    try:
        retrieval = await retriever.retrieve(plan)
    except Exception as exc:  # noqa: BLE001 - annotate the failing pipeline stage
        raise _TraceStageFailure(
            stage="retrieval",
            statuses=TraceStageStatuses(
                query_understanding="ready",
                retrieval="failed",
            ),
            cause=exc,
        ) from exc
    try:
        rerank = (
            await reranker.rerank(plan, retrieval) if reranker is not None else None
        )
    except Exception as exc:  # noqa: BLE001 - annotate the failing pipeline stage
        raise _TraceStageFailure(
            stage="rerank",
            statuses=TraceStageStatuses(
                query_understanding="ready",
                retrieval=retrieval.status.value,
                rerank="failed",
            ),
            cause=exc,
        ) from exc
    ranked: RetrievalResult | RerankResult = rerank or retrieval
    try:
        documents = context_adapter(ranked, plan)
        final_context = _build_final_context(documents, retrieval)
    except Exception as exc:  # noqa: BLE001 - annotate the failing pipeline stage
        raise _TraceStageFailure(
            stage="context",
            statuses=TraceStageStatuses(
                query_understanding="ready",
                retrieval=retrieval.status.value,
                rerank=rerank.status.value if rerank is not None else "not_run",
                context="failed",
            ),
            cause=exc,
        ) from exc
    return build_evaluation_trace(
        run_id=run_id,
        case_id=case.case_id,
        plan=plan,
        retrieval=retrieval,
        rerank=rerank,
        final_context=final_context,
        context_token_count=sum(item.token_count for item in final_context),
        context_token_budget=context_token_budget,
        retrieval_version=retrieval_version,
        context_version=context_version,
        context_status="success" if final_context else "empty",
    )


def _build_final_context(
    documents: Sequence[EvaluationContextDocument],
    retrieval: RetrievalResult,
) -> list[EvaluationContext]:
    candidate_by_parent = {item.parent_id: item for item in retrieval.candidates}
    contexts: list[EvaluationContext] = []
    for rank, document in enumerate(documents, start=1):
        parent_id = str(document.metadata.get("parent_id") or document.id)
        candidate = candidate_by_parent.get(parent_id)
        if candidate is None:
            raise ValueError("context adapter returned a Parent outside retrieval")
        contexts.append(
            EvaluationContext(
                parent_id=parent_id,
                rank=rank,
                title=document.title,
                content=document.content,
                source_id=candidate.source_id,
                source_name=document.source,
                source_url=document.url,
                matched_child_id=candidate.primary_child_id,
                token_count=_document_token_count(document, candidate),
            )
        )
    return contexts


def _document_token_count(
    document: EvaluationContextDocument,
    candidate,
) -> int:
    packed = document.metadata.get("context_token_count")
    if isinstance(packed, int) and packed > 0:
        return packed
    return max(
        candidate.parent_context.token_count,
        candidate.primary_child_token_count,
        1,
    )
