"""Project the single Router Capability Contract into Jev Choice criteria."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from app.agent.foundation.routing import RouteKey

CONTRACT_PATH = Path(__file__).with_name("config") / "router_capability_contract.json"
ROUTER_QUESTION_INSTRUCTIONS = "Which MoodFlow subgraph should handle this single user request?"
_CRITERION_FIELDS = (
    ("Core", "core_capabilities"),
    ("Supported intents", "supported_intents"),
    ("Can handle", "can_handle"),
    ("Requires", "requires"),
    ("Cannot handle", "cannot_handle"),
    ("Positive examples", "positive_examples"),
    ("Negative examples", "negative_examples"),
)
_CHOICE_ORDER = (
    RouteKey.CURRENT_SONG, RouteKey.TOOL_CALLING,
    RouteKey.WORLDLINE, RouteKey.GENERAL_CHAT,
)


def project_criteria(contract: dict) -> dict[str, str]:
    """Validate and render criteria without route-specific prose in code."""
    if not isinstance(contract, dict) or not isinstance(contract.get("version"), str):
        raise ValueError("router capability contract has no version")
    expected = {route.value for route in RouteKey}
    route_keys = contract.get("route_keys")
    if (not isinstance(route_keys, list) or len(route_keys) != len(expected)
            or set(route_keys) != expected):
        raise ValueError("contract route keys do not match RouteKey")
    specs = contract.get("routes")
    if not isinstance(specs, list) or len(specs) != len(expected):
        raise ValueError("contract must contain one spec per route")
    criteria: dict[str, str] = {}
    for spec in specs:
        if not isinstance(spec, dict) or spec.get("route") not in expected:
            raise ValueError("contract contains an unknown route")
        route = spec["route"]
        if route in criteria:
            raise ValueError("contract contains a duplicate route")
        sections = []
        for heading, field in _CRITERION_FIELDS:
            values = spec.get(field)
            if (not isinstance(values, list) or not values
                    or any(not isinstance(value, str) or not value.strip() for value in values)):
                raise ValueError(f"invalid contract field: {route}.{field}")
            sections.append(f"{heading}: {'; '.join(values)}")
        criteria[route] = ". ".join(sections) + "."
    if set(criteria) != expected:
        raise ValueError("contract is missing a route")
    # Preserve the historical Choice key order so the ablation changes prose only.
    return {route.value: criteria[route.value] for route in _CHOICE_ORDER}


@lru_cache(maxsize=1)
def router_criteria() -> dict[str, str]:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    return project_criteria(contract)
