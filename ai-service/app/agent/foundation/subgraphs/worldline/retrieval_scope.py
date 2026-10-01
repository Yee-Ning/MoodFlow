"""Keep WorldLine knowledge grounded in its resolved root before ranking."""

import hashlib
import json

from app.rag.knowledge.entity_linking.registry import normalize_alias
from app.rag.query_understanding.models import (
    FilterCondition,
    FilterGroup,
    FilterOperator,
    MetadataField,
    MetadataFilter,
)


def root_knowledge_id(entity, plan):
    for item in plan.entities:
        reference = item.catalog_reference
        if reference is not None:
            catalog_id = f"{reference.provider}:{reference.entity_type.value}:{reference.entity_id}"
            matches = catalog_id == entity.entity_id
        else:
            matches = (
                item.entity_type is not None
                and item.entity_type.value == entity.entity_type.value
                and normalize_alias(item.canonical_name or "")
                == normalize_alias(entity.name)
            )
        if matches and item.knowledge_entity_id:
            return item.knowledge_entity_id
    return None


def scope_root_plan(plan, knowledge_id):
    condition = FilterCondition(
        field=MetadataField.ENTITY_IDS,
        operator=FilterOperator.MATCH_VALUE,
        value=knowledge_id,
    )
    children = [condition]
    if plan.metadata_filter.root is not None:
        children.append(plan.metadata_filter.root)
    scoped_filter = MetadataFilter(root=FilterGroup(operator="all", children=children))
    material = {
        **plan.model_dump(mode="json"),
        "metadata_filter": scoped_filter.model_dump(mode="json"),
    }
    material.pop("plan_id")
    digest = hashlib.sha256(
        json.dumps(
            material, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    return plan.model_copy(
        update={
            "plan_id": "query-plan:" + digest[:24],
            "metadata_filter": scoped_filter,
        }
    )


def root_candidates(result, knowledge_id):
    candidates = [item for item in result.candidates if knowledge_id in item.entity_ids]
    if len(candidates) == len(result.candidates):
        return result
    return result.model_copy(
        update={
            "candidates": candidates,
            "warnings": [
                *result.warnings,
                "worldline_unrelated_root_candidates_removed",
            ],
        }
    )
