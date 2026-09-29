"""Privacy-conscious decision metrics on the existing application logger."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.agent.foundation.routing import RouteDecision

from .client import DecisionResponse

if TYPE_CHECKING:
    from .router_v4 import CandidateTrace

logger = logging.getLogger(__name__)


def record_decision(decision_type: str, response: DecisionResponse | None, *,
                    candidate_count: int, selected_count: int,
                    confidence: float | None = None, fallback_reason: str | None = None,
                    agreement: bool | None = None, trace_id: str | None = None,
                    scores: dict[str, float] | None = None,
                    existing_route: str | None = None, jev_route: str | None = None,
                    applied_count: int | None = None,
                    selected_ids: list[str] | None = None,
                    required_preserved: bool | None = None,
                    requested_model: str | None = None,
                    latency_ms: float | None = None) -> None:
    event = {
        "decision_type": decision_type,
        "provider": "typesafe",
        "requested_model": response.requested_model if response else requested_model,
        "response_model": response.response_model if response else None,
        "evaluation_timestamp": response.evaluation_timestamp if response else datetime.now(UTC).isoformat(),
        "latency_ms": response.latency_ms if response else latency_ms,
        "confidence": confidence,
        "candidate_count": candidate_count,
        "selected_count": selected_count,
        "applied_count": applied_count,
        "fallback_used": fallback_reason is not None,
        "fallback_reason": fallback_reason,
        "agreement": agreement,
        "existing_route": existing_route,
        "jev_route": jev_route,
        "trace_id": trace_id,
        "scores": scores,
        "selected_ids": selected_ids,
        "required_preserved": required_preserved,
        "usage": response.usage if response else None,
    }
    logger.info("jev.decision %s", json.dumps(event, ensure_ascii=False, separators=(",", ":")))


def record_v4_shadow(baseline: RouteDecision, candidate: CandidateTrace) -> None:
    """Record aggregate decisions only: no query, fragment, secret or user identifier."""
    proposed = candidate.decision
    event = {
        "decision_type": "router_v4_shadow",
        "router_source": candidate.router_source,
        "fallback_reason": candidate.fallback_reason,
        "response_model": candidate.response_model,
        "latency_ms": candidate.latency_ms,
        "api_calls": candidate.api_calls,
        "usage": candidate.usage,
        "scores": candidate.scores,
        "confidence": candidate.confidence,
        "baseline_routes": [task.route_key.value for task in baseline.tasks],
        "candidate_routes": [task.route_key.value for task in proposed.tasks],
        "baseline_task_count": len(baseline.tasks),
        "candidate_task_count": len(proposed.tasks),
        "baseline_dependency_count": sum(len(task.depends_on) for task in baseline.tasks),
        "candidate_dependency_count": sum(len(task.depends_on) for task in proposed.tasks),
        "baseline_clarify": baseline.needs_clarification,
        "candidate_clarify": proposed.needs_clarification,
        "route_agreement": {task.route_key for task in baseline.tasks}
        == {task.route_key for task in proposed.tasks},
    }
    logger.info("jev.v4_shadow %s", json.dumps(event, ensure_ascii=False, separators=(",", ":")))
