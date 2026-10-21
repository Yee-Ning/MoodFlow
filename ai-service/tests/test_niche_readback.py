"""Verified partial recommendations finish instead of spending remaining reads."""

from hashlib import sha256
import json

from langchain_core.messages import AIMessage
import pytest

from app.agent.foundation.long_horizon.models import HistoryEventType, HistoryPointer
from app.agent.foundation.long_horizon.operations import active_operations
from app.agent.foundation.models import TaskStatus
from app.agent.foundation.routing import RouteKey, RoutingContext
from app.agent.foundation.subgraphs.tool_calling import create_tool_calling_subgraph
from app.agent.handoff.graph import build_handoff_graph
from app.agent.handoff.niche_readback import (
    NicheReadback, finish_niche_readback, niche_read_limit, reuse_niche_readback,
)
from app.agent.handoff.state import HandoffRun, RunStatus
from app.schemas.context import MusicAgentContext
from app.schemas.music import MusicSearchResult, MusicTrack
from app.schemas.popularity import SongPopularityResult
from app.tools.music import MusicServiceClient
from app.tools.popularity_tools import get_song_popularity_tool
from tests.test_handoff_runtime import Router, ToolModel, call, input_state
from tests.test_niche_live_comment_tool import install_reads, references
from tests.test_tool_calling_subgraph import invoke, tool_call
from tests.test_write_reconciliation import journal

__all__ = ["journal"]

REQUEST = ("请让 Curator 推荐最多3首适合安静阅读的冷门歌曲；"
           "只推荐网易云评论总数在100到10000之间的歌，给出歌名、歌手、song_id和评论总数；"
           "不足3首就少荐，不要用热门歌充数。不要建单或播放。")


@pytest.mark.asyncio
@pytest.mark.parametrize("counts", [{1: 689, **dict.fromkeys(range(2, 13), 2)},
                                    dict.fromkeys(range(1, 13), 99), {}])
async def test_actual_handoff_finishes_verified_batch_and_reuses_constraint_tasks(monkeypatch, journal, counts):
    reads = install_reads(monkeypatch, counts)
    searches = []

    async def search(client, keyword, limit=10):
        searches.append(keyword)
        return MusicSearchResult(keyword=keyword, limit=limit,
            songs=[MusicTrack(song_id=i, name=f"目录歌曲{i}", artists=["目录歌手"], album="目录专辑")
                   for i in range(1, 13)])

    monkeypatch.setattr(MusicServiceClient, "search_music", search)
    model = ToolModel(responses=[tool_call("search_music", "catalogue", {"keyword": "安静阅读", "limit": 12}),
        tool_call("get_song_popularity_tool", "verify", {"song_ids": list(range(1, 13))}),
        tool_call("search_music", "unnecessary-more", {"keyword": "再扩大候选"}), AIMessage(content="不应运行")])
    graph = build_handoff_graph(intent_router=Router(*[RouteKey.TOOL_CALLING] * 3),
        transfer_model=ToolModel(responses=[call()]), curator_model=model).compile()
    token = active_operations.set(journal)
    try:
        output = await graph.ainvoke(input_state(REQUEST, RoutingContext()), context=MusicAgentContext())
    finally:
        active_operations.reset(token)
    run = HandoffRun.model_validate(output["handoff_run"])
    assert run.status is RunStatus.COMPLETED and len(run.results) == 3
    assert all(r.status is TaskStatus.SUCCESS and r.data["client_actions"] == [] for r in run.results)
    assert len(model.seen_messages) == 2 and searches == ["安静阅读"] and reads.await_count == 12
    assert [r.data["tool_call_counts_by_phase"] for r in run.results] == [
        {"retrieval": 2, "execution": 0}, {"retrieval": 0, "execution": 0}, {"retrieval": 0, "execution": 0}]
    assert all(r.data.get("reason") is None and "tool_budget_stop" not in r.data for r in run.results)
    assert all(r.data["niche_readback_reused"] for r in run.results[1:])
    receipt = NicheReadback.model_validate(run.results[0].data["niche_readback"])
    assert receipt.request_sha256 == sha256(REQUEST.encode()).hexdigest()
    if 1 in counts and counts[1] == 689:
        assert "目录歌曲1" in run.final_response and "目录歌手" in run.final_response
        assert "song_id=1；评论总数689" in run.final_response and "按要求少荐" in run.final_response
        assert [song.song_id for song in receipt.catalogue] == list(range(1, 13))
        assert [r.data["entities"]["songs"][0]["song_id"] for r in run.results] == [1, 1, 1]
    else:
        assert "返回空集，不凑数" in run.final_response
        assert all(r.data["entities"]["songs"] == [] for r in run.results)
        if not counts:
            assert "12首评论数未知，未当作0" in run.final_response
    events = journal.writer.store.query_task_history(journal.identity.continuity_task_id, limit=100)
    proposals = [e for e in events if e.event_type is HistoryEventType.TOOL_CALL]
    successes = [e for e in events if e.event_type is HistoryEventType.TOOL_RESULT]
    assert [e.payload["name"] for e in proposals] == ["search_music", "get_song_popularity_tool"]
    assert all(e.payload["effect"] == "read" for e in proposals) and journal.write_events() == []
    typed_receipt = next(e for e in successes if e.payload["name"] == "get_song_popularity_tool")
    artifact = journal.writer.store.get_artifact(HistoryPointer.model_validate(typed_receipt.payload["result_ref"]).target_id)
    assert SongPopularityResult.model_validate(artifact.payload["result"]) == receipt.verifications[0]
    # Wrong-request, failed or corrupt metadata never supplies a completion.
    base = run.model_copy(update={"results": run.results[:1], "status": RunStatus.RUNNING, "final_response": None})
    for changed in ({"request_sha256": "0" * 64}, {"source_task_id": "another-task"},
                    {"verifications": [{"qualified_song_ids": [999]}]}):
        source = base.results[0].model_copy(update={"data": {**base.results[0].data,
            "niche_readback": {**base.results[0].data["niche_readback"], **changed}}})
        assert reuse_niche_readback(base.model_copy(update={"results": (source,)})) is None
    failed = base.results[0].model_copy(update={"status": TaskStatus.FAILED, "error": "失败"})
    assert reuse_niche_readback(base.model_copy(update={"results": (failed,)})) is None


