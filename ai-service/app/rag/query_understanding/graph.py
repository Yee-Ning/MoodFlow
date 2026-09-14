"""Reusable LangGraph for per-route retrieval query understanding."""

from __future__ import annotations

from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph

from app.rag.query_understanding.expansion import expand_queries
from app.rag.query_understanding.filters import build_metadata_filter
from app.rag.query_understanding.models import (
    CatalogReference,
    ContextResolution,
    ContextResolutionStatus,
    MetadataFilter,
    QueryAnalysis,
    QueryEntityStatus,
    QueryUnderstandingResult,
    QueryUnderstandingStatus,
    QueryVariant,
    ResolvedQueryEntity,
    SearchPlan,
)
from app.rag.query_understanding.planner import build_search_plan
from app.rag.query_understanding.service import QueryUnderstandingService
from app.schemas.player import PlayerContext


class QueryUnderstandingState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    current_input: str
    routing_context: PlayerContext
    catalog_references: list[CatalogReference]
    query_context: ContextResolution | None
    query_entities: list[ResolvedQueryEntity]
    query_analysis: QueryAnalysis | None
    query_variants: list[QueryVariant]
    metadata_filter: MetadataFilter | None
    search_plan: SearchPlan | None
    result: QueryUnderstandingResult | None


class QueryUnderstandingOutput(TypedDict):
    result: QueryUnderstandingResult


def create_query_understanding_state(
    query: str,
    *,
    messages: list[AnyMessage] | None = None,
    routing_context: PlayerContext | None = None,
    catalog_references: list[CatalogReference] | None = None,
    context: ContextResolution | None = None,
) -> QueryUnderstandingState:
    return QueryUnderstandingState(
        messages=list(messages or []),
        current_input=query,
        routing_context=routing_context or PlayerContext(),
        catalog_references=list(catalog_references or []),
        query_context=context,
        query_entities=[],
        query_analysis=None,
        query_variants=[],
        metadata_filter=None,
        search_plan=None,
        result=None,
    )


def build_query_understanding_graph(
    *, service: QueryUnderstandingService | None = None
) -> StateGraph:
    query_service = service or QueryUnderstandingService()

    async def resolve_context(
        state: QueryUnderstandingState,
    ) -> dict[str, ContextResolution]:
        existing = state.get("query_context")
        if existing is not None:
            return {"query_context": existing}
        context = await query_service.resolve_context(
            state["current_input"],
            messages=state["messages"],
            routing_context=state["routing_context"],
        )
        return {"query_context": context}

    def after_context(state: QueryUnderstandingState) -> str:
        context = state.get("query_context")
        if context is None:
            raise RuntimeError("query context was not produced")
        return (
            "request_context"
            if context.status is ContextResolutionStatus.NEEDS_INPUT
            else "resolve_entities"
        )

    def request_context(
        state: QueryUnderstandingState,
    ) -> dict[str, QueryUnderstandingResult]:
        context = state["query_context"]
        assert context is not None
        result = QueryUnderstandingResult(
            status=QueryUnderstandingStatus.NEEDS_INPUT,
            context=context,
            clarification_question=context.clarification_question,
        )
        return {"result": result}

    def resolve_entities(
        state: QueryUnderstandingState,
    ) -> dict[str, list[ResolvedQueryEntity]]:
        context = state["query_context"]
        assert context is not None
        return {
            "query_entities": query_service.resolve_entities(
                context,
                catalog_references=state.get("catalog_references", []),
            )
        }

    def after_entities(state: QueryUnderstandingState) -> str:
        return (
            "request_entity"
            if any(
                item.status is QueryEntityStatus.AMBIGUOUS
                for item in state.get("query_entities", [])
            )
            else "analyze_query"
        )

    def request_entity(
        state: QueryUnderstandingState,
    ) -> dict[str, QueryUnderstandingResult]:
        context = state["query_context"]
        assert context is not None
        ambiguous = [
            item
            for item in state["query_entities"]
            if item.status is QueryEntityStatus.AMBIGUOUS
        ]
        names = "、".join(item.surface for item in ambiguous[:5])
        question = f"“{names}”对应多个音乐实体，请明确是歌曲、歌手还是专辑。"
        return {
            "result": QueryUnderstandingResult(
                status=QueryUnderstandingStatus.NEEDS_INPUT,
                context=context,
                clarification_question=question,
            )
        }

    async def analyze_query(
        state: QueryUnderstandingState,
    ) -> dict[str, QueryAnalysis]:
        context = state["query_context"]
        assert context is not None
        analysis = await query_service.analyze(
            context, state.get("query_entities", [])
        )
        return {"query_analysis": analysis}

    def expand_query(
        state: QueryUnderstandingState,
    ) -> dict[str, list[QueryVariant]]:
        analysis = state["query_analysis"]
        assert analysis is not None
        return {
            "query_variants": expand_queries(
                analysis,
                maximum=query_service.config.max_query_variants,
                enable_hyde=query_service.config.enable_hyde,
            )
        }

    def build_filter(
        state: QueryUnderstandingState,
    ) -> dict[str, MetadataFilter]:
        analysis = state["query_analysis"]
        assert analysis is not None
        return {"metadata_filter": build_metadata_filter(analysis)}

    def plan_search(
        state: QueryUnderstandingState,
    ) -> dict[str, SearchPlan | QueryUnderstandingResult]:
        context = state["query_context"]
        analysis = state["query_analysis"]
        metadata_filter = state["metadata_filter"]
        assert context is not None
        assert analysis is not None
        assert metadata_filter is not None
        plan = build_search_plan(
            analysis,
            query_service.config,
            index_profile=query_service.index_profile,
            queries=state["query_variants"],
            metadata_filter=metadata_filter,
        )
        result = QueryUnderstandingResult(
            status=QueryUnderstandingStatus.READY,
            context=context,
            analysis=analysis,
            search_plan=plan,
        )
        return {"search_plan": plan, "result": result}

    builder = StateGraph(
        QueryUnderstandingState, output_schema=QueryUnderstandingOutput
    )
    builder.add_node("resolve_context", resolve_context)
    builder.add_node("request_context", request_context)
    builder.add_node("resolve_entities", resolve_entities)
    builder.add_node("request_entity", request_entity)
    builder.add_node("analyze_query", analyze_query)
    builder.add_node("expand_query", expand_query)
    builder.add_node("build_filter", build_filter)
    builder.add_node("plan_search", plan_search)
    builder.add_edge(START, "resolve_context")
    builder.add_conditional_edges(
        "resolve_context",
        after_context,
        {
            "request_context": "request_context",
            "resolve_entities": "resolve_entities",
        },
    )
    builder.add_edge("request_context", END)
    builder.add_conditional_edges(
        "resolve_entities",
        after_entities,
        {"request_entity": "request_entity", "analyze_query": "analyze_query"},
    )
    builder.add_edge("request_entity", END)
    builder.add_edge("analyze_query", "expand_query")
    builder.add_edge("analyze_query", "build_filter")
    builder.add_edge(["expand_query", "build_filter"], "plan_search")
    builder.add_edge("plan_search", END)
    return builder


def create_query_understanding_graph(
    *, service: QueryUnderstandingService | None = None
) -> CompiledStateGraph:
    return build_query_understanding_graph(service=service).compile()
