"""Curator v1 acceptance: controlled catalog, existing tools, real SQLite A ledger."""

import json
import re
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from langgraph.runtime import Runtime

from app.agent.foundation.long_horizon.models import HistoryEventType
from app.agent.foundation.long_horizon.operations import (
    OperationJournal,
    active_operations,
)
from app.agent.foundation.models import TaskStatus
from app.agent.foundation.routing import (
    RouteDecision,
    RouteKey,
    RouteTask,
    RoutingContext,
)
from app.agent.handoff.agents import execute_owned_task
from app.agent.handoff.curator import _catalog, curation_request, execute_curation
from app.agent.handoff.graph import build_handoff_graph
from app.agent.handoff.state import new_run
from app.schemas.context import MusicAgentContext
from app.schemas.music import (
    CreatedPlaylistResult,
    MusicSearchResult,
    MusicTrack,
    PlaylistDetailResult,
    UserPlaylist,
    UserPlaylistsResult,
)
from app.tools.music import MUSIC_AGENT_TOOLS, MusicServiceClient, MusicServiceError
from tests import test_write_reconciliation as write_helpers
from tests.test_handoff_runtime import Router, ToolModel, call, input_state

journal = write_helpers.journal


@pytest.fixture(autouse=True)
def isolate_operations():
    token = active_operations.set(None)
    yield
    active_operations.reset(token)


class Catalog:
    def __init__(
        self, monkeypatch, scene="雨天夜跑", count=15, *, mode=None, fail_at=None
    ):
        # IDs/names are controlled service fixtures, never model-generated songs.
        self.songs = [
            MusicTrack(
                song_id=100_000 + i,
                name=f"目录曲目{i}",
                artists=["目录歌手"],
                album=f"{scene} 199{i % 10}",
            )
            for i in range(count)
        ]
        self.scene = scene
        self.calls, self.members, self.mode, self.fail_at = [], [], mode, fail_at
        self.exists, self.name = False, ""
        for name in (
            "search_music",
            "get_liked_song_ids",
            "create_playlist",
            "add_song_to_playlist",
        ):
            method = getattr(self, name)

            async def invoke(client, *args, _method=method, **kwargs):
                return await _method(*args, **kwargs)

            monkeypatch.setattr(MusicServiceClient, name, invoke)

    async def search_music(self, keyword, limit=10):
        self.calls.append(("search", keyword))
        if self.mode == "search_failure":
            raise MusicServiceError("timeout", "目录暂时不可用。")
        if self.mode == "wrong_query":
            keyword = "另一项查询"
        return MusicSearchResult(keyword=keyword, limit=limit, songs=self.songs)

    async def get_liked_song_ids(self):
        self.calls.append(("likes",))
        if self.mode == "likes_failure":
            raise MusicServiceError("http_401", "未登录。")
        return [self.songs[-1].song_id, 999999] if self.songs else [999999]

    async def create_playlist(self, name, privacy=0):
        self.calls.append(("create", name, privacy))
        self.exists, self.name = True, name
        if self.mode == "create_unknown":
            raise MusicServiceError("http_502", "创建回执丢失。")
        return CreatedPlaylistResult(
            playlist_id=777, name=name, status="created", message="后端受理。"
        )

    async def add_song_to_playlist(self, playlist_id, song_id):
        self.calls.append(("add", playlist_id, song_id))
        if len(self.members) == self.fail_at or self.mode == "write_readback":
            if self.mode == "write_rejected":
                raise MusicServiceError("http_403", "后端拒绝。")
            self.members.append(song_id)
            raise MusicServiceError("http_502", "加歌回执丢失。")
        self.members.append(song_id)
        return SimpleNamespace(
            model_dump_json=lambda: json.dumps(
                {
                    "playlist_id": playlist_id,
                    "song_id": song_id,
                    "status": "added",
                    "message": "后端受理。",
                }
            )
        )

    def read_client(self):
        async def playlists():
            return UserPlaylistsResult(
                playlists=[UserPlaylist(playlist_id=777, name=self.name, editable=True)]
                if self.exists
                else []
            )

        async def detail(playlist_id):
            songs = [song for song in self.songs if song.song_id in self.members]
            return PlaylistDetailResult(
                playlist_id=playlist_id,
                name=self.name,
                description="",
                cover_url="",
                creator="测试账号",
                track_count=len(songs),
                songs=songs,
            )

        return SimpleNamespace(get_user_playlists=playlists, get_playlist_detail=detail)


