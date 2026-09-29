"""Rule Guard → frozen Jev heads → task orchestration, with lazy Legacy fallback."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable
from dataclasses import dataclass
from time import perf_counter
from typing import Literal

from typesafe_sdk import Choice, Noul

from app.agent.foundation.routing import RouteDecision, RouteKey, RoutingContext

from .capability_criteria import ROUTER_QUESTION_INSTRUCTIONS, router_criteria
from .client import DecisionClient, DecisionResponse, JevDecisionError
from .router_v4 import _HEAD_INSTRUCTIONS, _NOUL_FALSE, frozen_thresholds
from .task_orchestrator_v5 import (
    IntentPlan, OrchestrationFailure, TaskOrchestrator, clarify,
    needs_decomposition_review, parse_task_clauses,
)

_ROUTES = tuple(router_criteria())
_UNSUPPORTED_EXTERNAL_ACTION = re.compile(
    r"(?:订|预订|预约|购买|买|发送|叫).*(?:机票|航班|火车票|酒店|出租车|外卖|邮件|短信|会议)"
)
_UNSUPPORTED_COUNTERFACTUAL = re.compile(
    r"(?:如果|假如|倘若|要是).*(?:从未|从来没|没有|没|未曾|不曾|晚|早)"
    r".*(?:发行|出道|存在|创作|演唱|推出)"
)


class JevRouteFailure(ValueError):
    """A Jev answer cannot safely be converted to a task plan."""


@dataclass(frozen=True)
class JevDecision:
    selected_routes: frozenset[RouteKey]
    fragment_routes: tuple[RouteKey, ...]
    confidence: tuple[float, ...]
    scores: dict[str, float]
    response_model: str | None
    usage: dict[str, int]
    api_calls: int
    latency_ms: float


@dataclass(frozen=True)
class RouterTraceV5:
    decision: RouteDecision
    router_source: Literal["jev_single", "jev_multi", "legacy_hybrid_fallback"]
    fallback_reason: str | None
    total_router_latency_ms: float
    jev_latency_ms: float
    orchestration_latency_ms: float
    fallback_latency_ms: float
    api_calls: int
    usage: dict[str, int]
    response_model: str | None
    scores: dict[str, float] | None
    confidence: float | None
    classifier_hot_path_calls: int
    classifier_fallback_calls: int


class RuleGuard:
    """Deterministic query validation and cardinality hints, no route choice."""

    def inspect(self, text: str):
        if not text.strip() or len(text) > 4_000:
            return ()
        return tuple(parse_task_clauses(text))

    def preflight_question(self, clauses) -> str | None:
        # The parser already removed negated clauses. A prohibited action
        # mentioned only in a negation must not cancel a supported request.
        if any(_UNSUPPORTED_EXTERNAL_ACTION.search(clause.text) for clause in clauses):
            return "当前仅支持音乐相关能力；请把外部预订或通信请求交给相应服务。"
        if any(_UNSUPPORTED_COUNTERFACTUAL.search(clause.text) for clause in clauses):
            return "当前无法模拟未发生的音乐历史。请改为询问可核实的音乐实体关系。"
        return None


def _sum_usage(responses: list[DecisionResponse]) -> dict[str, int]:
    usage: dict[str, int] = {}
    for response in responses:
        for name, value in (response.usage or {}).items():
            if isinstance(value, int):
                usage[name] = usage.get(name, 0) + value
    return usage


class JevDecisionLayer:
    """Frozen Choice V2 for Single and four independent frozen Noul for Multi."""

    def __init__(self, client: DecisionClient, *, confidence_threshold: float = .8):
        if confidence_threshold != .8:
            raise ValueError("V5 requires frozen Single confidence threshold .8")
        self.client = client
        self.confidence_threshold = confidence_threshold
        self.thresholds = frozen_thresholds()

    async def decide(self, text: str, clauses) -> JevDecision:
        started = perf_counter()
        criteria = router_criteria()
        if len(clauses) == 1:
            response = await self.client.decide(
                {"query": text[:4000]},
                {"route": Choice(
                    instructions=ROUTER_QUESTION_INSTRUCTIONS, criteria=criteria,
                )},
            )
            answer = response.answers["route"]
            if answer.kind != "choice" or answer.choice is None or answer.confidence is None:
                raise JevRouteFailure("schema_failure")
            if answer.confidence < self.confidence_threshold:
                raise JevRouteFailure("low_confidence")
            route = RouteKey(answer.choice)
            return JevDecision(
                frozenset({route}), (route,), (answer.confidence,),
                answer.probabilities or {}, response.response_model,
                _sum_usage([response]), 1, (perf_counter() - started) * 1000,
            )
        questions = {
            route: Noul(
                instructions=_HEAD_INSTRUCTIONS.format(route=route),
                criteria={"true": description, "false": _NOUL_FALSE},
            ) for route, description in criteria.items()
        }
        fragment_state = {f"fragment_{index}": clause.text for index, clause in enumerate(clauses)}
        fragment_questions = {
            name: Choice(
                instructions=f"Which MoodFlow subgraph should handle {name} as a separate affirmative task?",
                criteria=criteria,
            ) for name in fragment_state
        }
        outcomes = await asyncio.gather(
            self.client.decide({"query": text[:4000]}, questions),
            self.client.decide(fragment_state, fragment_questions),
            return_exceptions=True,
        )
        if any(isinstance(item, BaseException) for item in outcomes):
            failure = next(item for item in outcomes if isinstance(item, BaseException))
            raise failure
        route_response, fragment_response = outcomes
        scores: dict[str, float] = {}
        for route in _ROUTES:
            answer = route_response.answers[route]
            if answer.kind != "noul" or answer.noul is None:
                raise JevRouteFailure("schema_failure")
            scores[route] = answer.noul
        selected = frozenset(RouteKey(route) for route, value in scores.items()
                             if value >= self.thresholds[route])
        fragment_routes: list[RouteKey] = []
        confidence: list[float] = []
        for name in fragment_state:
            answer = fragment_response.answers[name]
            if answer.kind != "choice" or answer.choice is None or answer.confidence is None:
                raise JevRouteFailure("schema_failure")
            fragment_routes.append(RouteKey(answer.choice))
            confidence.append(answer.confidence)
        return JevDecision(
            selected, tuple(fragment_routes), tuple(confidence), scores,
            route_response.response_model, _sum_usage([route_response, fragment_response]),
            2, (perf_counter() - started) * 1000,
        )


def _reason(exc: Exception) -> str:
    if isinstance(exc, OrchestrationFailure):
        return "task_conflict" if str(exc) == "task_conflict" else "orchestration_failure"
    if isinstance(exc, JevRouteFailure):
        return str(exc)
    if isinstance(exc, JevDecisionError):
        name = str(exc).lower()
        if "timeout" in name:
            return "timeout"
        if any(word in name for word in ("valueerror", "validation", "schema", "keyerror")):
            return "schema_failure"
        return "api_failure"
    return "schema_failure" if isinstance(exc, (KeyError, TypeError, ValueError)) else "orchestration_failure"


class JevRouterV5:
    """MainGraph router candidate; Legacy Hybrid is constructed only on failure."""

    def __init__(
        self, client: DecisionClient, *, fallback_router=None,
        confidence_threshold: float = .8,
    ):
        self.guard = RuleGuard()
        self.jev = JevDecisionLayer(client, confidence_threshold=confidence_threshold)
        self.orchestrator = TaskOrchestrator()
        self.fallback_router = fallback_router

    async def route(self, text: str, context: RoutingContext | None = None) -> RouteDecision:
        return (await self.route_with_trace(text, context)).decision

    async def route_with_trace(
        self, text: str, context: RoutingContext | None = None,
        *, fallback_decision: RouteDecision | Awaitable[RouteDecision] | None = None,
    ) -> RouterTraceV5:
        started = perf_counter()
        context = context or RoutingContext()
        clauses = self.guard.inspect(text)
        source: Literal["jev_single", "jev_multi", "legacy_hybrid_fallback"] = (
            "jev_multi" if len(clauses) > 1 else "jev_single"
        )
        jev_latency = orchestration_latency = fallback_latency = 0.0
        jev: JevDecision | None = None
        reason: str | None = None
        classifier_fallback_calls = 0
        attempted_jev_calls = 0
        jev_start = orchestration_start = None
        try:
            preflight = self.guard.preflight_question(clauses)
            if preflight:
                decision = clarify(preflight)
            elif not clauses:
                decision = clarify("请说明你希望执行的音乐相关请求；已否定的操作不会执行。")
            elif needs_decomposition_review(text, list(clauses)):
                # A compact request with multiple predicates is unsafe to
                # execute as one task. No full Hybrid run on ordinary Singles.
                raise OrchestrationFailure("unreliable_task_boundary")
            else:
                jev_start = perf_counter()
                attempted_jev_calls = 1 if len(clauses) == 1 else 2
                jev = await self.jev.decide(text, clauses)
                jev_latency = (perf_counter() - jev_start) * 1000
                orchestration_start = perf_counter()
                plan = IntentPlan(
                    text, clauses, jev.fragment_routes, jev.confidence,
                    jev.selected_routes, context,
                )
                decision = self.orchestrator.build(plan)
                orchestration_latency = (perf_counter() - orchestration_start) * 1000
        except (JevDecisionError, JevRouteFailure, OrchestrationFailure,
                KeyError, ValueError, TypeError, AttributeError) as exc:
            reason = _reason(exc)
            if jev_start is not None and jev_latency == 0:
                jev_latency = (perf_counter() - jev_start) * 1000
            if orchestration_start is not None and orchestration_latency == 0:
                orchestration_latency = (perf_counter() - orchestration_start) * 1000
            fallback_start = perf_counter()
            if fallback_decision is not None:
                decision = (await fallback_decision if isinstance(fallback_decision, Awaitable)
                            else fallback_decision)
            else:
                if self.fallback_router is None:
                    from app.agent.foundation.hybrid_routing import create_hybrid_router
                    self.fallback_router = create_hybrid_router()
                from app.agent.foundation.hybrid_routing import classifier_fallback_counter
                counter: list[int] = [0]
                token = classifier_fallback_counter.set(counter)
                try:
                    decision = await self.fallback_router.route(text, context)
                finally:
                    classifier_fallback_counter.reset(token)
                classifier_fallback_calls = counter[0]
            fallback_latency = (perf_counter() - fallback_start) * 1000
            source = "legacy_hybrid_fallback"
        decision = RouteDecision.model_validate(decision.model_dump())
        return RouterTraceV5(
            decision=decision, router_source=source, fallback_reason=reason,
            total_router_latency_ms=round((perf_counter() - started) * 1000, 2),
            jev_latency_ms=round(jev_latency, 2),
            orchestration_latency_ms=round(orchestration_latency, 2),
            fallback_latency_ms=round(fallback_latency, 2),
            api_calls=attempted_jev_calls,
            usage=jev.usage if jev else {}, response_model=jev.response_model if jev else None,
            scores=jev.scores if jev else None,
            confidence=jev.confidence[0] if jev and len(jev.confidence) == 1 else None,
            classifier_hot_path_calls=0,
            classifier_fallback_calls=classifier_fallback_calls,
        )
