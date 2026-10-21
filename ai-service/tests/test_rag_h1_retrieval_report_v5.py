"""Offline scoring eligibility must not conceal failed production retrieval."""

from copy import deepcopy

import pytest

from evals import rag_h1_retrieval_report_v5 as report
from evals.rag_h1_bundle_v5 import H1Error


@pytest.fixture
def complete_data():
    ids = [f"case-{i}" for i in range(25)]
    state = {"status": "incomplete", "completed_case_count": 25, "error_code": None,
             "protected_unchanged": True, "guard_denied": ["socket.getaddrinfo"] * 25,
             "new_deepseek_calls": 0, "judge_calls": 0, "secret_reads": 0, "baseline_rerun": False}
    rows = [{"case_id": case_id, "healthy": True, "search_calls": 1, "rrf_calls": 1,
             "leg_results": [{"error_code": None} for _ in range(7)]} for case_id in ids]
    root = "/collections/" + report.retrieval.baseline.COLLECTION
    requests = [{"method": "GET", "path": "/"}] * 50 + [{"method": "GET", "path": root}] * 25 + [
        {"method": "POST", "path": root + "/points/query/batch"}] * 25
    diagnostic = {"guard_denied": ["socket.getaddrinfo"], "http_attempts": [{"method": "GET", "path": "/"}],
                  "query_api_calls": 0, "error_type": "Gate0ForbiddenOperation",
                  "dns_attempts": [{"host": "127.0.0.1", "port": 7897, "stack": [{"file": "httpcore/_sync/http_proxy.py"}]}]}
    return state, rows, requests, diagnostic, ids


def test_advisory_issue_scoring_is_explicit_and_preserves_raw_incomplete(complete_data):
    state, rows, requests, diagnostic, ids = complete_data
    before = deepcopy(state)
    result = report.scoring_eligibility(*complete_data)
    assert result["valid_for_scoring"] and result["runner_status"] == "incomplete"
    assert result["runner_status_preserved"] and result["unresolved_runtime_audit_flag_for_West_review"]
    assert "Inference" in result["classification_basis"]
    assert result["additional_diagnostic_query_calls"] == 0
    assert state == before


def test_normal_complete_run_needs_no_advisory_override(complete_data):
    state, rows, requests, diagnostic, ids = complete_data
    state.update(status="complete", guard_denied=[])
    assert report.scoring_eligibility(*complete_data)["audit_issue"] is None


@pytest.mark.parametrize("mutation", ["missing_case", "unhealthy", "leg_failure", "extra_fusion", "changed_frozen", "new_judge", "reran_baseline"])
def test_failed_or_changed_experiment_cannot_be_rescued_by_probe_diagnostic(complete_data, mutation):
    state, rows, _, _, _ = complete_data
    if mutation == "missing_case":
        rows.pop()
    elif mutation == "unhealthy":
        rows[0]["healthy"] = False
    elif mutation == "leg_failure":
        rows[0]["leg_results"][0]["error_code"] = "dense_query_failed"
    elif mutation == "extra_fusion":
        rows[0]["rrf_calls"] = 2
    elif mutation == "changed_frozen":
        state["protected_unchanged"] = False
    elif mutation == "new_judge":
        state["judge_calls"] = 1
    else:
        state["baseline_rerun"] = True
    with pytest.raises(H1Error, match="missing_or_unhealthy"):
        report.scoring_eligibility(*complete_data)


@pytest.mark.parametrize("mutation", ["unknown_socket", "wrong_destination", "extra_query", "write", "missing_binding", "missing_evidence"])
def test_unknown_audit_issues_extra_operations_or_missing_evidence_fail_closed(complete_data, mutation):
    state, _, requests, diagnostic, _ = complete_data
    if mutation == "unknown_socket":
        state["guard_denied"][0] = "socket.connect"
    elif mutation == "wrong_destination":
        diagnostic["dns_attempts"][0]["host"] = "other"
    elif mutation == "extra_query":
        requests.append(deepcopy(requests[-1]))
    elif mutation == "write":
        requests.append({"method": "PUT", "path": "/collections/forbidden"})
    elif mutation == "missing_binding":
        requests.pop(50)
    else:
        diagnostic.clear()
    with pytest.raises(H1Error):
        report.scoring_eligibility(*complete_data)


def test_latency_uses_nearest_rank_p95_and_does_not_fabricate_independent_leg_time():
    assert report.latency_stats([3.0, 1.0, 2.0]) == {"n": 3, "mean_ms": 2.0, "median_ms": 2.0,
        "min_ms": 1.0, "max_ms": 3.0, "p95_nearest_rank_ms": 3.0}
    rows = [{"leg_results": [{"leg_id": "q0:decomposed:dense", "channel": "dense", "variant_index": 0,
                              "hit_count": count, "shared_search_wall_ms": timing}]} for count, timing in ((80, 2.0), (40, 4.0))]
    leg = report.leg_summary(rows)[0]
    assert leg["hit_count_total"] == 120 and leg["hit_count_mean"] == 60
    assert leg["shared_search_latency"]["mean_ms"] == 3
    assert leg["independent_leg_latency_ms"] is None


def test_entity_resolution_outputs_are_counted_separately_from_noop_query_completion():
    rows = [{"query_understanding": {"completion_triggered": False, "completion_changed": False, "entities": entities}}
            for entities in ([], [{"status": "resolved"}, {"status": "ambiguous"}])]
    summary = report.query_understanding_summary(rows)
    assert summary["completion_triggered"] == summary["completion_changed"] == 0
    assert summary["cases_with_entity_output"] == 1
    assert summary["entity_status_counts"] == {"resolved": 1, "ambiguous": 1}
    assert not summary["entities_used_as_filters_or_query_rewrites"]


def test_stage_localization_distinguishes_raw_recall_from_fusion_selection_and_rerank():
    parents = ["raw_miss", "fusion_miss", "selection_miss", "rerank_miss", "hit", "overfetch_hit"]

    def hits(names):
        return [{"parent_id": p} for p in names]
    row = {"case_id": "x", "question_group": "timeline", "relevant_parent_ids": parents,
           "leg_results": [{"hits": hits(parents[1:])}], "rrf_top40": hits(parents[2:5]),
           "selection_input_rrf": hits(parents[2:]), "selection": hits(parents[3:]), "rerank_top12": hits(parents[4:])}
    localized = report.stage_localization(row)
    assert localized["exclusive_terminal_classes"] == {
        "not_in_any_leg": ["raw_miss"], "raw_retrieved_below_rrf80": ["fusion_miss"],
        "selection_dropped": ["selection_miss"], "rerank_dropped": ["rerank_miss"], "rerank_top12_hit": ["hit", "overfetch_hit"]}
    assert localized["rrf40_missed_but_rerank12_hit"] == ["overfetch_hit"]
    assert sum(len(v) for v in localized["exclusive_terminal_classes"].values()) == len(parents)