def responses(catalog, *, ids=None, extra=None):
    plan = {"queries": ["夜跑", "雨夜"], "tags": ["雨天", "夜跑"]}
    if "港乐" in catalog.scene:
        plan = {
            "queries": ["90年代港乐", "1990 粤语"],
            "tags": ["港乐", "粤语", "90年代"],
        }
    elif "加班" in catalog.scene:
        plan = {"queries": ["深夜 加班", "提神"], "tags": ["深夜", "加班", "提神"]}
    elif "火星" in catalog.scene:
        plan = {"queries": ["火星殖民地音乐"], "tags": ["火星殖民地"]}
    default = catalog.songs[-1:] + catalog.songs[:-1][:14]
    payload = {"song_ids": [song.song_id for song in default] if ids is None else ids}
    if extra:
        payload.update(extra)
    return ToolModel(
        responses=[
            AIMessage(content=json.dumps(plan, ensure_ascii=False)),
            AIMessage(content=json.dumps(payload, ensure_ascii=False)),
        ]
    )


async def curate(text, catalog, operations, *, model=None, owned=False):
    task = RouteTask(
        task_id="curation-task", route_key=RouteKey.TOOL_CALLING, input_text=text
    )
    state = {
        "messages": [],
        "current_input": text,
        "routing_context": RoutingContext(),
        "task_results": [],
        "continuity_context": {},
    }
    runtime = Runtime(context=MusicAgentContext(session_cookie="isolated-cookie"))
    token = active_operations.set(operations)
    try:
        if owned:
            run = new_run(
                "test-run",
                text,
                RouteDecision(tasks=[task], is_complete=True),
                started_at=datetime.now(UTC),
            )
            return await execute_owned_task(
                state, run, runtime, curator_model=model or responses(catalog)
            )
        return await execute_curation(
            task,
            state,
            runtime,
            request=curation_request(text),
            model=model or responses(catalog),
            tools=MUSIC_AGENT_TOOLS,
        )
    finally:
        active_operations.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text,count", [("雨天夜跑歌单", 10), ("90 年代港乐", 15), ("深夜加班提神", 20)]
)
async def test_three_scenes_create_real_catalog_only_playlists_and_grounded_intros(
    monkeypatch, journal, text, count
):
    catalog = Catalog(monkeypatch, scene=text, count=count)
    if "港乐" in text:
        from tests.test_curator_curation_v2 import install_era

        install_era(monkeypatch, catalog)
    result = await curate(text, catalog, journal, owned=True)
    assert result.status is TaskStatus.SUCCESS, result.content
    data = result.data["curation"]
    expected = {song.song_id: song.model_dump() for song in catalog.songs}
    assert len(data["selected_songs"]) == min(count, 15)  # default request size
    for song in data["selected_songs"]:
        assert song == expected[song["song_id"]]
        assert (
            data["catalog_sources"][str(song["song_id"])][0]["tool"] == "search_music"
        )
    assert data["accepted_song_ids"] == catalog.members
    assert data["accepted_song_ids"][0] == catalog.songs[-1].song_id
    assert 999999 not in catalog.members  # history is preference, not catalog evidence
    names = {song["name"] for song in data["selected_songs"]}
    assert set(re.findall(r"《(.*?)》", data["introduction"])) <= names
    assert set(data["introduction_song_ids"]) <= set(catalog.members)
    assert data["polish_request"]["songs"] == data["selected_songs"]
    assert "已生成歌单选曲" in result.content and "尚未全部确认" in result.content
    assert (
        "已经全部完成" not in result.content
        and "已执行：" not in result.content
        and "已确认：" not in result.content
    )
    proposals = [
        row
        for row in journal.write_events()
        if row.event_type is HistoryEventType.TOOL_CALL
    ]
    assert len(proposals) == len(catalog.members) + 1
    assert all(
        row.payload["name"] in {"create_playlist_tool", "add_song_to_playlist"}
        for row in proposals
    )
    assert all(
        fact["source"] == "operation_journal" and fact["state"] is None
        for fact in result.data["result_facts"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [10, 20])
async def test_explicit_count_is_bounded_and_supported(monkeypatch, journal, count):
    catalog = Catalog(monkeypatch, count=count)
    result = await curate(
        f"生成雨天夜跑歌单，{count}首",
        catalog,
        journal,
        model=responses(catalog, ids=[song.song_id for song in catalog.songs]),
    )
    assert result.status is TaskStatus.SUCCESS
    assert len(catalog.members) == count
    assert result.data["tool_calls"] <= 25


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [0, 3])
async def test_obscure_or_insufficient_results_never_pad(monkeypatch, journal, count):
    catalog = Catalog(monkeypatch, scene="火星殖民地音乐", count=count)
    result = await curate("生成火星殖民地音乐歌单", catalog, journal)
    assert result.status is TaskStatus.SUCCESS
    assert f"只找到 {count} 首" in result.content
    assert len(catalog.members) == count
    assert sum(call[0] == "create" for call in catalog.calls) == bool(count)
    if count == 0:
        assert result.data["curation"]["introduction"] is None
        assert journal.write_events() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ids,extra",
    [
        ([123456789], None),
        ([100000, 100000], None),
        ([True], None),
        (["100000"], None),
        ([100000], {"introduction": "虚构歌曲已完成"}),
        ([100000], {"state": "confirmed"}),
    ],
)
async def test_model_cannot_invent_ids_intros_or_completion(
    monkeypatch, journal, ids, extra
):
    catalog = Catalog(monkeypatch)
    result = await curate(
        "雨天夜跑歌单", catalog, journal, model=responses(catalog, ids=ids, extra=extra)
    )
    assert result.status is TaskStatus.FAILED
    assert journal.write_events() == []
    assert not catalog.members and not catalog.exists
    assert "虚构歌曲" not in result.content and "已确认：" not in result.content


