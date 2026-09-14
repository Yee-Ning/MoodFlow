"""Orchestrate context, entity, constraint, expansion, and plan construction."""

from __future__ import annotations

from langchain_core.messages import BaseMessage

from app.rag.query_understanding.analyzer import QueryAnalyzer
from app.rag.query_understanding.config import (
    QueryUnderstandingConfig,
    load_query_understanding_config,
)
from app.rag.query_understanding.constraints import extract_query_constraints
from app.rag.query_understanding.context import QueryContextResolver
from app.rag.query_understanding.entity_resolver import QueryEntityResolver
from app.rag.query_understanding.models import (
    CatalogReference,
    ContextResolution,
    ContextResolutionStatus,
    IndexProfile,
    QueryAnalysis,
    QueryEntityStatus,
    QueryUnderstandingResult,
    QueryUnderstandingStatus,
    ResolvedQueryEntity,
    SearchPlan,
)
from app.rag.query_understanding.planner import build_search_plan
from app.schemas.player import PlayerContext


class QueryUnderstandingService:
    def __init__(
        self,
        *,
        config: QueryUnderstandingConfig | None = None,
        context_resolver: QueryContextResolver | None = None,
        entity_resolver: QueryEntityResolver | None = None,
        analyzer: QueryAnalyzer | None = None,
        index_profile: IndexProfile | None = None,
    ) -> None:
        self.config = config or load_query_understanding_config()
        self.context_resolver = context_resolver or QueryContextResolver()
        self.entity_resolver = entity_resolver or QueryEntityResolver()
        self.analyzer = analyzer or QueryAnalyzer(self.config)
        self.index_profile = index_profile

    async def resolve_context(
        self,
        query: str,
        *,
        messages: list[BaseMessage] | None = None,
        routing_context: PlayerContext | None = None,
    ) -> ContextResolution:
        return await self.context_resolver.resolve(
            query,
            messages=messages,
            routing_context=routing_context,
        )

    async def understand(
        self,
        query: str,
        *,
        messages: list[BaseMessage] | None = None,
        routing_context: PlayerContext | None = None,
        context: ContextResolution | None = None,
        catalog_references: list[CatalogReference] | None = None,
    ) -> QueryUnderstandingResult:
        resolved_context = context or await self.resolve_context(
            query,
            messages=messages,
            routing_context=routing_context,
        )
        if resolved_context.status is ContextResolutionStatus.NEEDS_INPUT:
            return QueryUnderstandingResult(
                status=QueryUnderstandingStatus.NEEDS_INPUT,
                context=resolved_context,
                clarification_question=resolved_context.clarification_question,
            )
        entities = self.resolve_entities(
            resolved_context,
            catalog_references=catalog_references,
        )
        ambiguous = [
            item for item in entities if item.status is QueryEntityStatus.AMBIGUOUS
        ]
        if ambiguous:
            names = "、".join(item.surface for item in ambiguous[:5])
            return QueryUnderstandingResult(
                status=QueryUnderstandingStatus.NEEDS_INPUT,
                context=resolved_context,
                clarification_question=(
                    f"“{names}”对应多个音乐实体，请明确是歌曲、歌手还是专辑。"
                ),
            )

        analysis = await self.analyze(resolved_context, entities)
        plan = self.plan(analysis)
        return QueryUnderstandingResult(
            status=QueryUnderstandingStatus.READY,
            context=resolved_context,
            analysis=analysis,
            search_plan=plan,
        )

    def resolve_entities(
        self,
        context: ContextResolution,
        *,
        catalog_references: list[CatalogReference] | None = None,
    ) -> list[ResolvedQueryEntity]:
        if context.status is not ContextResolutionStatus.RESOLVED:
            return []
        standalone = context.standalone_query
        assert standalone is not None
        return self.entity_resolver.resolve(
            standalone,
            context_entities=context.entities,
            catalog_references=catalog_references,
        )

    async def analyze(
        self,
        context: ContextResolution,
        entities: list[ResolvedQueryEntity],
    ) -> QueryAnalysis:
        if context.status is not ContextResolutionStatus.RESOLVED:
            raise ValueError("query context must be resolved before analysis")
        standalone = context.standalone_query
        assert standalone is not None
        analyzed_types = await self.analyzer.analyze(standalone)
        constraints, constraint_warnings = extract_query_constraints(
            standalone, entities
        )
        entity_warnings = (
            []
            if any(
                item.status is QueryEntityStatus.RESOLVED for item in entities
            )
            else ["no_resolved_query_entity"]
        )
        confidences = [analyzed_types.confidence]
        confidences.extend(item.confidence for item in entities if item.confidence)
        return QueryAnalysis(
            original_query=context.original_query,
            standalone_query=standalone,
            query_types=analyzed_types.query_types,
            entities=entities,
            constraints=constraints,
            context=context,
            confidence=min(confidences),
            warnings=list(
                dict.fromkeys(
                    [
                        *analyzed_types.warnings,
                        *constraint_warnings,
                        *entity_warnings,
                    ]
                )
            ),
        )

    def plan(self, analysis: QueryAnalysis) -> SearchPlan:
        return build_search_plan(
            analysis,
            self.config,
            index_profile=self.index_profile,
        )
