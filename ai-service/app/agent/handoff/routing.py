"""Local preflight and rules precede optional V5 triage and Hybrid fallback."""

import logging
import re
from time import perf_counter

from app.agent.decision.jev.client import JevClient
from app.agent.decision.jev.router_v5 import JevRouterV5, RuleGuard
from app.agent.decision.jev.task_orchestrator_v5 import (
    IntentPlan,
    TaskOrchestrator,
    clarify,
)
from app.agent.foundation.routing import IntentSource, RouteDecision, RouteKey
from app.agent.foundation.rule_routing.rule_router import RuleRouter
from app.config.settings import get_settings, jev_requested
from app.llm.runtime import RuntimeLimitError
from app.agent.handoff.observability import emit, record_fallback

logger = logging.getLogger(__name__)
NO_PLAN_QUESTION = "暂时无法确定可执行的音乐或聊天任务，请说明目标或把请求拆成具体步骤。"
# Preflight strips clause syntax; Legacy Hybrid may retain it in evidence.
# Permit only that prefix, never text from an excluded or preceding action.
_HYBRID_CLAUSE_PREFIX = re.compile(
    r"(?:[ \t\r\n,，。.!?！？;；、]|然后|接着|随后|顺便|另外|同时|以及|并且|并|最后|再)+"
)


def hybrid_span_is_grounded(text, item, clauses):
    for clause in clauses:
        if not clause.start < item.end <= clause.end:
            continue
        if clause.start <= item.start < item.end:
            return True
        if _HYBRID_CLAUSE_PREFIX.fullmatch(text[item.start:clause.start]):
            return True
    return False


def input_question(text):
    if not isinstance(text, str) or not text.strip():
        return "请告诉我你希望完成的音乐或聊天任务。"
    if len(text) > 4_000:
        return "请求过长，请缩短到 4000 字以内，或拆成具体步骤。"
    return None


class LazyHybridRouter:
    """Do not load the classifier until a rule miss actually needs Hybrid."""

    def __init__(self, factory):
        self.factory = factory
        self.router = None

    async def route(self, text, context):
        if self.router is None:
            self.router = self.factory()
        return await self.router.route(text, context)