@pytest.mark.asyncio
async def test_model_tool_call_is_rejected_before_creation(monkeypatch, journal):
    catalog = Catalog(monkeypatch)
    model = ToolModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "create_playlist_tool",
                        "args": {"name": "伪造"},
                        "id": "fake",
                    }
                ],
            )
        ]
    )
    result = await curate("雨天夜跑歌单", catalog, journal, model=model)
    assert result.status is TaskStatus.FAILED and not catalog.exists


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["search_failure", "wrong_query"])
async def test_invalid_catalog_results_do_not_become_selections(
    monkeypatch, journal, mode
):
    catalog = Catalog(monkeypatch, mode=mode)
    result = await curate("雨天夜跑歌单", catalog, journal)
    assert "只找到 0 首" in result.content and not catalog.exists
    assert result.data["curation"]["selected_songs"] == []


@pytest.mark.asyncio
async def test_era_query_is_not_a_release_year_fact(monkeypatch, journal):
    catalog = Catalog(monkeypatch, scene="90 年代港乐")
    catalog.songs = [
        song.model_copy(update={"album": "港乐精选"}) for song in catalog.songs
    ]
    result = await curate("90 年代港乐", catalog, journal)
    assert (
        "只找到 0 首" in result.content and "缺少可核实的歌曲年代证据" in result.content
    )
    assert not catalog.exists


@pytest.mark.asyncio
async def test_missing_preference_history_is_honest_but_does_not_block_scene_search(
    monkeypatch, journal
):
    catalog = Catalog(monkeypatch, mode="likes_failure")
    result = await curate("雨天夜跑歌单", catalog, journal)
    assert result.status is TaskStatus.SUCCESS and "红心历史不可用" in result.content
    assert "红心曲目" not in result.data["curation"]["introduction"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,expected", [("write_unknown", "unknown"), ("write_rejected", None)]
)
async def test_partial_write_stops_with_exact_a_status_and_no_false_intro_members(
    monkeypatch, journal, mode, expected
):
    catalog = Catalog(monkeypatch, mode=mode, fail_at=2)
    result = await curate("雨天夜跑歌单", catalog, journal)
    assert result.status is TaskStatus.FAILED
    data = result.data["curation"]
    assert len(data["accepted_song_ids"]) == 2
    assert sum(call[0] == "add" for call in catalog.calls) == 3
    assert result.data["result_facts"][-1]["state"] == expected
    assert set(data["introduction_song_ids"]) <= set(data["accepted_song_ids"])
    assert set(re.findall(r"《(.*?)》", data["introduction"])) <= {
        song.name for song in catalog.songs if song.song_id in data["accepted_song_ids"]
    }
    if expected:
        assert "效果未知" in result.content and "不会重复写入" in result.content
        await curate("雨天夜跑歌单", catalog, journal)
        assert sum(call[0] == "create" for call in catalog.calls) == 1
        assert sum(call[0] == "add" for call in catalog.calls) == 3


@pytest.mark.asyncio
async def test_creation_unknown_does_not_guess_playlist_id_or_dispatch_adds(
    monkeypatch, journal
):
    catalog = Catalog(monkeypatch, mode="create_unknown")
    result = await curate("雨天夜跑歌单", catalog, journal)
    assert "效果未知" in result.content
    assert result.data["curation"]["playlist"] is None
    assert catalog.members == []


@pytest.mark.asyncio
async def test_a_readback_can_confirm_addition_without_promoting_creation(
    monkeypatch, journal
):
    catalog = Catalog(monkeypatch, mode="write_readback")
    operations = OperationJournal(
        journal.writer, journal.accounting, read_client=catalog.read_client()
    )
    result = await curate("雨天夜跑歌单", catalog, operations)
    assert result.status is TaskStatus.SUCCESS
    facts = result.data["result_facts"]
    assert facts[0]["state"] is None  # create response is not a readback
    assert all(fact["state"] == "confirmed" for fact in facts[1:])
    assert all(fact["source"] == "operation_journal" for fact in facts)
    assert "未全部只读核对" in result.content
    assert sum(call[0] == "add" for call in catalog.calls) == 15


