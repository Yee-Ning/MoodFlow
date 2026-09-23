"""Privacy-conscious decision metrics on the existing application logger."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from .client import DecisionResponse

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
