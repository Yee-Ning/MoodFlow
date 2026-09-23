import json
from pathlib import Path

import pytest

from evals.jev_shadow_metrics import context_metrics, memory_metrics, router_metrics


def test_router_failure_categories_and_denominator():
    rows = [
        {"case_id": "a", "query": "a", "gold_route": ["general_chat"],
         "existing_route": ["general_chat"], "jev_route": ["worldline"],
         "fallback_used": False, "latency_ms": 10},
        {"case_id": "b", "query": "b", "gold_route": ["tool_calling"],
         "existing_route": ["general_chat"], "jev_route": ["tool_calling"],
         "fallback_used": False, "latency_ms": 20},
        {"case_id": "c", "query": "c", "gold_route": ["current_song"],
         "existing_route": ["general_chat"], "jev_route": ["worldline"],
         "fallback_used": True, "latency_ms": None},
    ]
    metrics, failures = router_metrics(rows)
    assert metrics["existing_accuracy"] == 1 / 3
    assert metrics["jev_accuracy"] == 1 / 3
    assert metrics["fallback_rate"] == 1 / 3
    assert {item["kind"] for item in failures} == {
        "old_right_jev_wrong", "old_wrong_jev_right", "both_wrong"}


def test_context_required_drop_is_blocking_and_uses_same_pool():
    rows = [{"case_id": "c1", "query": "q", "candidate_ids": ["r", "u", "i"],
             "gold_labels": {"r": "required", "u": "useful", "i": "irrelevant"},
             "baseline_selected_ids": ["r", "u", "i"],
             "jev_preselect_ids": ["u", "i"], "jev_selected_ids": ["u", "i"],
             "baseline_tokens": 100, "jev_tokens": 80, "jev_scores": {"u": .9, "i": .8},
             "fallback_used": False, "latency_ms": 50}]
    metrics, failures = context_metrics(rows)
    assert metrics["jev_required_preservation"] == 0
    assert metrics["jev_useful_recall"] == 1
    assert metrics["token_reduction"] == pytest.approx(.2)
    assert {item["kind"] for item in failures} == {
        "required_false_drop", "irrelevant_false_keep"}


def test_memory_uncertain_excluded_and_key_drop_audited():
    rows = [{"case_id": "m", "query": "q", "candidates": [
        {"memory_id": "key", "gold_useful": True},
        {"memory_id": "bad", "gold_useful": False},
        {"memory_id": "edge", "gold_useful": "uncertain"}],
        "baseline_selected_ids": ["key", "bad", "edge"],
        "jev_selected_ids": ["bad"], "jev_scores": {"key": .1, "bad": .9},
        "fallback_used": False, "latency_ms": 20}]
    metrics, failures = memory_metrics(rows)
    assert metrics["useful_labels"] == 1
    assert metrics["uncertain_labels"] == 1
    assert metrics["jev_key_memory_recall"] == 0
    assert metrics["candidate_reduction"] == pytest.approx(2 / 3)
    assert {item["kind"] for item in failures} == {
        "key_memory_false_drop", "irrelevant_false_keep"}


def test_diagnostic_sets_have_traceable_labels_and_unique_cases():
    dataset_dir = Path(__file__).resolve().parents[1] / "evals" / "datasets"
    router = [json.loads(line) for line in
              (dataset_dir / "jev_router_diagnostic_v1.jsonl").read_text(encoding="utf-8").splitlines()]
    context = [json.loads(line) for line in
               (dataset_dir / "jev_context_diagnostic_v1.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(router) == 47
    assert len({row["case_id"] for row in router}) == len(router)
    assert sum("historically_failed" in row["tags"] for row in router) == 14
    assert sum("multi_turn" in row["tags"] for row in router) == 2
    assert len(context) == 30
    assert len({row["case_id"] for row in context}) == len(context)
    assert all({item["gold_label"] for item in row["items"]} <=
               {"required", "useful", "irrelevant"} for row in context)
