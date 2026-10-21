"""Trusted producer replay plus fail-closed checks for the add-only exception."""

import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool

from app.agent.foundation.agent_history_source import bind_produced_message, bind_tool_result
from app.agent.foundation.models import TaskStatus
from app.agent.foundation.subgraphs.tool_calling.graph import (
    _action_intent_errors, _scoped_action_intent_errors, create_tool_calling_subgraph,
)
from app.agent.foundation.subgraphs.tool_calling.playlist_inheritance import (
    capture_created_receipts, current_invocation, inherited_playlist_id,
)
from app.schemas.music import AddSongToPlaylistArgs
from tests.test_tool_call_phase_limits import FIXTURE, batch, invoke, search_tool
from tests.test_tool_calling_subgraph import scripted_assistant, tool_call

EVIDENCE = json.loads((Path(__file__).parent / "fixtures/add_song_authority_evidence.json").read_text(encoding="utf-8"))
TEXT = EVIDENCE["task0"]["input_text"]


def trusted_state(text=TEXT):
    state = {"current_input": text, "task_id": "task0", "pack_run_id": "current-run",
             "continuity_context": {"user_id": "test-account"}}
    invocation = current_invocation(state)
    create = bind_produced_message(tool_call("create_playlist_tool", "create", {"name": "阅读"}), invocation)
    receipt = bind_tool_result(ToolMessage(name="create_playlist_tool", tool_call_id="create",
        content=json.dumps({"status": "created", "playlist_id": 55, "name": "阅读"}),
        additional_kwargs={"executed_create": True}), create)
    add = bind_produced_message(tool_call("add_song_to_playlist", "add", {"playlist_id": 55, "song_id": 1}), invocation)
    state["messages"] = [create, receipt, add]
    state["created_playlist_receipts"] = capture_created_receipts(state)
    return state


@pytest.mark.parametrize("text", [TEXT, "新建学习歌单，加入3首歌曲", "创建私密歌单，设为私密，加入至少3首钢琴歌曲"])
def test_current_scoped_receipt_and_explicit_adjacent_request_allow(text):
    state = trusted_state(text)
    assert _action_intent_errors(state)  # Old default remains closed.
    assert inherited_playlist_id(state, state["messages"][-1].tool_calls[0]) == 55
    assert _scoped_action_intent_errors(state) == {}


@pytest.mark.parametrize("text", [
    "新建学习歌单，不要加入3首歌曲",
    "新建学习歌单，先不要加3首歌曲",
    "新建学习歌单，只查看，不加入3首歌曲",
    "新建学习歌单，只讨论加入3首歌曲",
    "新建学习歌单，加入读书群",
    "新建学习歌单，名称为“加入3首歌曲”",
    "新建学习歌单，“加入3首歌曲”",
    "新建学习歌单，引用：加入3首歌曲",
    "新建学习歌单，标题是加入3首歌曲",
    "新建学习歌单，名称为阅读，设为私密，加入3首歌曲",
    "新建学习歌单，搜索钢琴，加入3首歌曲",
    "新建学习歌单，播放音乐，加入3首歌曲",
    "新建学习歌单，加入3首歌曲？",
    "新建学习歌单，可以加入3首歌曲吗",
    "如果新建学习歌单，加入3首歌曲",
    "新建学习歌单，如果有空就加入3首歌曲",
    "等创建学习歌单后，加入3首歌曲",
    "当创建学习歌单时，加入3首歌曲",
    "创建学习歌单的话，加入3首歌曲",
    "新建学习歌单，先取消加歌，名称为阅读",
    "新建两个歌单，加入3首歌曲",
    "新建学习歌单，创建跑步歌单，加入3首歌曲",
    "新建学习歌单，给另一个加入3首歌曲",
    "不要新建学习歌单，加入3首歌曲",
    "新建学习歌单，但是加入3首歌曲",
    "新建学习歌单，名称为搜索钢琴，加入3首歌曲",
    "新建学习歌单，名称为“阅读”，给朋友加入3首歌曲",
    "新建学习歌单，名称为“加入3首歌曲”，加入3首歌曲",
    "返回真实歌单ID",
])
def test_unsafe_or_ambiguous_text_still_denies_with_valid_receipt(text):
    state = trusted_state(text)
    assert "add" in _scoped_action_intent_errors(state)


@pytest.mark.parametrize("change", ["old_run", "other_task", "other_user", "other_owner", "missing_user",
    "unbound_receipt", "unbound_create", "unbound_add", "wrong_playlist", "failed", "not_created",
    "malformed", "string_id", "bool_id", "two_creates", "wrong_call_id", "orphan", "no_anchor",
    "cached_receipt", "missing_execution_proof"])
def test_receipt_binding_rejects_history_foreign_unknown_or_ambiguous(change):
    state = trusted_state()
    create, receipt, add = state["messages"]
    fields = {"old_run": ("run_id", "old-run"), "other_task": ("route_task_id", "task1"),
              "other_user": ("user_id", "other-user"), "other_owner": ("owner_agent", "general_chat")}
    if change in fields:
        field, value = fields[change]
        # Two mutually consistent OLD messages still do not prove current ownership.
        for message in (create, receipt):
            message.additional_kwargs[field] = value
    elif change == "missing_user":
        receipt.additional_kwargs.pop("user_id")
    elif change.startswith("unbound_"):
        {"unbound_receipt": receipt, "unbound_create": create, "unbound_add": add}[change].additional_kwargs.clear()
    elif change == "wrong_playlist":
        add.tool_calls[0]["args"]["playlist_id"] = 66
    elif change == "failed":
        receipt.status = "error"
    elif change in {"not_created", "string_id", "bool_id"}:
        body = json.loads(receipt.content)
        body.update({"status": "failed"} if change == "not_created" else {"playlist_id": "55" if change == "string_id" else True})
        receipt.content = json.dumps(body)
    elif change == "malformed":
        receipt.content = "not JSON"
    elif change == "two_creates":
        state["messages"].insert(2, receipt.model_copy(deep=True))
    elif change == "wrong_call_id":
        receipt.tool_call_id = "foreign"
    elif change == "orphan":
        state["messages"].remove(create)
    elif change == "no_anchor":
        state.pop("pack_run_id")
    elif change == "cached_receipt":
        receipt.additional_kwargs["executed_create"] = False
    elif change == "missing_execution_proof":
        receipt.additional_kwargs.pop("executed_create")
    state["created_playlist_receipts"] = capture_created_receipts(state)
    assert inherited_playlist_id(state, add.tool_calls[0]) is None
    assert "add" in _scoped_action_intent_errors(state)


