from dataclasses import dataclass
from re import Pattern

from app.agent.foundation.routing import IntentType


@dataclass(frozen=True)
class RuleSpec:
    name: str
    intent: IntentType
    sub_intent: str
    patterns: tuple[Pattern[str], ...]
    priority: int
    confidence: float
    exclude_patterns: tuple[Pattern[str], ...] = ()

    def __post_init__(self) -> None:
        if not self.name or not self.sub_intent or not self.patterns:
            raise ValueError("rule name, sub-intent, and patterns are required")
        if self.priority < 0:
            raise ValueError("rule priority must not be negative")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("rule confidence must be between zero and one")


@dataclass(frozen=True)
class RuleMatch:
    rule_name: str
    intent: IntentType
    sub_intent: str
    confidence: float
    priority: int
    evidence: str
    start: int
    end: int

    @property
    def span_length(self) -> int:
        return self.end - self.start

    def overlaps(self, other: "RuleMatch") -> bool:
        return self.start < other.end and other.start < self.end
