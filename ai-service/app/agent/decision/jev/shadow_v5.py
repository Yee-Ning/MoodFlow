"""Bounded, non-blocking, privacy-preserving V5 Router shadow runner."""

from __future__ import annotations

import asyncio
import json
import logging
from collections import Counter, deque
from datetime import UTC, datetime
from typing import Awaitable

from app.agent.foundation.routing import RouteDecision, RoutingContext

from .router_v5 import JevRouterV5, RouterTraceV5

logger = logging.getLogger(__name__)
_pending: set[asyncio.Task[None]] = set()
_max_pending = 32
_counts: Counter[str] = Counter()
_latencies: deque[float] = deque(maxlen=10_000)
_tokens: deque[int] = deque(maxlen=10_000)


def _routes(decision: RouteDecision) -> list[str]:
    return sorted({task.route_key.value for task in decision.tasks})


def _record(baseline: RouteDecision, trace: RouterTraceV5, *, sample_kind: str = "synthetic") -> None:
    decision = trace.decision
    comparable = trace.router_source != "legacy_hybrid_fallback"
    event = {
        "timestamp": datetime.now(UTC).isoformat(),
        "decision_type": "router_v5_shadow",
        "sample_kind": sample_kind,
        "model_version": trace.response_model,
        "router_source": trace.router_source,
        "baseline_routes": _routes(baseline),
        "candidate_routes": _routes(decision),
        "baseline_task_count": len(baseline.tasks),
        "candidate_task_count": len(decision.tasks),
        "route_agreement": _routes(baseline) == _routes(decision) if comparable else None,
        "task_count_agreement": len(baseline.tasks) == len(decision.tasks) if comparable else None,
        "clarify_disagreement": (
            baseline.needs_clarification != decision.needs_clarification if comparable else None
        ),
        "candidate_clarify": decision.needs_clarification,
        "dependency_edge_count": sum(len(task.depends_on) for task in decision.tasks),
        "confidence": trace.confidence,
        "route_probabilities": trace.scores,
        "jev_latency_ms": trace.jev_latency_ms,
        "orchestration_latency_ms": trace.orchestration_latency_ms,
        "fallback_latency_ms": trace.fallback_latency_ms,
        "total_router_latency_ms": trace.total_router_latency_ms,
        "api_calls": trace.api_calls,
        "usage": trace.usage,
        "classifier_hot_path_calls": trace.classifier_hot_path_calls,
        "classifier_fallback_calls": trace.classifier_fallback_calls,
        "fallback_reason": trace.fallback_reason,
        "candidate_subgraph_dispatch_count": 0,
        "candidate_tool_action_dispatch_count": 0,
    }
    _counts["total_requests"] += 1
    _counts[f"sample_kind:{sample_kind}"] += 1
    _counts[trace.router_source] += 1
    _counts["comparable"] += int(comparable)
    _counts["agreement"] += int(event["route_agreement"] is True)
    _counts["fallback"] += int(trace.fallback_reason is not None)
    _counts["timeout"] += int(trace.fallback_reason == "timeout")
    _counts["clarify"] += int(decision.needs_clarification)
    if comparable:
        for route in event["candidate_routes"]:
            _counts[f"route:{route}"] += 1
    _latencies.append(trace.total_router_latency_ms)
    _tokens.append(sum(value for value in trace.usage.values() if isinstance(value, int)))
    logger.info("jev.v5_shadow %s", json.dumps(event, ensure_ascii=False, separators=(",", ":")))


def _record_failure(error_type: str, *, sample_kind: str = "production") -> None:
    _counts["shadow_failure"] += 1
    _counts[f"shadow_failure:{sample_kind}"] += 1
    logger.warning("jev.v5_shadow_failure %s", json.dumps({
        "decision_type": "router_v5_shadow", "exception_type": error_type,
        "sample_kind": sample_kind,
    }, separators=(",", ":")))


async def _run(
    candidate: JevRouterV5, query: str, context: RoutingContext,
    baseline_task: Awaitable[RouteDecision],
    sample_kind: str,
) -> None:
    try:
        trace = await candidate.route_with_trace(
            query, context, fallback_decision=baseline_task,
        )
        baseline = await baseline_task
        _record(baseline, trace, sample_kind=sample_kind)
    except Exception as exc:
        _record_failure(type(exc).__name__, sample_kind=sample_kind)


def schedule_shadow(
    candidate: JevRouterV5, query: str, context: RoutingContext,
    baseline_task: Awaitable[RouteDecision],
    *, sample_kind: str = "production",
) -> bool:
    """Dispatch candidate computation without delaying or dispatching its tasks."""
    if sample_kind not in {"production", "acceptance", "synthetic"}:
        sample_kind = "production"
    if len(_pending) >= _max_pending:
        _counts["dropped"] += 1
        _counts[f"dropped:{sample_kind}"] += 1
        logger.warning("jev.v5_shadow_dropped capacity sample_kind=%s", sample_kind)
        return False
    task = asyncio.create_task(_run(candidate, query, context, baseline_task, sample_kind))
    _pending.add(task)
    task.add_done_callback(_pending.discard)
    return True


def snapshot() -> dict[str, object]:
    """Process-local anonymous window, excluding raw query and task fragments."""
    values = sorted(_latencies)

    def percentile(p: float) -> float | None:
        if not values:
            return None
        at = (len(values) - 1) * p
        low = int(at)
        high = min(low + 1, len(values) - 1)
        return round(values[low] + (values[high] - values[low]) * (at - low), 2)

    total = _counts["total_requests"]
    return {
        "sample_size": total,
        "counts": dict(_counts),
        "jev_single_rate": _counts["jev_single"] / total if total else None,
        "jev_multi_rate": _counts["jev_multi"] / total if total else None,
        "agreement_rate": (
            _counts["agreement"] / _counts["comparable"] if _counts["comparable"] else None
        ),
        "fallback_rate": _counts["fallback"] / total if total else None,
        "clarify_rate": _counts["clarify"] / total if total else None,
        "p50_ms": percentile(.5), "p95_ms": percentile(.95), "p99_ms": percentile(.99),
        "tokens_per_request": sum(_tokens) / total if total else None,
        "pending": len(_pending),
        "candidate_subgraph_dispatch_count": 0,
        "candidate_tool_action_dispatch_count": 0,
    }