class HybridFallback:
    def __init__(self, router):
        self.router = router

    async def route(self, text, context):
        try:
            decision = RouteDecision.model_validate(
                (await self.router.route(text, context)).model_dump()
            )
            if decision.is_complete and decision.tasks:
                clauses = RuleGuard().inspect(text)
                # Legacy rules do not recognize every negation prefix supported
                # by preflight. Never reintroduce an excluded fragment on fallback.
                if any(
                    text[item.start:item.end] != item.evidence
                    or not hybrid_span_is_grounded(text, item, clauses)
                    for item in decision.intents
                ) or any(
                    not task.intent_orders and (
                        task.input_text != text or len(clauses) != 1
                        or clauses[0].text != text.strip()
                    )
                    for task in decision.tasks
                ):
                    raise ValueError("ungrounded_hybrid_plan")
                return decision
            return clarify(decision.clarification_question or NO_PLAN_QUESTION)
        except RuntimeLimitError:
            raise
        except Exception as exc:
            logger.warning(
                "jev.handoff_hybrid_failure error_type=%s", type(exc).__name__
            )
            return clarify(NO_PLAN_QUESTION)


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
    def __init__(self, baseline, client=None, *, shadow=True, confidence_threshold=0.8):
        self.baseline = baseline
        self.fallback = HybridFallback(baseline)
        self.candidate = (
            JevRouterV5(
                client, fallback_router=self.fallback,
                confidence_threshold=confidence_threshold,
            ) if client is not None else None
        )
        self.shadow = shadow
        self.preflight = RuleGuard()
        self.rules = RuleRouter()
        self.orchestrator = TaskOrchestrator()

    def rule_plan(self, text, context, clauses):
        matches = [self.rules.route(clause.text, context) for clause in clauses]
        # A partial match must not discard the rest of the user's request; two
        # matches in one clause may hide two actions even with the same route.
        if any(not match.is_complete or len(match.intents) != 1 for match in matches):
            return None
        routes = tuple(RouteKey(match.intents[0].intent.value) for match in matches)
        decision = self.orchestrator.build(IntentPlan(
            query=text, clauses=clauses, routes=routes,
            confidence=tuple(match.intents[0].confidence for match in matches),
            selected_routes=frozenset(routes), context=context,
        ))
        data = decision.model_dump()
        for intent, match in zip(data["intents"], matches, strict=False):
            intent.update(source=IntentSource.RULE, sub_intent=match.intents[0].sub_intent)
        return RouteDecision.model_validate(data)

    async def route(self, text, context):
        started = perf_counter()
        question = input_question(text)
        if question:
            emit("jev.handoff_entry_local", {"decision_class": "clarification", "v5_attempted": False})
            return clarify(question)
        clauses = self.preflight.inspect(text)
        attempt_class = "Single" if len(clauses) == 1 else "Multi"
        question = self.preflight.preflight_question(clauses)
        if question or not clauses:
            emit("jev.handoff_entry_local", {"decision_class": "clarification", "v5_attempted": False,
                                             "attempt_class": attempt_class})
            return clarify(question or "请说明希望执行的任务；已否定的操作不会执行。")
        decision = self.rule_plan(text, context, clauses)
        if decision is not None:
            emit("jev.handoff_entry_local", {"decision_class": "rule", "attempt_class": attempt_class,
                 "source": "rule", "tasks": len(decision.tasks), "v5_attempted": False,
                 "latency_ms": round((perf_counter() - started) * 1000, 2)})
            return decision
        if self.candidate is None:
            emit("jev.handoff_entry_local", {"decision_class": "hybrid", "attempt_class": attempt_class,
                                             "v5_attempted": False})
            return await self.fallback.route(text, context)
        # Awaited inside the parent ledger: observation remains subject to the
        # same budget/deadline and cannot leave unaccounted background requests.
        baseline = await self.fallback.route(text, context) if self.shadow else None
        try:
            trace = await self.candidate.route_with_trace(
                text, context, fallback_decision=baseline
            )
        except RuntimeLimitError as exc:
            emit("jev.handoff_entry", {"decision_class": "error", "attempt_class": attempt_class,
                 "v5_attempted": True, "fallback_reason": exc.reason,
                 "latency_ms": round((perf_counter() - started) * 1000, 2)}, warning=True)
            raise
        except Exception as exc:
            logger.warning(
                "jev.handoff_entry_failure error_type=%s", type(exc).__name__
            )
            fields = {"decision_class": "fallback", "attempt_class": attempt_class,
                      "v5_attempted": True, "router_source": "legacy_hybrid_fallback",
                      "fallback_reason": "entry_exception", "error_type": type(exc).__name__,
                      "latency_ms": round((perf_counter() - started) * 1000, 2)}
            emit("jev.handoff_entry", fields)
            record_fallback(fields)
            if baseline is not None:
                return baseline
            return await self.fallback.route(text, context)
        fields = {
                    "shadow": self.shadow,
                    "decision_class": {"jev_single": "Single", "jev_multi": "Multi",
                                       "legacy_hybrid_fallback": "fallback"}[trace.router_source],
                    "attempt_class": attempt_class,
                    "v5_attempted": True,
                    "router_source": trace.router_source,
                    "fallback_reason": trace.fallback_reason,
                    "response_model": trace.response_model,
                    "latency_ms": trace.total_router_latency_ms,
                    "entry_wall_latency_ms": round((perf_counter() - started) * 1000, 2),
                    "api_calls": trace.api_calls,
                    "usage": trace.usage,
                    "candidate_task_count": len(trace.decision.tasks),
                    "candidate_complete": trace.decision.is_complete,
                    "candidate_needs_input": trace.decision.needs_clarification,
                    "plan_agreement": plan_signature(baseline)
                    == plan_signature(trace.decision)
                    if baseline is not None
                    and trace.router_source != "legacy_hybrid_fallback"
                    else None,
                }
        emit("jev.handoff_entry", fields)
        if trace.router_source == "legacy_hybrid_fallback":
            record_fallback(fields)
        decision = baseline if self.shadow else trace.decision
        if not decision.is_complete or not decision.tasks:
            return clarify(decision.clarification_question or NO_PLAN_QUESTION)
        return RouteDecision.model_validate(decision.model_dump())


def configured_entry_router(baseline):
    if not jev_requested():
        return HandoffEntryRouter(baseline)
    settings = get_settings()
    if not settings.jev_handoff_router_enabled:
        return HandoffEntryRouter(baseline)
    return HandoffEntryRouter(
        baseline,
        JevClient(settings),
        shadow=settings.jev_handoff_router_shadow_mode,
        confidence_threshold=settings.jev_router_confidence_threshold,
    )
