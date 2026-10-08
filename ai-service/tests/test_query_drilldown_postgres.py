"""Real PostgreSQL Journal pagination and current-query production entry wiring."""

from datetime import UTC, datetime
import os
from types import SimpleNamespace

import pytest
from langgraph.graph import END, START, StateGraph

from app.agent.checkpointing import AsyncCompatiblePostgresSaver, create_checkpoint_serializer, create_memory_checkpointer
from app.agent.foundation.long_horizon.coordinator import ContinuityCoordinator
from app.agent.foundation.long_horizon.drilldown import HistoryDrilldown
from app.agent.foundation.long_horizon.models import ContinuityIdentity, HistoryEventType
from app.agent.foundation.long_horizon.postgres_store import PostgresContinuityStore
from app.agent.foundation.long_horizon.public_events import PublicDecision
from app.agent.foundation.long_horizon.runner import invoke
from app.agent.foundation.long_horizon.service import LongHorizonContextService
from app.agent.foundation.main_graph import build_main_graph
from app.agent.foundation.models import TaskResult
from app.agent.foundation.routing import RouteKey, RoutingContext
from app.agent.foundation.subgraphs.general_chat.state import GeneralChatState
from tests.test_context_loop_postgres_recovery import database
from tests.test_query_drilldown import bounded, message, refs

pytestmark = pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="isolated PostgreSQL acceptance requires DATABASE_URL")
__all__ = ["database"]


@pytest.fixture
def history(database):
    store = PostgresContinuityStore(database)
    coordinator = ContinuityCoordinator(LongHorizonContextService(store))
    session = coordinator.service.open_conversation("query-pg")
    identity = ContinuityIdentity(conversation_id=session.conversation_id, continuity_task_id=session.task_id,
        session_id=session.session_id, request_id="fixture", graph_mode="foundation")
    try:
        yield SimpleNamespace(store=store, coordinator=coordinator, reader=coordinator.history,
                              session=session, identity=identity)
    finally:
        store.close()


def test_postgres_ranked_pages_cross_sessions_and_survive_append_without_repeats(history):
    h = history
    rows = [message(h, str(index), "blueberry" + (" studio" if index % 4 == 0 else "")) for index in range(19)]
    h.store.close_session(h.session.session_id, datetime.now(UTC).isoformat())
    session = h.coordinator.service.open_task(h.session.task_id)
    current = h.identity.model_copy(update={"session_id": session.session_id})
    for index in range(19, 23):
        rows.append(message(h, str(index), "blueberry studio", identity=current))
    first = h.reader.read(current, query="blueberry studio", count=2, tokens=320)
    inserted = message(h, "later", "blueberry studio", identity=current)
    again = HistoryDrilldown(h.coordinator.writer)
    cursor, seen = first.next_cursor, refs(first)
    for _ in range(25):
        page = again.read(current, query="blueberry studio", cursor=cursor, count=2, tokens=320)
        bounded(h, page, 320)
        seen.extend(refs(page))
        cursor = page.next_cursor
        if not page.truncated:
            break
    else:
        pytest.fail("query pagination never finished")
    assert len(seen) == len(set(seen)) == len(rows)
    assert {target for _, target in seen} == {row.event_id for row in rows}
    assert ("event", inserted.event_id) not in seen
    assert ("event", inserted.event_id) in refs(again.read(current, query="blueberry studio", count=1))


@pytest.mark.asyncio
async def test_actual_foundation_entry_passes_current_query_to_history_and_contextpack(history):
    h = history
    for index in range(110):
        message(h, str(index), "无关的早期聊天")
    event = h.coordinator.writer.append_public_event(h.identity, "late", HistoryEventType.USER_DECISION,
        PublicDecision(decision_id="studio", text="blueberry studio 已确认，保留录音室版本"))
    assert ("event", event.event_id) not in refs(h.reader.read(h.identity))
    captured = []

    def answer(state):
        captured.append(state["continuity_context"])
        result = TaskResult(task_id=state["task_id"], route_key="general_chat", status="success",
                            content="沿用已确认的录音室版本")
        return {"task_result": result, "answer": result.content}

    builder = StateGraph(GeneralChatState)
    builder.add_node("answer", answer)
    builder.add_edge(START, "answer")
    builder.add_edge("answer", END)
    graph = build_main_graph(development_routes=[RouteKey.GENERAL_CHAT],
        general_chat_subgraph=builder.compile()).compile(
            checkpointer=AsyncCompatiblePostgresSaver(h.store._pool, serde=create_checkpoint_serializer()))
    before = {row.event_id: row.model_dump_json() for row in h.store.query_task_history(h.session.task_id, limit=200)}
    selected = h.reader.read(h.identity, query="blueberry studio 为什么选择录音室版本")
    assert refs(selected) == [("event", event.event_id)]
    assert before == {row.event_id: row.model_dump_json() for row in h.store.query_task_history(h.session.task_id, limit=200)}
    response = await invoke(message="blueberry studio 为什么选择录音室版本", thread_id=h.session.conversation_id,
        request_id="current", graph_mode="foundation", routing_context=RoutingContext(),
        coordinator=h.coordinator, graph=graph)
    assert response.status == "success" and len(captured) == 1
    assert captured[0]["key_events"][0]["ref"] == {"kind": "event", "target_id": event.event_id}
    from app.agent.foundation.context_engineering import build_context
    pack = build_context(RouteKey.GENERAL_CHAT, query="继续", system="系统规则", continuity_context=captured[0])
    assert event.event_id in str(pack.messages[0].content)
    after = {row.event_id: row.model_dump_json() for row in h.store.query_task_history(h.session.task_id, limit=200)}
    assert all(after[key] == body for key, body in before.items())
    # runner keeps its original one archival write. The direct query
    # above changed neither row count nor content; selection adds no write.
    assert len(h.store.query_task_history(h.session.task_id, event_type=HistoryEventType.HISTORY_ARCHIVE)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("graph_mode", ["foundation", "handoff"])
async def test_shared_begin_call_selects_current_query_for_both_graph_modes(history, graph_mode):
    h = history
    conversation = "handoff:v1:shared-query" if graph_mode == "handoff" else "shared-query"
    session = h.coordinator.service.open_conversation(conversation)
    identity = h.identity.model_copy(update={"conversation_id": conversation,
        "continuity_task_id": session.task_id, "session_id": session.session_id, "graph_mode": graph_mode})
    event = message(h, "topic", "blueberry studio", identity=identity)
    # This audits entry preparation before execution, independent of router/model.
    graph = build_main_graph(development_routes=[RouteKey.GENERAL_CHAT]).compile(checkpointer=create_memory_checkpointer())
    call = await h.coordinator.begin_call(graph=graph, graph_mode=graph_mode, thread_id="shared-query",
        message="blueberry studio", routing_context=RoutingContext(), request_id="current")
    try:
        assert call.graph_input["continuity_context"]["key_events"][0]["ref"]["target_id"] == event.event_id
    finally:
        await call.close()
