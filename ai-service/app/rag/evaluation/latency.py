"""Deterministic percentile summaries for persisted RAG stage timings."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from app.rag.evaluation.models import RAGEvaluationTrace


STAGE_TIMING_KEYS = {
    "retrieval": "retrieval_total",
    "rerank": "rerank_total",
    "context_adapter": "context_adapter_total",
    "generation": "generation_total",
}


class LatencyPercentiles(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sample_count: int = Field(ge=0)
    p50_ms: float | None = Field(default=None, ge=0)
    p95_ms: float | None = Field(default=None, ge=0)
    p99_ms: float | None = Field(default=None, ge=0)


class PipelineLatencyReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    retrieval: LatencyPercentiles
    rerank: LatencyPercentiles
    context_adapter: LatencyPercentiles
    generation: LatencyPercentiles


def summarize_latencies(values: Sequence[float]) -> LatencyPercentiles:
    ordered = sorted(float(value) for value in values)
    if any(not math.isfinite(value) or value < 0 for value in ordered):
        raise ValueError("latency samples must be finite and nonnegative")
    if not ordered:
        return LatencyPercentiles(sample_count=0)
    return LatencyPercentiles(
        sample_count=len(ordered),
        p50_ms=_percentile(ordered, 0.50),
        p95_ms=_percentile(ordered, 0.95),
        p99_ms=_percentile(ordered, 0.99),
    )


def build_pipeline_latency_report(
    traces: Sequence[RAGEvaluationTrace],
) -> PipelineLatencyReport:
    summaries = {
        stage: summarize_latencies(
            [
                trace.timings_ms[timing_key]
                for trace in traces
                if timing_key in trace.timings_ms
            ]
        )
        for stage, timing_key in STAGE_TIMING_KEYS.items()
    }
    return PipelineLatencyReport.model_validate(summaries)


def summarize_named_latencies(
    values: Mapping[str, Sequence[float]],
) -> dict[str, LatencyPercentiles]:
    return {name: summarize_latencies(samples) for name, samples in values.items()}


def _percentile(ordered: Sequence[float], percentile: float) -> float:
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction
