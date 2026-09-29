"""Ground Jev intent decisions as ordered MainGraph tasks without route deduplication."""

from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import uuid4

from app.agent.foundation.routing import (
    IntentItem, IntentSource, IntentType, RouteDecision, RouteKey, RouteTask,
    RoutingContext,
)

from .router_v4 import Clause, _safety_question, split_clauses

_ACTION_VERBS = re.compile(
    r"(?:搜索|查找|搜|播放|放|暂停|恢复|静音|调低|调高|调小|调大|"
    r"收藏|打开|查看|查|解释|说明|讲讲|说说|分析|解析|探索|梳理)"
)
_COMPACT_BOUNDARY = re.compile(
    r"(?<=》)(?=(?:播放|打开|收藏|查看|分析|解释))|"
    r"(?<=区别)(?=(?:查看|搜索|播放|打开|收藏|探索))"
)
_NEGATIVE_PREFIX = re.compile(r"^(?:不要|不用|无需|别|不必|请勿)")
_DEPENDENCY_REFERENCE = re.compile(r"(?:它|其|搜索到的|刚刚|播放的版本|这首|这个结果|选一首)")
_UNSPECIFIED_WORLDLINE_ROOT = re.compile(
    r"(?:其他|某位|某个|一些|一位|这个|那个)(?:艺人|歌手|乐队|专辑|歌曲)"
)
_UNCERTAIN_COMPOUND = re.compile(r"(?:分别|各自|各做|各来)")
_SEPARATELY = re.compile(r"^分别(?P<body>.+?)和(?P<second>[^和]+)$")


class OrchestrationFailure(ValueError):
    """A task plan cannot be safely grounded in the original query."""


@dataclass(frozen=True)
class IntentPlan:
    query: str
    clauses: tuple[Clause, ...]
    routes: tuple[RouteKey, ...]
    confidence: tuple[float, ...]
    selected_routes: frozenset[RouteKey]
    context: RoutingContext


def _expand_compact(clause: Clause) -> list[Clause]:
    """Find a second imperative in compact text; never assign its route here."""
    boundaries = [match.start() for match in _COMPACT_BOUNDARY.finditer(clause.text)]
    if not boundaries:
        return [clause]
    starts = [0, *boundaries]
    ends = [*boundaries, len(clause.text)]
    return [Clause(clause.text[start:end], clause.start + start, clause.start + end,
                   clause.sequential or (index > 0 and bool(_DEPENDENCY_REFERENCE.search(clause.text[start:end]))))
            for index, (start, end) in enumerate(zip(starts, ends)) if start < end]


def parse_task_clauses(query: str) -> list[Clause]:
    """Syntax-only cardinality hint, preserving exact user substrings and negation."""
    preliminary = split_clauses(query)
    clauses: list[Clause] = []
    for clause in preliminary:
        separately = _SEPARATELY.match(clause.text)
        if separately and _ACTION_VERBS.match(separately.group("body")):
            first = separately.group("body")
            second = separately.group("second")
            first_start = clause.start + clause.text.index(first)
            second_start = clause.start + clause.text.rindex(second)
            clauses.extend((
                Clause(first, first_start, first_start + len(first), clause.sequential),
                Clause(second, second_start, second_start + len(second), False),
            ))
            continue
        if any(mark in clause.text for mark in ("，", ",", "、")):
            parts = re.split(r"[，,、]", clause.text)
            if len(parts) == 2 and all(_ACTION_VERBS.search(part) for part in parts):
                left, right = (part.strip() for part in parts)
                left_start = clause.start + clause.text.index(left)
                right_start = clause.start + clause.text.index(right, len(parts[0]))
                clauses.extend((
                    Clause(left, left_start, left_start + len(left), clause.sequential),
                    Clause(right, right_start, right_start + len(right),
                           bool(_DEPENDENCY_REFERENCE.search(right))),
                ))
                continue
        clauses.extend(_expand_compact(clause))
    # A fragment that still advertises distinct requests without a grounded
    # boundary must not silently collapse to one executable action.
    return [clause for clause in clauses if not _NEGATIVE_PREFIX.match(clause.text)]


def needs_decomposition_review(query: str, clauses: list[Clause]) -> bool:
    if len(clauses) != 1:
        return False
    clause = clauses[0].text
    return bool(_UNCERTAIN_COMPOUND.search(clause) or len(_ACTION_VERBS.findall(clause)) >= 2)


def clarify(question: str) -> RouteDecision:
    return RouteDecision(needs_clarification=True, clarification_question=question)


class TaskOrchestrator:
    """One grounded task per request clause, including repeated route keys."""

    def build(self, plan: IntentPlan) -> RouteDecision:
        clauses, routes = plan.clauses, plan.routes
        if not clauses or len(clauses) != len(routes) or len(routes) != len(plan.confidence):
            raise OrchestrationFailure("fragment_count_mismatch")
        if any(plan.query[clause.start:clause.end] != clause.text for clause in clauses):
            raise OrchestrationFailure("ungrounded_fragment")
        # Noul identifies candidate route classes, while Choice assigns each
        # executable clause. An ungrounded extra Noul route never creates a task.
        if not set(routes) <= plan.selected_routes:
            raise OrchestrationFailure("task_conflict")
        question = _safety_question(list(clauses), list(routes), plan.context)
        if not question and any(
            route is RouteKey.WORLDLINE and _UNSPECIFIED_WORLDLINE_ROOT.search(clause.text)
            for clause, route in zip(clauses, routes, strict=True)
        ):
            question = "请指明要探索的具体歌曲、歌手或专辑。"
        if question:
            return clarify(question)
        run_id = uuid4().hex
        intents: list[IntentItem] = []
        tasks: list[RouteTask] = []
        for index, (clause, route, certainty) in enumerate(
            zip(clauses, routes, plan.confidence, strict=True)
        ):
            intents.append(IntentItem(
                intent=IntentType(route.value), confidence=certainty,
                source=IntentSource.LLM, evidence=clause.text,
                start=clause.start, end=clause.end, order=index,
            ))
            dependency = [tasks[-1].task_id] if tasks and clause.sequential else []
            tasks.append(RouteTask(
                task_id=f"{run_id}:{route.value}:{index}", route_key=route,
                input_text=clause.text, intent_orders=[index], depends_on=dependency,
            ))
        return RouteDecision.model_validate(RouteDecision(
            intents=intents, is_complete=True, coverage=1.0, tasks=tasks,
        ).model_dump())