@pytest.mark.parametrize("text", [REQUEST.replace("最多3首", "3首"), REQUEST + "然后播放第一首。",
    REQUEST + "并且创建歌单。", REQUEST + "继续找更多。", REQUEST + "逐一核实全部候选。",
    REQUEST.replace("100到10000", "1000到2000"), "只推荐最多3首安静歌曲，不建单或播放。"])
def test_completion_rule_does_not_capture_exact_counts_effects_other_work_or_other_policy(text):
    assert niche_read_limit(text) is None


@pytest.mark.asyncio
async def test_missing_catalogue_and_invalid_or_failed_receipt_cannot_become_recommendations(monkeypatch):
    install_reads(monkeypatch, {1: 100})
    raw = await get_song_popularity_tool.ainvoke({"song_ids": [1]})
    record = {"name": "get_song_popularity_tool", "status": "success", "content": raw}
    assert finish_niche_readback(REQUEST, "task", [record], []) is None
    assert finish_niche_readback(REQUEST, "task", [{**record, "status": "error"}], references([1])[0].songs) is None
    corrupt = json.loads(raw)
    corrupt["songs"][0]["comment_count"] = 99
    assert finish_niche_readback(REQUEST, "task", [{**record, "content": json.dumps(corrupt)}], references([1])[0].songs) is None


@pytest.mark.asyncio
async def test_model_cannot_finish_with_unverified_recommendations(monkeypatch):
    reads = install_reads(monkeypatch, {1: 100})
    model = ToolModel(responses=[AIMessage(content="推荐目录歌曲1，评论总数100。")])
    output = await invoke(create_tool_calling_subgraph(model=model), text=REQUEST, prior_entities=references([1]))
    result = output["task_result"]
    reads.assert_not_awaited()
    assert result.status is TaskStatus.FAILED and result.data["reason"] == "niche_verification_missing"
    assert "未提供冷门推荐" in result.content and "评论总数100" not in result.content
