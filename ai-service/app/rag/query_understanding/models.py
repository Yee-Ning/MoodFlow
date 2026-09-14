"""Strict, retriever-neutral contracts for online query understanding."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.rag.enrichment.models import ContentType
from app.rag.knowledge.entity_linking.models import EntityType


class ContextDependency(StrEnum):
    STANDALONE = "standalone"
    CURRENT_SONG = "current_song"
    CONVERSATION = "conversation"
    AMBIGUOUS = "ambiguous"


class ContextResolutionStatus(StrEnum):
    RESOLVED = "resolved"
    NEEDS_INPUT = "needs_input"


class QueryType(StrEnum):
    FACT = "fact"
    TIMELINE = "timeline"
    RELATIONSHIP = "relationship"
    COMPARISON = "comparison"
    INFLUENCE_STYLE = "influence_style"
    EXPLORATORY = "exploratory"


class QueryEntityStatus(StrEnum):
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    AMBIGUOUS = "ambiguous"


class SearchChannel(StrEnum):
    BOTH = "both"
    DENSE = "dense"
    LEXICAL = "lexical"


class QueryVariantKind(StrEnum):
    ORIGINAL = "original"
    ALIAS = "alias"
    DECOMPOSED = "decomposed"
    SEMANTIC = "semantic"


class RetrievalStrategy(StrEnum):
    HYBRID = "hybrid"
    MULTI_QUERY_HYBRID = "multi_query_hybrid"


class QueryUnderstandingStatus(StrEnum):
    READY = "ready"
    NEEDS_INPUT = "needs_input"


class CatalogReference(BaseModel):
    """Provider-specific identity kept separate from knowledge-index identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str = Field(min_length=1, max_length=100)
    entity_id: str = Field(min_length=1, max_length=128)
    entity_type: EntityType
    name: str = Field(min_length=1, max_length=500)


class ResolvedQueryEntity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    surface: str = Field(min_length=1, max_length=500)
    canonical_name: str | None = Field(default=None, max_length=500)
    entity_type: EntityType | None = None
    knowledge_entity_id: str | None = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9_]*:[a-z0-9][a-z0-9_]*$",
    )
    catalog_reference: CatalogReference | None = None
    aliases: list[str] = Field(default_factory=list, max_length=100)
    status: QueryEntityStatus
    confidence: float = Field(ge=0, le=1)
    candidate_knowledge_ids: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_resolution(self) -> "ResolvedQueryEntity":
        if self.status is QueryEntityStatus.RESOLVED:
            if self.knowledge_entity_id is None and self.catalog_reference is None:
                raise ValueError("resolved query entity requires a stable identity")
            if self.canonical_name is None or self.entity_type is None:
                raise ValueError("resolved query entity requires canonical type and name")
        elif self.knowledge_entity_id is not None:
            raise ValueError("unresolved query entity cannot have a knowledge id")
        if (
            self.status is QueryEntityStatus.AMBIGUOUS
            and not self.candidate_knowledge_ids
        ):
            raise ValueError("ambiguous query entity requires candidates")
        return self


class ContextResolution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: ContextResolutionStatus
    dependency: ContextDependency
    original_query: str = Field(min_length=1, max_length=4_000)
    standalone_query: str | None = Field(default=None, min_length=1, max_length=4_000)
    evidence: list[str] = Field(default_factory=list, max_length=20)
    entities: list[ResolvedQueryEntity] = Field(default_factory=list, max_length=20)
    clarification_question: str | None = Field(default=None, max_length=1_000)

    @model_validator(mode="after")
    def validate_status_payload(self) -> "ContextResolution":
        if self.status is ContextResolutionStatus.RESOLVED:
            if self.standalone_query is None or self.clarification_question is not None:
                raise ValueError("resolved context requires only a standalone query")
        else:
            if self.standalone_query is not None or not self.clarification_question:
                raise ValueError("unresolved context requires a clarification question")
        return self


