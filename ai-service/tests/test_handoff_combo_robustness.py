import json
from typing import TypedDict
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.graph import END, START, StateGraph

from app.agent.foundation.models import TaskResult, TaskStatus
from app.agent.foundation.routing import RouteKey, RoutingContext
from app.agent.foundation.subgraphs.tool_calling.assistant import all_tool_calling_tools
from app.agent.foundation.subgraphs.tool_calling.scope import scoped_playback_tools
from app.agent.foundation.subgraphs.worldline.models import WorldLineResult
from app.agent.handoff.graph import build_handoff_graph
from app.agent.handoff.progress import create_combination_status_tool
from app.agent.handoff.state import HandoffRun, RunStatus
from app.llm.runtime import RuntimeLimitError, estimate_tokens
from app.schemas.context import MusicAgentContext
from app.schemas.music import MusicSearchResult, MusicTrack, SongLikeResult
from app.tools.music import MusicServiceClient, MusicServiceError
from tests.test_handoff_runtime import Router, ToolModel, call, input_state, player_context


TRACK = MusicTrack(song_id=11, name="十年", artists=["陈奕迅"], album="黑白灰",
                   cover_url="", duration=240000)


class WorldState(TypedDict):
    task_id: str
    task_result: TaskResult


def world_graph(*, fail=False):
    world = WorldLineResult.model_validate({
        "root_entity": {"entity_id": "netease:artist:2116", "entity_type": "artist",
                        "name": "陈奕迅", "source": "netease"},
        "graph": {"root": "netease:artist:2116", "nodes": [
            {"id": "netease:artist:2116", "type": "artist", "name": "陈奕迅"}]},
        "narrative": "已完成探索。",
        "branches": [{"id": f"branch-{index}", "title": "探索陈奕迅", "description": "沿目录节点探索。",
                      "branch_type": "artist", "target_entity": {
                          "id": "netease:artist:2116", "type": "artist", "name": "陈奕迅"}}
                     for index in range(3)],
    })
    def execute(state):
        return {"task_result": TaskResult(
            task_id=state["task_id"], route_key=RouteKey.WORLDLINE,
            status=TaskStatus.FAILED if fail else TaskStatus.SUCCESS,
            content="探索服务暂时不可用。" if fail else "已完成探索。",
            error="探索服务暂时不可用。" if fail else None,
            data={"reason": "retrieval_failed"} if fail else {"worldline_result": world.model_dump(mode="json")},
        )}
    builder = StateGraph(WorldState)
    builder.add_node("execute", execute)
    builder.add_edge(START, "execute")
    builder.add_edge("execute", END)
    return builder.compile(checkpointer=False)


def transfer_model():
    return ToolModel(responses=[call(call_id="to-curator"),
                               call("transfer_to_explorer_agent", call_id="to-explorer"),
                               call(call_id="back-to-curator")])


@pytest.mark.parametrize("text", ["播放周杰伦的晴天", "请播放《十年》"])
def test_simple_playback_removes_unneeded_schemas_without_raising_budgets(text):
    original = all_tool_calling_tools()
    scoped = scoped_playback_tools(text, original)
    assert {item.name for item in scoped} == {"search_music", "play_song"}
    def size(tools):
        return estimate_tokens([item.tool_call_schema.model_json_schema() for item in tools])
    assert size(scoped) < size(original) / 3


@pytest.mark.parametrize("text", ["播放专辑黑白灰", "播放我的歌单", "不要播放十年",
                                  "搜索十年并收藏", "播放十年然后暂停", "播放当前这首歌"])
