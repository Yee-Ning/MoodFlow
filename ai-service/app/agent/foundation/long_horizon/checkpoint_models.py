"""Versioned JSON for private typed subgraph state, without serde allowlist changes."""

from pydantic import BaseModel

from app.agent.foundation.subgraphs.worldline import models as worldline_models
from app.rag.query_understanding import models as query_models

# Only these existing, validated business DTOs may be rehydrated. Never import
# a module or instantiate a class named by checkpoint content.
SCHEMAS = {f"{module.__name__}.{name}": schema
           for module in (worldline_models, query_models)
           for name, schema in vars(module).items()
           if isinstance(schema, type) and issubclass(schema, BaseModel) and schema.__module__ == module.__name__}


def encode(value):
    if isinstance(value, BaseModel):
        type_id = f"{type(value).__module__}.{type(value).__name__}"
        if type_id in SCHEMAS:
            return {"continuity_typed_state": 1, "type_id": type_id, "value": value.model_dump(mode="json")}
        return value
    if isinstance(value, dict):
        return {key: encode(item) for key, item in value.items()}
    if isinstance(value, list):
        return [encode(item) for item in value]
    if isinstance(value, tuple):
        return tuple(encode(item) for item in value)
    return value


def decode(value):
    if isinstance(value, dict):
        if set(value) == {"continuity_typed_state", "type_id", "value"}:
            schema = SCHEMAS.get(value["type_id"])
            if value["continuity_typed_state"] != 1 or schema is None:
                raise ValueError("unsupported checkpoint state schema")
            return schema.model_validate(value["value"])
        return {key: decode(item) for key, item in value.items()}
    if isinstance(value, list):
        return [decode(item) for item in value]
    if isinstance(value, tuple):
        return tuple(decode(item) for item in value)
    return value
