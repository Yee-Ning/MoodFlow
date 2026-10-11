import json
from pathlib import Path

import pytest

from evals.jev_router_baseline_v1 import (
    DATASET, QUESTIONABLE, Recorder, failure_rows, multi_metrics, prepare,
    single_metrics, summarize,
)


def _row(case_id, gold, selected, jev=None, task="single_intent", status="historical_gold", tags=None):
    return {
        "case_id": case_id, "query": case_id, "gold_routes": gold,
        "selected_routes": selected, "jev_routes": selected if jev is None else jev,
        "task": task, "label_status": status, "tags": tags or [],
        "jev_error": None, "fallback_reason": None, "jev_calls": 1,
        "jev_api_latency_ms": 10.0, "jev_wall_latency_ms": 12.0,
        "hybrid_latency_ms": 2.0, "usage": {"input_tokens": 3, "output_tokens": 2},
        "response_model": "test", "questionable_reason": "unsupported" if status == "questionable" else None,
        "baseline_routes": selected, "confidence": .9, "probabilities": None,
        "jev_structured_output": {"route": {"choice": selected[0] if selected else None}},
    }


def test_prepare_keeps_historical_labels_and_candidates_separate(monkeypatch, tmp_path):
    before = Path("evals/datasets/router_eval_v2.jsonl").read_bytes()
    original_diagnostic = DATASET.read_bytes()
    generated = tmp_path / DATASET.name
    monkeypatch.setattr("evals.jev_router_baseline_v1.DATASET", generated)
    prepare()
    rows = [json.loads(line) for line in generated.read_text(encoding="utf-8").splitlines()]
    original = [json.loads(line) for line in before.decode("utf-8").splitlines()]
    assert len(rows) == len(original) + 12
    assert [(row["case_id"], row["gold_routes"]) for row in rows[:len(original)]] == [
        (row["case_id"], row["gold_routes"]) for row in original
    ]
    assert {row["case_id"] for row in rows if row["label_status"] == "questionable"} == QUESTIONABLE
    assert Path("evals/datasets/router_eval_v2.jsonl").read_bytes() == before
    assert DATASET.read_bytes() == original_diagnostic


def test_metrics_separate_single_multi_boundary_and_candidate():
    rows = [
        _row("s1", ["current_song"], ["current_song"], tags=["boundary"]),
        _row("s2", ["general_chat"], ["worldline"], jev=["general_chat"]),
        _row("m1", ["general_chat", "tool_calling"], ["general_chat"],
             task="multi_intent", tags=["boundary"]),
        _row("c1", [], ["general_chat"], task="negative", status="candidate"),
    ]
    result = summarize(rows)
    assert result["single_intent"]["jev_choice"]["accuracy"] == 1
    assert result["single_intent"]["main_graph_selected"]["accuracy"] == .5
    assert result["multi_intent"]["main_graph_selected"]["under_routing_rate"] == 1
    assert result["boundary_cases"]["count"] == 2
    assert result["candidate_cases"]["forced_route_count"] == 1
    assert result["usage_total_formal"]["input_tokens"] == 9
    assert result["usage_total_all_calls"]["input_tokens"] == 12


def test_empty_predictions_and_questionable_failure_trace():
    assert single_metrics([_row("s", ["worldline"], [])], "selected_routes")["confusion_matrix"]["worldline"]["none"] == 1
    assert multi_metrics([_row("m", ["current_song", "tool_calling"], ["general_chat"], task="multi_intent")], "selected_routes")["over_routing_rate"] == 1
    row = _row("q", ["worldline"], ["general_chat"], status="questionable")
    failure = failure_rows([row])[0]
    assert failure["label_ambiguity"]
    assert "capability_mismatch" in failure["error_category"]
    assert failure["jev_raw_structured_output"]
    shadow_only_error = _row("shadow", ["general_chat"], ["general_chat"], jev=["worldline"])
    assert failure_rows([shadow_only_error])[0]["selected_routes"] == ["general_chat"]


def test_capability_contract_has_one_complete_spec_per_route():
    contract = json.loads(Path("evals/config/router_capability_contract.json").read_text(encoding="utf-8"))
    assert {route["route"] for route in contract["routes"]} == {
        "current_song", "tool_calling", "general_chat", "worldline"
    }
    required = {"core_capabilities", "supported_intents", "required_context",
                "required_entities", "supported_actions", "unsupported_or_not_yet_verified",
                "failure_boundaries", "ambiguous_queries", "multi_intent_combinations",
                "can_handle", "cannot_handle", "requires", "positive_examples",
                "negative_examples", "boundary_examples", "status"}
    for route in contract["routes"]:
        assert required <= route.keys()
        assert set(route["status"].values()) <= {"A", "B", "C", "D"}


@pytest.mark.asyncio
async def test_recorder_preserves_safe_failure_category():
    class FailingClient:
        async def decide(self, _state, _questions):
            raise ValueError("invalid response")

    recorder = Recorder(FailingClient())
    with pytest.raises(ValueError):
        await recorder.decide({"query": "hello"}, {})
    assert recorder.calls == 1
    assert recorder.error == "ValueError"
    assert recorder.responses == []
