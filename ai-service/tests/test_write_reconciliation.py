"""V7-shaped uncertain writes reconcile with a real append-only SQLite ledger."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from langchain_core.messages import ToolMessage

from app.agent.foundation.long_horizon.accounting import DurableAccounting
from app.agent.foundation.long_horizon.execution import ContinuityConflict, ExecutionLocks
from app.agent.foundation.long_horizon.history_writer import HistoryWriter, digest
from app.agent.foundation.long_horizon.models import ContinuityIdentity, HistoryEventType
from app.agent.foundation.long_horizon.operations import OperationJournal, active_operations
from app.agent.foundation.long_horizon.public_events import PublicTool
from app.agent.foundation.long_horizon.service import LongHorizonContextService
from app.agent.foundation.long_horizon.store import SQLiteContinuityStore
from app.agent.foundation.subgraphs.general_chat.assistant import answer_general_chat
from app.agent.foundation.routing import RouteKey, RoutingContext
from app.agent.handoff.graph import build_handoff_graph
from app.schemas.music import MusicTrack, PlaylistDetailResult, UserPlaylist, UserPlaylistsResult
from app.tools.music import MusicServiceClient, MusicServiceError, handle_music_tool_error
from tests.test_handoff_runtime import Router, input_state


@pytest.fixture
def journal(tmp_path):
    store = SQLiteContinuityStore(tmp_path / "write-history.db")
    session = LongHorizonContextService(store).open_conversation("test-write")
    identity = ContinuityIdentity(conversation_id=session.conversation_id, continuity_task_id=session.task_id,
        session_id=session.session_id, request_id="test-request", graph_mode="handoff", run_id="test-run")
    lease = ExecutionLocks(store).acquire("test-write", "test-account-scope")
    accounting = DurableAccounting(HistoryWriter(store), identity, lease)
    yield OperationJournal(accounting.writer, accounting)
    lease.release()


def request(name="add_song_to_playlist", args=None, call_id="one"):
    return SimpleNamespace(tool_call={"id": call_id, "name": name,
        "args": args if args is not None else {"playlist_id": 18445771229, "song_id": 2652820720}})


def operation_id(journal):
    return journal.write_events()[0].payload["operation_id"]


def reads(present=True, complete=True, editable=True):
    songs = [MusicTrack(id=2652820720, name="晴天(深情版)", artists=["Lucky小爱"], album="测试")] if present else []
    client = SimpleNamespace(
        get_user_playlists=AsyncMock(return_value=UserPlaylistsResult(playlists=[
            UserPlaylist(playlist_id=18445771229, name="MoodFlowTest-case", editable=editable)])),
        get_playlist_detail=AsyncMock(return_value=PlaylistDetailResult(playlist_id=18445771229,
            name="MoodFlowTest-case", description="", cover_url="", creator="测试账号",
            track_count=len(songs) if complete else len(songs) + 10, songs=songs)),
        get_liked_song_ids=AsyncMock(return_value=[2652820720] if present else []),
    )
    return client


@pytest.mark.asyncio
async def test_v7_http_502_after_effect_is_read_back_and_receipted_once(journal, monkeypatch):
    seen, membership = [], []
    def backend(req):
        seen.append((req.method, req.url.path))
        if req.method == "POST":
            membership.append({"id": 2652820720, "name": "晴天(深情版)", "artists": ["Lucky小爱"], "album": "测试"})
            return httpx.Response(502, json={"code": 502, "message": "网易云 API 响应缺少有效字段: code", "data": None})
        playlist = {"id": 18445771229, "name": "MoodFlowTest-case", "coverImgUrl": "",
                    "trackCount": len(membership), "creator": "测试账号", "subscribed": False}
        return httpx.Response(200, json={"code": 200, "message": "成功", "data":
            [playlist] if req.url.path == "/api/user/playlists" else
            {"playlist": playlist, "description": "", "songs": membership}})
    original_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(
        **kwargs, transport=httpx.MockTransport(backend)))
    client = MusicServiceClient("isolated-session")
    reconciled = OperationJournal(journal.writer, journal.accounting, read_client=client)
    async def execute(call):
        result = await client.add_song_to_playlist(**call.tool_call["args"])
        return ToolMessage(content=result.model_dump_json(), tool_call_id=call.tool_call["id"])
    response = await reconciled.call(request(), execute)
    oid = operation_id(reconciled)
    state = reconciled.state(oid)
    assert json.loads(response.content)["status"] == "added"
    assert state["state"] == "confirmed" and state["confirmation_source"] == "readback"
    assert reconciled.unknown_writes("test-run") == set()
    await reconciled.call(request(call_id="invented-new-call"), execute)
    await reconciled.reconcile(oid)
    assert sum(method == "POST" for method, _ in seen) == 1
    events = reconciled.write_events(oid)
    assert [row.payload["status"] for row in events] == ["proposed", "unknown", "unknown", "success"]
    assert events[1].payload["error_code"] == "http_502"
    assert events[1].payload["error_message"] == "网易云 API 响应缺少有效字段: code"
    evidence = reconciled.read_artifact(state["reconciliation_ref"])
    assert evidence["proposal_ref"]["target_id"] == events[0].event_id
    assert evidence["observations"]["present"] is True
    assert state["result_ref"] and len([row for row in events if row.payload["status"] == "success"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("present,expected", [(True, "confirmed"), (False, "unknown")])
async def test_toolnode_error_response_has_explicit_unknown_semantics(journal, present, expected):
    operations = OperationJournal(journal.writer, journal.accounting, read_client=reads(present=present))
    execute = AsyncMock(return_value=ToolMessage(content=json.dumps({"success": False,
        "error": "http_502", "message": "网易云 API 响应缺少有效字段: code"}), tool_call_id="one", status="error"))
    response = await operations.call(request(), execute)
    assert operations.state(operation_id(operations))["state"] == expected
    if expected == "unknown":
        assert response.status == "error"
        payload = json.loads(response.content)
        assert payload["write_state"] == "unknown" and payload["operation_id"] == operation_id(operations)
        assert "禁止重复写入" in payload["message"]
    else:
        assert response.status == "success"
    execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_original_exception_carries_unknown_state_without_losing_original_code(journal):
    execute = AsyncMock(side_effect=MusicServiceError("http_502", "原始 502 错误"))
    with pytest.raises(MusicServiceError) as captured:
        await journal.call(request(), execute)
    payload = json.loads(handle_music_tool_error(captured.value))
    assert payload["error"] == "http_502" and "原始 502 错误" in payload["message"]
    assert payload["write_state"] == "unknown" and payload["operation_id"] == operation_id(journal)


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["http_400", "http_401", "http_403", "http_422"])
async def test_definite_failure_is_terminal_and_never_reads_or_rewrites(journal, code):
    client = reads()
    operations = OperationJournal(journal.writer, journal.accounting, read_client=client)
    execute = AsyncMock(side_effect=MusicServiceError(code, "明确拒绝"))
    with pytest.raises(MusicServiceError):
        await operations.call(request(), execute)
    oid = operation_id(operations)
    assert operations.state(oid)["state"] == "failed"
    assert not operations.unknown_writes("test-run")
    assert (await operations.reconcile(oid))["state"] == "failed"
    with pytest.raises(MusicServiceError):
        await operations.call(request(call_id="second"), execute)
    execute.assert_awaited_once()
    client.get_user_playlists.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_negative_read_is_not_failure_then_later_read_confirms(journal):
    client = reads(present=False)
    operations = OperationJournal(journal.writer, journal.accounting, read_client=client)
    execute = AsyncMock(side_effect=MusicServiceError("timeout", "原请求超时"))
    with pytest.raises(MusicServiceError):
        await operations.call(request(), execute)
    oid = operation_id(operations)
    assert operations.state(oid)["state"] == "unknown"
    assert "仍未知" in await operations.answer_query("刚才那首歌加上了吗")
    assert len(operations.unknown_writes("test-run")) == 1
    client.get_playlist_detail.return_value = reads().get_playlist_detail.return_value
    assert (await operations.reconcile(oid))["state"] == "confirmed"
    assert "补记成功" in await operations.answer_query("刚才那首歌加上了吗")
    execute.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("complete,expected", [(False, "unknown"), (True, "confirmed")])
async def test_removal_absence_requires_complete_owned_playlist(journal, complete, expected):
    operations = OperationJournal(journal.writer, journal.accounting, read_client=reads(present=False, complete=complete))
    execute = AsyncMock(side_effect=OSError("lost response"))
    try:
        await operations.call(request("remove_song_from_playlist_tool"), execute)
    except OSError:
        assert expected == "unknown"
    assert operations.state(operation_id(operations))["state"] == expected
    execute.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("liked", [True, False])
async def test_like_reconciliation_uses_read_only_cloud_state(journal, liked):
    client = reads(present=liked)
    operations = OperationJournal(journal.writer, journal.accounting, read_client=client)
    execute = AsyncMock(side_effect=OSError("response lost"))
    response = await operations.call(request("set_song_liked_tool", {"song_id": 2652820720, "liked": liked}), execute)
    assert json.loads(response.content)["liked"] is liked
    assert operations.state(operation_id(operations))["confirmation_source"] == "readback"
    client.get_liked_song_ids.assert_awaited_once()
    execute.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("editable", [False, True])
async def test_unowned_or_failed_read_stays_unknown(journal, editable):
    client = reads(editable=editable)
    if editable:
        client.get_playlist_detail.side_effect = MusicServiceError("http_502", "读取失败")
    operations = OperationJournal(journal.writer, journal.accounting, read_client=client)
    execute = AsyncMock(side_effect=OSError("lost response"))
    with pytest.raises(OSError):
        await operations.call(request(), execute)
    assert operations.state(operation_id(operations))["state"] == "unknown"
    execute.assert_awaited_once()
    if not editable:
        client.get_playlist_detail.assert_not_awaited()


@pytest.mark.asyncio
async def test_old_proposed_only_receipt_recovers_without_executing(journal):
    req = request()
    oid = "op:" + digest(["test-run", req.tool_call["name"], req.tool_call["args"], "write"])[7:]
    journal.writer.append_public_event(journal.identity, "operation:" + oid + ":proposed", HistoryEventType.TOOL_CALL,
        PublicTool(run_id="test-run", operation_id=oid, name=req.tool_call["name"], parameters=req.tool_call["args"],
                   status="proposed", effect="write"))
    operations = OperationJournal(journal.writer, journal.accounting, read_client=reads())
    execute = AsyncMock(side_effect=AssertionError("must never replay"))
    response = await operations.call(request(call_id="recovered"), execute)
    assert json.loads(response.content)["status"] == "added"
    assert operations.state(oid)["state"] == "confirmed"
    execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_new_run_does_not_bypass_unresolved_identical_write(journal):
    execute = AsyncMock(side_effect=OSError("lost"))
    with pytest.raises(OSError):
        await journal.call(request(), execute)
    original = operation_id(journal)
    journal.accounting.identity = journal.identity.model_copy(update={"run_id": "another-run"})
    with pytest.raises(ContinuityConflict, match="effect unknown"):
        await journal.call(request(call_id="new-run"), execute)
    assert operation_id(journal) == original
    execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_canonical_defaults_and_legacy_operation_ids_do_not_duplicate_writes(journal):
    execute = AsyncMock(return_value=ToolMessage(content=json.dumps({"playlist_id": 99, "name": "测试", "status": "created", "message": "已创建"}), tool_call_id="one"))
    await journal.call(request("create_playlist_tool", {"name": "测试"}), execute)
    await journal.call(request("create_playlist_tool", {"name": "测试", "privacy": 0}, call_id="two"), execute)
    execute.assert_awaited_once()
    assert len([row for row in journal.write_events() if row.event_type is HistoryEventType.TOOL_CALL]) == 1


@pytest.mark.asyncio
async def test_unknown_creation_is_not_confirmed_from_name_or_incomplete_privacy_data(journal):
    client = reads()
    client.get_user_playlists.side_effect = [UserPlaylistsResult(playlists=[]), UserPlaylistsResult(playlists=[
        UserPlaylist(playlist_id=99, name="新建测试", editable=True)])]
    operations = OperationJournal(journal.writer, journal.accounting, read_client=client)
    execute = AsyncMock(side_effect=OSError("response lost"))
    with pytest.raises(OSError):
        await operations.call(request("create_playlist_tool", {"name": "新建测试", "privacy": 10}), execute)
    view = operations.state(operation_id(operations))
    assert view["state"] == "unknown" and not view["result_ref"]
    evidence = operations.read_artifact(view["reconciliation_ref"])
    assert evidence["observations"]["matching_new_ids"] == [99]
    assert evidence["error_code"] == "creation_privacy_unobservable"
    execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_reconciliation_evidence_corruption_stops_without_write_or_read_replay(journal, monkeypatch):
    operations = OperationJournal(journal.writer, journal.accounting, read_client=reads(present=False))
    execute = AsyncMock(side_effect=OSError("lost"))
    with pytest.raises(OSError):
        await operations.call(request(), execute)
    oid = operation_id(operations)
    ref = operations.state(oid)["reconciliation_ref"]["target_id"]
    original_get = journal.writer.store.get_artifact
    def corrupted(key):
        value = original_get(key)
        if key == ref:
            return value.model_copy(update={"payload": {**value.payload, "verdict": "confirmed"}})
        return value
    monkeypatch.setattr(journal.writer.store, "get_artifact", corrupted)
    with pytest.raises(ContinuityConflict, match="integrity"):
        await operations.reconcile(oid)
    execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_receipt_repair_reuses_committed_read_evidence_after_restart(journal, monkeypatch):
    client = reads()
    operations = OperationJournal(journal.writer, journal.accounting, read_client=client)
    original_append = journal.writer.append_public_event
    def interrupt(identity, key, kind, body, **kwargs):
        if key.endswith(":success"):
            raise ContinuityConflict("process terminated before success receipt")
        return original_append(identity, key, kind, body, **kwargs)
    monkeypatch.setattr(journal.writer, "append_public_event", interrupt)
    execute = AsyncMock(side_effect=OSError("original lost response"))
    with pytest.raises(ContinuityConflict):
        await operations.call(request(), execute)
    oid = operation_id(operations)
    assert operations.state(oid)["state"] == "unknown"
    monkeypatch.setattr(journal.writer, "append_public_event", original_append)
    restarted_store = SQLiteContinuityStore(journal.writer.store.path)
    restarted = OperationJournal(HistoryWriter(restarted_store), journal.accounting)
    assert (await restarted.reconcile(oid))["state"] == "confirmed"
    assert (await restarted.reconcile(oid))["state"] == "confirmed"
    client.get_playlist_detail.assert_awaited_once()
    execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_auth_scope_change_blocks_reconciliation_and_cross_task_query(journal):
    execute = AsyncMock(side_effect=OSError("lost"))
    with pytest.raises(OSError):
        await journal.call(request(), execute)
    oid = operation_id(journal)
    journal.accounting.lease.scope = "different-account"
    operations = OperationJournal(journal.writer, journal.accounting, read_client=reads())
    with pytest.raises(ContinuityConflict, match="scope"):
        operations.state(oid)
    journal.accounting.lease.scope = "test-account-scope"
    with pytest.raises(ContinuityConflict, match="not found"):
        await journal.reconcile("op:" + "0" * 64)


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["failed", "unknown", "confirmed"])
async def test_generalchat_uses_three_states_without_model_or_write_retry(journal, state):
    client = reads(present=state == "confirmed")
    operations = OperationJournal(journal.writer, journal.accounting, read_client=client)
    execute = AsyncMock(side_effect=MusicServiceError("http_403" if state == "failed" else "http_502", "原始错误"))
    try:
        await operations.call(request(), execute)
    except MusicServiceError:
        assert state != "confirmed"
    model = AsyncMock(side_effect=AssertionError("must use ledger evidence"))
    token = active_operations.set(operations)
    try:
        response = await answer_general_chat([], "刚才那首歌加上了吗", RoutingContext(), [], model=model)
        graph = build_handoff_graph(intent_router=Router(RouteKey.GENERAL_CHAT), general_model=model).compile()
        out = await graph.ainvoke(input_state("刚才那首歌加上了吗"))
    finally:
        active_operations.reset(token)
    word = {"failed": "明确拒绝", "unknown": "仍未知", "confirmed": "只读核对已确认"}[state]
    assert word in response.text and word in out["final_response"]
    assert out["handoff_run"]["status"] == "completed"
    execute.assert_awaited_once()
    model.ainvoke.assert_not_awaited()
    model.bind_tools.assert_not_called()
