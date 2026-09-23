import pytest

from app.agent.decision.jev.client import DecisionResponse
from app.agent.decision.jev.evaluation import summarize_decisions
from app.agent.decision.jev.telemetry import record_decision


def test_evaluation_reports_gold_metrics_only_when_present():
    result = summarize_decisions([
        {"decision_type": "router", "requested_model": "jev-latest", "response_model": "jev-1.13.0",
         "existing_route": "general_chat", "jev_route": "tool_calling", "gold_route": "tool_calling",
         "agreement": False, "confidence": 0.9, "latency_ms": 20, "fallback_used": False},
        {"decision_type": "memory_filter", "candidate_count": 2, "selected_count": 1,
         "selected_ids": ["m1"], "key_memory_ids": ["m1"], "irrelevant_memory_ids": ["m2"],
         "fallback_used": False},
        {"decision_type": "context_select", "candidate_count": 2, "selected_count": 1,
         "selected_ids": ["c1"], "useful_context_ids": ["c1"], "required_preserved": True,
         "baseline_tokens": 100, "final_tokens": 80, "downstream_compatible": True,
         "fallback_used": False},
    ])
    assert result["response_models"] == {"jev-1.13.0": 1}
    assert result["router"]["route_accuracy"] == 1
    assert result["router"]["agreement_rate"] == 0
    assert result["memory_filter"]["key_memory_recall"] == 1
    assert result["memory_filter"]["irrelevant_memory_reduction"] == 1
    assert result["context_select"]["required_preservation"] == 1
    assert result["context_select"]["final_token_reduction"] == pytest.approx(0.2)


def test_missing_gold_is_unavailable_not_zero():
    result = summarize_decisions([{"decision_type": "router", "fallback_used": True}])
    assert result["router"]["route_accuracy"] is None
    assert result["router"]["mean_latency_ms"] is None


def test_telemetry_records_alias_resolution_without_secret(caplog):
    result = DecisionResponse("jev-latest", "jev-1.13.0", "2026-09-23T00:00:00+00:00",
                              10.0, None, {})
    with caplog.at_level("INFO", logger="app.agent.decision.jev.telemetry"):
        record_decision("router", result, candidate_count=4, selected_count=1,
                        existing_route="general_chat", jev_route="tool_calling")
    assert '"requested_model":"jev-latest"' in caplog.text
    assert '"response_model":"jev-1.13.0"' in caplog.text
    assert '"evaluation_timestamp":"2026-09-23T00:00:00+00:00"' in caplog.text
