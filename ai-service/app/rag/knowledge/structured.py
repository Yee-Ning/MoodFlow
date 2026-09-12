"""Provider-compatible strict JSON Schemas derived from Pydantic models."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import BaseModel


def strict_model_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Make every declared property required, while nullable stays explicit."""

    schema = deepcopy(model.model_json_schema())
    _make_strict(schema)
    return schema


def _make_strict(value: object) -> None:
    if isinstance(value, list):
        for item in value:
            _make_strict(item)
        return
    if not isinstance(value, dict):
        return
    value.pop("default", None)
    properties = value.get("properties")
    if isinstance(properties, dict):
        value["additionalProperties"] = False
        value["required"] = list(properties)
    for child in value.values():
        _make_strict(child)
