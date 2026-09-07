"""LangGraph foundation contracts for MoodFlow's future multi-intent agent."""

from app.agent.foundation.graph_factory import create_main_graph
from app.agent.foundation.main_graph import build_main_graph
from app.agent.foundation.models import GraphError, TaskResult, TaskStatus
from app.agent.foundation.routing import (
    IntentItem,
    IntentSource,
    IntentType,
    PlayMode,
    PlayerState,
    RouteDecision,
    RouteKey,
    RouteTask,
    RoutingContext,
)
from app.agent.foundation.state import BaseAgentState, MainState, create_main_state

__all__ = [
    "BaseAgentState",
    "GraphError",
    "IntentItem",
    "IntentSource",
    "IntentType",
    "MainState",
    "PlayMode",
    "PlayerState",
    "RouteDecision",
    "RouteKey",
    "RouteTask",
    "RoutingContext",
    "TaskResult",
    "TaskStatus",
    "build_main_graph",
    "create_main_graph",
    "create_main_state",
]
