"""Deterministic SearchPlan builder."""

import hashlib
import json

from app.rag.query_understanding.config import QueryUnderstandingConfig
from app.rag.query_understanding.expansion import expand_queries
from app.rag.query_understanding.filters import build_metadata_filter
from app.rag.query_understanding.models import (
    IndexProfile,
    MetadataFilter,
    QueryAnalysis,
    QueryVariant,
    RetrievalStrategy,
    SearchPlan,
)


def build_search_plan(
    analysis: QueryAnalysis,
    config: QueryUnderstandingConfig,
    *,
    index_profile: IndexProfile | None = None,
    queries: list[QueryVariant] | None = None,
    metadata_filter: MetadataFilter | None = None,
) -> SearchPlan:
    resolved_queries = queries or expand_queries(
        analysis,
        maximum=config.max_query_variants,
        enable_hyde=config.enable_hyde,
    )
    strategy = (
        RetrievalStrategy.HYBRID
        if len(resolved_queries) == 1
        else RetrievalStrategy.MULTI_QUERY_HYBRID
    )
    warnings = list(analysis.warnings)
    if any(
        item.status.value == "resolved" and item.knowledge_entity_id is None
        for item in analysis.entities
    ):
        warnings.append("catalog_entity_has_no_knowledge_index_id")
    material = {
        "analysis": analysis.model_dump(mode="json"),
        "queries": [item.model_dump(mode="json") for item in resolved_queries],
        "config": config.model_dump(mode="json"),
        "index_profile": (
            index_profile.model_dump(mode="json") if index_profile is not None else None
        ),
    }
    encoded = json.dumps(
        material, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    plan_id = "query-plan:" + hashlib.sha256(encoded).hexdigest()[:24]
    return SearchPlan(
        plan_id=plan_id,
        original_query=analysis.original_query,
        standalone_query=analysis.standalone_query,
        query_types=analysis.query_types,
        entities=analysis.entities,
        queries=resolved_queries,
        metadata_filter=metadata_filter or build_metadata_filter(analysis),
        post_exclusions=analysis.constraints.negative_terms,
        strategy=strategy,
        candidate_top_k=config.candidate_top_k,
        final_top_k=config.final_top_k,
        needs_rerank=len(resolved_queries) > 1,
        analysis_version=config.analysis_version,
        prompt_version=config.prompt_version,
        index_profile=index_profile,
        warnings=list(dict.fromkeys(warnings)),
    )
