"""Generate, validate, repair and retry bounded structured model output."""

import logging
from collections.abc import Awaitable, Callable
from time import perf_counter
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

from .errors import FailureType, StructuredOutputError, ValidationFailure
from .parser import ModelOutput, parse_json_object
from .retry import retry_feedback
from .validator import validate_object

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)
Generator = Callable[[int, str | None], Awaitable[str | ModelOutput]]
Repair = Callable[[dict[str, Any], ValidationFailure], dict[str, Any] | None]


class StructuredOutputService(Generic[T]):
    def __init__(
        self,
        schema_name: str,
        schema: type[T],
        *,
        semantic_validator: Callable[[T], None] | None = None,
        repair: Repair | None = None,
        max_attempts: int = 2,
    ) -> None:
        if not 1 <= max_attempts <= 3:
            raise ValueError("max_attempts must be between 1 and 3")
        self.schema_name = schema_name
        self.schema = schema
        self.semantic_validator = semantic_validator
        self.repair = repair
        self.max_attempts = max_attempts

    def _validate(self, payload: dict[str, Any]) -> tuple[T, bool]:
        try:
            return validate_object(self.schema, payload, self.semantic_validator), False
        except ValidationFailure as failure:
            if self.repair is None:
                raise
            repaired = self.repair(payload, failure)
            if repaired is None or repaired == payload:
                raise
            try:
                return validate_object(self.schema, repaired, self.semantic_validator), True
            except ValidationFailure as repaired_failure:
                repaired_failure.repair_applied = True
                raise

    def validate(self, raw: str | dict[str, Any]) -> T:
        """Validate non-generative structured input, such as a tool call."""
        started = perf_counter()
        repair_applied = False
        try:
            parsed = parse_json_object(raw)
            repair_applied = parsed.repair_applied
            output, repaired = self._validate(parsed.value)
        except ValidationFailure as exc:
            self._log(1, repair_applied or exc.repair_applied, exc.failure_type, "failed", started)
            raise StructuredOutputError(
                self.schema_name, exc.failure_type, attempts=1
            ) from exc
        self._log(1, parsed.repair_applied or repaired, None, "success", started)
        return output

    async def run(self, generate: Generator) -> T:
        started = perf_counter()
        feedback: str | None = None
        last_failure: ValidationFailure | None = None
        for attempt in range(1, self.max_attempts + 1):
            repaired = False
            try:
                raw = await generate(attempt, feedback)
                if isinstance(raw, ModelOutput):
                    if raw.status != "completed":
                        kind = (
                            FailureType.TRUNCATION
                            if raw.incomplete_reason == "max_output_tokens"
                            else FailureType.MODEL_ERROR
                        )
                        raise ValidationFailure(kind, "model_incomplete")
                    if raw.output_types and any(
                        item not in {"message", "reasoning"} for item in raw.output_types
                    ):
                        raise ValidationFailure(FailureType.MODEL_ERROR, "unexpected_output_type")
                    if not raw.text.strip():
                        raise ValidationFailure(FailureType.MODEL_ERROR, "empty_model_output")
                    raw = raw.text
                parsed = parse_json_object(raw)
                output, repaired = self._validate(parsed.value)
                repaired = repaired or parsed.repair_applied
            except ValidationFailure as exc:
                last_failure = exc
                self._log(attempt, repaired or exc.repair_applied, exc.failure_type,
                          "retry" if attempt < self.max_attempts else "failed", started)
                feedback = retry_feedback(exc)
                continue
            except Exception as exc:
                self._log(attempt, repaired, FailureType.MODEL_ERROR, "failed", started)
                raise StructuredOutputError(
                    self.schema_name, FailureType.MODEL_ERROR, attempts=attempt
                ) from exc
            self._log(attempt, repaired, None, "success", started)
            return output
        assert last_failure is not None
        raise StructuredOutputError(
            self.schema_name,
            FailureType.RETRY_EXHAUSTED,
            attempts=self.max_attempts,
            last_failure_type=last_failure.failure_type,
        ) from last_failure

    def _log(
        self,
        attempt: int,
        repair_applied: bool,
        failure_type: FailureType | None,
        final_status: str,
        started: float,
    ) -> None:
        logger.info(
            "structured_output.%s",
            final_status,
            extra={
                "schema_name": self.schema_name,
                "attempt": attempt,
                "first_pass_success": attempt == 1 and final_status == "success",
                "repair_applied": repair_applied,
                "retry_count": attempt - 1,
                "failure_type": failure_type.value if failure_type else None,
                "latency_ms": round((perf_counter() - started) * 1_000, 2),
                "final_status": final_status,
            },
        )