@pytest.mark.parametrize("task_id,run_id,allowed", [
    ("task0", "current-run", True), ("task1", "current-run", False), ("task0", "another-run", False),
])
def test_independent_active_hop_is_the_authority(task_id, run_id, allowed):
    from app.agent.foundation.routing import RouteTask, RouteKey
    from app.agent.handoff.hop_context import CurrentHopSnapshot, HopLease, active_hop

    state = trusted_state()
    hop = CurrentHopSnapshot(run_id=run_id, owner_agent="curator",
        task_json=RouteTask(task_id=task_id, route_key=RouteKey.TOOL_CALLING, input_text=TEXT).model_dump_json(),
        envelope_json=None, envelope_digest=None, envelope_audit=None,
        chain=("general_chat", "curator"), committed=True, lease=HopLease())
    token = active_hop.set(hop)
    try:
        assert (inherited_playlist_id(state, state["messages"][-1].tool_calls[0]) == 55) is allowed
        assert bool(_scoped_action_intent_errors(state)) is not allowed
    finally:
        active_hop.reset(token)


@pytest.mark.parametrize("row", EVIDENCE["existing"], ids=lambda row: row["action"] + row["text"])
def test_e06_existing_thirty_cases_are_byte_equivalent(row):
    state = {"current_input": row["text"], "messages": [tool_call(row["action"], "mutation", row["args"])]}
    assert _action_intent_errors(state) == row["baseline"]
    assert _action_intent_errors(state, inherited_playlist_id=55) == row["baseline"]


@pytest.mark.parametrize("row", EVIDENCE["actions"], ids=lambda row: row["action"])
def test_e05_all_twenty_three_actions_remain_equivalent(row):
    state = {"current_input": row["text"], "messages": [tool_call(row["action"], row["action"])]}
    assert _action_intent_errors(state) == row["errors"]
    assert _action_intent_errors(state, inherited_playlist_id=55) == row["errors"]


@pytest.mark.asyncio
@pytest.mark.parametrize("bound,cached", [(True, False), (False, False), (True, True)],
    ids=["e02-scoped-task0", "injected-unscoped-denied", "journal-replayed-create-denied"])
async def test_real_task0_replay_requires_producer_binding(bound, cached, monkeypatch):
    case, seen, added, observed = FIXTURE["item3"], [], [], []

    @tool("create_playlist_tool")
    async def create(name: str, privacy: int = 0) -> str:
        """Simulate a successful creation, never call a live service."""
        return json.dumps({"playlist_id": case["playlist_id"], "name": name, "status": "created", "message": "已创建"})

    @tool("add_song_to_playlist", args_schema=AddSongToPlaylistArgs)
    async def add(playlist_id: int, song_id: int) -> str:
        """Record controlled additions."""
        added.append(song_id)
        return json.dumps({"playlist_id": playlist_id, "song_id": song_id, "status": "added", "message": "已添加"})

    queue = [*[tool_call(row["name"], f"saved-{i}", row["args"]) for i, row in enumerate(case["observed_calls"])],
        batch([{"name": "add_song_to_playlist", "args": {"playlist_id": case["playlist_id"], "song_id": song["song_id"]}}
               for song in case["songs"][:3]]), AIMessage(content="已完成")]

    async def unbound(messages, *_):
        observed.append({"messages": list(messages)})
        return queue.pop(0)

    if cached:
        async def replay_journal(request, execute):
            if request.tool_call["name"] == "create_playlist_tool":
                return ToolMessage(name="create_playlist_tool", tool_call_id=request.tool_call["id"],
                    content=json.dumps({"playlist_id": case["playlist_id"], "name": "阅读", "status": "created", "message": "已创建"}))
            return await execute(request)
        monkeypatch.setattr("app.agent.foundation.subgraphs.tool_calling.graph.journal_tool_call", replay_journal)

    graph = create_tool_calling_subgraph(tools=[search_tool(case, seen), create, add],
        assistant=scripted_assistant(queue, observed) if bound else unbound)
    result = (await invoke(graph, TEXT))["task_result"]
    receipts = [m for m in observed[-1]["messages"] if isinstance(m, ToolMessage) and m.name == "create_playlist_tool"]
    assert len(receipts) == 1
    if bound and not cached:
        assert result.status is TaskStatus.SUCCESS
        assert added == [song["song_id"] for song in case["songs"][:3]]
        assert set(("owner_agent", "run_id", "route_task_id", "user_id")) <= receipts[0].additional_kwargs.keys()
        assert receipts[0].additional_kwargs["executed_create"] is True
    else:
        assert result.status is TaskStatus.FAILED
        assert added == []
        assert "unauthorized_action" in str(result.data)
        if cached:
            assert receipts[0].additional_kwargs["executed_create"] is False
        else:
            assert receipts[0].additional_kwargs == {}
