"""Choice-based shadow router with conservative single-task live takeover."""

from __future__ import annotations

from time import perf_counter
from uuid import uuid4

from typesafe_sdk import Choice

from app.agent.foundation.routing import RouteDecision, RouteKey, RouteTask
from app.config.settings import Settings

from .client import DecisionClient, JevDecisionError
from .capability_criteria import ROUTER_QUESTION_INSTRUCTIONS, router_criteria
from .telemetry import record_decision


async def compare_route(text: str, existing: RouteDecision, *,
                        settings: Settings, client: DecisionClient,
                        trace_id: str | None = None) -> RouteDecision:
    """Preserve multi-intent/clarification decisions; use Jev only for simple routing."""
    baseline = (
        existing.tasks[0].route_key
        if existing.is_complete and len(existing.tasks) == 1
        and existing.tasks[0].input_text.strip() == text.strip()
        and not existing.tasks[0].depends_on
        else None
    )
    response = None
    reason: str | None = None
    proposed: RouteKey | None = None
    confidence: float | None = None
    started = perf_counter()
    try:
        response = await client.decide(
            {"query": text[:4000]},
            {"route": Choice(
                instructions=ROUTER_QUESTION_INSTRUCTIONS,
                criteria=router_criteria(),
            )},
        )
        answer = response.answers["route"]
        proposed = RouteKey(answer.choice) if answer.kind == "choice" else None
        confidence = answer.confidence
        if proposed is None or confidence is None or not 0 <= confidence <= 1:
            reason = "invalid_response"
    except (JevDecisionError, KeyError, ValueError, TypeError, AttributeError, OSError) as exc:
        reason = str(exc) if isinstance(exc, JevDecisionError) else type(exc).__name__
    if baseline is None:
        reason = reason or "complex_existing_route"
    elif confidence is not None and confidence < settings.jev_router_confidence_threshold:
        reason = reason or "low_confidence"
    if settings.jev_shadow_mode:
        reason = reason or "shadow_mode"
    selected = existing
    if reason is None and proposed is not None and proposed != baseline:
        selected = RouteDecision(
            is_complete=True, coverage=1.0,
            tasks=[RouteTask(task_id=f"{uuid4().hex}:{proposed.value}",
                             route_key=proposed, input_text=text)],
        )
    record_decision(
        "router", response, candidate_count=len(RouteKey), selected_count=1 if proposed else 0,
        confidence=confidence, fallback_reason=reason,
        agreement=(proposed == baseline) if proposed is not None and baseline is not None else None,
        trace_id=trace_id,
        scores=response.answers["route"].probabilities if response and proposed else None,
        existing_route=baseline.value if baseline else None,
        jev_route=proposed.value if proposed else None,
        applied_count=len(selected.tasks),
        requested_model=settings.jev_model,
        latency_ms=round((perf_counter() - started) * 1000, 2),
    )
    return selected
