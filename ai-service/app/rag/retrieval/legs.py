"""Expand a SearchPlan into bounded dense and lexical retrieval legs."""

from __future__ import annotations

from dataclasses import dataclass

from app.rag.query_understanding.models import (
    QueryVariantKind,
    SearchChannel,
    SearchPlan,
)

from .config import RetrievalConfig


@dataclass(frozen=True, slots=True)
class RetrievalLeg:
    variant_index: int
    variant_kind: QueryVariantKind
    channel: SearchChannel
    text: str
    weight: float
    dense_vector: list[float] | None = None


def dense_variant_indexes(plan: SearchPlan) -> list[int]:
    return [
        index
        for index, variant in enumerate(plan.queries)
        if variant.channel in {SearchChannel.BOTH, SearchChannel.DENSE}
    ]


def build_retrieval_legs(
    plan: SearchPlan,
    dense_vectors: dict[int, list[float]],
    config: RetrievalConfig,
) -> list[RetrievalLeg]:
    legs: list[RetrievalLeg] = []
    for index, variant in enumerate(plan.queries):
        if variant.channel in {SearchChannel.BOTH, SearchChannel.DENSE}:
            vector = dense_vectors.get(index)
            if vector is not None:
                legs.append(
                    RetrievalLeg(
                        variant_index=index,
                        variant_kind=variant.kind,
                        channel=SearchChannel.DENSE,
                        text=variant.text,
                        weight=variant.weight * config.fusion.dense_weight,
                        dense_vector=vector,
                    )
                )
        if variant.channel in {SearchChannel.BOTH, SearchChannel.LEXICAL}:
            legs.append(
                RetrievalLeg(
                    variant_index=index,
                    variant_kind=variant.kind,
                    channel=SearchChannel.LEXICAL,
                    text=variant.text,
                    weight=variant.weight * config.fusion.lexical_weight,
                )
            )
    if not legs:
        raise ValueError("retrieval_plan_has_no_executable_legs")
    if len(legs) > config.fusion.maximum_legs:
        raise ValueError("retrieval_plan_exceeds_maximum_legs")
    return legs
