"""Strict JSON parsing with a small, deterministic Markdown-fence repair."""

import json
import re
from dataclasses import dataclass
from typing import Any

from .errors import FailureType, ValidationFailure

_JSON_FENCE = re.compile(r"^```(?:json)?\s*\n(?P<body>.*?)\n```$", re.DOTALL | re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class ModelOutput:
    text: str
    status: str = "completed"
    incomplete_reason: str | None = None
    output_types: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ParsedObject:
    value: dict[str, Any]
    repair_applied: bool = False


def model_output_from_response(response: Any) -> ModelOutput:
    details = getattr(response, "incomplete_details", None)
    return ModelOutput(
        text=getattr(response, "output_text", None) or "",
        status=str(getattr(response, "status", "unknown")),
        incomplete_reason=getattr(details, "reason", None),
        output_types=tuple(
            str(getattr(item, "type", "unknown"))
            for item in getattr(response, "output", ())
        ),
    )


def parse_json_object(raw: str | dict[str, Any]) -> ParsedObject:
    if isinstance(raw, dict):
        return ParsedObject(raw)
    if not isinstance(raw, str) or not raw.strip():
        raise ValidationFailure(FailureType.PARSE_ERROR, "empty_json")
    text = raw.strip().lstrip("\ufeff")
    fenced = _JSON_FENCE.fullmatch(text)
    repaired = fenced is not None
    if fenced is not None:
        text = fenced.group("body").strip()
    try:
        value = json.loads(text, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (json.JSONDecodeError, ValueError) as exc:
        failure_type = (
            FailureType.TRUNCATION
            if text.startswith("{") and not text.endswith("}")
            else FailureType.PARSE_ERROR
        )
        raise ValidationFailure(failure_type, "invalid_json") from exc
    if not isinstance(value, dict):
        raise ValidationFailure(FailureType.PARSE_ERROR, "expected_json_object")
    return ParsedObject(value, repair_applied=repaired)
