"""Regression checks for v2 gold separation and safety-critical evaluation math."""

from __future__ import annotations

from collections import Counter

from evals.jev_context_v2 import _case
from evals.jev_shadow_v2_metrics import context_v2_metrics, memory_v2_metrics, router_v2_metrics
from evals.jev_synthetic_memory_v2 import _delete_sql, synthetic_memories


def test_synthetic_corpus_has_required_categories_and_scoped_cleanup() -> None:
    rows = synthetic_memories()
    assert len(rows) == len({row["memory_id"] for row in rows}) == 100
    assert Counter(row["metadata"]["category"] for row in rows) == {
        "music_preference": 30, "negative_preference": 15, "habit": 20,
        "episodic": 15, "conflict": 10, "unrelated": 10,
    }
    assert all({"memory_id", "user_id", "memory_type", "content", "metadata"} <= row.keys()
               for row in rows)
    sql = _delete_sql()
    assert "user_id IN" in sql and "memory_id LIKE 'jev-v2-%'" in sql


def test_router_accuracy_excludes_multi_intent_and_followup() -> None:
    base = {"baseline_error": None, "jev_confidence": .8, "baseline_latency_ms": 1,
            "jev_latency_ms": 2, "jev_probabilities": None, "jev_noul_scores": None,
            "query": "test", "case_id": "test"}
    rows = [
        {**base, "task": "single_intent", "gold_routes": ["current_song"],
         "baseline_routes": ["worldline"], "jev_routes": ["current_song"]},
        {**base, "task": "multi_intent", "gold_routes": ["current_song", "tool_calling"],
         "baseline_routes": ["current_song"], "jev_routes": ["current_song"]},
        {**base, "task": "followup_analysis", "gold_routes": ["current_song"],
         "baseline_routes": [], "jev_routes": []},
    ]
    metrics, failures = router_v2_metrics(rows)
    assert metrics["single_cases"] == 1
    assert metrics["existing_accuracy"] == 0
    assert metrics["jev_accuracy"] == 1
    assert metrics["followup_analysis_cases"] == 1
    assert metrics["jev_multi_missing_intents"] == 1
    assert any(row["kind"] == "old_wrong_jev_right" for row in failures)


def test_memory_retrieval_miss_is_separate_from_filter_false_drop() -> None:
    row = {"retrieval_mode": "default", "gold_memory_ids": ["key", "missed"],
           "candidate_memories": [{"memory_id": "key", "memory_type": "profile", "gold_useful": True},
                                  {"memory_id": "other", "memory_type": "profile", "gold_useful": False}],
           "baseline_selected_ids": ["key", "other"], "jev_selected_ids": ["other"],
           "jev_scores": {"key": .1, "other": .9}, "case_id": "memory-test", "query": "test",
           "baseline_retrieval_latency_ms": 1, "jev_latency_ms": 2, "fallback_used": False}
    metrics, failures = memory_v2_metrics([row])
    default = metrics["default"]
    assert default["retrieval_key_recall"] == .5
    assert default["filter_key_recall"] == 0
    assert default["end_to_end_key_recall"] == 0
    assert default["false_drop_rate"] == 1
    assert [item["kind"] for item in failures] == ["jev_false_drop", "jev_false_keep"]


def test_context_gold_covers_required_handoff_tool_memory_and_rag() -> None:
    rows = [_case(family, 1, "周杰伦") for family in ("memory", "rag", "tool", "handoff", "mixed")]
    required_types = {item["source_type"] for row in rows for item in row["items"]
                      if item["gold_label"] == "required"}
    assert {"long_term_memory", "rag_evidence", "conversation", "handoff_projection"} <= required_types
    assert any(item["source_type"] == "tool_result" and item["gold_label"] == "useful"
               for row in rows for item in row["items"])


def test_context_required_drop_is_blocking_even_if_baseline_also_drops() -> None:
    row = {"case_id": "context-test", "query": "test", "gold_labels": {"r": "required"},
           "source_types": {"r": "rag_evidence"}, "label_reasons": {"r": "answer source"},
           "baseline_selected_ids": [], "jev_selected_ids": [], "jev_scores": {"r": .1},
           "baseline_tokens": 0, "jev_tokens": 0, "baseline_latency_ms": 1,
           "jev_latency_ms": 2, "fallback_used": False}
    metrics, failures = context_v2_metrics([row])
    assert metrics["jev_required_preservation"] == 0
    assert metrics["required_false_drops"] == 1
    assert failures[0]["kind"] == "required_false_drop"
