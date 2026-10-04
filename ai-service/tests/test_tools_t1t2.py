"""New tools share A's ledger and prove deletion without a second business write."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import ToolMessage
from pydantic import ValidationError

from app.agent.foundation.long_horizon.accounting import DurableAccounting
from app.agent.foundation.long_horizon.coordinator import ContinuityCoordinator
from app.agent.foundation.long_horizon.execution import ContinuityConflict, ExecutionLocks
from app.agent.foundation.long_horizon.history_writer import HistoryWriter, digest
from app.agent.foundation.long_horizon.models import ContinuityIdentity
from app.agent.foundation.long_horizon.operations import OperationJournal, active_operations, safe_tool_result
from app.agent.foundation.long_horizon.public_events import PublicTool
from app.agent.foundation.long_horizon.service import LongHorizonContextService
from app.agent.foundation.long_horizon.store import SQLiteContinuityStore
from app.agent.foundation.subgraphs.tool_calling.graph import _action_intent_errors, _reference_errors
from app.main import app
from app.schemas.context import MusicAgentContext
from app.agent.foundation.routing import RoutingContext
from app.schemas.music import DeletedPlaylistResult, PlaylistDetailResult, UserPlaylistsResult
from app.tools.music import (
    MUSIC_AGENT_TOOLS, MusicServiceClient, MusicServiceError, delete_playlist_tool,
    get_liked_songs_tool, has_delete_confirmation,
)


@pytest.fixture
def journal(tmp_path):
    store = SQLiteContinuityStore(tmp_path / "history.db")
    service = LongHorizonContextService(store)
    session = service.open_conversation("handoff:v1:t1t2")
    identity = ContinuityIdentity(conversation_id=session.conversation_id, continuity_task_id=session.task_id,
        session_id=session.session_id, request_id="test", graph_mode="handoff", run_id="run:t1t2")
    lease = ExecutionLocks(store).acquire(session.conversation_id, digest(["handoff", "42"]))
    accounting = DurableAccounting(HistoryWriter(store), identity, lease)
    yield OperationJournal(accounting.writer, accounting)
    lease.release()


def read_client(present=False, owner=42, special_type=0):
    from app.schemas.music import UserPlaylist
    return SimpleNamespace(
        get_account_id=AsyncMock(return_value=42),
        get_playlist_detail=AsyncMock(return_value=PlaylistDetailResult(
            playlist_id=77, name="MoodFlowTest-test", description="", creator="Test", cover_url="",
            track_count=0, songs=[], creator_id=owner, special_type=special_type, subscribed=False)),
        get_user_playlists=AsyncMock(return_value=UserPlaylistsResult(playlists=[
            UserPlaylist(playlist_id=77, name="MoodFlowTest-test", editable=True)] if present else [])),
    )


def request(confirmed=True, call_id="delete-one"):
    return SimpleNamespace(tool_call={"id": call_id, "name": "delete_playlist_tool",
        "args": {"playlist_id": 77, "confirmed": confirmed}})


@pytest.mark.asyncio
async def test_like_tool_is_authenticated_read_only_id_set(monkeypatch):
    seen = []
    def backend(req):
        seen.append((req.method, req.url.path, req.headers.get("cookie")))
        return httpx.Response(200, json={"code": 200, "message": "ok", "data": [102, 101, 102]})
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(**kw, transport=httpx.MockTransport(backend)))
    runtime = SimpleNamespace(context=MusicAgentContext(session_cookie="JSESSIONID=test"))
    result = json.loads(await get_liked_songs_tool.coroutine(runtime))
    assert result == {"song_ids": [101, 102]}
    assert seen == [("GET", "/api/user/liked-songs", "JSESSIONID=test")]
    assert "只读" in get_liked_songs_tool.description
    assert {"get_liked_songs", "delete_playlist_tool"} <= {tool.name for tool in MUSIC_AGENT_TOOLS}
    assert safe_tool_result("get_liked_songs", json.dumps(result))["result"] == result
    with pytest.raises(ValidationError):
        PublicTool(run_id="r", operation_id="o", name="get_liked_songs", effect="write", status="proposed")


@pytest.mark.parametrize("text,allowed", [
    ("确认删除歌单 77", True), ("确定删除整张歌单 ID=77", True),
    ("删除歌单 77", False), ("确认删除歌单 177", False),
    ("不要确认删除歌单 77", False), ("能不能确认删除歌单 77", False),
    ("取消删除，确认歌单 77", False), ("我已经确认，删除歌单 77", False),
    ("解释‘确认删除歌单 77’是什么意思", False), ("确认删除歌单 77 是什么意思", False),
    ("确认删除歌单 77 和 88", False), ("确认删除歌单 77”", True),
    ("确认删除歌单 77，但是不要执行", False), ("确认删除歌单 77。取消删除", False),
])
def test_confirmation_is_user_text_with_exact_target(text, allowed):
    assert has_delete_confirmation(text, 77) is allowed


@pytest.mark.asyncio
@pytest.mark.parametrize("text,confirmed", [("删除歌单 77", True), ("确认删除歌单 77", False),
    ("确认删除歌单 77，但是不要执行", True)])
async def test_model_confirmation_cannot_dispatch_without_user_confirmation(monkeypatch, text, confirmed):
    backend = AsyncMock()
    monkeypatch.setattr(MusicServiceClient, "delete_playlist", backend)
    runtime = SimpleNamespace(state={"current_input": text}, context=MusicAgentContext())
    with pytest.raises(MusicServiceError, match="未执行删除"):
        await delete_playlist_tool.coroutine(77, runtime, confirmed)
    backend.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_requires_journal_even_with_explicit_confirmation(monkeypatch):
    backend = AsyncMock()
    monkeypatch.setattr(MusicServiceClient, "delete_playlist", backend)
    token = active_operations.set(None)
    try:
        with pytest.raises(MusicServiceError, match="operation journal"):
            await delete_playlist_tool.coroutine(77, SimpleNamespace(state={"current_input": "确认删除歌单 77"}), True)
    finally:
        active_operations.reset(token)
    backend.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_response_must_reconcile_and_has_one_success_receipt(journal):
    client = read_client()
    operations = OperationJournal(journal.writer, journal.accounting, read_client=client)
    execute = AsyncMock(return_value=ToolMessage(content=DeletedPlaylistResult(
        playlist_id=77, status="deleted", message="已删除").model_dump_json(), tool_call_id="delete-one"))
    result = await operations.call(request(), execute)
    oid = operations.write_events()[0].payload["operation_id"]
    state = operations.state(oid)
    assert state["state"] == "confirmed" and state["confirmation_source"] == "readback"
    assert [e.payload["status"] for e in operations.write_events(oid)] == ["proposed", "unknown", "unknown", "success"]
    evidence = operations.read_artifact(state["reconciliation_ref"])
    assert evidence["observations"]["present"] is False
    assert evidence["reads"] == ["GET /api/user/profile", "GET /api/user/playlists"]
    assert json.loads(result.content)["status"] == "deleted"
    await operations.call(request(call_id="model-reinvented-id"), execute)
    execute.assert_awaited_once()
    client.get_user_playlists.assert_awaited_once()
    assert "只读核对" in await operations.answer_query("刚才歌单删除了吗")


@pytest.mark.asyncio
async def test_delete_unknown_never_repeats_then_readback_heals(journal):
    client = read_client(present=True)
    operations = OperationJournal(journal.writer, journal.accounting, read_client=client)
    execute = AsyncMock(side_effect=MusicServiceError("http_502", "读回失败，原始错误"))
    with pytest.raises(MusicServiceError):
        await operations.call(request(), execute)
    oid = operations.write_events()[0].payload["operation_id"]
    assert operations.state(oid)["state"] == "unknown"
    with pytest.raises(ContinuityConflict):
        await operations.call(request(call_id="again"), execute)
    execute.assert_awaited_once()
    client.get_user_playlists.return_value = UserPlaylistsResult(playlists=[])
    assert (await operations.reconcile(oid))["state"] == "confirmed"
    assert any(e.payload.get("error_message") == "读回失败，原始错误" for e in operations.write_events(oid))
    execute.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("owner,special,confirmed", [(99, 0, True), (42, 5, True), (None, None, True), (42, 0, False)])
async def test_baseline_failure_is_failed_with_no_business_dispatch(journal, owner, special, confirmed):
    operations = OperationJournal(journal.writer, journal.accounting, read_client=read_client(owner=owner, special_type=special))
    execute = AsyncMock()
    with pytest.raises(MusicServiceError):
        await operations.call(request(confirmed), execute)
    assert operations.state(operations.write_events()[0].payload["operation_id"])["state"] == "failed"
    execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_account_change_after_delete_blocks_confirmation(journal):
    client = read_client()
    client.get_account_id.side_effect = [42, 99]
    operations = OperationJournal(journal.writer, journal.accounting, read_client=client)
    execute = AsyncMock(return_value=ToolMessage(content='{"playlist_id":77,"status":"deleted","message":"OK"}', tool_call_id="one"))
    result = await operations.call(request(), execute)
    state = operations.state(operations.write_events()[0].payload["operation_id"])
    assert state["state"] == "unknown" and result.status == "error"
    assert operations.read_artifact(state["reconciliation_ref"])["error_code"] == "delete_account_mismatch"
    client.get_user_playlists.assert_not_awaited()


def test_graph_rejects_delete_without_explicit_user_request():
    from langchain_core.messages import AIMessage
    state = {"current_input": "查看歌单 77", "messages": [AIMessage(content="", tool_calls=[
        {"name": "delete_playlist_tool", "id": "bad", "args": {"playlist_id": 77, "confirmed": True}}])],
        "prior_entities": [], "tool_results": [], "routing_context": RoutingContext()}
    assert "bad" in _action_intent_errors(state)
    assert "bad" in _reference_errors(state)


def test_cloud_like_ids_are_trusted_for_like_mutations_only():
    from langchain_core.messages import AIMessage
    state = {"current_input": "取消红心", "messages": [AIMessage(content="", tool_calls=[
        {"name": "set_song_liked_tool", "id": "unlike", "args": {"song_id": 101, "liked": False}}])],
        "prior_entities": [], "tool_results": [{"name": "get_liked_songs", "content": '{"song_ids":[101]}'}],
        "routing_context": RoutingContext()}
    assert not _reference_errors(state)
    state["tool_results"] = [{"name": "get_liked_songs", "content": '{"song_ids":["101"]}'}]
    assert "unlike" in _reference_errors(state)


@pytest.mark.asyncio
async def test_status_api_queries_a_states_without_reads_or_receipts(journal, monkeypatch):
    execute = AsyncMock(side_effect=MusicServiceError("http_502", "unknown"))
    with pytest.raises(MusicServiceError):
        await journal.call(SimpleNamespace(tool_call={"name": "set_song_liked_tool", "id": "like",
            "args": {"song_id": 101, "liked": True}}), execute)
    oid = journal.write_events()[0].payload["operation_id"]
    coordinator = ContinuityCoordinator(LongHorizonContextService(journal.writer.store))
    monkeypatch.setattr("app.agent.foundation.long_horizon.coordinator.get_continuity_coordinator", lambda: coordinator)
    async def resolve(*args):
        return "42"
    monkeypatch.setattr("app.api.chat.UserIdentityProvider.resolve", resolve)
    client = TestClient(app)
    dto = {"thread_id": "t1t2", "graph_mode": "handoff", "operation_id": oid}
    count = len(journal.write_events())
    response = client.post("/api/ai/continuity/operations/status", json=dto)
    assert response.status_code == 200 and response.json()["state"] == "unknown"
    assert len(journal.write_events()) == count
    assert client.post("/api/ai/continuity/operations/status", json={**dto, "operation_id": "bad"}).status_code == 422
    assert client.post("/api/ai/continuity/operations/status", json={**dto, "graph_mode": "foundation"}).status_code == 409
    with pytest.raises(ContinuityConflict):
        coordinator.write_operation_status(**dto, user_id="99")
    async def anonymous(*args):
        return None
    monkeypatch.setattr("app.api.chat.UserIdentityProvider.resolve", anonymous)
    assert client.post("/api/ai/continuity/operations/status", json=dto).status_code == 401
