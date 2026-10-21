"""Public tool facts survive bounded previews and the actual model messages."""

import copy
import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from app.agent.foundation.context_engineering import (
    POLICIES,
    _compress_tool_json,
    _project_tool_result,
    build_context,
)
from app.agent.foundation.routing import RouteKey
from app.rag.chunk.token_counter import UnicodeHeuristicTokenCounter


FIXTURE = json.loads((Path(__file__).parent / "fixtures/tool_projection_item7.json").read_text(encoding="utf-8"))
TRACK = {"song_id": 7, "name": "Track", "artists": ["Artist"], "album": "Album", "cover_url": "", "duration": 100}


def model_result(raw, name="search_music", policy=None):
    call = {"name": name, "args": {}, "id": "projection-call", "type": "tool_call"}
    pack = build_context(
        RouteKey.TOOL_CALLING, policy=policy, system="rules", query="report the total",
        messages=[AIMessage(content="", tool_calls=[call]),
                  ToolMessage(content=json.dumps(raw, ensure_ascii=False), name=name, tool_call_id=call["id"])],
    )
    tool = next(message for message in pack.messages if isinstance(message, ToolMessage))
    assert tool.tool_call_id == call["id"] and tool.name == name
    return json.loads(tool.content), pack


@pytest.mark.parametrize("case", ["liked", "playlist"])
def test_real_item7_totals_survive_model_tool_message(case):
    sample = FIXTURE["cases"][case]
    raw = copy.deepcopy(sample["result"])
    result, pack = model_result(raw, sample["tool"])
    field = "song_ids" if case == "liked" else "songs"
    assert len(sample["result"][field]) == 18
    assert result[field] == sample["result"][field][:10]
    assert result["_projection"][field] == {"total": 18, "shown": 10, "truncated": True}
    if case == "playlist":
        assert result["track_count"] == 18
        assert result["playlist_id"] == raw["playlist_id"]
        assert result["name"] == raw["name"]
    assert raw == sample["result"]
    assert pack.total_tokens <= pack.budget


@pytest.mark.parametrize("facts", [
    {"creator": "Test account", "creator_id": 23, "special_type": 5, "subscribed": False, "track_count": 0},
    {"creator_id": None, "special_type": None, "subscribed": None, "track_count": 18},
    {"operation_id": "operation:test", "write_state": "unknown", "success": False,
     "error": "timeout", "message": "Effect unknown"},
    {"operation_id": "operation:failed", "write_state": "failed", "success": False},
    {"operation_id": "operation:confirmed", "write_state": "confirmed"},
    {"status": "needs_input", "reason": "pending_queue_change", "target_source": "prior_play_action",
     "song_id": 7, "song": {"song_id": 7, "name": "Track", "artist": "Artist", "album": "Album",
                             "lyrics_status": "NO_LYRICS"},
     "candidates": [{"song_id": 8, "name": "Other", "artist": "Artist", "album": "Album"}]},
])
def test_audited_identity_status_and_failure_facts(facts):
    result, _ = model_result(facts)
    assert result == facts


@pytest.mark.parametrize("metadata", [
    {"duration": 100, "artist_ids": [11, 12], "album_id": 19},
    {"aliases": ["Artist alias"], "album_size": 3, "music_size": 18},
    {"publish_time": 100000, "company": "Label", "size": 18, "artist_ids": [11]},
    {"track_count": 18},
])
def test_entity_search_metadata_facts_and_text_boundary(metadata):
    raw = {"query": "Artist", "entity_type": "artist", "entities": [
        {"id": 11, "name": "Artist", "source": "netease", "type": "artist",
         "metadata": {**metadata, "description": "long text", "secret": "omit"}}]}
    result, _ = model_result(raw, "search_entities_tool")
    assert result["query"] == "Artist"
    assert result["entities"][0]["source"] == "netease"
    assert result["entities"][0]["metadata"] == metadata


@pytest.mark.parametrize("name,arguments", [
    ("pause_player", {}), ("resume_player", {}), ("play_next", {}), ("play_previous", {}),
    ("set_volume", {"volume": 0.3}), ("set_muted", {"muted": False}),
    ("seek_to", {"seconds": 0}), ("seek_by", {"offset_seconds": -5}),
    ("set_play_mode", {"mode": "single-loop"}), ("play_song", {"song": TRACK}),
    ("insert_next_track", {"song": TRACK}), ("append_to_queue", {"song": TRACK}),
    ("play_album", {"album_id": 19, "name": "Album", "tracks": [TRACK]}),
    ("play_playlist", {"playlist_id": 21, "name": "Playlist", "tracks": [TRACK]}),
    ("jump_to_queue_item", {"song_id": 7, "index": 1}), ("set_liked", {"song_id": 7, "liked": False}),
    ("open_lyrics", {}), ("clear_queue", {}),
])
def test_all_client_action_arguments_remain_model_visible(name, arguments):
    raw = {"status": "pending_client_execution", "client_action": {"name": name, "arguments": arguments}}
    result, _ = model_result(raw, name)
    assert result == raw


