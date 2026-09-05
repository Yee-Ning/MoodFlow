from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agent.foundation.models import TaskResult, TaskStatus
from app.agent.foundation.routing import RouteKey
from app.agent.foundation.subgraphs.base import SubGraphOutput
from app.agent.foundation.subgraphs.worldline.state import WorldLineState

STUB_CONTENT = "WorldLine SubGraph stub completed."


def _stub_worldline(state: WorldLineState) -> dict[str, Any]:
    return {
        "answer": STUB_CONTENT,
        "task_result": TaskResult(
            task_id=state["task_id"],
            route_key=RouteKey.WORLDLINE,
            status=TaskStatus.SUCCESS,
            content=STUB_CONTENT,
            data={"stub": True},
        ),
    }


def build_worldline_subgraph() -> StateGraph:
    builder = StateGraph(WorldLineState, output_schema=SubGraphOutput)
    builder.add_node("worldline_stub", _stub_worldline)
    builder.add_edge(START, "worldline_stub")
    builder.add_edge("worldline_stub", END)
    return builder


def create_worldline_subgraph() -> CompiledStateGraph:
    return build_worldline_subgraph().compile()
