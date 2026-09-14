"""Deterministic weighted reciprocal-rank fusion."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.rag.query_understanding.models import SearchChannel

from .legs import RetrievalLeg
from .models import ChannelEvidence


@dataclass(frozen=True, slots=True)
class RawSearchHit:
    point_id: str
    score: float
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class LegSearchResult:
    leg: RetrievalLeg
    hits: list[RawSearchHit]
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class FusedSearchHit:
    point_id: str
    fusion_score: float
    payload: dict[str, Any]
    evidence: list[ChannelEvidence]


def weighted_rrf(
    results: list[LegSearchResult],
    *,
    k: int,
) -> list[FusedSearchHit]:
    scores: dict[str, float] = {}
    payloads: dict[str, dict[str, Any]] = {}
    evidence: dict[str, list[ChannelEvidence]] = {}
    for result in results:
        if result.error_code is not None:
            continue
        seen_in_leg: set[str] = set()
        for rank, hit in enumerate(result.hits, start=1):
            if hit.point_id in seen_in_leg:
                continue
            seen_in_leg.add(hit.point_id)
            scores[hit.point_id] = scores.get(hit.point_id, 0) + (
                result.leg.weight / (k + rank)
            )
            payloads.setdefault(hit.point_id, hit.payload)
            evidence.setdefault(hit.point_id, []).append(
                ChannelEvidence(
                    chunk_id=(
                        hit.payload.get("chunk_id")
                        if isinstance(hit.payload.get("chunk_id"), str)
                        else hit.point_id
                    ),
                    subquery_id=result.leg.subquery_id,
                    retrieval_leg=result.leg.retrieval_leg,
                    variant_index=result.leg.variant_index,
                    variant_kind=result.leg.variant_kind,
                    channel=result.leg.channel,
                    rank=rank,
                    raw_score=hit.score,
                    weight=result.leg.weight,
                )
            )
    fused = [
        FusedSearchHit(
            point_id=point_id,
            fusion_score=score,
            payload=payloads[point_id],
            evidence=sorted(
                evidence[point_id],
                key=lambda item: (item.variant_index, item.channel.value, item.rank),
            ),
        )
        for point_id, score in scores.items()
    ]
    return sorted(fused, key=lambda item: (-item.fusion_score, item.point_id))


def failed_channels(results: list[LegSearchResult]) -> list[SearchChannel]:
    return list(
        dict.fromkeys(
            result.leg.channel
            for result in results
            if result.error_code is not None
        )
    )
