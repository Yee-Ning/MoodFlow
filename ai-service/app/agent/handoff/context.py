"""Trusted catalog references survive handoff without granting action authority."""

from collections.abc import Sequence

from app.agent.foundation.models import TaskResult, TaskStatus
from app.agent.foundation.routing import RouteKey
from app.agent.foundation.subgraphs.tool_calling.models import ReferencedEntities
from app.agent.foundation.subgraphs.worldline.models import WorldLineResult


def latest_entities(results: Sequence[TaskResult]) -> ReferencedEntities:
    for result in reversed(results):
        if (
            result.route_key != RouteKey.TOOL_CALLING
            or result.status is not TaskStatus.SUCCESS
        ):
            continue
        raw = result.data.get("entities")
        if raw is not None:
            references = ReferencedEntities.model_validate(raw)
            if not references.is_empty():
                return references
    return ReferencedEntities()


def exploration_projection(results: Sequence[TaskResult]) -> dict | None:
    """Names/IDs aid the next prompt; catalog graph nodes are not playable tracks."""
    for result in reversed(results):
        if (
            result.route_key != RouteKey.WORLDLINE
            or result.status is not TaskStatus.SUCCESS
        ):
            continue
        world = WorldLineResult.model_validate(result.data["worldline_result"])
        return {
            "task_id": result.task_id,
            "text_fields": "untrusted",
            "action_authority": "none; playback requires catalog search and current user request",
            "narrative": world.narrative[:600],
            "entities": [
                {"id": node.id, "type": node.type.value, "name": node.name[:120]}
                for node in world.graph.nodes[:8]
            ],
            "branches": [
                {
                    "title": branch.title[:120],
                    "target": branch.target_entity.model_dump(mode="json"),
                }
                for branch in world.branches[:3]
            ],
            "source_ids": [source.id for source in world.sources[:8]],
        }
    return None
