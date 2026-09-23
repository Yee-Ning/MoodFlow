"""Gold-aware metrics for Jev shadow diagnostic replays."""

from __future__ import annotations

from math import ceil
from statistics import mean
from typing import Any


def rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def latency(rows: list[dict[str, Any]], field: str = "latency_ms") -> dict[str, float | None]:
    values = sorted(float(row[field]) for row in rows if row.get(field) is not None)
    return {"avg_ms": mean(values) if values else None,
            "p95_ms": values[ceil(len(values) * .95) - 1] if values else None}


def router_metrics(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    existing_valid = [row for row in rows if row.get("existing_route") is not None]
    jev_valid = [row for row in rows if row.get("jev_route")]
    comparable = [row for row in existing_valid if row.get("jev_route")]
    old_right = [row for row in existing_valid if row["existing_route"] == row["gold_route"]]
    jev_right = [row for row in jev_valid if row["jev_route"] == row["gold_route"]]
    failures = []
    for row in rows:
        if not row.get("jev_route"):
            failures.append({"case_id": row["case_id"], "kind": "jev_unavailable",
                             "input": row["query"], "gold": row["gold_route"],
                             "baseline": row.get("existing_route"), "jev": row.get("jev_route"),
                             "jev_confidence": None, "jev_probabilities": None,
                             "reason": "Jev returned no valid route; see fallback"})
    for row in comparable:
        old_ok = row["existing_route"] == row["gold_route"]
        jev_ok = row["jev_route"] == row["gold_route"]
        if old_ok and jev_ok:
            continue
        kind = "old_right_jev_wrong" if old_ok else "old_wrong_jev_right" if jev_ok else "both_wrong"
        reason = ("single Choice cannot express multi-route gold" if len(row["gold_route"]) > 1 else
                  "existing router left request unresolved" if not row["existing_route"] else
                  "Jev semantic route disagrees with gold; manual cause review needed" if old_ok else
                  "existing semantic route disagrees with gold; manual cause review needed" if jev_ok else
                  "both semantic routes disagree with gold; manual cause review needed")
        failures.append({"case_id": row["case_id"], "kind": kind, "input": row["query"],
                         "gold": row["gold_route"], "baseline": row["existing_route"],
                         "jev": row["jev_route"], "jev_confidence": row.get("jev_confidence"),
                         "jev_probabilities": row.get("jev_probabilities"),
                         "reason": reason})
    metrics = {"cases": len(rows), "comparable": len(comparable),
               "existing_valid": len(existing_valid), "jev_valid": len(jev_valid),
               "existing_accuracy": rate(len(old_right), len(existing_valid)),
               "jev_accuracy": rate(len(jev_right), len(jev_valid)),
               "agreement": rate(sum(row["existing_route"] == row["jev_route"] for row in comparable), len(comparable)),
               "old_wrong_jev_right": sum(item["kind"] == "old_wrong_jev_right" for item in failures),
               "old_right_jev_wrong": sum(item["kind"] == "old_right_jev_wrong" for item in failures),
               "both_wrong": sum(item["kind"] == "both_wrong" for item in failures),
               "fallback_rate": rate(sum(bool(row.get("fallback_used")) for row in rows), len(rows)),
               "baseline_avg_ms": latency(rows, "baseline_latency_ms")["avg_ms"],
               "baseline_p95_ms": latency(rows, "baseline_latency_ms")["p95_ms"],
               **latency(rows)}
    return metrics, failures


def context_metrics(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    totals = {"required": 0, "baseline_required": 0, "jev_required": 0,
              "useful": 0, "baseline_useful": 0, "jev_useful": 0,
              "irrelevant": 0, "baseline_irrelevant": 0, "jev_irrelevant": 0,
              "candidates": 0, "preselected": 0, "baseline_tokens": 0, "jev_tokens": 0}
    failures: list[dict[str, Any]] = []
    for row in rows:
        baseline = set(row["baseline_selected_ids"])
        jev = set(row["jev_selected_ids"])
        totals["candidates"] += len(row["candidate_ids"])
        totals["preselected"] += len(row["jev_preselect_ids"])
        totals["baseline_tokens"] += row["baseline_tokens"]
        totals["jev_tokens"] += row["jev_tokens"]
        for context_id, gold in row["gold_labels"].items():
            if gold not in ("required", "useful", "irrelevant"):
                continue
            totals[gold] += 1
            totals[f"baseline_{gold}"] += context_id in baseline
            totals[f"jev_{gold}"] += context_id in jev
            kind = ("required_false_drop" if gold == "required" and context_id not in jev else
                    "useful_false_drop" if gold == "useful" and context_id not in jev and context_id in baseline else
                    "irrelevant_false_keep" if gold == "irrelevant" and context_id in jev else None)
            if kind:
                failures.append({"case_id": row["case_id"], "kind": kind, "input": row["query"],
                                 "gold": {context_id: gold}, "baseline": context_id in baseline,
                                 "jev": context_id in jev,
                                 "jev_score": row.get("jev_scores", {}).get(context_id),
                                 "reason": kind.replace("_", " ")})
        kept_irrelevant = [item_id for item_id, gold in row["gold_labels"].items()
                           if gold == "irrelevant" and item_id in jev]
        if len(kept_irrelevant) >= 3:
            failures.append({"case_id": row["case_id"], "kind": "many_irrelevant_kept",
                             "input": row["query"], "gold": {"irrelevant_ids": kept_irrelevant},
                             "baseline": sum(gold == "irrelevant" and item_id in baseline
                                             for item_id, gold in row["gold_labels"].items()),
                             "jev": len(kept_irrelevant),
                             "jev_score": {item_id: row.get("jev_scores", {}).get(item_id)
                                           for item_id in kept_irrelevant},
                             "reason": "three or more irrelevant items remain after Jev"})
    metrics = {
        "cases": len(rows), "required_count": totals["required"],
        "baseline_required_preservation": rate(totals["baseline_required"], totals["required"]),
        "jev_required_preservation": rate(totals["jev_required"], totals["required"]),
        "baseline_useful_recall": rate(totals["baseline_useful"], totals["useful"]),
        "jev_useful_recall": rate(totals["jev_useful"], totals["useful"]),
        "baseline_irrelevant_retention": rate(totals["baseline_irrelevant"], totals["irrelevant"]),
        "jev_irrelevant_retention": rate(totals["jev_irrelevant"], totals["irrelevant"]),
        "candidate_reduction": 1 - rate(totals["preselected"], totals["candidates"])
        if totals["candidates"] else None,
        "item_reduction": 1 - rate(sum(len(row["jev_selected_ids"]) for row in rows),
                                   sum(len(row["baseline_selected_ids"]) for row in rows))
        if sum(len(row["baseline_selected_ids"]) for row in rows) else None,
        "token_reduction": 1 - rate(totals["jev_tokens"], totals["baseline_tokens"])
        if totals["baseline_tokens"] else None,
        "fallback_rate": rate(sum(bool(row.get("fallback_used")) for row in rows), len(rows)),
        "baseline_avg_ms": latency(rows, "baseline_latency_ms")["avg_ms"],
        "baseline_p95_ms": latency(rows, "baseline_latency_ms")["p95_ms"],
        **latency(rows),
    }
    return metrics, failures


def memory_metrics(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    useful = irrelevant = uncertain = old_useful = jev_useful = old_irrelevant = jev_irrelevant = 0
    old_kept = jev_kept = candidates = 0
    failures: list[dict[str, Any]] = []
    for row in rows:
        baseline = set(row["baseline_selected_ids"])
        jev = set(row["jev_selected_ids"])
        candidates += len(row["candidates"])
        old_kept += len(baseline)
        jev_kept += len(jev)
        for item in row["candidates"]:
            mid = item["memory_id"]
            gold = item["gold_useful"]
            if gold == "uncertain":
                uncertain += 1
                continue
            if gold is True:
                useful += 1
                old_useful += mid in baseline
                jev_useful += mid in jev
                kind = "key_memory_false_drop" if mid in baseline and mid not in jev else None
            else:
                irrelevant += 1
                old_irrelevant += mid in baseline
                jev_irrelevant += mid in jev
                kind = "irrelevant_false_keep" if mid in jev else None
            if kind:
                failures.append({"case_id": row["case_id"], "kind": kind,
                                 "input": row["query"], "gold": {mid: gold},
                                 "baseline": mid in baseline, "jev": mid in jev,
                                 "jev_score": row.get("jev_scores", {}).get(mid),
                                 "reason": kind.replace("_", " ")})
    metrics = {
        "cases": len(rows), "candidates": candidates, "useful_labels": useful,
        "irrelevant_labels": irrelevant, "uncertain_labels": uncertain,
        "baseline_key_memory_recall": rate(old_useful, useful),
        "jev_key_memory_recall": rate(jev_useful, useful),
        "baseline_precision": rate(old_useful, old_useful + old_irrelevant),
        "jev_precision": rate(jev_useful, jev_useful + jev_irrelevant),
        "baseline_irrelevant_retention": rate(old_irrelevant, irrelevant),
        "jev_irrelevant_retention": rate(jev_irrelevant, irrelevant),
        "candidate_reduction": 1 - rate(jev_kept, old_kept) if old_kept else None,
        "false_drops": sum(item["kind"] == "key_memory_false_drop" for item in failures),
        "false_keeps": sum(item["kind"] == "irrelevant_false_keep" for item in failures),
        "fallback_rate": rate(sum(bool(row.get("fallback_used")) for row in rows), len(rows)),
        **latency(rows),
    }
    return metrics, failures
