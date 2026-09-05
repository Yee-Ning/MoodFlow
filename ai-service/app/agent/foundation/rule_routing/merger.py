from collections.abc import Iterable

from app.agent.foundation.rule_routing.rule_types import RuleMatch


def _preference(match: RuleMatch) -> tuple[int, float, int, int, str]:
    return (
        match.priority,
        match.confidence,
        match.span_length,
        -match.start,
        match.rule_name,
    )


def deduplicate_matches(matches: Iterable[RuleMatch]) -> list[RuleMatch]:
    """Keep the strongest match for an identical intent, subtype, and span."""
    strongest: dict[tuple[object, str, int, int], RuleMatch] = {}
    for match in matches:
        key = (match.intent, match.sub_intent, match.start, match.end)
        existing = strongest.get(key)
        if existing is None or _preference(match) > _preference(existing):
            strongest[key] = match
    return list(strongest.values())


def resolve_conflicts(matches: Iterable[RuleMatch]) -> list[RuleMatch]:
    """Resolve overlapping spans by priority, confidence, then specificity."""
    candidates = sorted(deduplicate_matches(matches), key=_preference, reverse=True)
    accepted: list[RuleMatch] = []
    for candidate in candidates:
        if any(candidate.overlaps(existing) for existing in accepted):
            continue
        accepted.append(candidate)
    return sorted(accepted, key=lambda item: (item.start, item.end, item.rule_name))