def test_scope_keeps_collection_compound_negated_and_current_song_domains(text):
    original = all_tool_calling_tools()
    assert scoped_playback_tools(text, original) == original


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["play_explore", "explore_pause", "search_favorite"])
async def test_three_combinations_finish_every_subtask(case, monkeypatch):
    search = AsyncMock(return_value=MusicSearchResult(keyword="十年", limit=10, songs=[TRACK]))
    like = AsyncMock(return_value=SongLikeResult(song_id=11, liked=True, status="synced", message="已收藏"))
    monkeypatch.setattr(MusicServiceClient, "search_music", search)
    monkeypatch.setattr(MusicServiceClient, "set_song_liked", like)
    if case == "play_explore":
        routes, text = (RouteKey.TOOL_CALLING, RouteKey.WORLDLINE), "播放陈奕迅的十年；探索陈奕迅的世界线"
        responses = [call("search_music", args={"keyword": "十年"}, call_id="search"),
                     call("play_song", args={"song": TRACK.model_dump()}, call_id="play"),
                     AIMessage(content="已发送播放指令。")]
    elif case == "explore_pause":
        routes, text = (RouteKey.WORLDLINE, RouteKey.TOOL_CALLING), "探索陈奕迅的世界线；暂停一下"
        responses = [AIMessage(content="无需模型执行暂停。")]
    else:
        routes, text = (RouteKey.TOOL_CALLING, RouteKey.TOOL_CALLING), "搜索十年；收藏刚才搜索的第一首"
        responses = [call("search_music", args={"keyword": "十年"}, call_id="search"),
                     AIMessage(content="已找到十年。"),
                     call("set_song_liked_tool", args={"song_id": 11, "liked": True}, call_id="like"),
                     AIMessage(content="已收藏。")]
    graph = build_handoff_graph(intent_router=Router(*routes), transfer_model=transfer_model(),
                                curator_model=ToolModel(responses=responses),
                                subgraphs={RouteKey.WORLDLINE: world_graph()}).compile()
    output = await graph.ainvoke(input_state(text, player_context()), context=MusicAgentContext())
    run = HandoffRun.model_validate(output["handoff_run"])
    assert run.status is RunStatus.COMPLETED
    assert len(run.results) == 2 and not run.pending_tasks
    assert all(result.status is TaskStatus.SUCCESS for result in run.results)
    if case == "search_favorite":
        like.assert_awaited_once_with(11, True)
        assert run.results[1].data["client_actions"][0]["name"] == "set_liked"
    if case == "play_explore":
        assert run.results[0].data["client_actions"][0]["name"] == "play_song"
    if case == "explore_pause":
        assert run.results[1].data["client_actions"][0]["name"] == "pause_player"


@pytest.mark.asyncio
async def test_failure_preserves_completed_pending_failed_and_general_chat_query():
    routes = (RouteKey.TOOL_CALLING, RouteKey.WORLDLINE, RouteKey.TOOL_CALLING)
    text = "暂停一下；探索陈奕迅的世界线；暂停一下"
    graph = build_handoff_graph(intent_router=Router(*routes), transfer_model=transfer_model(), curator_model=ToolModel(responses=[AIMessage(content="无需模型暂停。")]),
                                subgraphs={RouteKey.WORLDLINE: world_graph(fail=True)}).compile()
    output = await graph.ainvoke(input_state(text, player_context()), context=MusicAgentContext())
    run = HandoffRun.model_validate(output["handoff_run"])
    assert run.status is RunStatus.FAILED
    assert [r.status for r in run.results] == [TaskStatus.SUCCESS, TaskStatus.FAILED]
    progress = await create_combination_status_tool(run.results).ainvoke({})
    assert [item["status"] for item in progress["tasks"]] == ["success", "failed", "pending"]
    assert progress["blocked_at"] == run.results[1].task_id
    assert progress["tasks"][1]["reason"] == "retrieval_failed"
    assert progress["tasks"][0]["pending_client_actions"] == ["pause_player"]
    assert "待客户端确认" in output["final_response"]
    assert "失败" in output["final_response"] and "待办" in output["final_response"]
    assert "全部完成" not in output["final_response"]

    model = ToolModel(responses=[call("get_combination_task_status", args={}, call_id="status"),
                                 AIMessage(content="暂停指令已发送，探索失败，最后一步还没有执行。")])
    chat = build_handoff_graph(intent_router=Router(RouteKey.GENERAL_CHAT), general_model=model).compile()
    state = input_state("刚才哪些步骤成功，卡在哪里？", player_context())
    state["task_results"] = list(run.results)
    answered = await chat.ainvoke(state, context=MusicAgentContext())
    assert not answered["handoff_failed"]
    reply = next(m for m in model.seen_messages[1] if isinstance(m, ToolMessage))
    queried = json.loads(reply.content)
    assert "第1步" in queried["message"] and "待客户端确认" in queried["message"]
    assert "第2步" in queried["message"] and "探索服务暂时不可用" in queried["message"]
    assert "第3步" in queried["message"] and "待办，尚未执行" in queried["message"]
    assert model.bound_names == [["get_current_song_context", "get_combination_task_status"]]


