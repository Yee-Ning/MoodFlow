"""Bounded comment requests and independent receipts through the actual graph."""
import json

from langchain_core.messages import AIMessage, ToolMessage
from pydantic import ValidationError
import pytest

from app.agent.foundation.long_horizon.models import HistoryEventType, HistoryPointer
from app.agent.foundation.long_horizon.operations import active_operations
from app.agent.foundation.models import TaskStatus
from app.agent.foundation.subgraphs.tool_calling import create_tool_calling_subgraph
from app.agent.foundation.subgraphs.tool_calling.graph import INVALID_TOOL_OUTPUT_MESSAGE
from app.schemas.popularity import SongPopularityArgs, SongPopularityResult
from app.tools.popularity_tools import get_song_popularity_tool
from tests.test_handoff_runtime import ToolModel
from tests.test_niche_live_comment_tool import install_reads, references
from tests.test_tool_calling_subgraph import invoke, tool_call
from tests.test_write_reconciliation import journal

__all__ = ["journal"]


@pytest.mark.asyncio
async def test_twelve_ids_enter_normal_verification(monkeypatch):
    ids = list(range(1, 13))
    reads = install_reads(monkeypatch, {song_id: 100 for song_id in ids})
    result = SongPopularityResult.model_validate_json(await get_song_popularity_tool.ainvoke({"song_ids": ids}))
    assert reads.await_count == 12 and [call.args[0] for call in reads.await_args_list] == ids
    assert result.qualified_song_ids == ids and result.unknown_song_ids == []
    assert all(song.comment_count == 100 and song.eligible is True for song in result.songs)


@pytest.mark.parametrize("schema", [SongPopularityArgs, get_song_popularity_tool.tool_call_schema])
def test_thirteen_ids_are_rejected_with_batch_guidance_in_both_schemas(schema):
    assert schema.model_json_schema()["properties"]["song_ids"]["maxItems"] == 12
    with pytest.raises(ValidationError, match="单次最多 12 个，请分多轮调用") as error:
        schema.model_validate({"song_ids": list(range(1, 14))})
    assert error.value.errors(include_input=False)[0]["type"] == "comment_tool_batch_limit"


@pytest.mark.asyncio
@pytest.mark.parametrize("oversized_first", [False, True])
async def test_three_batches_have_independent_audit_receipts_and_packed_fields(monkeypatch, journal, oversized_first):
    values = [99, 100, 10000, 10001, None, 0, 501, 99, 20000, None]
    counts = {song_id: values[(song_id - 1) % 10] for song_id in range(1, 31)
              if values[(song_id - 1) % 10] is not None}
    reads = install_reads(monkeypatch, counts)
    calls = [tool_call("get_song_popularity_tool", f"batch-{start}", {"song_ids": list(range(start, start + 10))})
             for start in (1, 11, 21)]
    if oversized_first:
        calls.insert(0, tool_call("get_song_popularity_tool", "too-many", {"song_ids": list(range(1, 31))}))
    model = ToolModel(responses=[*calls, AIMessage(content="推荐目录歌曲2、12、22，各自已核实评论总数100；不建单或播放。")])
    token = active_operations.set(journal)
    try:
        output = await invoke(create_tool_calling_subgraph(model=model), text="只推荐经核实的冷门歌曲，不建单不播放",
                              prior_entities=references(range(1, 31)))
    finally:
        active_operations.reset(token)
    result = output["task_result"]
    assert result.status is TaskStatus.SUCCESS and result.data["client_actions"] == []
    attempts = 4 if oversized_first else 3
    assert result.data["tool_calls"] == attempts
    assert result.data["tool_call_counts_by_phase"] == {"retrieval": attempts, "execution": 0}
    assert reads.await_count == 30 and [call.args[0] for call in reads.await_args_list] == list(range(1, 31))
    if oversized_first:
        rejected = next(m for m in model.seen_messages[1] if isinstance(m, ToolMessage) and m.tool_call_id == "too-many")
        error = json.loads(rejected.content)
        assert error["success"] is False and error["error"] == "invalid_tool_arguments"
        assert error["message"] == "单次最多 12 个，请分多轮调用"
    # Controlled model issues smaller calls after seeing the error. This asserts
    # usable feedback and actual calls, without promising arbitrary model choice.
    packed = {m.tool_call_id: SongPopularityResult.model_validate_json(m.content)
              for m in model.seen_messages[-1] if isinstance(m, ToolMessage)
              and m.name == "get_song_popularity_tool" and json.loads(m.content).get("success") is not False}
    assert set(packed) == {"batch-1", "batch-11", "batch-21"}
    events = journal.writer.store.query_task_history(journal.identity.continuity_task_id, limit=100)
    proposals = [e for e in events if e.event_type is HistoryEventType.TOOL_CALL]
    receipts = [e for e in events if e.event_type is HistoryEventType.TOOL_RESULT]
    assert len(proposals) == len(receipts) == 3
    assert all(e.payload["effect"] == "read" and len(e.payload["parameters"]["song_ids"]) == 10 for e in proposals)
    assert {e.payload["operation_id"] for e in proposals} == {e.payload["operation_id"] for e in receipts}
    artifacts = []
    for receipt in receipts:
        assert receipt.payload["status"] == "success"
        artifact = journal.writer.store.get_artifact(HistoryPointer.model_validate(receipt.payload["result_ref"]).target_id)
        artifacts.append(artifact.artifact_id)
        typed = SongPopularityResult.model_validate(artifact.payload["result"])
        start = typed.songs[0].song_id
        assert typed == packed[f"batch-{start}"]
        assert [song.song_id for song in typed.songs] == list(range(start, start + 10))
        assert [song.comment_count for song in typed.songs] == values
        assert [song.eligible for song in typed.songs] == [False, True, True, False, None, False, True, False, False, None]
        assert typed.qualified_song_ids == [start + 1, start + 6, start + 2]
        assert typed.unknown_song_ids == [start + 4, start + 9]
    assert len(set(artifacts)) == 3 and journal.write_events() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("name,args", [
    ("get_song_popularity_tool", {"song_ids": [True]}),
    ("get_song_popularity_tool", {"song_ids": list(range(1, 14)), "min_comments": 0}),
    ("search_music", {"keyword": "安静", "song_ids": list(range(1, 31))}),
])
async def test_other_errors_keep_original_generic_feedback(monkeypatch, name, args):
    reads = install_reads(monkeypatch, {1: 100})
    model = ToolModel(responses=[tool_call(name, "invalid", args), AIMessage(content="参数无效，未执行。")])
    await invoke(create_tool_calling_subgraph(model=model), text="只推荐歌曲", prior_entities=references(range(1, 31)))
    message = next(m for m in model.seen_messages[-1] if isinstance(m, ToolMessage))
    assert json.loads(message.content) == {"success": False, "error": "invalid_tool_arguments", "message": INVALID_TOOL_OUTPUT_MESSAGE,
                                         "operation_id": None, "write_state": None}
    reads.assert_not_awaited()
