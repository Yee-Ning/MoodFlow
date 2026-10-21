"""Model invocation, read receipt and typed pack visibility, not registry alone."""
import json
from unittest.mock import AsyncMock

from langchain_core.messages import AIMessage, ToolMessage
import pytest
from pydantic import ValidationError

from app.agent.foundation.context_engineering import ContextCollector
from app.agent.foundation.long_horizon.models import HistoryEventType, HistoryPointer
from app.agent.foundation.long_horizon.operations import active_operations, safe_tool_result
from app.agent.foundation.models import TaskStatus
from app.agent.foundation.routing import RoutingContext
from app.agent.foundation.subgraphs.tool_calling import ReferencedEntities, SongReference, create_tool_calling_subgraph
from app.schemas.popularity import SongPopularityArgs, SongPopularityResult
from app.tools import popularity
from app.tools.music import MusicServiceClient, MusicServiceError
from app.tools.popularity_tools import get_song_popularity_tool
from tests.test_handoff_runtime import ToolModel
from tests.test_tool_calling_subgraph import invoke, tool_call
from tests.test_write_reconciliation import journal

__all__ = ["journal"]


def references(ids):
    return [ReferencedEntities(songs=[SongReference(position=i + 1, song_id=song_id,
        name=f"目录歌曲{song_id}", artists=["目录歌手"], album="目录专辑") for i, song_id in enumerate(ids)])]


def install_reads(monkeypatch, counts):
    async def read(client, song_id):
        if song_id not in counts:
            raise MusicServiceError("http_502", "服务暂不可用")
        return counts[song_id]
    mock = AsyncMock(side_effect=lambda song_id: None)
    async def observed(client, song_id):
        await mock(song_id)
        return await read(client, song_id)
    monkeypatch.setattr(MusicServiceClient, "get_song_comment_count", observed)
    monkeypatch.setattr(popularity, "_limiter", lambda: popularity.PopularityReadLimiter(0))
    return mock


@pytest.mark.asyncio
async def test_actual_model_tool_call_journal_and_pack_retain_verified_unknown_and_eligibility(monkeypatch, journal):
    reads = install_reads(monkeypatch, {1: 99, 2: 100, 3: 10000, 4: 10001, 6: 0})
    model = ToolModel(responses=[tool_call("get_song_popularity_tool", "comment-check", {"song_ids": [1, 2, 3, 4, 5, 6]}),
                                AIMessage(content="推荐目录歌曲2：评论总数100；目录歌曲3：评论总数10000。评论数不是播放量。")])
    # Real builder/model bind/agent/validation/ToolNode/journal/pack chain.
    # The fake model actually emits a tool call; registration alone cannot pass.
    graph = create_tool_calling_subgraph(model=model)
    token = active_operations.set(journal)
    try:
        output = await invoke(graph, text="推荐冷门歌曲，核实评论数，不建单不播放", prior_entities=references(range(1, 7)))
    finally:
        active_operations.reset(token)
    result = output["task_result"]
    assert result.status is TaskStatus.SUCCESS and result.data["client_actions"] == []
    assert result.data["tool_call_counts_by_phase"] == {"retrieval": 1, "execution": 0}
    assert result.data["tool_calls"] == 1
    assert len(model.seen_messages) == 2 and reads.await_count == 6
    received = next(m for m in model.seen_messages[-1] if isinstance(m, ToolMessage) and m.name == "get_song_popularity_tool")
    typed = SongPopularityResult.model_validate_json(received.content)
    assert typed.qualified_song_ids == [2, 3] and typed.rejected_song_ids == [1, 4, 6] and typed.unknown_song_ids == [5]
    assert [r.comment_count for r in typed.songs] == [99, 100, 10000, 10001, None, 0]
    assert [r.eligible for r in typed.songs] == [False, True, True, False, None, False]
    events = journal.writer.store.query_task_history(journal.identity.continuity_task_id, limit=100)
    calls = [e for e in events if e.event_type == HistoryEventType.TOOL_CALL]
    receipts = [e for e in events if e.event_type == HistoryEventType.TOOL_RESULT]
    assert len(calls) == len(receipts) == 1 and calls[0].payload["name"] == "get_song_popularity_tool"
    assert calls[0].payload["effect"] == "read" and receipts[0].payload["status"] == "success"
    artifact = journal.writer.store.get_artifact(HistoryPointer.model_validate(receipts[0].payload["result_ref"]).target_id)
    assert SongPopularityResult.model_validate(artifact.payload["result"]) == typed
    assert journal.write_events() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("count,qualified", [(99, False), (100, True), (10000, True), (10001, False), (None, None), (0, False)])
async def test_interval_and_unknown_use_existing_reader_and_classifier(monkeypatch, count, qualified):
    reads = install_reads(monkeypatch, {} if count is None else {1: count})
    result = SongPopularityResult.model_validate_json(await get_song_popularity_tool.ainvoke({"song_ids": [1, 1]}))
    assert len(result.songs) == 1 and result.songs[0].comment_count == count and result.songs[0].eligible is qualified
    assert result.songs[0].status == ("unknown" if count is None else "known")
    assert result.qualified_song_ids == ([1] if qualified else []) and reads.await_count == 1
    if count is None:
        assert result.unknown_song_ids == [1] and result.rejected_song_ids == []
    assert "不是播放量" in result.proxy_note


