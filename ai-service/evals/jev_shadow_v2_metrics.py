"""Separate gold-aware metrics for v2 Router, Memory and Context replays."""

from __future__ import annotations

from collections import Counter
from math import ceil
from statistics import mean
from typing import Any


def ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def timing(rows: list[dict[str, Any]], field: str) -> dict[str, float | None]:
    values = sorted(float(row[field]) for row in rows if row.get(field) is not None)
    return {"avg_ms": mean(values) if values else None,
            "p95_ms": values[ceil(.95 * len(values)) - 1] if values else None}


def router_v2_metrics(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    single = [row for row in rows if row["task"] == "single_intent"]
    multi = [row for row in rows if row["task"] == "multi_intent"]
    valid_jev = [row for row in single if row["jev_routes"]]
    comparable = [row for row in valid_jev if row["baseline_error"] is None]
    failures = []
    for row in comparable:
        old_ok = row["baseline_routes"] == row["gold_routes"]
        jev_ok = row["jev_routes"] == row["gold_routes"]
        if old_ok and jev_ok:
            continue
        kind = "old_right_jev_wrong" if old_ok else "old_wrong_jev_right" if jev_ok else "both_wrong"
        failures.append({"component": "Router", "case_id": row["case_id"],
                         "kind": kind, "input": row["query"], "gold": row["gold_routes"],
                         "baseline_result": row["baseline_routes"], "jev_result": row["jev_routes"],
                         "jev_confidence": row["jev_confidence"],
                         "jev_probabilities": row["jev_probabilities"],
                         "reason": (f"single-intent gold={row['gold_routes'][0]}; "
                                    f"baseline={row['baseline_routes'] or 'unresolved'}; "
                                    f"Jev={row['jev_routes'] or 'unresolved'}; "
                                    f"case tags={row.get('tags', [])}")})
    missing = extra = 0
    for row in multi:
        gold, predicted = set(row["gold_routes"]), set(row["jev_routes"])
        missing += len(gold - predicted)
        extra += len(predicted - gold)
        if gold != predicted:
            failures.append({"component": "Router", "case_id": row["case_id"],
                             "kind": "multi_intent_mismatch", "input": row["query"],
                             "gold": row["gold_routes"], "baseline_result": row["baseline_routes"],
                             "jev_result": row["jev_routes"],
                             "jev_probabilities": row["jev_noul_scores"],
                             "reason": f"missing={sorted(gold - predicted)}; extra={sorted(predicted - gold)}"})
    metrics = {
        "single_cases": len(single), "single_jev_valid": len(valid_jev),
        "existing_accuracy": ratio(sum(row["baseline_routes"] == row["gold_routes"] for row in single), len(single)),
        "jev_accuracy": ratio(sum(row["jev_routes"] == row["gold_routes"] for row in valid_jev), len(valid_jev)),
        "agreement": ratio(sum(row["baseline_routes"] == row["jev_routes"] for row in comparable), len(comparable)),
        "old_wrong_jev_right": sum(item["kind"] == "old_wrong_jev_right" for item in failures),
        "old_right_jev_wrong": sum(item["kind"] == "old_right_jev_wrong" for item in failures),
        "both_wrong": sum(item["kind"] == "both_wrong" for item in failures),
        "mean_jev_confidence": mean(row["jev_confidence"] for row in valid_jev
                                    if row["jev_confidence"] is not None) if valid_jev else None,
        "existing_latency": timing(single, "baseline_latency_ms"),
        "jev_latency": timing(single, "jev_latency_ms"),
        "multi_cases": len(multi),
        "existing_multi_detected": sum(len(row["baseline_routes"]) >= 2 for row in multi),
        "jev_multi_detected": sum(len(row["jev_routes"]) >= 2 for row in multi),
        "jev_multi_exact": sum(row["jev_routes"] == row["gold_routes"] for row in multi),
        "jev_multi_missing_intents": missing, "jev_multi_extra_intents": extra,
        "multi_latency": timing(multi, "jev_latency_ms"),
        "followup_analysis_cases": sum(row["task"] == "followup_analysis" for row in rows),
    }
    return metrics, failures


def memory_v2_metrics(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    failures = []
    by_mode = {}
    for mode in ("default", "episodic_only_probe"):
        group = [row for row in rows if row["retrieval_mode"] == mode]
        retrieved_gold = selected_gold = positives = negatives = selected_negatives = selected_total = candidates = 0
        for row in group:
            gold = set(row["gold_memory_ids"])
            baseline = set(row["baseline_selected_ids"])
            selected = set(row["jev_selected_ids"])
            retrieved_gold += len(gold & baseline)
            selected_gold += len(gold & selected)
            candidates += len(row["candidate_memories"])
            selected_total += len(selected)
            for item in row["candidate_memories"]:
                mid = item["memory_id"]
                if item["gold_useful"]:
                    positives += 1
                    if mid not in selected:
                        failures.append({"component": "Memory", "case_id": row["case_id"],
                                         "kind": "jev_false_drop", "input": row["query"],
                                         "gold": {mid: True}, "baseline_result": True,
                                         "jev_result": False, "jev_probability": row["jev_scores"].get(mid),
                                         "reason": "target memory was retrieved but Jev score fell below filter threshold"})
                else:
                    negatives += 1
                    if mid in selected:
                        selected_negatives += 1
                        failures.append({"component": "Memory", "case_id": row["case_id"],
                                         "kind": "jev_false_keep", "input": row["query"],
                                         "gold": {mid: False}, "baseline_result": True,
                                         "jev_result": True, "jev_probability": row["jev_scores"].get(mid),
                                         "reason": "other fact not required for this narrow synthetic query"})
        gold_total = sum(len(row["gold_memory_ids"]) for row in group)
        by_mode[mode] = {
            "cases": len(group), "gold_targets": gold_total,
            "retrieved_gold": retrieved_gold, "selected_gold": selected_gold,
            "retrieval_key_recall": ratio(retrieved_gold, gold_total),
            "filter_key_recall": ratio(selected_gold, retrieved_gold),
            "end_to_end_key_recall": ratio(selected_gold, gold_total),
            "baseline_precision": ratio(positives, candidates),
            "jev_precision": ratio(selected_gold, selected_total),
            "false_drop_rate": ratio(retrieved_gold - selected_gold, retrieved_gold),
            "false_keep_rate": ratio(selected_negatives, negatives),
            "candidate_reduction": 1 - ratio(selected_total, candidates) if candidates else None,
            "candidate_occurrences": candidates,
            "episodic_candidate_occurrences": sum(item["memory_type"] == "episodic"
                                                 for row in group for item in row["candidate_memories"]),
            "baseline_retrieval_latency": timing(group, "baseline_retrieval_latency_ms"),
            "jev_filter_latency": timing(group, "jev_latency_ms"),
            "fallback_rate": ratio(sum(row["fallback_used"] for row in group), len(group)),
        }
    return by_mode, failures


def context_v2_metrics(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    counts: Counter[str] = Counter()
    failures = []
    for row in rows:
        baseline = set(row["baseline_selected_ids"])
        selected = set(row["jev_selected_ids"])
        for cid, label in row["gold_labels"].items():
            counts[label] += 1
            counts[f"baseline_{label}"] += cid in baseline
            counts[f"jev_{label}"] += cid in selected
            kind = ("required_false_drop" if label == "required" and cid not in selected else
                    "useful_false_drop" if label == "useful" and cid not in selected else
                    "irrelevant_false_keep" if label == "irrelevant" and cid in selected else None)
            if kind:
                failures.append({"component": "Context", "case_id": row["case_id"],
                                 "kind": kind, "input": row["query"],
                                 "gold": {cid: label}, "source_type": row["source_types"][cid],
                                 "baseline_result": cid in baseline, "jev_result": cid in selected,
                                 "jev_probability": row["jev_scores"].get(cid),
                                 "reason": row["label_reasons"][cid]})
        kept_irrelevant = [cid for cid, label in row["gold_labels"].items()
                           if label == "irrelevant" and cid in selected]
        if len(kept_irrelevant) >= 3:
            failures.append({"component": "Context", "case_id": row["case_id"],
                             "kind": "many_irrelevant_kept", "input": row["query"],
                             "gold": {"irrelevant_ids": kept_irrelevant},
                             "baseline_result": sum(label == "irrelevant" and cid in baseline
                                                    for cid, label in row["gold_labels"].items()),
                             "jev_result": len(kept_irrelevant),
                             "jev_probability": {cid: row["jev_scores"].get(cid) for cid in kept_irrelevant},
                             "reason": "three or more task-irrelevant items remain"})
    baseline_tokens = sum(row["baseline_tokens"] for row in rows)
    jev_tokens = sum(row["jev_tokens"] for row in rows)
    baseline_items = sum(len(row["baseline_selected_ids"]) for row in rows)
    jev_items = sum(len(row["jev_selected_ids"]) for row in rows)
    metrics = {
        "cases": len(rows), "label_counts": {key: counts[key] for key in ("required", "useful", "irrelevant")},
        "baseline_required_preservation": ratio(counts["baseline_required"], counts["required"]),
        "jev_required_preservation": ratio(counts["jev_required"], counts["required"]),
        "baseline_useful_recall": ratio(counts["baseline_useful"], counts["useful"]),
        "jev_useful_recall": ratio(counts["jev_useful"], counts["useful"]),
        "baseline_irrelevant_retention": ratio(counts["baseline_irrelevant"], counts["irrelevant"]),
        "jev_irrelevant_retention": ratio(counts["jev_irrelevant"], counts["irrelevant"]),
        "context_reduction": 1 - ratio(jev_items, baseline_items) if baseline_items else None,
        "token_reduction": 1 - ratio(jev_tokens, baseline_tokens) if baseline_tokens else None,
        "required_false_drops": sum(item["kind"] == "required_false_drop" for item in failures),
        "useful_false_drops": sum(item["kind"] == "useful_false_drop" for item in failures),
        "baseline_latency": timing(rows, "baseline_latency_ms"),
        "jev_filter_latency": timing(rows, "jev_latency_ms"),
        "fallback_rate": ratio(sum(row["fallback_used"] for row in rows), len(rows)),
    }
    return metrics, failures
