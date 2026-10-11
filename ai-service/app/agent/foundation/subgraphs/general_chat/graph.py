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
from app.agent.foundation.subgraphs.general_chat.clarification import (
    clarification_response,
)
from app.agent.foundation.subgraphs.general_chat.conversation import capability_response
from app.agent.foundation.subgraphs.general_chat.results import read_result_receipt
from app.agent.foundation.subgraphs.general_chat.session import (
    expire_pending_confirmation,
    fence_preferences,
    session_for,
)

MAX_VISIBLE_RESPONSE_CHARACTERS = 2_000


def _visible_text(message: AIMessage) -> str:
    content = str(message.text).strip()
    if not content:
        raise RuntimeError("General-chat assistant returned an empty response")
    if message.additional_kwargs.get(
        "general_chat_structured"
    ) or message.additional_kwargs.get("general_chat_song"):
        if len(content) > 10_000:
            raise RuntimeError("structured page exceeds TaskResult budget")
        return content
    if len(content) > MAX_VISIBLE_RESPONSE_CHARACTERS:
        content = content[: MAX_VISIBLE_RESPONSE_CHARACTERS - 1].rstrip() + "…"
    return content


def build_general_chat_subgraph(
    *,
    assistant: GeneralChatAssistant | None = None,
    model: BaseChatModel | None = None,
    song_reader=None,
    session_store=None,
) -> StateGraph:
    async def agent(state: GeneralChatState) -> dict[str, Any]:
        clarification = clarification_response(
            state["current_input"], state["messages"]
        )
        if clarification is not None:
            session_for(
                state.get("continuity_context"), store=session_store
            ).pending = None
            response = clarification
        elif assistant is not None:
            session = session_for(state.get("continuity_context"), store=session_store)
            expire_pending_confirmation(session, state["messages"])
            response = await capability_response(
                state["current_input"],
                state["routing_context"],
                state.get("prior_task_results", []),
                await read_result_receipt(state.get("prior_task_results", [])),
                session,
                structured_results=state.get("structured_results", ()),
                song_reader=song_reader,
            )
            if response is None:
                response = await assistant(
                    state["messages"],
                    state["current_input"],
                    state["routing_context"],
                    state.get("prior_task_results", []),
                )
                if isinstance(response, AIMessage):
                    response = fence_preferences(
                        response, state["current_input"], session
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
                structured_results=state.get("structured_results"),
                song_reader=song_reader,
                session_store=session_store,
            )
        if not isinstance(response, AIMessage):
            raise RuntimeError("General-chat assistant returned a non-AI message")
        if response.tool_calls or response.invalid_tool_calls:
            raise RuntimeError("General-chat assistant attempted to call a tool")
        content = _visible_text(response)
        data = {"response_kind": "general_chat"}
        if response.additional_kwargs.get("general_chat_structured"):
            data["structured_facts"] = response.additional_kwargs[
                "general_chat_structured"
            ]
        if response.additional_kwargs.get("general_chat_song"):
            data["current_song_dialogue"] = response.additional_kwargs[
                "general_chat_song"
            ]
        return {
            "messages": [response],
            "answer": content,
            "task_result": TaskResult(
                task_id=state["task_id"],
                route_key=RouteKey.GENERAL_CHAT,
                status=TaskStatus.NEEDS_INPUT
                if response.additional_kwargs.get("general_chat_clarification")
                else TaskStatus.SUCCESS,
                content=content,
                data=data,
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
    song_reader=None,
    session_store=None,
) -> CompiledStateGraph:
    return build_general_chat_subgraph(
        assistant=assistant,
        model=model,
        song_reader=song_reader,
        session_store=session_store,
    ).compile(checkpointer=False)