@pytest.mark.asyncio
@pytest.mark.parametrize("counts", [{1: 99, 2: 10001}, {}])
async def test_empty_qualified_set_produces_honest_empty_answer_and_no_write_or_play(monkeypatch, journal, counts):
    install_reads(monkeypatch, counts)
    model = ToolModel(responses=[tool_call("get_song_popularity_tool", "verify-empty", {"song_ids": [1, 2]}),
                                AIMessage(content="无满足100–10000区间且评论数已核实的歌曲，返回空集，不凑数。")])
    token = active_operations.set(journal)
    try:
        output = await invoke(create_tool_calling_subgraph(model=model), text="只推荐冷门歌曲，不要建单或播放", prior_entities=references([1, 2]))
    finally:
        active_operations.reset(token)
    message = next(m for m in model.seen_messages[-1] if isinstance(m, ToolMessage))
    result = SongPopularityResult.model_validate_json(message.content)
    assert result.qualified_song_ids == [] and "返回空集" in result.message and "不放宽区间或凑数" in result.message
    assert "空集" in output["task_result"].content and "不凑数" in output["task_result"].content
    assert output["task_result"].data["client_actions"] == [] and journal.write_events() == []
    assert output["task_result"].data["tool_calls"] == 1
    assert [c["name"] for c in output["task_result"].data["tool_call_details"]] == ["get_song_popularity_tool"]


@pytest.mark.asyncio
@pytest.mark.parametrize("context", [None, RoutingContext(has_current_song=True, current_song_id=999)])
async def test_untrusted_song_id_is_rejected_before_reader_or_journal(monkeypatch, journal, context):
    reads = install_reads(monkeypatch, {999: 100})
    model = ToolModel(responses=[tool_call("get_song_popularity_tool", "untrusted", {"song_ids": [999]}),
                                AIMessage(content="该ID没有可信目录来源，未核实或推荐。")])
    token = active_operations.set(journal)
    try:
        output = await invoke(create_tool_calling_subgraph(model=model), text="只推荐冷门歌曲", prior_entities=references([1]), context=context)
    finally:
        active_operations.reset(token)
    reads.assert_not_awaited()
    message = next(m for m in model.seen_messages[-1] if isinstance(m, ToolMessage))
    assert json.loads(message.content)["error"] == "untrusted_entity_reference"
    assert not journal.writer.store.query_task_history(journal.identity.continuity_task_id, event_type=HistoryEventType.TOOL_CALL, limit=100)
    assert output["task_result"].data["client_actions"] == []


@pytest.mark.parametrize("arguments", [{"song_ids": [True]}, {"song_ids": ["1"]}, {"song_ids": [1.0]},
    {"song_ids": [0]}, {"song_ids": []}, {"song_ids": list(range(1, 62))},
    {"song_ids": [1], "min_comments": 0}, {"song_ids": [1], "max_comments": 99999}])
def test_public_arguments_are_strict_bounded_and_cannot_override_thresholds(arguments):
    with pytest.raises(ValidationError):
        SongPopularityArgs.model_validate(arguments)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["min_comments", "max_comments"])
async def test_model_threshold_override_is_rejected_before_tool_execution(monkeypatch, field):
    reads = install_reads(monkeypatch, {1: 100})
    model = ToolModel(responses=[tool_call("get_song_popularity_tool", "override",
        {"song_ids": [1], field: 0}), AIMessage(content="不能覆盖服务端评论数策略，未执行。")])
    await invoke(create_tool_calling_subgraph(model=model), text="只推荐冷门歌曲", prior_entities=references([1]))
    reads.assert_not_awaited()
    message = next(m for m in model.seen_messages[-1] if isinstance(m, ToolMessage))
    assert json.loads(message.content)["error"] == "invalid_tool_arguments"


@pytest.mark.asyncio
async def test_result_whitelist_and_atomic_projection_reject_corruption(monkeypatch):
    install_reads(monkeypatch, {1: 100, 2: 10000})
    raw = await get_song_popularity_tool.ainvoke({"song_ids": [1, 2, 3]})
    data = safe_tool_result("get_song_popularity_tool", raw)["result"]
    items = ContextCollector().collect(system="规则", query="核实评论数", messages=[
        tool_call("get_song_popularity_tool", "paired", {"song_ids": [1, 2, 3]}),
        ToolMessage(content=raw, tool_call_id="paired", name="get_song_popularity_tool")])
    item = next(x for x in items if x.id == "tool:paired")
    assert json.loads(item.content) == data and item.required and not item.compressible
    for change in ({"qualified_song_ids": [3]}, {"min_comments": 0}, {"unknown_song_ids": []}):
        with pytest.raises(ValidationError):
            SongPopularityResult.model_validate({**data, **change})
    with pytest.raises(ValidationError):
        SongPopularityResult.model_validate({**data, "songs": [*data["songs"][:-1],
            {"song_id": 3, "status": "unknown", "comment_count": 0, "eligible": False}]})


@pytest.mark.asyncio
async def test_registration_does_not_force_model_selection_or_call_reader(monkeypatch):
    reads = install_reads(monkeypatch, {1: 100})
    model = ToolModel(responses=[AIMessage(content="没有评论数核实回执，尚未完成冷门核实。")])
    output = await invoke(create_tool_calling_subgraph(model=model), text="推荐冷门歌曲", prior_entities=references([1]))
    assert "get_song_popularity_tool" in model.bound_names[0]
    reads.assert_not_awaited()
    assert output["task_result"].data["tool_calls"] == 0 and output["task_result"].data["tool_call_details"] == []
