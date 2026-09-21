import os
from typing import Protocol

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer


class CheckpointerFactory(Protocol):
    """Construction boundary for local or persistent LangGraph checkpointers."""

    def __call__(self) -> BaseCheckpointSaver: ...


_persistent_checkpointer_factory: CheckpointerFactory | None = None


def create_memory_checkpointer() -> InMemorySaver:
    """Create the unit-test/local-fallback checkpointer for agent graphs."""
    serializer = JsonPlusSerializer(
        allowed_msgpack_modules=[
            ("app.schemas.context", "SongContext"),
            ("app.agent.foundation.models", "GraphError"),
            ("app.agent.foundation.models", "TaskResult"),
            ("app.agent.foundation.models", "TaskStatus"),
            ("app.agent.foundation.routing", "IntentItem"),
            ("app.agent.foundation.routing", "IntentSource"),
            ("app.agent.foundation.routing", "IntentType"),
            ("app.schemas.player", "PlayerState"),
            ("app.schemas.player", "PlayMode"),
            ("app.agent.foundation.routing", "RouteDecision"),
            ("app.agent.foundation.routing", "RouteKey"),
            ("app.agent.foundation.routing", "RouteTask"),
            ("app.agent.foundation.routing", "RoutingContext"),
            (
                "app.agent.foundation.subgraphs.tool_calling.models",
                "ReferencedEntities",
            ),
            (
                "app.agent.foundation.subgraphs.tool_calling.models",
                "SongReference",
            ),
            (
                "app.agent.foundation.subgraphs.tool_calling.models",
                "PlaylistReference",
            ),
        ]
    )
    return InMemorySaver(serde=serializer)


def configure_persistent_checkpointer(
    factory: CheckpointerFactory | None,
) -> None:
    """Install the process-startup provider for a persistent checkpointer."""
    global _persistent_checkpointer_factory
    _persistent_checkpointer_factory = factory


def create_runtime_checkpointer() -> BaseCheckpointSaver:
    """Resolve the configured runtime backend and fail closed when it is absent."""
    mode = os.getenv("AI_LANGGRAPH_CHECKPOINTER_MODE", "memory").strip().lower()
    if mode == "memory":
        return create_memory_checkpointer()
    if mode != "persistent":
        raise ValueError(f"unsupported LangGraph checkpointer mode:{mode}")
    if _persistent_checkpointer_factory is None:
        raise RuntimeError(
            "AI_LANGGRAPH_CHECKPOINTER_MODE=persistent requires a registered "
            "persistent checkpointer factory"
        )
    return _persistent_checkpointer_factory()