def test_combination_status_keeps_structured_tasks_and_failures():
    task = {"position": 1, "task_id": "task:1", "route_key": "tool_calling", "status": "failed",
            "reason": "tool_failed", "error": "timeout", "steps": [
                {"name": "search_music", "status": "error", "error": "timeout", "message": "Timed out"}],
            "pending_client_actions": ["pause_player"]}
    raw = {"run_id": "run:1", "stop_reason": "task_failed", "blocked_at": "task:1", "tasks": [task],
           "message": "The task failed"}
    result, _ = model_result(raw, "get_combination_task_status")
    assert result == raw
    assert _project_tool_result({"tasks": [], "reason": "no_combination_status"}) == {
        "tasks": [], "reason": "no_combination_status"}


@pytest.mark.parametrize("field", ["songs", "playlists", "entities", "tracks", "artists", "song_ids",
                                       "artist_ids", "aliases", "tasks", "steps", "pending_client_actions", "candidates"])
def test_every_audited_list_reports_truncation_at_original_limit(field):
    raw = {field: list(range(18)), "total": 99, "count": 18}
    result = _project_tool_result(raw)
    assert result[field] == list(range(10))
    assert result["_projection"][field] == {"total": 18, "shown": 10, "truncated": True}
    assert result["total"] == 99 and result["count"] == 18
    assert raw[field] == list(range(18))


@pytest.mark.parametrize("count", [0, 1, 10])
def test_untruncated_lists_preserve_existing_shape(count):
    raw = {"song_ids": list(range(1, count + 1)), "track_count": count}
    assert _project_tool_result(raw) == raw


def test_nested_and_root_lists_have_cardinality_at_their_own_level():
    rows = list(range(18))
    result = _project_tool_result({"client_action": {"name": "play_album", "arguments": {"tracks": rows}}})
    args = result["client_action"]["arguments"]
    assert args["tracks"] == rows[:10]
    assert args["_projection"]["tracks"]["total"] == 18
    root = _project_tool_result(rows)
    assert root == {"items": rows[:10], "_projection": {"items": {"total": 18, "shown": 10, "truncated": True}}}


@pytest.mark.parametrize("field", ["songs", "song_ids", "entities"])
def test_budget_reduction_keeps_original_total_and_updates_shown(field):
    raw = {field: [{"song_id": i, "name": "Track", "artists": ["Artist"], "album": "Album"}
                   for i in range(1, 19)], "track_count": 18}
    policy = POLICIES[RouteKey.TOOL_CALLING].model_copy(update={
        "total_input_budget": 200, "reserved_output_tokens": 0, "safety_margin": 0})
    result, pack = model_result(raw, policy=policy)
    assert 1 <= len(result[field]) < 10
    assert result["_projection"][field] == {"total": 18, "shown": len(result[field]), "truncated": True}
    assert result["track_count"] == 18
    assert result[field][0]["song_id"] == 1
    assert pack.total_tokens <= pack.budget == 200


def test_budget_truncation_alone_and_nested_action_truncation_are_honest():
    counter = UnicodeHeuristicTokenCounter()
    raw = {"client_action": {"name": "play_album", "arguments": {"tracks": [TRACK] * 10, "album_id": 19}}}
    result = json.loads(_compress_tool_json(json.dumps(raw), 170, counter))
    args = result["client_action"]["arguments"]
    assert 1 <= len(args["tracks"]) < 10
    assert args["_projection"]["tracks"] == {"total": 10, "shown": len(args["tracks"]), "truncated": True}
    assert counter.count(json.dumps(result, ensure_ascii=False, separators=(",", ":"))) <= 170


def test_small_budget_refuses_instead_of_dropping_facts_or_widening_limit():
    policy = POLICIES[RouteKey.TOOL_CALLING].model_copy(update={
        "total_input_budget": 50, "reserved_output_tokens": 0, "safety_margin": 0})
    with pytest.raises(ValueError, match="required context exceeds input budget"):
        model_result(FIXTURE["cases"]["playlist"]["result"], policy=policy)


def test_text_and_unknown_field_projection_rules_remain_unchanged():
    result = _project_tool_result({"description": "omit", "debug": "omit", "secret": "omit",
                                   "original_lyrics": "x" * 6000, "name": "Track"})
    assert result == {"original_lyrics": "x" * 4000, "name": "Track"}
    assert _project_tool_result("x" * 6000) == "x" * 4000
