"""Orchestrate online hybrid retrieval and stop at the candidate pool."""

from __future__ import annotations

import asyncio
from time import perf_counter

from app.rag.query_understanding.models import (
    FilterCondition,
    FilterOperator,
    MetadataField,
    QueryType,
    SearchChannel,
    SearchPlan,
)

from .binding import RetrievalIndexBinding
from .config import RetrievalConfig, load_retrieval_config
from .encoder import QueryEncoder, QwenQueryEncoder
from .filter_compiler import compile_qdrant_filter
from .fusion import failed_channels, weighted_rrf
from .legs import build_retrieval_legs, dense_variant_indexes
from .models import (
    RetrievalResult,
    RetrievalStatus,
    RetrievalTimings,
)
from .qdrant import HybridSearchBackend, QdrantHybridSearchBackend
from .selection import select_candidates


class HybridRetrievalService:
    def __init__(
        self,
        binding: RetrievalIndexBinding,
        *,
        config: RetrievalConfig | None = None,
        encoder: QueryEncoder | None = None,
        backend: HybridSearchBackend | None = None,
    ) -> None:
        self.binding = binding
        self.config = config or load_retrieval_config()
        self.encoder = encoder or QwenQueryEncoder(self.config.dense)
        self.backend = backend or QdrantHybridSearchBackend(self.config)
        self._binding_validated = False
        self._validation_lock = asyncio.Lock()

    async def retrieve(self, plan: SearchPlan) -> RetrievalResult:
        started_at = perf_counter()
        if plan.index_profile is None:
            return self._result(
                plan,
                RetrievalStatus.UNAVAILABLE,
                started_at,
                warnings=["retrieval_index_profile_missing"],
            )
        if plan.index_profile != self.binding.profile:
            return self._result(
                plan,
                RetrievalStatus.UNAVAILABLE,
                started_at,
                warnings=["retrieval_index_profile_mismatch"],
            )
        try:
            await self._validate_binding_once()
        except Exception:
            return self._result(
                plan,
                RetrievalStatus.UNAVAILABLE,
                started_at,
                warnings=["retrieval_index_validation_failed"],
            )

        warnings: list[str] = []
        failed: list[SearchChannel] = []
        dense_indexes = dense_variant_indexes(plan)
        dense_vectors: dict[int, list[float]] = {}
        embedding_started = perf_counter()
        if dense_indexes:
            texts = [plan.queries[index].text for index in dense_indexes]
            try:
                vectors = await asyncio.to_thread(self.encoder.encode_queries, texts)
                dense_vectors = dict(zip(dense_indexes, vectors, strict=True))
            except Exception:
                warnings.append("dense_query_encoding_failed")
                failed.append(SearchChannel.DENSE)
        embedding_ms = (perf_counter() - embedding_started) * 1_000

        try:
            legs = build_retrieval_legs(plan, dense_vectors, self.config)
        except ValueError:
            return self._result(
                plan,
                RetrievalStatus.UNAVAILABLE,
                started_at,
                warnings=[*warnings, "retrieval_plan_has_no_available_channel"],
                failed_channels=failed,
                embedding_ms=embedding_ms,
            )
        query_filter = compile_qdrant_filter(plan.metadata_filter)
        fused_limit = min(
            plan.candidate_top_k * self.config.fusion.overfetch_multiplier,
            self.config.selection.maximum_candidates
            * self.config.fusion.overfetch_multiplier,
        )
        per_leg_limit = max(self.config.fusion.per_leg_top_k, fused_limit)
        query_started = perf_counter()
        try:
            results = await self.backend.search(
                self.binding,
                legs,
                query_filter,
                limit=per_leg_limit,
            )
        except Exception:
            qdrant_ms = (perf_counter() - query_started) * 1_000
            return self._result(
                plan,
                RetrievalStatus.UNAVAILABLE,
                started_at,
                warnings=[*warnings, "qdrant_query_failed"],
                failed_channels=[*failed, *(item.channel for item in legs)],
                embedding_ms=embedding_ms,
                qdrant_ms=qdrant_ms,
            )
        qdrant_ms = (perf_counter() - query_started) * 1_000
        failed.extend(failed_channels(results))
        failed = list(dict.fromkeys(failed))
        for channel in failed_channels(results):
            warnings.append(f"{channel.value}_retrieval_degraded")
        if results and all(item.error_code is not None for item in results):
            return self._result(
                plan,
                RetrievalStatus.UNAVAILABLE,
                started_at,
                warnings=[*warnings, "all_retrieval_channels_failed"],
                failed_channels=failed,
                embedding_ms=embedding_ms,
                qdrant_ms=qdrant_ms,
            )

        processing_started = perf_counter()
        fused = weighted_rrf(results, k=self.config.fusion.rrf_k)[:fused_limit]
        candidates, selection_warnings = select_candidates(
            fused,
            plan,
            self.config,
        )
        warnings.extend(selection_warnings)
        processing_ms = (perf_counter() - processing_started) * 1_000
        if _needs_exploratory_entity_fallback(plan, len(candidates), self.config):
            fallback_started = perf_counter()
            try:
                relaxed_results = await self.backend.search(
                    self.binding,
                    legs,
                    None,
                    limit=per_leg_limit,
                )
            except Exception:
                qdrant_ms += (perf_counter() - fallback_started) * 1_000
                warnings.append("exploratory_entity_fallback_failed")
            else:
                qdrant_ms += (perf_counter() - fallback_started) * 1_000
                fallback_processing_started = perf_counter()
                if any(item.error_code is None for item in relaxed_results):
                    relaxed_fused = weighted_rrf(
                        relaxed_results, k=self.config.fusion.rrf_k
                    )[:fused_limit]
                    relaxed_candidates, relaxed_warnings = select_candidates(
                        relaxed_fused, plan, self.config
                    )
                    seen = {item.parent_id for item in candidates}
                    for item in relaxed_candidates:
                        if len(candidates) >= min(
                            plan.candidate_top_k,
                            self.config.selection.maximum_candidates,
                        ):
                            break
                        if item.parent_id not in seen:
                            candidates.append(item)
                            seen.add(item.parent_id)
                    warnings.extend(relaxed_warnings)
                    warnings.append("exploratory_entity_filter_relaxed_for_recall")
                else:
                    warnings.append("exploratory_entity_fallback_failed")
                processing_ms += (perf_counter() - fallback_processing_started) * 1_000
        if candidates:
            status = RetrievalStatus.DEGRADED if failed else RetrievalStatus.SUCCESS
        else:
            status = RetrievalStatus.DEGRADED if failed else RetrievalStatus.EMPTY
        return self._result(
            plan,
            status,
            started_at,
            candidates=candidates,
            warnings=warnings,
            failed_channels=failed,
            embedding_ms=embedding_ms,
            qdrant_ms=qdrant_ms,
            processing_ms=processing_ms,
        )

    async def _validate_binding_once(self) -> None:
        if self._binding_validated:
            return
        async with self._validation_lock:
            if not self._binding_validated:
                await self.backend.validate_binding(self.binding)
                self._binding_validated = True

    def _result(
        self,
        plan: SearchPlan,
        status: RetrievalStatus,
        started_at: float,
        *,
        candidates=None,
        warnings=None,
        failed_channels=None,
        embedding_ms: float = 0,
        qdrant_ms: float = 0,
        processing_ms: float = 0,
    ) -> RetrievalResult:
        return RetrievalResult(
            status=status,
            plan_id=plan.plan_id,
            index_profile=self.binding.profile,
            candidates=list(candidates or []),
            warnings=list(dict.fromkeys(warnings or [])),
            failed_channels=list(dict.fromkeys(failed_channels or [])),
            timings=RetrievalTimings(
                embedding_ms=embedding_ms,
                qdrant_ms=qdrant_ms,
                processing_ms=processing_ms,
                total_ms=(perf_counter() - started_at) * 1_000,
            ),
        )


def _needs_exploratory_entity_fallback(
    plan: SearchPlan,
    candidate_count: int,
    config: RetrievalConfig,
) -> bool:
    threshold = config.selection.exploratory_entity_fallback_below
    if (
        threshold == 0
        or candidate_count >= min(threshold, plan.candidate_top_k)
        or QueryType.EXPLORATORY not in plan.query_types
    ):
        return False
    root = plan.metadata_filter.root
    if root is None or root.operator != "all" or len(root.children) != 1:
        return False
    only = root.children[0]
    return (
        isinstance(only, FilterCondition)
        and only.field is MetadataField.ENTITY_IDS
        and only.operator is FilterOperator.MATCH_ANY
        and isinstance(only.value, list)
        and len(only.value) == 1
    )
