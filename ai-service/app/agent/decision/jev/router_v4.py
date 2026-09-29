"""Opt-in, side-effect-free Jev Router candidate that produces MainGraph decisions."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Literal
from uuid import uuid4

from typesafe_sdk import Choice, Noul

from app.agent.foundation.routing import (
    IntentItem, IntentSource, IntentType, RouteDecision, RouteKey, RouteTask,
    RoutingContext,
)

from .capability_criteria import ROUTER_QUESTION_INSTRUCTIONS, router_criteria
from .client import DecisionClient, DecisionResponse, JevDecisionError

_FROZEN_CONFIG = Path(__file__).resolve().parents[4] / "evals/config/jev_multi_frozen_strategy_v3.json"
_ROUTES = tuple(router_criteria())
_HEAD_INSTRUCTIONS = (
    "Does this request contain a distinct affirmative supported intent for {route}? "
    "Judge this route independently of the other three; a separate unsupported request "
    "does not create a supported route."
)
_NOUL_FALSE = (
    "No distinct affirmative supported task for this route; mere mentions, negations "
    "and unresolved entities do not count."
)
_SPLIT = re.compile(
    r"(?:请勿|不要|不用|无需|别|不必)[^,，]{0,60}[，,]|"
    r"[;；。!?！？]+|[,，](?=\s*(?:先|再|然后|接着|随后|最后|另外|顺便|同时))|"
    r"然后|接着|随后|顺便|另外|同时|以及|并且|"
    r"再(?=(?:帮|请|给|把|说|讲|解|分|收|播|搜|查|看|探|打|调|静|暂|换))|"
    r"并(?!(?:非|不|没|未|行|列))|最后",
)
_SEQUENCE = re.compile(r"然后|接着|随后|最后|再|^先$")
_NEGATED = re.compile(r"^(?:请勿|不要|不用|无需|别|不必|do not\b|don't\b)", re.I)
_PRONOUN = re.compile(r"(?:这个|那个|这位|那位|同名|他|她|它)")
_COUNTERFACTUAL = re.compile(r"(?:如果|假如|倘若|要是).*(?:从未|从来没|没有|未曾).*(?:发行|出道|存在|创作|演唱)")
_CURRENT_REFERENCE = re.compile(r"(?:这首歌|当前(?:这)?首|正在(?:播放|放)|在播|眼下(?:这)?首|它)")
_TARGET_ACTION = re.compile(r"(?:收藏|喜欢|歌词页|这首歌的歌词|当前歌曲的歌词|加入.*歌单)")
_PLAY_NAMED = re.compile(r"(?:播放|切换到|切到|来一首)\s*《[^》]+》")


@dataclass(frozen=True)
class Clause:
    text: str
    start: int
    end: int
    sequential: bool


@dataclass(frozen=True)
class CandidateTrace:
    decision: RouteDecision
    router_source: Literal["jev_single", "jev_multi", "hybrid_fallback"]
    fallback_reason: str | None
    latency_ms: float
    api_calls: int
    usage: dict[str, int]
    response_model: str | None
    scores: dict[str, float] | None
    confidence: float | None


class TaskConstructionError(ValueError):
    """Jev heads disagree or the query cannot be grounded as executable tasks."""


def frozen_thresholds() -> dict[str, float]:
    data = json.loads(_FROZEN_CONFIG.read_text(encoding="utf-8"))
    if data.get("mode") != "noul" or data.get("config", {}).get("family") != "per_route_threshold":
        raise RuntimeError("Jev Multi V3 strategy is not frozen Noul")
    thresholds = data["config"]["thresholds"]
    if set(thresholds) != set(_ROUTES) or any(not 0 <= value <= 1 for value in thresholds.values()):
        raise RuntimeError("invalid frozen Jev Multi thresholds")
    return thresholds


def split_clauses(query: str) -> list[Clause]:
    """Split only at clause syntax; never infer routes from a query-specific rule."""
    clauses: list[Clause] = []
    cursor = 0
    between = ""
    for match in _SPLIT.finditer(query):
        raw = query[cursor:match.start()]
        value = raw.strip(" \t\r\n,，。.!?！？;；")
        if value:
            start = cursor + raw.index(value)
            sequential = bool(_SEQUENCE.search(between))
            if clauses and value.startswith("再") and ("，" in between or "," in between):
                value = value[1:]
                start += 1
                sequential = True
            if value:
                clauses.append(Clause(value, start, start + len(value), sequential))
            between = ""
        between += match.group()
        cursor = match.end()
    raw = query[cursor:]
    value = raw.strip(" \t\r\n,，。.!?！？;；")
    if value:
        start = cursor + raw.index(value)
        sequential = bool(_SEQUENCE.search(between))
        # A comma can split before 再 even when the following action verb is
        # absent from the conservative separator list. Treat it as order syntax.
        if clauses and value.startswith("再") and ("，" in between or "," in between):
            value = value[1:]
            start += 1
            sequential = True
        if value:
            clauses.append(Clause(value, start, start + len(value), sequential))
    return [clause for clause in clauses if not _NEGATED.match(clause.text)]


def _clarify(question: str) -> RouteDecision:
    return RouteDecision(needs_clarification=True, clarification_question=question)


def _safety_question(clauses: list[Clause], routes: list[RouteKey], context: RoutingContext) -> str | None:
    if not clauses:
        return "请说明你希望执行的操作；已否定的操作不会执行。"
    if any(_COUNTERFACTUAL.search(clause.text) for clause in clauses):
        return "当前无法模拟未发生的音乐历史。请改为询问可核实的实体关系或普通概念。"
    for index, (clause, route) in enumerate(zip(clauses, routes)):
        prior_play = any(
            routes[prior] is RouteKey.TOOL_CALLING and _PLAY_NAMED.search(clauses[prior].text)
            for prior in range(index)
        )
        if route is RouteKey.CURRENT_SONG and not context.has_current_song and not prior_play:
            return "目前没有正在播放的歌曲。请先播放一首歌，或指明歌曲。"
        if (route is RouteKey.TOOL_CALLING and _TARGET_ACTION.search(clause.text)
                and _CURRENT_REFERENCE.search(clause.text) and not context.has_current_song
                and not prior_play):
            return "当前没有可操作的歌曲。请先播放歌曲或说明目标歌曲。"
        if route is RouteKey.WORLDLINE and _PRONOUN.search(clause.text):
            if "同名" in clause.text or not any(
                not _PRONOUN.search(previous.text) for previous in clauses[:index]
            ):
                return "请说明要探索的具体歌曲、歌手或专辑，以及需要区分的同名实体。"
    return None


def _make_decision(clauses: list[Clause], routes: list[RouteKey], confidence: list[float]) -> RouteDecision:
    if not clauses or len(clauses) != len(routes) or len(routes) != len(confidence):
        raise TaskConstructionError("fragment_count_mismatch")
    run_id = uuid4().hex
    intents: list[IntentItem] = []
    tasks: list[RouteTask] = []
    for index, (clause, route, certainty) in enumerate(zip(clauses, routes, confidence)):
        intents.append(IntentItem(
            intent=IntentType(route.value), confidence=certainty, source=IntentSource.LLM,
            evidence=clause.text, start=clause.start, end=clause.end, order=index,
        ))
        dependency = [tasks[-1].task_id] if clause.sequential and tasks else []
        tasks.append(RouteTask(
            task_id=f"{run_id}:{route.value}:{index}", route_key=route,
            input_text=clause.text, intent_orders=[index], depends_on=dependency,
        ))
    return RouteDecision(intents=intents, is_complete=True, coverage=1.0, tasks=tasks)


def _usage(responses: list[DecisionResponse]) -> dict[str, int]:
    total: dict[str, int] = {}
    for response in responses:
        for key, value in (response.usage or {}).items():
            if isinstance(value, int):
                total[key] = total.get(key, 0) + value
    return total


class JevRouterCandidate:
    """One Choice for Single; parallel frozen Noul and clause Choice calls for Multi."""

    def __init__(self, client: DecisionClient, *, fallback_router=None) -> None:
        self.client = client
        self.fallback_router = fallback_router
        self.thresholds = frozen_thresholds()

    async def route(self, text: str, context: RoutingContext | None = None) -> RouteDecision:
        return (await self.route_with_trace(text, context)).decision

    async def route_with_trace(
        self, text: str, context: RoutingContext | None = None,
        *, fallback_decision: RouteDecision | None = None,
    ) -> CandidateTrace:
        if not text.strip() or len(text) > 4_000:
            raise ValueError("query must contain 1..4000 nonblank characters")
        context = context or RoutingContext()
        started = perf_counter()
        clauses = split_clauses(text)
        responses: list[DecisionResponse] = []
        api_calls = 0
        source: Literal["jev_single", "jev_multi", "hybrid_fallback"] = (
            "jev_single" if len(clauses) <= 1 else "jev_multi"
        )
        scores: dict[str, float] | None = None
        confidence: float | None = None
        reason: str | None = None
        try:
            if not clauses:
                decision = _clarify("请说明你希望执行的操作；已否定的操作不会执行。")
            elif len(clauses) == 1:
                api_calls = 1
                response = await self.client.decide(
                    {"query": text[:4000]},
                    {"route": Choice(
                        instructions=ROUTER_QUESTION_INSTRUCTIONS, criteria=router_criteria(),
                    )},
                )
                responses.append(response)
                answer = response.answers["route"]
                if answer.kind != "choice" or answer.choice is None or answer.confidence is None:
                    raise TaskConstructionError("invalid_choice")
                route = RouteKey(answer.choice)
                confidence = answer.confidence
                scores = answer.probabilities
                question = _safety_question(clauses, [route], context)
                decision = _clarify(question) if question else _make_decision(clauses, [route], [confidence])
            else:
                # Two lightweight Jev calls run concurrently. The Noul question wording,
                # state and thresholds are the frozen V3 strategy; clause Choice constructs tasks.
                criteria = router_criteria()
                heads = {
                    route: Noul(
                        instructions=_HEAD_INSTRUCTIONS.format(route=route),
                        criteria={"true": description, "false": _NOUL_FALSE},
                    ) for route, description in criteria.items()
                }
                fragments = {f"fragment_{i}": clause.text for i, clause in enumerate(clauses)}
                fragment_questions = {
                    name: Choice(
                        instructions=f"Which MoodFlow subgraph should handle {name} as a separate affirmative task?",
                        criteria=criteria,
                    ) for name in fragments
                }
                api_calls = 2
                route_response, fragment_response = await asyncio.gather(
                    self.client.decide({"query": text[:4000]}, heads),
                    self.client.decide(fragments, fragment_questions),
                )
                responses.extend((route_response, fragment_response))
                scores = {}
                for route in _ROUTES:
                    answer = route_response.answers[route]
                    if answer.kind != "noul" or answer.noul is None:
                        raise TaskConstructionError("invalid_noul")
                    scores[route] = answer.noul
                selected = {route for route, value in scores.items() if value >= self.thresholds[route]}
                fragment_routes: list[RouteKey] = []
                certainties: list[float] = []
                for name in fragments:
                    answer = fragment_response.answers[name]
                    if answer.kind != "choice" or answer.choice is None or answer.confidence is None:
                        raise TaskConstructionError("invalid_fragment_choice")
                    fragment_routes.append(RouteKey(answer.choice))
                    certainties.append(answer.confidence)
                if not selected or {route.value for route in fragment_routes} != selected:
                    raise TaskConstructionError("route_fragment_conflict")
                question = _safety_question(clauses, fragment_routes, context)
                decision = (
                    _clarify(question) if question else _make_decision(clauses, fragment_routes, certainties)
                )
        except (JevDecisionError, TaskConstructionError, KeyError, ValueError, TypeError, AttributeError) as exc:
            reason = str(exc) if isinstance(exc, (JevDecisionError, TaskConstructionError)) else type(exc).__name__
            if fallback_decision is not None:
                decision = fallback_decision
            else:
                if self.fallback_router is None:
                    from app.agent.foundation.hybrid_routing import create_hybrid_router
                    self.fallback_router = create_hybrid_router()
                decision = await self.fallback_router.route(text, context)
            decision = RouteDecision.model_validate(decision.model_dump())
            source = "hybrid_fallback"
        return CandidateTrace(
            decision=RouteDecision.model_validate(decision.model_dump()),
            router_source=source, fallback_reason=reason,
            latency_ms=round((perf_counter() - started) * 1000, 2),
            api_calls=api_calls, usage=_usage(responses),
            response_model=responses[0].response_model if responses else None,
            scores=scores, confidence=confidence,
        )
