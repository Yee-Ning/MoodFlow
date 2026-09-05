from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agent.foundation.models import TaskResult, TaskStatus
from app.agent.foundation.routing import RouteKey
from app.agent.foundation.subgraphs.base import SubGraphOutput
from app.agent.foundation.subgraphs.tool_calling.state import ToolCallingState

STUB_CONTENT = "ToolCalling SubGraph stub completed."


def _stub_tool_calling(state: ToolCallingState) -> dict[str, Any]:
    return {
        "answer": STUB_CONTENT,
        "task_result": TaskResult(
            task_id=state["task_id"],
            route_key=RouteKey.TOOL_CALLING,
            status=TaskStatus.SUCCESS,
            content=STUB_CONTENT,
            data={"stub": True},
        ),
    }


def build_tool_calling_subgraph() -> StateGraph:
    builder = StateGraph(ToolCallingState, output_schema=SubGraphOutput)
    builder.add_node("tool_calling_stub", _stub_tool_calling)
    builder.add_edge(START, "tool_calling_stub")
    builder.add_edge("tool_calling_stub", END)
    return builder


def create_tool_calling_subgraph() -> CompiledStateGraph:
    return build_tool_calling_subgraph().compile()
