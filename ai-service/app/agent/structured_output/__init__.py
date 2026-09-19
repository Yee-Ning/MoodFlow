"""Shared mechanics for untrusted LLM structured output."""

from .errors import FailureType, SemanticValidationError, StructuredOutputError
from .parser import ModelOutput
from .service import StructuredOutputService

__all__ = [
    "FailureType",
    "ModelOutput",
    "SemanticValidationError",
    "StructuredOutputError",
    "StructuredOutputService",
]
