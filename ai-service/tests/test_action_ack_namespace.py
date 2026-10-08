"""ACK namespace, immutable History binding and transport compatibility."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.agent.foundation.long_horizon.coordinator import ContinuityCoordinator
from app.agent.foundation.long_horizon.history_writer import digest
from app.agent.foundation.long_horizon.models import ContinuityIdentity, HistoryEventType
from app.agent.foundation.long_horizon.public_events import PublicAction
from app.agent.foundation.long_horizon.service import LongHorizonContextService
from app.agent.foundation.long_horizon.store import SQLiteContinuityStore
from app.main import app
from app.schemas.chat import ClientActionStreamEvent, FinalStreamEvent, FoundationResponse, FoundationTaskOutput
from app.schemas.client_actions import ClientAction


@pytest.fixture
def receipts(tmp_path, monkeypatch):
    coordinator = ContinuityCoordinator(LongHorizonContextService(SQLiteContinuityStore(tmp_path / "ack.db")))
    monkeypatch.setattr("app.agent.foundation.long_horizon.coordinator.get_continuity_coordinator", lambda: coordinator)
    async def resolve(*args):
        return "test-user"
    monkeypatch.setattr("app.api.chat.UserIdentityProvider.resolve", resolve)
    # Even if rollout/default selection changes, ACK must never consult it.
    def forbidden(*args):
        raise AssertionError("ACK used rollout defaults")
    monkeypatch.setattr("app.api.chat.select_chat_graph", forbidden)
    return coordinator, TestClient(app)


def save_action(coordinator, mode, action_id, *, thread="ack-test"):
    conversation = f"handoff:v1:{thread}" if mode == "handoff" else thread
    session = coordinator.service.open_conversation(conversation)
    lease = coordinator.locks.acquire(conversation, digest([mode, "test-user"]))
    lease.release()
    identity = ContinuityIdentity(conversation_id=conversation, continuity_task_id=session.task_id,
                                 session_id=session.session_id, request_id="create", graph_mode=mode)
    event = coordinator.writer.append_public_event(identity, "action:" + action_id, HistoryEventType.CLIENT_ACTION,
        PublicAction(action_id=action_id, name="pause_player", arguments_digest=digest({})))
    return event


@pytest.mark.parametrize("mode", ["foundation", "handoff"])
def test_explicit_and_legacy_receipts_bind_to_history_once(receipts, mode):
    coordinator, client = receipts
    action = save_action(coordinator, mode, "action-one")
    body = {"thread_id": "ack-test", "action_id": "action-one", "status": "applied"}
    for dto in [body, {**body, "graph_mode": mode}, body]:
        response = client.post("/api/ai/actions/ack", json=dto)
        assert response.status_code == 200, response.text
        assert response.json() == {"action_id": "action-one", "status": "applied"}
    rows = coordinator.store.query_task_history(action.task_id, event_type=HistoryEventType.ACTION_ACK)
    assert len(rows) == 1
    ack = rows[0]
    assert ack.payload["action_ref"] == {"kind": "event", "target_id": action.event_id}
    assert (ack.task_id, ack.session_id, ack.payload["action_id"]) == (action.task_id, action.session_id, "action-one")
    conflict = client.post("/api/ai/actions/ack", json={**body, "status": "failed"})
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "ack_state_conflict"


def test_wrong_mode_unknown_thread_and_foreign_identity_are_rejected(receipts, monkeypatch):
    coordinator, client = receipts
    action = save_action(coordinator, "handoff", "owned")
    body = {"thread_id": "ack-test", "action_id": "owned", "status": "applied"}
    mismatch = client.post("/api/ai/actions/ack", json={**body, "graph_mode": "foundation"})
    assert mismatch.status_code == 409
    assert mismatch.json()["detail"]["code"] == "ack_graph_mode_mismatch"
    for dto in [{**body, "action_id": "unknown"}, {**body, "thread_id": "wrong"}]:
        response = client.post("/api/ai/actions/ack", json=dto)
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "ack_action_unavailable"
    async def foreign(*args):
        return "foreign"
    monkeypatch.setattr("app.api.chat.UserIdentityProvider.resolve", foreign)
    assert client.post("/api/ai/actions/ack", json=body).status_code == 409
    assert not coordinator.store.query_task_history(action.task_id, event_type=HistoryEventType.ACTION_ACK)


def test_ambiguous_id_requires_mode_and_closed_task_keeps_its_binding(receipts):
    coordinator, client = receipts
    one = save_action(coordinator, "foundation", "shared")
    two = save_action(coordinator, "handoff", "shared")
    body = {"thread_id": "ack-test", "action_id": "shared", "status": "applied"}
    response = client.post("/api/ai/actions/ack", json=body)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ack_action_ambiguous"
    closed_at = datetime.now(UTC).isoformat()
    coordinator.store.close_session(two.session_id, closed_at)
    coordinator.store.close_task(two.task_id, closed_at)
    save_action(coordinator, "handoff", "new-task-action")
    assert client.post("/api/ai/actions/ack", json={**body, "graph_mode": "handoff"}).status_code == 200
    assert len(coordinator.store.query_task_history(two.task_id, event_type=HistoryEventType.ACTION_ACK)) == 1
    assert not coordinator.store.query_task_history(one.task_id, event_type=HistoryEventType.ACTION_ACK)


def test_request_validation(receipts):
    _, client = receipts
    body = {"thread_id": "ack-test", "action_id": "one", "status": "applied"}
    for dto in [{**body, "graph_mode": "legacy"}, {**body, "thread_id": ""},
                {**body, "status": "unknown"}, {**body, "extra": True}]:
        assert client.post("/api/ai/actions/ack", json=dto).status_code == 422


@pytest.mark.parametrize("mode", ["foundation", "handoff"])
def test_json_and_sse_stamp_action_owner_without_changing_business_dto(receipts, monkeypatch, mode):
    _, client = receipts
    action = ClientAction.model_validate({"action_id": "wire-action", "task_id": "business-task",
                                         "name": "pause_player", "arguments": {}})
    original = action.model_dump(mode="json")
    response = FoundationResponse(status="success", message="已暂停", results=[FoundationTaskOutput(
        task_id="business-task", status="success", subgraph="tool_calling", answer="已暂停",
        metadata={"client_actions": [original]})])
    async def invoke(**kwargs):
        return response
    async def stream(**kwargs):
        yield ClientActionStreamEvent(action=action)
        yield FinalStreamEvent(response=response)
    monkeypatch.setattr(f"app.api.chat.invoke_{mode}_assistant", invoke)
    monkeypatch.setattr(f"app.api.chat.stream_{mode}_assistant", stream)
    monkeypatch.setattr("app.api.chat.select_chat_graph", lambda *args: mode)
    body = {"thread_id": "wire-thread", "message": "暂停播放"}
    expected = {**original, "graph_mode": mode, "thread_id": "wire-thread"}
    result = client.post(f"/api/ai/{mode}", json=body)
    assert result.status_code == 200, result.text
    assert result.json()["results"][0]["metadata"]["client_actions"] == [expected]
    wire = client.post("/api/ai/chat", json=body).text
    import json
    assert "event: action\ndata: " + json.dumps(expected, ensure_ascii=False, separators=(",", ":")) in wire
    assert action.model_dump(mode="json") == original
    assert response.results[0].metadata["client_actions"] == [original]
