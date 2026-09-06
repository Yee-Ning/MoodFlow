from collections.abc import Sequence

from app.agent.foundation.routing import (
    IntentItem,
    IntentSource,
    RouteDecision,
    RoutingContext,
)
from app.agent.foundation.rule_routing.coverage import calculate_coverage
from app.agent.foundation.rule_routing.merger import resolve_conflicts
from app.agent.foundation.rule_routing.normalizer import NormalizedText, normalize_text
from app.agent.foundation.rule_routing.rule_registry import get_default_rules
from app.agent.foundation.rule_routing.rule_types import RuleMatch, RuleSpec

COMPLETE_CONFIDENCE = 0.90


def _collect_matches(
    normalized: NormalizedText,
    rules: Sequence[RuleSpec],
) -> list[RuleMatch]:
    matches: list[RuleMatch] = []
    for rule in rules:
        for pattern in rule.patterns:
            for match in pattern.finditer(normalized.text):
                excluded = any(
                    exclusion_match.start() < match.end()
                    and match.start() < exclusion_match.end()
                    for exclusion in rule.exclude_patterns
                    for exclusion_match in exclusion.finditer(normalized.text)
                )
                if excluded:
                    continue
                start, end = normalized.original_span(*match.span())
                matches.append(
                    RuleMatch(
                        rule_name=rule.name,
                        intent=rule.intent,
                        sub_intent=rule.sub_intent,
                        confidence=rule.confidence,
                        priority=rule.priority,
                        evidence=normalized.original[start:end],
                        start=start,
                        end=end,
                    )
                )
    return matches


class RuleRouter:
    """High-precision, low-recall first layer of the hybrid router."""

    def __init__(self, rules: Sequence[RuleSpec] | None = None) -> None:
        self._rules = tuple(rules) if rules is not None else get_default_rules()

    def route(
        self,
        query: str,
        context: RoutingContext | None = None,
    ) -> RouteDecision:
        # Context is intentionally read-only and reserved for context-aware rules.
        _routing_context = RoutingContext() if context is None else context
        normalized = normalize_text(query)
        if not normalized.text:
            return RouteDecision(needs_clarification=True, coverage=1.0)

        matches = resolve_conflicts(_collect_matches(normalized, self._rules))
        intents = [
            IntentItem(
                intent=match.intent,
                sub_intent=match.sub_intent,
                confidence=match.confidence,
                source=IntentSource.RULE,
                evidence=match.evidence,
                start=match.start,
                end=match.end,
                order=order,
            )
            for order, match in enumerate(matches)
        ]
        coverage = calculate_coverage(query, matches)
        confident = bool(intents) and all(
            item.confidence >= COMPLETE_CONFIDENCE for item in intents
        )
        is_complete = confident and coverage.is_fully_covered

        return RouteDecision(
            intents=intents,
            unresolved_text=coverage.unresolved_text,
            unresolved_segments=coverage.unresolved_segments,
            is_complete=is_complete,
            needs_semantic=not is_complete,
            needs_llm=False,
            needs_clarification=False,
            coverage=coverage.ratio,
        )


def route_by_rules(
    query: str,
    context: RoutingContext | None = None,
) -> RouteDecision:
    return RuleRouter().route(query, context)
