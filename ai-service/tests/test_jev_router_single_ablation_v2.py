from evals.jev_router_single_ablation_v2 import (
    CONTRACT_PATH, CONTRACT_SHA256, GOLD, GOLD_SHA256, OLD_CRITERIA,
    eligible_cases, failure_rows, legacy_gold_correction, sha256, variant_metrics,
)


def test_ablation_inputs_are_frozen_and_single_only():
    assert sha256(GOLD) == GOLD_SHA256
    assert sha256(CONTRACT_PATH) == CONTRACT_SHA256
    cases = eligible_cases()
    assert len(cases) == 46
    assert all(row["scoring_status"] == "scored" and row["task"] == "single_intent"
               and len(row["adjudicated_label"]) == 1 for row in cases)
    assert OLD_CRITERIA["worldline"] == "Research music knowledge or relationships"


def test_gold_correction_rescores_the_same_phase_one_predictions():
    metrics = legacy_gold_correction(eligible_cases())
    assert metrics["paired_count"] == 30
    assert metrics["same_phase_one_predictions_old_labels"]["correct"] == 23
    assert metrics["same_phase_one_predictions_adjudicated_labels"]["correct"] == 23
    assert metrics["label_flip_outcomes"] == {"newly_correct": 4, "newly_incorrect": 4}


def test_metrics_and_failure_rows_include_boundary_and_api_failures():
    def row(case_id, gold, predicted, confidence, error=None):
        return {"case_id": case_id, "query": case_id, "original_label": [gold],
                "adjudicated_label": [gold], "action": "KEEP",
                "A": {"predicted_routes": predicted, "confidence": confidence,
                      "probabilities": None, "error_category": error,
                      "error_type": "TimeoutError" if error else None,
                      "structured_output": None, "usage": {"input_tokens": 2},
                      "response_model": "model" if not error else None,
                      "api_latency_ms": 10 if not error else None,
                      "wall_latency_ms": 11}}

    rows = [row("concept", "general_chat", ["worldline"], .4),
            row("entity", "worldline", ["worldline"], .9),
            row("song", "current_song", ["current_song"], .9),
            row("search", "tool_calling", [], None, "api_failure")]
    for item in rows:
        item["B"] = item["A"]
    metrics = variant_metrics(rows, "A")
    assert metrics["count"] == 4
    assert metrics["correct"] == 2
    assert metrics["general_chat_worldline_confusion"] == 1
    assert metrics["low_confidence_count"] == 1
    assert metrics["api_failure_count"] == 1
    assert metrics["parse_schema_failure_count"] == 0
    assert metrics["token_usage"]["input_tokens"] == 8
    failures = failure_rows(rows)
    assert [item["case_id"] for item in failures] == ["concept", "search"]
    assert "A_low_confidence" in failures[0]["categories"]
    assert "B_api_failure" in failures[1]["categories"]
