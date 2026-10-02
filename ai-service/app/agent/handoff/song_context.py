"""Read-only song context tool scoped to one trusted task snapshot."""

from collections.abc import Sequence

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import Field

from app.agent.foundation.models import TaskResult, TaskStatus
from app.agent.foundation.routing import RoutingContext
from app.agent.foundation.subgraphs.current_song.graph import (
    CONTEXT_UNAVAILABLE_MESSAGE,
    NO_CURRENT_SONG_MESSAGE,
    PENDING_QUEUE_CHANGE_MESSAGE,
    _previous_action_target,
)
from app.agent.handoff.contracts import ContractModel
from app.context.music import MusicContextProvider
from app.schemas.context import SongContext


class SongContextArguments(ContractModel):
    pass


class ReadOnlySongTool(StructuredTool):
    def _to_args_and_kwargs(self, tool_input, tool_call_id):
        # LangChain otherwise short-circuits validation for an empty schema.
        SongContextArguments.model_validate(tool_input)
        return (), {}


class SongContextRead(ContractModel):
    status: TaskStatus
    reason: str
    message: str
    song: SongContext | None = None
    song_id: int | None = Field(default=None, gt=0)
    target_source: str | None = None


async def invoke_song_context(context_tool, arguments, *, tool_call_id=None):
    """Journal direct reads too; ToolNode already journals its own invocations."""
    from uuid import uuid4
    from types import SimpleNamespace
    from langchain_core.messages import ToolMessage
    from app.agent.foundation.long_horizon.operations import active_operations

    operations = active_operations.get()
    if operations is None:
        return await context_tool.ainvoke(arguments)
    call_id = tool_call_id or "song-context:" + uuid4().hex
    async def execute(_):
        content = await context_tool.ainvoke(arguments)
        return ToolMessage(content=content, name=context_tool.name, tool_call_id=call_id)
    response = await operations.call(SimpleNamespace(tool_call={"id": call_id, "name": context_tool.name, "args": arguments}), execute)
    return response.content


def create_song_context_tool(
    context: RoutingContext,
    prior_results: Sequence[TaskResult] = (),
    *,
    provider: MusicContextProvider | None = None,
) -> BaseTool:
    snapshot = RoutingContext.model_validate(context.model_dump())
    prior = [TaskResult.model_validate(item.model_dump()) for item in prior_results]
    source = provider or MusicContextProvider()

    async def read_song() -> str:
        """只读获取本轮可信播放器或前置播放动作所指歌曲的资料；无需填写歌曲 ID。"""
        song_id, pending = _previous_action_target(prior)
        target_source = "prior_play_action" if song_id is not None else "player_context"
        if song_id is None and pending:
            return SongContextRead(
                status=TaskStatus.NEEDS_INPUT,
                reason="pending_queue_change",
                message=PENDING_QUEUE_CHANGE_MESSAGE,
            ).model_dump_json()
        if song_id is None:
            song_id = snapshot.current_song_id if snapshot.has_current_song else None
        if song_id is None:
            return SongContextRead(
                status=TaskStatus.NEEDS_INPUT,
                reason="no_current_song",
                message=NO_CURRENT_SONG_MESSAGE,
            ).model_dump_json()
        try:
            song = SongContext.model_validate(await source.get_song(song_id))
            if song.song_id != song_id:
                raise ValueError("song context ID mismatch")
        except Exception:
            return SongContextRead(
                status=TaskStatus.FAILED,
                reason="context_unavailable",
                message=CONTEXT_UNAVAILABLE_MESSAGE,
                song_id=song_id,
                target_source=target_source,
            ).model_dump_json()
        return SongContextRead(
            status=TaskStatus.SUCCESS,
            reason="resolved",
            message="歌曲资料已获取。",
            song=song,
            song_id=song_id,
            target_source=target_source,
        ).model_dump_json()

    return ReadOnlySongTool.from_function(
        coroutine=read_song,
        name="get_current_song_context",
        description=read_song.__doc__,
        args_schema=SongContextArguments,
    )
