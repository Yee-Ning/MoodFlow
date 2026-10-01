"""One validated, observable boundary for TypeSafe System One decisions."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter
from typing import Any, Mapping, Protocol

from typesafe_sdk import AsyncTypeSafeClient, Choice, Noul, RetryPolicy, Score

from app.config.settings import Settings, get_settings
from app.llm.runtime import RuntimeLimitError, accounted_call

logger = logging.getLogger(__name__)
Question = Choice | Noul | Score
_SECRET_FIELD = re.compile(r"(?:api[_-]?key|token|password|secret)", re.IGNORECASE)
_SECRET_VALUE = re.compile(
    r"(?i)\b(api[_-]?key|token|password|secret)\b\s*[:=]\s*[^\s,;]+"
)
_BEARER = re.compile(r"(?i)\bbearer\s+[^\s,;]+")


class JevDecisionError(Exception):
    """Safe failure category; upstream exception text is never exposed."""


@dataclass(frozen=True)
class DecisionAnswer:
    kind: str
    choice: str | None = None
    confidence: float | None = None
    probabilities: dict[str, float] | None = None
    noul: float | None = None
    score: float | None = None


@dataclass(frozen=True)
class DecisionResponse:
    requested_model: str
    response_model: str
    evaluation_timestamp: str
    latency_ms: float
    usage: dict[str, int] | None
    answers: dict[str, DecisionAnswer]


class DecisionClient(Protocol):
    async def decide(
        self, state: Any, questions: Mapping[str, Question]
    ) -> DecisionResponse: ...


def _probability(value: Any) -> float:
    number = float(value)
    if not 0 <= number <= 1:
        raise ValueError("probability out of range")
    return number


def _safe_state(value: Any, api_key: str) -> Any:
    if isinstance(value, str):
        text = value.replace(api_key, "[redacted]") if api_key else value
        return _BEARER.sub(
            "Bearer [redacted]", _SECRET_VALUE.sub("secret=[redacted]", text)
        )
    if isinstance(value, dict):
        return {
            key: _safe_state(item, api_key)
            for key, item in value.items()
            if not _SECRET_FIELD.search(str(key))
        }
    if isinstance(value, (list, tuple)):
        return [_safe_state(item, api_key) for item in value]
    return value


def _parse_answer(raw: Any, question: Question) -> DecisionAnswer:
    if isinstance(question, Choice):
        if raw.type != "choice" or raw.choice not in question.criteria:
            raise ValueError("invalid Choice response")
        probabilities = {
            key: _probability(value) for key, value in raw.probabilities.items()
        }
        if set(probabilities) != set(question.criteria):
            raise ValueError("incomplete Choice distribution")
        return DecisionAnswer(
            "choice",
            choice=raw.choice,
            confidence=_probability(raw.confidence),
            probabilities=probabilities,
        )
    if isinstance(question, Noul):
        if raw.type != "noul":
            raise ValueError("invalid Noul response")
        return DecisionAnswer("noul", noul=_probability(raw.noul))
    if raw.type != "score":
        raise ValueError("invalid Score response")
    probabilities = {
        str(key): _probability(value) for key, value in raw.probabilities.items()
    }
    return DecisionAnswer(
        "score",
        score=float(raw.score),
        confidence=_probability(raw.confidence),
        probabilities=probabilities,
    )


class JevClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    async def decide(
        self, state: Any, questions: Mapping[str, Question]
    ) -> DecisionResponse:
        key = self.settings.typesafe_api_key
        if key is None or not key.get_secret_value():
            raise JevDecisionError("missing_api_key")
        started = perf_counter()
        try:
            async with AsyncTypeSafeClient(
                api_key=key.get_secret_value(),
                model=self.settings.jev_model,
                timeout=self.settings.jev_timeout_seconds,
                retry=RetryPolicy(max_retries=0),
            ) as client:
                safe = _safe_state(state, key.get_secret_value())
                raw = await accounted_call(
                    lambda: client.system_one(
                        state=safe,
                        questions=questions,
                        model=self.settings.jev_model,
                    ),
                    {"state": safe, "questions": dict(questions)},
                    output_tokens=256,
                    retry=False,
                )
            if set(raw.answers) != set(questions):
                raise ValueError("missing or unexpected answers")
            answers = {
                name: _parse_answer(raw.answers[name], question)
                for name, question in questions.items()
            }
            return DecisionResponse(
                requested_model=self.settings.jev_model,
                response_model=raw.model,
                evaluation_timestamp=datetime.now(UTC).isoformat(),
                latency_ms=round((perf_counter() - started) * 1000, 2),
                usage=raw.usage.model_dump() if raw.usage else None,
                answers=answers,
            )
        except (JevDecisionError, RuntimeLimitError):
            raise
        except Exception as exc:
            # SDK/HTTP errors can include request details. Never log or relay their text.
            logger.warning("jev.request_failed error_type=%s", type(exc).__name__)
            raise JevDecisionError(type(exc).__name__) from None