@pytest.mark.asyncio
async def test_no_journal_means_no_external_writes(monkeypatch):
    catalog = Catalog(monkeypatch)
    result = await curate("雨天夜跑歌单", catalog, None)
    assert result.status is TaskStatus.FAILED
    assert catalog.calls == []


@pytest.mark.parametrize(
    "text",
    [
        "不要生成雨天夜跑歌单",
        "搜索雨天夜跑歌单",
        "播放90年代港乐",
        "删除歌单",
        "创建名叫跑步的歌单",
        "新建空歌单",
        "雨天夜跑歌单完成了吗",
        "如果生成雨天夜跑歌单",
        "暂停播放",
        "你好",
        "创建深夜加班歌单但不加歌",
        "推荐雨天夜跑歌单，不创建",
        "仅推荐雨天夜跑歌单",
        "生成雨天夜跑歌单可以吗",
        "我有雨天夜跑歌单",
        "90年代港乐挺好的",
        "我觉得深夜加班提神很好",
        "我有一个晨间通勤歌单",
    ],
)
def test_curator_dispatch_does_not_expand_unrelated_or_negated_authority(text):
    assert curation_request(text) is None


@pytest.mark.asyncio
async def test_unsupported_count_clarifies_without_writes(monkeypatch, journal):
    catalog = Catalog(monkeypatch)
    result = await curate("生成雨天夜跑歌单，50首", catalog, journal)
    assert result.status is TaskStatus.NEEDS_INPUT and catalog.calls == []


def test_private_playlist_parameters_come_from_user_clause():
    request = curation_request("生成私密雨天夜跑歌单，10首")
    assert (
        request.privacy == 10 and request.count == 10 and request.name == "雨天夜跑歌单"
    )


@pytest.mark.parametrize(
    "text", ["给我一张雨天夜跑歌单", "我想要雨天夜跑歌单", "来一份雨天夜跑歌单"]
)
def test_natural_request_prefixes_keep_the_scene(text):
    request = curation_request(text)
    assert (
        request is not None
        and request.scene == "雨天夜跑"
        and request.name == "雨天夜跑歌单"
    )


@pytest.mark.parametrize("bad_id", [True, "100000", 0, -1])
def test_catalog_compatibility_coercions_cannot_create_trusted_ids(bad_id):
    content = json.dumps(
        {
            "keyword": "夜跑",
            "limit": 50,
            "songs": [
                {
                    "song_id": bad_id,
                    "name": "曲目",
                    "artists": ["歌手"],
                    "album": "夜跑",
                }
            ],
        }
    )
    with pytest.raises(ValueError):
        _catalog(content, "夜跑")


@pytest.mark.asyncio
async def test_selector_may_reject_all_irrelevant_catalog_candidates(
    monkeypatch, journal
):
    catalog = Catalog(monkeypatch, scene="无关曲目")
    result = await curate(
        "生成火星殖民地音乐歌单", catalog, journal, model=responses(catalog, ids=[])
    )
    assert (
        result.status is TaskStatus.SUCCESS and "只找到 0 首符合场景" in result.content
    )
    assert not catalog.exists and journal.write_events() == []


@pytest.mark.asyncio
async def test_draft_never_echoes_model_tag_injections(monkeypatch, journal):
    catalog = Catalog(monkeypatch)
    model = responses(catalog)
    model.responses[0] = AIMessage(
        content=json.dumps(
            {"queries": ["夜跑"], "tags": ["《虚构曲目》是90年代经典，全部已确认"]},
            ensure_ascii=False,
        )
    )
    result = await curate("雨天夜跑歌单", catalog, journal, model=model)
    assert result.status is TaskStatus.SUCCESS
    assert "虚构曲目" not in result.content and "全部已确认" not in result.content


@pytest.mark.asyncio
async def test_full_handoff_keeps_existing_edges_and_curator_model_budget(
    monkeypatch, journal
):
    catalog = Catalog(monkeypatch, count=10)
    model = responses(catalog)
    graph = build_handoff_graph(
        intent_router=Router(RouteKey.TOOL_CALLING),
        transfer_model=ToolModel(responses=[call()]),
        curator_model=model,
    ).compile()
    token = active_operations.set(journal)
    try:
        output = await graph.ainvoke(
            input_state("雨天夜跑歌单"),
            context=MusicAgentContext(session_cookie="isolated-cookie"),
        )
    finally:
        active_operations.reset(token)
    assert not output["handoff_failed"], output["final_response"]
    assert len(catalog.members) == 10
    run = output["handoff_run"]
    assert len(run["hops"]) == 1 and run["hops"][0]["target_agent"] == "curator"
    usage = next(row for row in run["usage"] if row["agent"] == "curator")
    assert usage["model_calls"] == 2 and usage["reported_tokens"] > 0
    assert "未全部只读核对" in output["final_response"]
