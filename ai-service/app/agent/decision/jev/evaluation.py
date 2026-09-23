"""Small, dataset-independent Jev versus existing-baseline metric aggregator."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from statistics import mean
from typing import Any, Iterable


def _rate(values: list[bool]) -> float | None:
    return sum(values) / len(values) if values else None


def _average(values: list[float]) -> float | None:
    return mean(values) if values else None


def summarize_decisions(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(records)
    summary: dict[str, Any] = {
        "evaluation_timestamp": datetime.now(UTC).isoformat(),
        "requested_models": dict(Counter(str(row["requested_model"]) for row in rows if row.get("requested_model"))),
        "response_models": dict(Counter(str(row["response_model"]) for row in rows if row.get("response_model"))),
    }
    for kind in ("router", "memory_filter", "context_select"):
        group = [row for row in rows if row.get("decision_type") == kind]
        metrics: dict[str, Any] = {
            "count": len(group),
            "fallback_rate": _rate([bool(row.get("fallback_used")) for row in group]),
            "mean_latency_ms": _average([float(row["latency_ms"]) for row in group if row.get("latency_ms") is not None]),
            "mean_candidate_count": _average([float(row["candidate_count"]) for row in group if row.get("candidate_count") is not None]),
            "mean_selected_count": _average([float(row["selected_count"]) for row in group if row.get("selected_count") is not None]),
        }
        if kind == "router":
            metrics.update({
                "agreement_rate": _rate([bool(row["agreement"]) for row in group if row.get("agreement") is not None]),
                "route_accuracy": _rate([row["jev_route"] == row["gold_route"] for row in group
                                         if row.get("jev_route") and row.get("gold_route")]),
                "mean_confidence": _average([float(row["confidence"]) for row in group if row.get("confidence") is not None]),
            })
        elif kind == "memory_filter":
            key_recall = []
            irrelevant_reduction = []
            for row in group:
                selected = set(row.get("selected_ids") or [])
                keys = set(row.get("key_memory_ids") or [])
                irrelevant = set(row.get("irrelevant_memory_ids") or [])
                if keys:
                    key_recall.append(len(selected & keys) / len(keys))
                if irrelevant:
                    irrelevant_reduction.append(len(irrelevant - selected) / len(irrelevant))
            metrics.update({"key_memory_recall": _average(key_recall),
                            "irrelevant_memory_reduction": _average(irrelevant_reduction),
                            "fail_open_rate": _rate([bool(row.get("fallback_used")) and
                                                     row.get("applied_count") == row.get("candidate_count")
                                                     for row in group if row.get("fallback_used")])})
        else:
            useful_recall = []
            token_reduction = []
            for row in group:
                selected = set(row.get("selected_ids") or [])
                useful = set(row.get("useful_context_ids") or [])
                if useful:
                    useful_recall.append(len(selected & useful) / len(useful))
                if row.get("baseline_tokens") and row.get("final_tokens") is not None:
                    token_reduction.append(1 - float(row["final_tokens"]) / float(row["baseline_tokens"]))
            metrics.update({
                "required_preservation": _rate([bool(row["required_preserved"]) for row in group
                                                if row.get("required_preserved") is not None]),
                "useful_context_recall": _average(useful_recall),
                "optional_reduction": _average([1 - float(row["selected_count"]) / float(row["candidate_count"])
                                                for row in group if row.get("candidate_count")]),
                "final_token_reduction": _average(token_reduction),
                "downstream_compatibility": _rate([bool(row["downstream_compatible"]) for row in group
                                                   if row.get("downstream_compatible") is not None]),
            })
        summary[kind] = metrics
    return summary
