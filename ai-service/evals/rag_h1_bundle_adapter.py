"""Evaluation-only seven-leg query-understanding bundle; no production edits.

The explicit H1 protocol adds one HyDE dense leg beyond the production six-leg
guard. The original guard still builds the six subquery legs with unchanged
configuration. This adapter is only for the approved H1 evaluation scope.
"""

from __future__ import annotations

import hashlib
import json

from app.agent.foundation.rag_source import literal_search_plan
from app.rag.query_understanding.models import (
    QueryVariant,
    QueryVariantKind,
    RetrievalStrategy,
    SearchChannel,
    SearchPlan,
)
from app.rag.retrieval.legs import RetrievalLeg, build_retrieval_legs


def bundle_plan(completed_query, queries, hyde, profile, *, limit=40):
    if limit != 40 or len(queries) != 3:
        raise ValueError("h1_bundle_requires_40_candidates_and_3_subqueries")
    base = literal_search_plan(completed_query, profile, limit)
    variants = [QueryVariant(text=text, kind=QueryVariantKind.DECOMPOSED,
                             channel=SearchChannel.BOTH, weight=1.0) for text in queries]
    variants.append(QueryVariant(text=hyde, kind=QueryVariantKind.SEMANTIC,
                                 channel=SearchChannel.DENSE, weight=1.0))
    payload = base.model_dump(mode="json")
    payload.update(queries=[v.model_dump(mode="json") for v in variants],
                   strategy=RetrievalStrategy.MULTI_QUERY_HYBRID.value)
    identity = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload["plan_id"] = "query-plan:" + hashlib.sha256(identity.encode()).hexdigest()[:24]
    return SearchPlan.model_validate(payload)


def seven_legs(plan, dense_vectors, config):
    """Preserve the real six-leg builder; explicitly append the approved seventh."""
    if (len(plan.queries) != 4 or config.fusion.maximum_legs != 6
            or config.fusion.dense_weight != 1.0 or config.fusion.lexical_weight != 1.0
            or any(v.channel != SearchChannel.BOTH or v.weight != 1.0 for v in plan.queries[:3])
            or plan.queries[3].channel != SearchChannel.DENSE or plan.queries[3].weight != 1.0
            or set(dense_vectors) != {0, 1, 2, 3}):
        raise ValueError("h1_seven_leg_contract_mismatch")
    six_plan = plan.model_copy(update={"queries": plan.queries[:3]})
    result = build_retrieval_legs(six_plan, {i: dense_vectors[i] for i in range(3)}, config)
    if len(result) != 6:
        raise ValueError("h1_subquery_leg_count_mismatch")
    result.append(RetrievalLeg(variant_index=3, variant_kind=QueryVariantKind.SEMANTIC,
                              channel=SearchChannel.DENSE, text=plan.queries[3].text,
                              weight=1.0, dense_vector=dense_vectors[3]))
    return result
