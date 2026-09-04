from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.tools import BaseTool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from app.agent.song_assistant import (
    MAX_VISIBLE_RESPONSE_CHARACTERS,
    answer_current_song,
    bind_music_tools,
)
from app.context.builder import build_context
from app.context.music import MusicContextProvider
from app.schemas.chat import (
    STRUCTURED_OUTPUT_ADAPTER,
    ChatContext,
    ChatOutput,
    FinalStreamEvent,
    MessageStreamEvent,
    SongAssistantStreamEvent,
    StructuredOutput,
    UpdateStreamEvent,
)
from app.schemas.context import MusicAgentContext, SongContext
from app.tools.music import MUSIC_AGENT_TOOLS, handle_music_tool_error

MAX_TOOL_CALLS = 5
TOOL_LIMIT_MESSAGE = (
    "这次任务需要的工具调用次数超过安全上限，我已停止继续操作。"
    "请缩小请求范围或分成两次完成。"
)


class MusicAgentState(MessagesState):
    current_song_id: int | None
    song_context: SongContext | None
    response: dict[str, Any] | None
    tool_call_count: int


# Compatibility alias for imports created in the previous module.
CurrentSongState = MusicAgentState

SongAssistant = Callable[
    [list[BaseMessage], int | None, SongContext | None],
    Awaitable[AIMessage],
]


def create_memory_checkpointer() -> InMemorySaver:
    serializer = JsonPlusSerializer(
        allowed_msgpack_modules=[("app.schemas.context", "SongContext")]
    )
    return InMemorySaver(serde=serializer)


def _final_output(message: AIMessage) -> ChatOutput:
    text = str(message.text).strip()
    if not text:
        raise RuntimeError("Music agent returned an empty final response")
    if len(text) > MAX_VISIBLE_RESPONSE_CHARACTERS:
        text = text[: MAX_VISIBLE_RESPONSE_CHARACTERS - 1].rstrip() + "…"
    return ChatOutput(type="chat", message=text, search_query=None)


def route_after_agent(state: MusicAgentState) -> str:
    """Use LangGraph's standard last-AI-message tool call condition."""
    return tools_condition(state)


def create_current_song_graph(
    *,
    provider: MusicContextProvider | None = None,
    assistant: SongAssistant | None = None,
    model: BaseChatModel | None = None,
    tools: Sequence[BaseTool] | None = None,
    checkpointer: InMemorySaver | None = None,
):
    graph_tools = list(tools or MUSIC_AGENT_TOOLS)
    bound_model = bind_music_tools(model, graph_tools) if model is not None else None

    async def load_song_context(state: MusicAgentState) -> dict[str, Any]:
        current_song_id = state.get("current_song_id")
        if current_song_id is None:
            return {"song_context": None}

        resolved = await build_context(
            ChatContext(current_song_id=current_song_id),
            provider=provider,
        )
        return {"song_context": resolved.current_song}

    async def agent(state: MusicAgentState) -> dict[str, Any]:
        if assistant is not None:
            result = await assistant(
                state["messages"],
                state.get("current_song_id"),
                state.get("song_context"),
            )
        else:
            result = await answer_current_song(
                state["messages"],
                state.get("current_song_id"),
                state.get("song_context"),
                model=bound_model,
            )

        tool_call_count = state.get("tool_call_count", 0)
        requested_calls = len(result.tool_calls)
        if requested_calls and tool_call_count + requested_calls > MAX_TOOL_CALLS:
            result = AIMessage(content=TOOL_LIMIT_MESSAGE)
            requested_calls = 0

        response: StructuredOutput | None = None
        if not result.tool_calls:
            response = _final_output(result)

        return {
            "messages": [result],
            "response": (
                response.model_dump(mode="json") if response is not None else None
            ),
            "tool_call_count": tool_call_count + requested_calls,
        }

    builder = StateGraph(MusicAgentState, context_schema=MusicAgentContext)
    builder.add_node("load_song_context", load_song_context)
    builder.add_node("agent", agent)
    builder.add_node(
        "tools",
        ToolNode(graph_tools, handle_tool_errors=handle_music_tool_error),
    )
    builder.add_edge(START, "load_song_context")
    builder.add_edge("load_song_context", "agent")
    builder.add_conditional_edges(
        "agent",
        route_after_agent,
        {"tools": "tools", END: END},
    )
    builder.add_edge("tools", "agent")
    return builder.compile(checkpointer=checkpointer or create_memory_checkpointer())


CURRENT_SONG_GRAPH = create_current_song_graph()


async def stream_current_song_assistant(
    message: str,
    thread_id: str,
    current_song_id: int | None,
    *,
    session_cookie: str | None = None,
    graph: Any | None = None,
) -> AsyncIterator[SongAssistantStreamEvent]:
    final_response: StructuredOutput | None = None
    async for part in (graph or CURRENT_SONG_GRAPH).astream(
        {
            "messages": [HumanMessage(content=message)],
            "current_song_id": current_song_id,
            "song_context": None,
            "response": None,
            "tool_call_count": 0,
        },
        {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": MAX_TOOL_CALLS * 2 + 4,
        },
        context=MusicAgentContext(session_cookie=session_cookie),
        stream_mode=["messages", "updates"],
        version="v2",
    ):
        if part["type"] == "messages":
            message_chunk, metadata = part["data"]
            if (
                metadata.get("langgraph_node") != "agent"
                or metadata.get("ls_integration") != "langchain_chat_model"
            ):
                continue
            delta = str(message_chunk.text)
            if delta:
                yield MessageStreamEvent(delta=delta)
            continue

        if part["type"] != "updates":
            continue

        for node, update in part["data"].items():
            if node not in {"load_song_context", "agent", "tools"}:
                continue
            yield UpdateStreamEvent(node=node, status="completed")
            if node == "agent" and isinstance(update, dict):
                response = update.get("response")
                if response is not None:
                    final_response = STRUCTURED_OUTPUT_ADAPTER.validate_python(response)

    if final_response is None:
        raise RuntimeError("Music agent graph returned no final response")
    yield FinalStreamEvent(response=final_response)
