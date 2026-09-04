from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, MessagesState, StateGraph

from app.agent.song_assistant import answer_current_song
from app.context.builder import build_context
from app.context.music import MusicContextProvider
from app.schemas.chat import (
    STRUCTURED_OUTPUT_ADAPTER,
    ChatContext,
    FinalStreamEvent,
    MessageStreamEvent,
    SongAssistantStreamEvent,
    StructuredOutput,
    UpdateStreamEvent,
)
from app.schemas.context import SongContext


class CurrentSongState(MessagesState):
    current_song_id: int | None
    song_context: SongContext | None
    response: dict[str, Any] | None


SongAssistant = Callable[
    [list[BaseMessage], int | None, SongContext | None],
    Awaitable[StructuredOutput],
]


def create_memory_checkpointer() -> InMemorySaver:
    serializer = JsonPlusSerializer(
        allowed_msgpack_modules=[("app.schemas.context", "SongContext")]
    )
    return InMemorySaver(serde=serializer)


def create_current_song_graph(
    *,
    provider: MusicContextProvider | None = None,
    assistant: SongAssistant = answer_current_song,
    checkpointer: InMemorySaver | None = None,
):
    async def load_song_context(state: CurrentSongState) -> dict[str, Any]:
        current_song_id = state.get("current_song_id")
        if current_song_id is None:
            return {"song_context": None}

        resolved = await build_context(
            ChatContext(current_song_id=current_song_id),
            provider=provider,
        )
        return {"song_context": resolved.current_song}

    async def song_assistant(state: CurrentSongState) -> dict[str, Any]:
        result = await assistant(
            state["messages"],
            state.get("current_song_id"),
            state.get("song_context"),
        )
        return {
            "messages": [AIMessage(content=result.message)],
            "response": result.model_dump(mode="json"),
        }

    builder = StateGraph(CurrentSongState)
    builder.add_node("load_song_context", load_song_context)
    builder.add_node("song_assistant", song_assistant)
    builder.add_edge(START, "load_song_context")
    builder.add_edge("load_song_context", "song_assistant")
    builder.add_edge("song_assistant", END)
    return builder.compile(checkpointer=checkpointer or create_memory_checkpointer())


CURRENT_SONG_GRAPH = create_current_song_graph()


async def stream_current_song_assistant(
    message: str,
    thread_id: str,
    current_song_id: int | None,
    *,
    graph: Any | None = None,
) -> AsyncIterator[SongAssistantStreamEvent]:
    final_response: StructuredOutput | None = None
    async for part in (graph or CURRENT_SONG_GRAPH).astream(
        {
            "messages": [HumanMessage(content=message)],
            "current_song_id": current_song_id,
            "song_context": None,
            "response": None,
        },
        {"configurable": {"thread_id": thread_id}},
        stream_mode=["messages", "updates"],
        version="v2",
    ):
        if part["type"] == "messages":
            message_chunk, metadata = part["data"]
            if (
                metadata.get("langgraph_node") != "song_assistant"
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
            if node not in {"load_song_context", "song_assistant"}:
                continue
            yield UpdateStreamEvent(node=node, status="completed")
            if node == "song_assistant" and isinstance(update, dict):
                response = update.get("response")
                if response is not None:
                    final_response = STRUCTURED_OUTPUT_ADAPTER.validate_python(response)

    if final_response is None:
        raise RuntimeError("Song assistant graph returned no response")
    yield FinalStreamEvent(response=final_response)
