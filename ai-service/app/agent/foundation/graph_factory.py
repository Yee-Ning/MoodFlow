from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from app.agent.checkpointing import create_memory_checkpointer
from app.agent.foundation.main_graph import build_main_graph
from app.agent.foundation.routing import RouteKey
if TYPE_CHECKING:
    from app.agent.foundation.hybrid_routing import AsyncIntentRouter

CheckpointerFactory = Callable[[], BaseCheckpointSaver]


def create_main_graph(
    *,
    development_routes: Sequence[RouteKey] | None = None,
    intent_router: AsyncIntentRouter | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
    checkpointer_factory: CheckpointerFactory = create_memory_checkpointer,
) -> CompiledStateGraph:
    """Compile MainGraph with one injectable persistence boundary."""
    resolved_checkpointer = (
        checkpointer if checkpointer is not None else checkpointer_factory()
    )
    return build_main_graph(development_routes=development_routes, intent_router=intent_router).compile(
        checkpointer=resolved_checkpointer
    )
