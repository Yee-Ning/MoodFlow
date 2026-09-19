"""Safe error categories; raw model output never belongs in these messages."""

from enum import StrEnum


class FailureType(StrEnum):
    PARSE_ERROR = "parse_error"
    SCHEMA_VALIDATION_ERROR = "schema_validation_error"
    SEMANTIC_VALIDATION_ERROR = "semantic_validation_error"
    TRUNCATION = "truncation"
    MODEL_ERROR = "model_error"
    RETRY_EXHAUSTED = "retry_exhausted"


class SemanticValidationError(ValueError):
    """Business validation failure with a safe, stable reason code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ValidationFailure(Exception):
    def __init__(self, failure_type: FailureType, code: str) -> None:
        super().__init__(code)
        self.failure_type = failure_type
        self.code = code
        self.repair_applied = False


class StructuredOutputError(RuntimeError):
    """Public-safe terminal failure; the original exception is kept as its cause."""

    def __init__(
        self,
        schema_name: str,
        failure_type: FailureType,
        *,
        attempts: int,
        last_failure_type: FailureType | None = None,
    ) -> None:
        super().__init__(f"{schema_name}:{failure_type.value}")
        self.schema_name = schema_name
        self.failure_type = failure_type
        self.attempts = attempts
        self.last_failure_type = last_failure_type or failure_type
