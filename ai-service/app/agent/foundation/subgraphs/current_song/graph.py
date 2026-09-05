from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agent.foundation.models import TaskResult, TaskStatus
from app.agent.foundation.routing import RouteKey
from app.agent.foundation.subgraphs.base import SubGraphOutput
from app.agent.foundation.subgraphs.current_song.state import CurrentSongState

STUB_CONTENT = "CurrentSong SubGraph stub completed."


def _stub_current_song(state: CurrentSongState) -> dict[str, Any]:
    return {
        "answer": STUB_CONTENT,
        "task_result": TaskResult(
            task_id=state["task_id"],
            route_key=RouteKey.CURRENT_SONG,
            status=TaskStatus.SUCCESS,
            content=STUB_CONTENT,
            data={"stub": True},
        ),
    }


def build_current_song_subgraph() -> StateGraph:
    builder = StateGraph(CurrentSongState, output_schema=SubGraphOutput)
    builder.add_node("current_song_stub", _stub_current_song)
    builder.add_edge(START, "current_song_stub")
    builder.add_edge("current_song_stub", END)
    return builder


def create_current_song_subgraph() -> CompiledStateGraph:
    return build_current_song_subgraph().compile()
