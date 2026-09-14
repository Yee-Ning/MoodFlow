"""Bounded asynchronous reranking with observable RRF-order fallback."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Sequence
from time import perf_counter
from typing import Protocol

from app.rag.query_understanding.models import SearchPlan
from app.rag.retrieval.models import RetrievalResult, RetrievalStatus

from .config import RerankConfig, load_rerank_config
from .input_builder import build_rerank_inputs
from .models import (
    RerankInput,
    RerankedCandidate,
    RerankResult,
    RerankStatus,
    RerankTimings,
)
from .runtime import QwenRerankScorer, RerankScorer


class CandidateReranker(Protocol):
    async def rerank(
        self,
        plan: SearchPlan,
        candidate_pool: RetrievalResult,
    ) -> RerankResult: ...


class QwenCandidateReranker:
    def __init__(
        self,
        config: RerankConfig | None = None,
        *,
        scorer: RerankScorer | None = None,
    ) -> None:
        self.config = config or load_rerank_config()
        self.scorer = scorer or QwenRerankScorer(self.config.model)
        self._inference_slots = asyncio.Semaphore(self.config.maximum_concurrency)

    async def rerank(
        self,
        plan: SearchPlan,
        candidate_pool: RetrievalResult,
    ) -> RerankResult:
        started_at = perf_counter()
        if candidate_pool.status in {
            RetrievalStatus.EMPTY,
            RetrievalStatus.UNAVAILABLE,
        }:
            status = (
                RerankStatus.EMPTY
                if candidate_pool.status is RetrievalStatus.EMPTY
                else RerankStatus.UNAVAILABLE
            )
            return self._result(status, candidate_pool, started_at)
        if len(candidate_pool.candidates) > self.config.maximum_candidates:
            return self._result(
                RerankStatus.UNAVAILABLE,
                candidate_pool,
                started_at,
                warnings=["rerank_candidate_pool_too_large"],
            )
        if (
            candidate_pool.plan_id != plan.plan_id
            or candidate_pool.index_profile != plan.index_profile
        ):
            return self._result(
                RerankStatus.UNAVAILABLE,
                candidate_pool,
                started_at,
                warnings=["rerank_binding_mismatch"],
            )
        try:
            inputs = build_rerank_inputs(plan, candidate_pool, self.config)
        except (TypeError, ValueError):
            return self._fallback(
                candidate_pool,
                started_at,
                warning="rerank_input_invalid",
            )

        inference_started = perf_counter()
        task = asyncio.create_task(self._score_with_slot(inputs))
        try:
            scores = await asyncio.wait_for(
                asyncio.shield(task),
                timeout=self.config.timeout_seconds,
            )
        except TimeoutError:
            task.add_done_callback(_consume_background_result)
            return self._fallback(
                candidate_pool,
                started_at,
                warning="rerank_timeout",
                inference_ms=(perf_counter() - inference_started) * 1_000,
            )
        except Exception:
            return self._fallback(
                candidate_pool,
                started_at,
                warning="rerank_inference_failed",
                inference_ms=(perf_counter() - inference_started) * 1_000,
            )
        inference_ms = (perf_counter() - inference_started) * 1_000
        if not _valid_scores(scores, len(candidate_pool.candidates)):
            return self._fallback(
                candidate_pool,
                started_at,
                warning="rerank_scores_invalid",
                inference_ms=inference_ms,
            )
        scored = [
            (candidate, retrieval_rank, score)
            for retrieval_rank, (candidate, score) in enumerate(
                zip(candidate_pool.candidates, scores, strict=True),
                start=1,
            )
        ]
        ranked = sorted(
            scored,
            key=lambda item: (
                -item[2],
                -item[0].fusion_score,
                item[0].point_id,
            ),
        )[: self.config.top_k]
        candidates = [
            RerankedCandidate(
                candidate=candidate,
                retrieval_rank=retrieval_rank,
                rerank_rank=rerank_rank,
                rerank_score=score,
            )
            for rerank_rank, (candidate, retrieval_rank, score) in enumerate(
                ranked,
                start=1,
            )
        ]
        return self._result(
            RerankStatus.SUCCESS,
            candidate_pool,
            started_at,
            candidates=candidates,
            inference_ms=inference_ms,
        )

    async def _score_with_slot(self, inputs: Sequence[RerankInput]) -> list[float]:
        async with self._inference_slots:
            return await asyncio.to_thread(self.scorer.score, inputs)

    def _fallback(
        self,
        candidate_pool: RetrievalResult,
        started_at: float,
        *,
        warning: str,
        inference_ms: float = 0,
    ) -> RerankResult:
        candidates = [
            RerankedCandidate(
                candidate=candidate,
                retrieval_rank=rank,
                rerank_rank=rank,
                rerank_score=None,
            )
            for rank, candidate in enumerate(
                candidate_pool.candidates[: self.config.top_k],
                start=1,
            )
        ]
        return self._result(
            RerankStatus.DEGRADED,
            candidate_pool,
            started_at,
            candidates=candidates,
            fallback_used=True,
            warnings=[warning],
            inference_ms=inference_ms,
        )

    def _result(
        self,
        status: RerankStatus,
        candidate_pool: RetrievalResult,
        started_at: float,
        *,
        candidates: Sequence[RerankedCandidate] = (),
        fallback_used: bool = False,
        warnings: Sequence[str] = (),
        inference_ms: float = 0,
    ) -> RerankResult:
        return RerankResult(
            status=status,
            plan_id=candidate_pool.plan_id,
            index_profile=candidate_pool.index_profile,
            model_name=self.config.model.name,
            model_revision=self.config.model.revision,
            rerank_version=self.config.rerank_version,
            prompt_version=self.config.prompt_version,
            candidate_pool_status=candidate_pool.status,
            candidate_pool_warnings=candidate_pool.warnings,
            input_count=len(candidate_pool.candidates),
            output_top_k=self.config.top_k,
            candidates=list(candidates),
            fallback_used=fallback_used,
            warnings=list(warnings),
            timings=RerankTimings(
                inference_ms=inference_ms,
                total_ms=(perf_counter() - started_at) * 1_000,
            ),
        )


def _valid_scores(scores: Sequence[float], expected: int) -> bool:
    return len(scores) == expected and all(
        isinstance(score, (float, int))
        and math.isfinite(score)
        and 0 <= score <= 1
        for score in scores
    )


def _consume_background_result(task: asyncio.Task[list[float]]) -> None:
    if task.cancelled():
        return
    try:
        task.exception()
    except asyncio.CancelledError:
        return
