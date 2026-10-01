"""Optional V5 entry triage; Shadow never dispatches the candidate plan."""

import json
import logging

from app.agent.decision.jev.client import JevClient
from app.agent.decision.jev.router_v5 import JevRouterV5
from app.agent.foundation.routing import RouteDecision
from app.config.settings import get_settings, jev_requested
from app.llm.runtime import RuntimeLimitError

logger = logging.getLogger(__name__)


def plan_signature(plan):
    ids = {task.task_id: index for index, task in enumerate(plan.tasks)}
    return (
        plan.is_complete,
        tuple(
            (
                task.route_key,
                task.input_text,
                tuple(ids[item] for item in task.depends_on),
            )
            for task in plan.tasks
        ),
    )


class HandoffEntryRouter:
    def __init__(self, baseline, client, *, shadow=True, confidence_threshold=0.8):
        self.baseline = baseline
        self.candidate = JevRouterV5(
            client, fallback_router=baseline, confidence_threshold=confidence_threshold
        )
        self.shadow = shadow

    async def route(self, text, context):
        # Awaited inside the parent ledger: observation remains subject to the
        # same budget/deadline and cannot leave unaccounted background requests.
        baseline = await self.baseline.route(text, context) if self.shadow else None
        try:
            trace = await self.candidate.route_with_trace(
                text, context, fallback_decision=baseline
            )
        except RuntimeLimitError:
            raise
        except Exception as exc:
            logger.warning(
                "jev.handoff_entry_failure error_type=%s", type(exc).__name__
            )
            if baseline is not None:
                return baseline
            raise
        logger.info(
            "jev.handoff_entry %s",
            json.dumps(
                {
                    "shadow": self.shadow,
                    "router_source": trace.router_source,
                    "fallback_reason": trace.fallback_reason,
                    "response_model": trace.response_model,
                    "latency_ms": trace.total_router_latency_ms,
                    "api_calls": trace.api_calls,
                    "usage": trace.usage,
                    "candidate_task_count": len(trace.decision.tasks),
                    "plan_agreement": plan_signature(baseline)
                    == plan_signature(trace.decision)
                    if baseline is not None
                    and trace.router_source != "legacy_hybrid_fallback"
                    else None,
                },
                separators=(",", ":"),
            ),
        )
        decision = baseline if self.shadow else trace.decision
        return RouteDecision.model_validate(decision.model_dump())


def configured_entry_router(baseline):
    if not jev_requested():
        return baseline
    settings = get_settings()
    if not settings.jev_handoff_router_enabled:
        return baseline
    return HandoffEntryRouter(
        baseline,
        JevClient(settings),
        shadow=settings.jev_handoff_router_shadow_mode,
        confidence_threshold=settings.jev_router_confidence_threshold,
    )