class QueryConstraints(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    entity_ids: list[str] = Field(default_factory=list, max_length=20)
    entity_types: list[EntityType] = Field(default_factory=list, max_length=20)
    start_year: int | None = Field(default=None, ge=1000, le=2999)
    end_year: int | None = Field(default=None, ge=1000, le=2999)
    languages: list[str] = Field(default_factory=list, max_length=10)
    content_types: list[ContentType] = Field(default_factory=list, max_length=20)
    negative_terms: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_constraints(self) -> "QueryConstraints":
        if (self.start_year is None) != (self.end_year is None):
            raise ValueError("query time range requires both bounds")
        if (
            self.start_year is not None
            and self.end_year is not None
            and self.start_year > self.end_year
        ):
            raise ValueError("query start year must not exceed end year")
        for field_name in (
            "entity_ids",
            "entity_types",
            "languages",
            "content_types",
            "negative_terms",
        ):
            values = getattr(self, field_name)
            if len(values) != len(dict.fromkeys(values)):
                raise ValueError(f"{field_name} must be unique")
        return self


class QueryAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    original_query: str = Field(min_length=1, max_length=4_000)
    standalone_query: str = Field(min_length=1, max_length=4_000)
    query_types: list[QueryType] = Field(min_length=1, max_length=3)
    entities: list[ResolvedQueryEntity] = Field(default_factory=list, max_length=20)
    constraints: QueryConstraints = Field(default_factory=QueryConstraints)
    context: ContextResolution
    confidence: float = Field(ge=0, le=1)
    warnings: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def validate_analysis(self) -> "QueryAnalysis":
        if self.context.status is not ContextResolutionStatus.RESOLVED:
            raise ValueError("query analysis requires resolved context")
        if self.context.standalone_query != self.standalone_query:
            raise ValueError("analysis and context standalone queries must match")
        if len(self.query_types) != len(set(self.query_types)):
            raise ValueError("query types must be unique")
        return self


class MetadataField(StrEnum):
    ENTITY_IDS = "entity_ids"
    ENTITY_TYPES = "entity_types"
    YEAR = "year"
    START_YEAR = "start_year"
    END_YEAR = "end_year"
    LANGUAGE = "language"
    CONTENT_TYPE = "content_type"
    KNOWLEDGE_STATUS = "knowledge_status"
    VERIFICATION_STATUSES = "verification_statuses"


class FilterOperator(StrEnum):
    MATCH_VALUE = "match_value"
    MATCH_ANY = "match_any"
    RANGE = "range"


class FilterCondition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["condition"] = "condition"
    field: MetadataField
    operator: FilterOperator
    value: str | int | list[str] | None = None
    gte: int | None = None
    lte: int | None = None

    @model_validator(mode="after")
    def validate_operator_payload(self) -> "FilterCondition":
        if self.operator is FilterOperator.MATCH_VALUE:
            if not isinstance(self.value, (str, int)) or self.gte is not None or self.lte is not None:
                raise ValueError("match_value requires one scalar value")
        elif self.operator is FilterOperator.MATCH_ANY:
            if (
                not isinstance(self.value, list)
                or not self.value
                or any(not item for item in self.value)
                or self.gte is not None
                or self.lte is not None
            ):
                raise ValueError("match_any requires nonempty string values")
        elif self.operator is FilterOperator.RANGE:
            if self.value is not None or (self.gte is None and self.lte is None):
                raise ValueError("range requires at least one bound and no value")
        return self


class FilterGroup(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["group"] = "group"
    operator: Literal["all", "any"]
    children: list[FilterCondition | FilterGroup] = Field(min_length=1, max_length=50)


FilterGroup.model_rebuild()


class MetadataFilter(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    root: FilterGroup | None = None


class QueryVariant(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(min_length=1, max_length=1_000)
    kind: QueryVariantKind
    channel: SearchChannel
    weight: float = Field(gt=0, le=1)


class IndexProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    collection_name: str = Field(min_length=1, max_length=300)
    source_release_id: str = Field(min_length=1, max_length=200)
    index_schema_version: str = Field(pattern=r"^v\d+$")
    projection_version: str = Field(pattern=r"^v\d+$")
    registry_version: str = Field(pattern=r"^v\d+$")


class SearchPlan(BaseModel):
    """Executable boundary consumed by the future online retriever."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    plan_id: str = Field(pattern=r"^query-plan:[0-9a-f]{24}$")
    original_query: str = Field(min_length=1, max_length=4_000)
    standalone_query: str = Field(min_length=1, max_length=4_000)
    query_types: list[QueryType] = Field(min_length=1, max_length=3)
    entities: list[ResolvedQueryEntity] = Field(default_factory=list, max_length=20)
    queries: list[QueryVariant] = Field(min_length=1, max_length=10)
    metadata_filter: MetadataFilter
    post_exclusions: list[str] = Field(default_factory=list, max_length=20)
    strategy: RetrievalStrategy
    candidate_top_k: int = Field(ge=1, le=200)
    final_top_k: int = Field(ge=1, le=50)
    needs_rerank: bool
    analysis_version: str = Field(pattern=r"^v\d+$")
    prompt_version: str = Field(pattern=r"^v\d+$")
    index_profile: IndexProfile | None = None
    warnings: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def validate_plan(self) -> "SearchPlan":
        if self.final_top_k > self.candidate_top_k:
            raise ValueError("final_top_k must not exceed candidate_top_k")
        if self.strategy is RetrievalStrategy.HYBRID and len(self.queries) != 1:
            raise ValueError("single-query hybrid plan requires exactly one query")
        texts = [item.text.casefold() for item in self.queries]
        if len(texts) != len(set(texts)):
            raise ValueError("search-plan queries must be unique")
        exclusions = [item.casefold() for item in self.post_exclusions]
        if any(not item.strip() for item in self.post_exclusions):
            raise ValueError("search-plan exclusions cannot be blank")
        if len(exclusions) != len(set(exclusions)):
            raise ValueError("search-plan exclusions must be unique")
        return self


class QueryUnderstandingResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: QueryUnderstandingStatus
    context: ContextResolution
    analysis: QueryAnalysis | None = None
    search_plan: SearchPlan | None = None
    clarification_question: str | None = Field(default=None, max_length=1_000)

    @model_validator(mode="after")
    def validate_result(self) -> "QueryUnderstandingResult":
        if self.status is QueryUnderstandingStatus.READY:
            if self.analysis is None or self.search_plan is None:
                raise ValueError("ready query understanding requires analysis and plan")
            if self.clarification_question is not None:
                raise ValueError("ready query understanding cannot ask for clarification")
        else:
            if self.analysis is not None or self.search_plan is not None:
                raise ValueError("input request cannot contain analysis or plan")
            if not self.clarification_question:
                raise ValueError("input request requires a clarification question")
        return self
