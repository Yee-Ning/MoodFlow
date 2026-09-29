"""Anonymous V5 Shadow aggregation contracts."""
from __future__ import annotations

import json

from deploy.jev_shadow_summary import _tokens, summarize


def test_summary_separates_acceptance_and_production_without_raw_query():
    event = {
        "decision_type": "router_v5_shadow", "sample_kind": "acceptance",
        "router_source": "jev_multi", "candidate_routes": ["general_chat", "tool_calling"],
        "route_agreement": False, "task_count_agreement": True,
        "clarify_disagreement": False, "fallback_reason": None,
        "total_router_latency_ms": 100, "api_calls": 2,
        "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        "candidate_subgraph_dispatch_count": 0, "candidate_tool_action_dispatch_count": 0,
    }
    lines = ["log jev.v5_shadow " + json.dumps(event) + "\n",
             "log jev.v5_shadow_failure " + json.dumps({"sample_kind": "acceptance"}) + "\n",
             "log jev.v5_shadow_dropped capacity sample_kind=acceptance\n",
             "other log with user data; ignore it\n"]
    result = summarize(lines)
    assert result["production"]["sample_size"] == 0
    group = result["acceptance"]
    assert group["sample_size"] == 1 and group["multi_count"] == 1
    assert group["route_distribution"] == {"general_chat": 1, "tool_calling": 1}
    assert group["normal_latency_ms"] == {"avg": 100, "p50": 100.0, "p95": 100.0}
    assert group["tokens_per_query"] == 15
    assert group["shadow_failure_count"] == 1 and group["dropped_count"] == 1
    assert "user data" not in json.dumps(result)


def test_summary_keeps_fallback_separate_and_counts_tokens_once():
    event = {"decision_type": "router_v5_shadow", "sample_kind": "production",
             "router_source": "legacy_hybrid_fallback", "candidate_routes": [],
             "route_agreement": None, "task_count_agreement": None,
             "fallback_reason": "timeout", "total_router_latency_ms": 200,
             "api_calls": 1, "usage": {"prompt_tokens": 4, "completion_tokens": 2}}
    group = summarize(["jev.v5_shadow " + json.dumps(event)])["production"]
    assert group["fallback_count"] == group["timeout_count"] == 1
    assert group["route_comparable_count"] == 0
    assert group["fallback_latency_ms"]["p95"] == 200
    assert group["tokens_per_query"] == 6
    assert _tokens({"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}) == 15
