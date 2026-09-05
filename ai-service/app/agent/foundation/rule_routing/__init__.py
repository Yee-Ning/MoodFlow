"""High-precision first layer of MoodFlow's hybrid multi-intent router."""

from app.agent.foundation.rule_routing.rule_router import RuleRouter, route_by_rules
from app.agent.foundation.rule_routing.rule_types import RuleMatch, RuleSpec

__all__ = ["RuleMatch", "RuleRouter", "RuleSpec", "route_by_rules"]
