from functools import lru_cache
from typing import Any
from uuid import uuid4

from langchain_core.messages import HumanMessage

from app.agent.foundation.models import TaskResult, TaskStatus
from app.agent.foundation.subgraphs.worldline.graph import (
    create_worldline_subgraph,
)
from app.agent.foundation.subgraphs.worldline.models import WorldLineResult
from app.agent.foundation.subgraphs.worldline.state import create_worldline_state


class WorldLineExecutionError(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.public_message = message
        self.status_code = status_code


@lru_cache
def get_worldline_graph():
    return create_worldline_subgraph()


async def generate_worldline(
    query: str,
    *,
    graph: Any | None = None,
    thread_id: str | None = None,
) -> WorldLineResult:
    run_id = uuid4().hex
    output = await (graph or get_worldline_graph()).ainvoke(
        create_worldline_state(
            query,
            task_id=f"worldline:{run_id}",
            messages=[HumanMessage(content=query)],
        ),
        {
            "configurable": {
                "thread_id": thread_id or f"worldline-api:{run_id}",
            }
        },
    )
    raw_result = output.get("task_result")
    result = (
        raw_result
        if isinstance(raw_result, TaskResult)
        else TaskResult.model_validate(raw_result)
    )
    if result.status is not TaskStatus.SUCCESS:
        reason = str(result.data.get("reason") or "worldline_failed")
        raise WorldLineExecutionError(
            reason,
            result.content or "音乐世界线暂时无法生成。",
            _status_code(reason, result.status),
        )
    payload = result.data.get("worldline_result")
    if payload is None:
        raise WorldLineExecutionError(
            "invalid_worldline_result",
            "音乐世界线返回了无法识别的数据。",
            502,
        )
    return WorldLineResult.model_validate(payload)


def _status_code(reason: str, status: TaskStatus) -> int:
    if status is TaskStatus.NEEDS_INPUT:
        return {
            "entity_not_found": 404,
            "entity_ambiguous": 409,
            "invalid_entity_query": 422,
        }.get(reason, 422)
    if reason in {"entity_search_failed", "entity_facts_failed"}:
        return 503
    return 502
