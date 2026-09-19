"""Bounded retry feedback that contains no model or user content."""

from .errors import FailureType, ValidationFailure


def retry_feedback(failure: ValidationFailure) -> str:
    kind = failure.failure_type
    if kind is FailureType.TRUNCATION:
        instruction = "The previous JSON response was incomplete. Return a shorter complete object."
    elif kind is FailureType.PARSE_ERROR:
        instruction = "The previous response was not one valid JSON object. Return only JSON."
    elif kind is FailureType.SCHEMA_VALIDATION_ERROR:
        instruction = "The previous object did not match the required schema. Correct its fields and types."
    elif kind is FailureType.SEMANTIC_VALIDATION_ERROR:
        instruction = "The previous object violated the supplied evidence or relationship rules. Correct only grounded fields."
    else:
        instruction = "The previous model response did not complete. Return one valid object."
    return f"{instruction} Validation code: {failure.code}. Do not add unsupported facts."