@pytest.mark.asyncio
async def test_tool_failure_after_search_cannot_be_reported_as_all_success(monkeypatch):
    monkeypatch.setattr(MusicServiceClient, "search_music", AsyncMock(return_value=MusicSearchResult(keyword="十年", limit=10, songs=[TRACK])))
    monkeypatch.setattr(MusicServiceClient, "set_song_liked", AsyncMock(side_effect=MusicServiceError("unavailable", "收藏服务暂时不可用。")))
    model = ToolModel(responses=[call("search_music", args={"keyword": "十年"}, call_id="search"),
                                call("set_song_liked_tool", args={"song_id": 11, "liked": True}, call_id="like"),
                                AIMessage(content="已经全部完成！")])
    graph = build_handoff_graph(intent_router=Router(RouteKey.TOOL_CALLING, RouteKey.WORLDLINE),
                                transfer_model=transfer_model(), curator_model=model,
                                subgraphs={RouteKey.WORLDLINE: world_graph()}).compile()
    output = await graph.ainvoke(input_state("搜索十年并收藏；探索陈奕迅的世界线", player_context()), context=MusicAgentContext())
    run = HandoffRun.model_validate(output["handoff_run"])
    assert run.status is RunStatus.FAILED
    result = run.results[0]
    assert [step["status"] for step in result.data["tool_call_details"]] == ["success", "error"]
    assert result.data["tool_call_details"][1]["error"] == "unavailable"
    assert result.data["client_actions"] == []
    assert "已经全部完成" not in output["final_response"]
    assert "收藏服务暂时不可用" in output["final_response"]
    assert len(run.pending_tasks) == 1


@pytest.mark.asyncio
async def test_resource_stop_retains_already_dispatched_player_action(monkeypatch):
    class FailFinalModel(ToolModel):
        async def _agenerate(self, *args, **kwargs):
            if len(self.seen_messages) == 2:
                raise RuntimeLimitError("token_limit")
            return await super()._agenerate(*args, **kwargs)
    monkeypatch.setattr(MusicServiceClient, "search_music", AsyncMock(return_value=MusicSearchResult(keyword="十年", limit=10, songs=[TRACK])))
    model = FailFinalModel(responses=[call("search_music", args={"keyword": "十年"}, call_id="search"),
                                     call("play_song", args={"song": TRACK.model_dump()}, call_id="play")])
    graph = build_handoff_graph(intent_router=Router(RouteKey.TOOL_CALLING, RouteKey.WORLDLINE),
                                transfer_model=transfer_model(), curator_model=model,
                                subgraphs={RouteKey.WORLDLINE: world_graph()}).compile()
    output = await graph.ainvoke(input_state("播放陈奕迅的十年；探索陈奕迅的世界线", player_context()), context=MusicAgentContext())
    run = HandoffRun.model_validate(output["handoff_run"])
    assert run.status is RunStatus.FAILED
    assert run.results[0].data["reason"] == "token_limit"
    assert run.results[0].data["client_actions"][0]["name"] == "play_song"
    assert len(run.results[0].data["tool_call_details"]) == 2
    assert len(run.pending_tasks) == 1
    assert "待客户端确认" in output["final_response"]


@pytest.mark.asyncio
async def test_song_playback_without_command_requires_input(monkeypatch):
    monkeypatch.setattr(MusicServiceClient, "search_music", AsyncMock(return_value=MusicSearchResult(keyword="十年", limit=10, songs=[])))
    model = ToolModel(responses=[call("search_music", args={"keyword": "十年"}, call_id="search"),
                                AIMessage(content="已经播放。")])
    graph = build_handoff_graph(intent_router=Router(RouteKey.TOOL_CALLING), transfer_model=transfer_model(),
                                curator_model=model).compile()
    output = await graph.ainvoke(input_state("播放陈奕迅的十年", RoutingContext()), context=MusicAgentContext())
    run = HandoffRun.model_validate(output["handoff_run"])
    assert run.status is RunStatus.NEEDS_INPUT
    assert run.results[0].data["client_actions"] == []
    assert "尚未发送播放指令" in output["final_response"]


@pytest.mark.asyncio
async def test_malformed_final_message_retains_previous_successes(monkeypatch):
    monkeypatch.setattr(MusicServiceClient, "search_music", AsyncMock(
        return_value=MusicSearchResult(keyword="十年", limit=10, songs=[TRACK])))
    malformed = AIMessage(content="", invalid_tool_calls=[{
        "name": "play_song", "args": "{private-invalid", "id": "bad", "error": "invalid JSON",
    }])
    model = ToolModel(responses=[call("search_music", args={"keyword": "十年"}, call_id="search"),
                                call("play_song", args={"song": TRACK.model_dump()}, call_id="play"),
                                malformed])
    graph = build_handoff_graph(intent_router=Router(RouteKey.TOOL_CALLING, RouteKey.WORLDLINE),
                                transfer_model=transfer_model(), curator_model=model,
                                subgraphs={RouteKey.WORLDLINE: world_graph()}).compile()
    output = await graph.ainvoke(input_state("播放陈奕迅的十年；探索陈奕迅的世界线", player_context()),
                                 context=MusicAgentContext())
    run = HandoffRun.model_validate(output["handoff_run"])
    assert run.status is RunStatus.FAILED
    assert run.results[0].data["reason"] == "malformed_tool_call"
    assert len(run.results[0].data["tool_call_details"]) == 2
    assert run.results[0].data["client_actions"][0]["name"] == "play_song"
    assert "private-invalid" not in run.results[0].model_dump_json()
