"""Regression checks for calibrated multi-label selection and Memory ranking."""

from __future__ import annotations

import pytest

from app.agent.memory.models import MemoryRecord, MemoryRetrievalRequest
from app.agent.memory.retriever import MemoryRetriever
from evals import jev_targeted_v3
from evals.jev_shadow_v1 import read_jsonl, write_jsonl
from evals.jev_synthetic_memory_v2 import synthetic_memories
from evals.jev_targeted_v3 import ROUTES, select_routes


def _record(category: str, number: int) -> MemoryRecord:
    row = next(item for item in synthetic_memories()
               if item["metadata"]["category"] == category
               and item["memory_id"].endswith(f"-{number:03d}"))
    values = {key: value for key, value in row.items() if key != "metadata"}
    values["structured_content"] = row["metadata"]
    return MemoryRecord.model_validate(values)


def test_profile_topic_ranks_above_unrelated_profile_without_scene() -> None:
    target = _record("music_preference", 1)
    distractor = _record("music_preference", 2)
    request = MemoryRetrievalRequest(user_id=target.user_id,
                                     query="我对周杰伦的音乐是什么态度？")
    assert MemoryRetriever._profile_relevance(target, request) == 1
    assert MemoryRetriever._profile_relevance(distractor, request) < .7
    malformed_subject = distractor.model_copy(update={"structured_content": {"subject": "--"}})
    assert MemoryRetriever._profile_relevance(malformed_subject, request) < .7
    blank = MemoryRetrievalRequest(user_id=target.user_id)
    assert MemoryRetriever._profile_relevance(target, blank) == .75


def test_episodic_history_cue_boost_is_bounded_and_query_specific() -> None:
    episode = _record("episodic", 1)
    history = MemoryRetrievalRequest(user_id=episode.user_id, query="我上次关于和弦聊了什么？")
    current = MemoryRetrievalRequest(user_id=episode.user_id, query="请分析和弦")
    assert MemoryRetriever._episode_relevance(episode, .6, history) == pytest.approx(.9)
    assert MemoryRetriever._episode_relevance(episode, .6, current) == .6
    assert MemoryRetriever._episode_relevance(episode, .95, history) == 1


def test_multi_label_threshold_supports_zero_one_three_and_cap() -> None:
    scores = dict.fromkeys(ROUTES, .1)
    thresholds = dict.fromkeys(ROUTES, .5)
    assert select_routes(scores, thresholds, 3) == []
    scores["current_song"] = .9
    assert select_routes(scores, thresholds, 3) == ["current_song"]
    scores["tool_calling"] = .8
    scores["worldline"] = .7
    assert select_routes(scores, thresholds, 3) == ["current_song", "tool_calling", "worldline"]
    assert select_routes(scores, thresholds, 2) == ["current_song", "tool_calling"]
    with pytest.raises(ValueError):
        select_routes({"current_song": .9}, thresholds, 3)


def test_router_calibration_ignores_frozen_test_scores(tmp_path, monkeypatch) -> None:
    source = tmp_path / "router.jsonl"
    output = tmp_path / "calibration.jsonl"
    monkeypatch.setattr(jev_targeted_v3, "ROUTER_RESULTS", source)
    monkeypatch.setattr(jev_targeted_v3, "ROUTER_CALIBRATION", output)
    scores = {route: .1 for route in ROUTES}
    scores["current_song"] = .8
    rows = [{"split": "dev", "task": "single_intent", "jev_scores": scores,
             "gold_routes": ["current_song"]} for _ in range(15)]
    rows += [{"split": "dev", "task": "multi_intent", "jev_scores": scores,
              "gold_routes": ["current_song", "tool_calling"]} for _ in range(7)]
    rows.append({"split": "test", "task": "multi_intent", "jev_scores": scores,
                 "gold_routes": ["general_chat"]})
    write_jsonl(source, rows)
    jev_targeted_v3.calibrate_router_v3()
    first = read_jsonl(output)[0]
    rows[-1]["gold_routes"] = ["current_song", "tool_calling", "worldline"]
    write_jsonl(source, rows)
    jev_targeted_v3.calibrate_router_v3()
    assert read_jsonl(output)[0] == first
