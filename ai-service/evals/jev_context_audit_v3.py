"""Case-level review of v2 Context false drops and frozen-score threshold replay."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from app.agent.foundation.context_engineering import ContextItem, ContextSelector, POLICIES
from app.agent.foundation.routing import RouteKey
from evals.jev_shadow_v1 import DATASETS, REPORTS, read_jsonl, write_jsonl
from evals.jev_targeted_v3 import split_for

AUDITED_GOLD = DATASETS / "context_gold_audit_v3.jsonl"
AUDIT = REPORTS / "context_false_drop_audit_v3.jsonl"
CALIBRATION = REPORTS / "context_threshold_calibration_v3.jsonl"
RESULTS = REPORTS / "context_threshold_replay_v3.jsonl"
DEFAULT_THRESHOLD = .5


def prepare_context_audit_v3() -> Path:
    """Correct a status-only tool result label before any threshold search."""
    cases = read_jsonl(DATASETS / "context_eval_v2.jsonl")
    by_id = {row["case_id"]: row for row in read_jsonl(REPORTS / "context_eval_v2_results.jsonl")}
    splits = split_for("context")
    reviews = []
    for case in cases:
        result = by_id[case["case_id"]]
        case["split"] = splits[case["case_id"]]
        for item in case["items"]:
            cid = item["context_id"]
            if cid in result["jev_selected_ids"] or item["gold_label"] != "useful":
                continue
            old_gold = item["gold_label"]
            if cid == "tool_result:source-index" and case["family"] == "rag":
                # A success message says a lookup happened but contains no source identifier or evidence.
                item["gold_label"] = "irrelevant"
                item["label_reason"] = "status-only lookup; no source identifier or answer evidence"
                category = "gold_label_unreasonable"
            elif cid == "tool_result:version" and case["family"] == "mixed":
                category = "uniform_threshold_too_high_for_tool_result"
            else:
                category = "jev_misjudgment"
            reviews.append({"case_id": case["case_id"], "split": case["split"],
                            "context_id": cid, "source_type": item["source_type"],
                            "query": case["query"], "content": item["context_item"]["content"],
                            "original_gold": old_gold, "audited_gold": item["gold_label"],
                            "baseline_keep": cid in result["baseline_selected_ids"],
                            "jev_keep": False, "jev_probability": result["jev_scores"].get(cid),
                            "classification": category,
                            "reason": item["label_reason"]})
    if len(reviews) != 9 or Counter(row["classification"] for row in reviews) != {
        "gold_label_unreasonable": 6, "uniform_threshold_too_high_for_tool_result": 3,
    }:
        raise ValueError("unexpected Context false-drop audit inventory")
    write_jsonl(AUDITED_GOLD, cases)
    write_jsonl(AUDIT, reviews)
    return AUDIT


def _replay(case: dict[str, Any], result: dict[str, Any], tool_threshold: float) -> dict[str, Any]:
    pool = [ContextItem.model_validate(item["context_item"]) for item in case["items"]]
    scores = result["jev_scores"]
    proposed = [item for item in pool if item.required or item.type not in {
        "conversation", "long_term_memory", "rag_evidence", "history_drilldown",
        "artifact_projection", "handoff_projection", "tool_result",
    } or scores[item.id] >= (tool_threshold if item.type == "tool_result" else DEFAULT_THRESHOLD)]
    start = perf_counter()
    selected = ContextSelector().build(proposed, POLICIES[RouteKey.WORLDLINE])
    select_ms = (perf_counter() - start) * 1000
    return {"case_id": case["case_id"], "split": case["split"],
            "gold_labels": {item["context_id"]: item["gold_label"] for item in case["items"]},
            "baseline_selected_ids": result["baseline_selected_ids"],
            "selected_ids": [item.id for item in selected.items],
            "baseline_tokens": result["baseline_tokens"], "selected_tokens": selected.total_tokens,
            "jev_api_ms": result["jev_latency_ms"],
            "jev_total_ms": result["jev_latency_ms"] + select_ms,
            "baseline_total_ms": result["baseline_latency_ms"],
            "requested_model": result["requested_model"],
            "response_model": result["response_model"],
            "evaluation_timestamp": result["evaluation_timestamp"]}


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    for row in rows:
        selected = set(row["selected_ids"])
        for cid, gold in row["gold_labels"].items():
            counts[gold] += 1
            counts[f"kept_{gold}"] += cid in selected
    baseline_tokens = sum(row["baseline_tokens"] for row in rows)
    return {"cases": len(rows), "required_preservation": counts["kept_required"] / counts["required"],
            "useful_recall": counts["kept_useful"] / counts["useful"],
            "irrelevant_retention": counts["kept_irrelevant"] / counts["irrelevant"],
            "false_drop": counts["useful"] - counts["kept_useful"],
            "token_reduction": 1 - sum(row["selected_tokens"] for row in rows) / baseline_tokens}


def calibrate_context_v3() -> Path:
    cases = [case for case in read_jsonl(AUDITED_GOLD) if case["split"] == "dev"]
    results = {row["case_id"]: row for row in read_jsonl(REPORTS / "context_eval_v2_results.jsonl")}
    rows = []
    for threshold in (.3, .35, .4, .45, .5, .55):
        replay = [_replay(case, results[case["case_id"]], threshold) for case in cases]
        metrics = _metrics(replay)
        if metrics["required_preservation"] != 1:
            raise ValueError("required Context item was dropped")
        rows.append({"tool_result_threshold": threshold, **metrics,
                     "objective": 2 * metrics["useful_recall"] - metrics["irrelevant_retention"]})
    rows.sort(key=lambda row: (-row["objective"], row["irrelevant_retention"],
                               -row["tool_result_threshold"]))
    write_jsonl(CALIBRATION, rows)
    return CALIBRATION


def replay_context_test_v3() -> Path:
    calibration = read_jsonl(CALIBRATION)
    threshold = calibration[0]["tool_result_threshold"]
    cases = [case for case in read_jsonl(AUDITED_GOLD) if case["split"] == "test"]
    results = {row["case_id"]: row for row in read_jsonl(REPORTS / "context_eval_v2_results.jsonl")}
    output = []
    replay_timestamp = datetime.now(UTC).isoformat()
    for case in cases:
        output.append({"replay_timestamp": replay_timestamp,
                       "tool_result_threshold": threshold,
                       "policy": "optimized", **_replay(case, results[case["case_id"]], threshold)})
        output.append({"replay_timestamp": replay_timestamp,
                       "tool_result_threshold": DEFAULT_THRESHOLD,
                       "policy": "baseline_jev", **_replay(case, results[case["case_id"]], DEFAULT_THRESHOLD)})
    write_jsonl(RESULTS, output)
    return RESULTS
