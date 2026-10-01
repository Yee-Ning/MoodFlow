"""Handoff uses the existing public task/event projection and continuity runner."""

from app.agent.foundation.streaming import (
    invoke_foundation_assistant,
    stream_foundation_assistant,
)


async def invoke_handoff_assistant(**kwargs):
    return await invoke_foundation_assistant(**kwargs, graph_mode="handoff")


async def stream_handoff_assistant(**kwargs):
    async for event in stream_foundation_assistant(**kwargs, graph_mode="handoff"):
        yield event
