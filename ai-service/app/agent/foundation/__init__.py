"""LangGraph foundation contracts for MoodFlow's future multi-intent agent."""

from app.agent.foundation.graph_factory import create_main_graph
from app.agent.foundation.main_graph import build_main_graph
from app.agent.foundation.models import GraphError, TaskResult, TaskStatus
from app.agent.foundation.routing import RouteDecision, RouteKey, RouteTask
from app.agent.foundation.state import BaseAgentState, MainState, create_main_state

__all__ = [
    "BaseAgentState",
    "GraphError",
    "MainState",
    "RouteDecision",
    "RouteKey",
    "RouteTask",
    "TaskResult",
    "TaskStatus",
    "build_main_graph",
    "create_main_graph",
    "create_main_state",
]
