"""Retrieval-time query understanding and search-plan construction."""

from .config import QueryUnderstandingConfig, load_query_understanding_config
from .filters import build_metadata_filter
from .models import (
    CatalogReference,
    ContextDependency,
    ContextResolution,
    ContextResolutionStatus,
    FilterCondition,
    FilterGroup,
    FilterOperator,
    IndexProfile,
    MetadataField,
    MetadataFilter,
    QueryAnalysis,
    QueryConstraints,
    QueryType,
    QueryVariant,
    ResolvedQueryEntity,
    RetrievalStrategy,
    SearchPlan,
)
from .planner import build_search_plan

__all__ = [
    "CatalogReference",
    "ContextDependency",
    "ContextResolution",
    "ContextResolutionStatus",
    "FilterCondition",
    "FilterGroup",
    "FilterOperator",
    "IndexProfile",
    "MetadataField",
    "MetadataFilter",
    "QueryAnalysis",
    "QueryConstraints",
    "QueryType",
    "QueryUnderstandingConfig",
    "QueryVariant",
    "ResolvedQueryEntity",
    "RetrievalStrategy",
    "SearchPlan",
    "build_metadata_filter",
    "build_search_plan",
    "load_query_understanding_config",
]
