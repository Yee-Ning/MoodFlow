"""Compile the trusted metadata filter AST into Qdrant models."""

from __future__ import annotations

from qdrant_client import models

from app.rag.query_understanding.models import (
    FilterCondition,
    FilterGroup,
    FilterOperator,
    MetadataFilter,
)


def compile_qdrant_filter(value: MetadataFilter) -> models.Filter | None:
    if value.root is None:
        return None
    return _compile_group(value.root)


def _compile_group(group: FilterGroup) -> models.Filter:
    children = [
        _compile_group(item) if isinstance(item, FilterGroup) else _compile_condition(item)
        for item in group.children
    ]
    if group.operator == "all":
        return models.Filter(must=children)
    return models.Filter(should=children)


def _compile_condition(condition: FilterCondition) -> models.FieldCondition:
    key = condition.field.value
    if condition.operator is FilterOperator.MATCH_VALUE:
        assert isinstance(condition.value, (str, int))
        return models.FieldCondition(
            key=key,
            match=models.MatchValue(value=condition.value),
        )
    if condition.operator is FilterOperator.MATCH_ANY:
        assert isinstance(condition.value, list)
        return models.FieldCondition(
            key=key,
            match=models.MatchAny(any=condition.value),
        )
    if condition.operator is FilterOperator.RANGE:
        return models.FieldCondition(
            key=key,
            range=models.Range(gte=condition.gte, lte=condition.lte),
        )
    raise ValueError(f"unsupported_filter_operator:{condition.operator}")
