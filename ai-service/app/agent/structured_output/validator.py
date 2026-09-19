"""Schema and business validation kept distinct for retry decisions."""

from collections.abc import Callable
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from .errors import (
    FailureType,
    SemanticValidationError,
    ValidationFailure,
)

T = TypeVar("T", bound=BaseModel)


def validate_object(
    schema: type[T],
    value: dict[str, object],
    semantic_validator: Callable[[T], None] | None = None,
) -> T:
    try:
        output = schema.model_validate(value)
    except ValidationError as exc:
        first = exc.errors(include_input=False, include_context=False)[0]
        location = ".".join(str(part) for part in first["loc"])
        code = f"{first['type']}:{location}"[:120]
        raise ValidationFailure(FailureType.SCHEMA_VALIDATION_ERROR, code) from exc
    if semantic_validator is not None:
        try:
            semantic_validator(output)
        except SemanticValidationError as exc:
            raise ValidationFailure(
                FailureType.SEMANTIC_VALIDATION_ERROR, exc.code[:120]
            ) from exc
        except ValueError as exc:
            raise ValidationFailure(
                FailureType.SEMANTIC_VALIDATION_ERROR, "business_constraint"
            ) from exc
    return output
