"""Actual Foundation graph, PostgreSQL saver, automatic rollover and archival."""

import os

import pytest
from langgraph.graph import END, START, StateGraph

from app.agent.checkpointing import AsyncCompatiblePostgresSaver, create_checkpoint_serializer
from app.agent.foundation.long_horizon.coordinator import ContinuityCoordinator
from app.agent.foundation.long_horizon.models import HistoryEventType, HandoffProjectionV2
from app.agent.foundation.long_horizon.postgres_store import PostgresContinuityStore
from app.agent.foundation.long_horizon.public_events import PublicDecision
from app.agent.foundation.long_horizon.runner import invoke
from app.agent.foundation.long_horizon.service import LongHorizonContextService, RolloverPolicy
from app.agent.foundation.main_graph import build_main_graph
from app.agent.foundation.models import TaskResult
from app.agent.foundation.routing import RouteDecision, RouteKey, RouteTask, RoutingContext
from app.agent.foundation.subgraphs.general_chat.state import GeneralChatState
from tests.test_context_loop_postgres_recovery import database
from tests.test_foundation_projection import GOAL, CONCLUSION, TODO

pytestmark = pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="isolated PostgreSQL acceptance requires DATABASE_URL")
__all__ = ["database"]


@pytest.mark.asyncio
@pytest.mark.parametrize("automatic", [False, True])
async def test_continue_task_across_real_foundation_rollover_has_no_missing_material(database, automatic):
    store = PostgresContinuityStore(database)
    service = LongHorizonContextService(store, policy=RolloverPolicy(active_context_threshold=1 if automatic else 5200))
    coordinator = ContinuityCoordinator(service)
    observed, executions = [], []

    class Router:
        async def route(self, message, context):
            if message == GOAL:
                return RouteDecision(is_complete=True, tasks=[
                    RouteTask(task_id="search", route_key=RouteKey.GENERAL_CHAT, input_text="筛选候选"),
                    RouteTask(task_id="confirm", route_key=RouteKey.GENERAL_CHAT, input_text=TODO)])
            return RouteDecision(is_complete=True, tasks=[
                RouteTask(task_id="continue", route_key=RouteKey.GENERAL_CHAT, input_text=message)])

    def answer(state):
        observed.append(state.get("continuity_context", {}))
        executions.append(state["task_id"])
        result = TaskResult(task_id=state["task_id"], route_key="general_chat",
            status="needs_input" if state["task_id"] == "confirm" else "success",
            content=CONCLUSION if state["task_id"] == "search" else "请确认名单")
        return {"task_result": result, "answer": result.content}

    builder = StateGraph(GeneralChatState)
    builder.add_node("answer", answer)
    builder.add_edge(START, "answer")
    builder.add_edge("answer", END)
    graph = build_main_graph(intent_router=Router(), general_chat_subgraph=builder.compile()).compile(
        checkpointer=AsyncCompatiblePostgresSaver(store._pool, serde=create_checkpoint_serializer()))
    options = dict(thread_id="foundation-real-rollover", graph_mode="foundation", graph=graph,
        coordinator=coordinator, routing_context=RoutingContext())
    try:
        await invoke(message=GOAL, request_id="first", decisions=(
            PublicDecision(decision_id="constraint", kind="constraint", text="不要现场版"),
            PublicDecision(decision_id="decision", text="按候选名单继续")), **options)
        task = store.active_task(options["thread_id"])
        old = store.active_session(task.task_id)
        before = {row.event_id: row.model_dump_json() for row in store.query_task_history(task.task_id, limit=100)}
        response = await invoke(message="继续刚才那个任务", request_id="second", new_session=not automatic, **options)
        assert response.status == "success"
        new = store.active_session(task.task_id)
        assert new.session_id != old.session_id and new.previous_session_id == old.session_id
        projection = store.get_handoff(new.inherited_handoff_id)
        assert isinstance(projection, HandoffProjectionV2)
        material = {"goal": projection.goal == GOAL,
            "conclusion": CONCLUSION in projection.summary and CONCLUSION in projection.completed_steps,
            "todo": TODO in projection.pending_items,
            "next": "继续待办 " + TODO in projection.next_actions,
            "constraints": "不要现场版" in projection.constraints,
            "decisions": "按候选名单继续" in projection.decisions,
            "refs": bool(projection.key_message_refs)}
        assert [field for field, present in material.items() if not present] == []
        assert observed[-1]["handoff"] == projection.model_dump(mode="json")
        assert observed[-1]["session_id"] == new.session_id
        assert executions == ["search", "confirm", "continue"]  # no projection/model dispatch
        assert projection.token_count == service.counter.count(projection.model_dump_json(exclude_none=True)) <= 1200
        assert projection.agent_progress.last_agent is None and projection.source_checkpoint_ref is None
        assert not store.query_task_history(task.task_id, event_type=HistoryEventType.RUN_PROGRESS)
        archives = store.query_task_history(task.task_id, event_type=HistoryEventType.SESSION_PROJECTION)
        assert len(archives) == 1 and HandoffProjectionV2.model_validate(archives[0].payload["projection"]) == projection
        after = {row.event_id: row.model_dump_json() for row in store.query_task_history(task.task_id, limit=100)}
        assert all(after[key] == body for key, body in before.items())
        assert len(store.sessions_for_task(task.task_id)) == 2
    finally:
        store.close()
