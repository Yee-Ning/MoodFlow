"""General conversation SubGraph without tools or retrieval."""

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agent.foundation.models import TaskResult, TaskStatus
from app.agent.foundation.routing import RouteKey
from app.agent.foundation.subgraphs.base import SubGraphOutput
from app.agent.foundation.subgraphs.general_chat.assistant import (
    GeneralChatAssistant,
    answer_general_chat,
)
from app.agent.foundation.subgraphs.general_chat.state import GeneralChatState
from app.agent.foundation.subgraphs.general_chat.clarification import clarification_response

MAX_VISIBLE_RESPONSE_CHARACTERS = 2_000


def _visible_text(message: AIMessage) -> str:
    content = str(message.text).strip()
    if not content:
        raise RuntimeError("General-chat assistant returned an empty response")
    if len(content) > MAX_VISIBLE_RESPONSE_CHARACTERS:
        content = content[: MAX_VISIBLE_RESPONSE_CHARACTERS - 1].rstrip() + "…"
    return content


def build_general_chat_subgraph(
    *,
    assistant: GeneralChatAssistant | None = None,
    model: BaseChatModel | None = None,
) -> StateGraph:
    async def agent(state: GeneralChatState) -> dict[str, Any]:
        clarification = clarification_response(state["current_input"], state["messages"])
        if clarification is not None:
            response = clarification
        elif assistant is not None:
            response = await assistant(
                state["messages"],
                state["current_input"],
                state["routing_context"],
                state.get("prior_task_results", []),
            )
        else:
            response = await answer_general_chat(
                state["messages"],
                state["current_input"],
                state["routing_context"],
                state.get("prior_task_results", []),
                model=model,
                continuity_context=state.get("continuity_context"),
                route_task_id=state["task_id"],
            )
        if not isinstance(response, AIMessage):
            raise RuntimeError("General-chat assistant returned a non-AI message")
        if response.tool_calls or response.invalid_tool_calls:
            raise RuntimeError("General-chat assistant attempted to call a tool")
        content = _visible_text(response)
        return {
            "messages": [response],
            "answer": content,
            "task_result": TaskResult(
                task_id=state["task_id"],
                route_key=RouteKey.GENERAL_CHAT,
                status=TaskStatus.NEEDS_INPUT if response.additional_kwargs.get(
                    "general_chat_clarification"
                ) else TaskStatus.SUCCESS,
                content=content,
                data={"response_kind": "general_chat"},
            ),
        }

    builder = StateGraph(GeneralChatState, output_schema=SubGraphOutput)
    builder.add_node("agent", agent)
    builder.add_edge(START, "agent")
    builder.add_edge("agent", END)
    return builder


def create_general_chat_subgraph(
    *,
    assistant: GeneralChatAssistant | None = None,
    model: BaseChatModel | None = None,
) -> CompiledStateGraph:
    return build_general_chat_subgraph(assistant=assistant, model=model).compile(
        checkpointer=False
    )
