from langgraph.graph import END, START, StateGraph

from app.agent.foundation.models import TaskResult, TaskStatus
from app.agent.foundation.routing import RouteKey
from app.agent.foundation.state.base import BaseAgentState
from app.agent.foundation.subgraphs.base import SubGraphOutput


class GeneralChatState(BaseAgentState):
    task_id: str
    task_result: TaskResult | None


def create_general_chat_subgraph():
    """Foundation stub, matching the three existing business subgraphs."""
    def run(state: GeneralChatState):
        return {"task_result": TaskResult(
            task_id=state["task_id"], route_key=RouteKey.GENERAL_CHAT,
            status=TaskStatus.SUCCESS, content="GeneralChat SubGraph stub completed.",
            data={"stub": True},
        )}

    graph = StateGraph(GeneralChatState, output_schema=SubGraphOutput)
    graph.add_node("general_chat_stub", run)
    graph.add_edge(START, "general_chat_stub")
    graph.add_edge("general_chat_stub", END)
    return graph.compile()
