"""Frozen production-Choice Jev router replay; no prompt or threshold changes."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from time import perf_counter
from typing import Any

from app.agent.decision.jev.client import DecisionResponse, JevClient
from app.agent.decision.jev.router import compare_route
from app.agent.foundation.hybrid_routing import create_hybrid_router
from app.agent.foundation.routing import RouteDecision
from app.config.settings import get_settings
from evals.jev_shadow_v1 import read_jsonl, write_jsonl

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "evals/datasets/router_eval_v2.jsonl"
DATASET = ROOT / "evals/datasets/jev_router_baseline_v1.jsonl"
RESULTS = ROOT / "evals/reports/jev_router_baseline_v1_results.jsonl"
METRICS = ROOT / "evals/reports/jev_router_baseline_v1_metrics.json"
FAILURES = ROOT / "evals/reports/jev_router_failure_cases_v1.jsonl"
ROUTES = ("current_song", "tool_calling", "general_chat", "worldline")

# The historical labels are copied without alteration. These IDs are review
# candidates because the current Worldline graph does not simulate alternate history.
QUESTIONABLE = {
    "manual-hard-019", "manual-hard-020", "manual-hard-021", "manual-hard-027",
    "manual-test-worldline-001", "manual-world-002", "manual-world-007",
    "manual-world-017", "manual-world-022", "manual-multi-009",
    "manual-multi-010", "manual-multi-011", "manual-multi-014",
    "manual-multi-020", "manual-multi-025",
}

# Proposed cases have no approved Gold label and never enter the formal metrics.
CANDIDATES = [
    ("cap-current-01", "当前播放的歌的歌词主题是什么？", ["current_song"], "single_intent", ["boundary", "current_vs_general"]),
    ("cap-general-01", "什么是和声？", ["general_chat"], "single_intent", ["boundary", "general_vs_worldline"]),
    ("cap-tool-01", "搜索林俊杰的歌曲", ["tool_calling"], "single_intent", ["boundary", "search_vs_exploration"]),
    ("cap-world-01", "构建林俊杰的音乐世界线并展示有来源的关系", ["worldline"], "single_intent", ["boundary", "search_vs_exploration"]),
    ("cap-tool-02", "把正在播放的歌收藏", ["tool_calling"], "single_intent", ["boundary", "information_vs_action"]),
    ("cap-current-02", "解释正在播放的歌为什么伤感", ["current_song"], "single_intent", ["boundary", "information_vs_action"]),
    ("cap-multi-01", "搜索林俊杰的歌，然后聊聊为什么旋律让人放松", ["tool_calling", "general_chat"], "multi_intent", ["boundary"]),
    ("cap-multi-02", "探索林俊杰的音乐世界线，再分析当前这首歌的歌词", ["worldline", "current_song"], "multi_intent", ["boundary"]),
    ("cap-ambiguous-01", "讲讲这首歌的背景", [], "boundary", ["ambiguous_entity", "missing_context"]),
    ("cap-missing-01", "分析当前这首歌", ["current_song"], "boundary", ["missing_context"]),
    ("cap-negative-01", "如果这首歌从未发行，真实音乐史会怎样变化？", [], "negative", ["unsupported_counterfactual"]),
    ("cap-negative-02", "帮我预订明天的演唱会门票", [], "negative", ["unsupported_action"]),
]


def prepare() -> Path:
    rows = []
    for row in read_jsonl(SOURCE):
        rows.append({**row, "label_status": "questionable" if row["case_id"] in QUESTIONABLE else "historical_gold",
                     "questionable_reason": "Worldline counterfactual simulation is not implemented" if row["case_id"] in QUESTIONABLE else None,
                     "source_dataset": str(SOURCE.relative_to(ROOT))})
    rows.extend({"case_id": case_id, "query": query, "recent_context": [],
                 "gold_routes": proposed, "task": task, "label_source": "capability_contract_v1_proposal",
                 "label_status": "candidate", "tags": tags,
                 "questionable_reason": None, "source_dataset": "capability_contract_v1"}
                for case_id, query, proposed, task, tags in CANDIDATES)
    if len({row["case_id"] for row in rows}) != len(rows):
        raise ValueError("duplicate case_id")
    write_jsonl(DATASET, rows)
    return DATASET


class Recorder:
    def __init__(self, delegate: JevClient):
        self.delegate = delegate
        self.responses: list[DecisionResponse] = []
        self.calls = 0
        self.error: str | None = None

    async def decide(self, state: Any, questions: Any) -> DecisionResponse:
        self.calls += 1
        try:
            response = await self.delegate.decide(state, questions)
        except Exception as exc:
            self.error = str(exc) if type(exc).__name__ == "JevDecisionError" else type(exc).__name__
            raise
        self.responses.append(response)
        return response


def _percentile(values: list[float], percentage: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentage
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return round(ordered[low] + (ordered[high] - ordered[low]) * (position - low), 2)


def timing(rows: list[dict[str, Any]], field: str) -> dict[str, float | None]:
    values = [float(row[field]) for row in rows if row.get(field) is not None]
    return {"count": len(values), "average": round(mean(values), 2) if values else None,
            "p50": _percentile(values, .50), "p95": _percentile(values, .95),
            "p99": _percentile(values, .99)}


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def single_metrics(rows: list[dict[str, Any]], field: str) -> dict[str, Any]:
    confusion = {route: {candidate: 0 for candidate in (*ROUTES, "none", "multiple")}
                 for route in ROUTES}
    for row in rows:
        predicted = row[field]
        value = predicted[0] if len(predicted) == 1 else "none" if not predicted else "multiple"
        confusion[row["gold_routes"][0]][value] += 1
    per_route = {}
    for route in ROUTES:
        tp = confusion[route][route]
        fp = sum(confusion[other][route] for other in ROUTES if other != route)
        fn = sum(value for key, value in confusion[route].items() if key != route)
        p, r = _rate(tp, tp + fp), _rate(tp, tp + fn)
        per_route[route] = {"support": tp + fn, "precision": p, "recall": r,
                            "f1": round(2 * p * r / (p + r), 4) if p is not None and r is not None and p + r else 0.0}
    correct = sum(row[field] == row["gold_routes"] for row in rows)
    return {"count": len(rows), "correct": correct, "accuracy": _rate(correct, len(rows)),
            "macro_f1": round(mean(item["f1"] for item in per_route.values()), 4),
            "per_route": per_route, "confusion_matrix": confusion}


def multi_metrics(rows: list[dict[str, Any]], field: str) -> dict[str, Any]:
    tp = fp = fn = exact = over = under = 0
    route_stats = {route: {"tp": 0, "fp": 0, "fn": 0} for route in ROUTES}
    for row in rows:
        gold, predicted = set(row["gold_routes"]), set(row[field])
        exact += gold == predicted
        over += bool(predicted - gold)
        under += bool(gold - predicted)
        tp += len(gold & predicted)
        fp += len(predicted - gold)
        fn += len(gold - predicted)
        for route in ROUTES:
            route_stats[route]["tp"] += route in gold and route in predicted
            route_stats[route]["fp"] += route not in gold and route in predicted
            route_stats[route]["fn"] += route in gold and route not in predicted
    precision, recall = _rate(tp, tp + fp), _rate(tp, tp + fn)
    per_route_f1 = {}
    for route, counts in route_stats.items():
        p = _rate(counts["tp"], counts["tp"] + counts["fp"])
        r = _rate(counts["tp"], counts["tp"] + counts["fn"])
        per_route_f1[route] = round(2 * p * r / (p + r), 4) if p is not None and r is not None and p + r else 0.0
    return {"count": len(rows), "exact_match": _rate(exact, len(rows)), "exact_count": exact,
            "micro_precision": precision, "micro_recall": recall,
            "micro_f1": round(2 * precision * recall / (precision + recall), 4) if precision is not None and recall is not None and precision + recall else 0.0,
            "macro_f1": round(mean(per_route_f1.values()), 4), "per_route_f1": per_route_f1,
            "over_routing_rate": _rate(over, len(rows)), "under_routing_rate": _rate(under, len(rows)),
            "tp": tp, "fp": fp, "fn": fn}


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    formal = [row for row in rows if row["label_status"] != "candidate"]
    single = [row for row in formal if row["task"] == "single_intent"]
    multi = [row for row in formal if row["task"] == "multi_intent"]
    boundary = [row for row in formal if "boundary" in row["tags"]]
    evaluated = single + multi
    return {
        "formal_label_policy": "historical labels unchanged, including questionable labels; candidates excluded",
        "single_intent": {"jev_choice": single_metrics(single, "jev_routes"),
                          "main_graph_selected": single_metrics(single, "selected_routes")},
        "multi_intent": {"main_graph_selected": multi_metrics(multi, "selected_routes"),
                         "jev_choice_diagnostic_only": multi_metrics(multi, "jev_routes")},
        "boundary_cases": {"count": len(boundary),
                           "single_jev_choice": single_metrics([r for r in boundary if r["task"] == "single_intent"], "jev_routes"),
                           "single_main_graph_selected": single_metrics([r for r in boundary if r["task"] == "single_intent"], "selected_routes"),
                           "multi_main_graph_selected": multi_metrics([r for r in boundary if r["task"] == "multi_intent"], "selected_routes")},
        "candidate_cases": {"count": sum(row["label_status"] == "candidate" for row in rows),
                            "forced_route_count": sum(bool(row["jev_routes"]) for row in rows if row["label_status"] == "candidate" and row["task"] == "negative")},
        "failures": {"parse_schema_rate": _rate(sum((r["jev_error"] or "") in {"ValueError", "ValidationError", "KeyError", "TypeError", "AttributeError"} for r in evaluated), len(evaluated)),
                     "api_failure_rate": _rate(sum(bool(r["jev_error"]) and (r["jev_error"] or "") not in {"ValueError", "ValidationError", "KeyError", "TypeError", "AttributeError"} for r in evaluated), len(evaluated)),
                     "fallback_rate": _rate(sum(bool(r["fallback_reason"]) for r in evaluated), len(evaluated)),
                     "fallback_reasons": dict(Counter(r["fallback_reason"] for r in evaluated if r["fallback_reason"])),
                     "retry_rate": _rate(sum(r["jev_calls"] > 1 for r in evaluated), len(evaluated)),
                     "timeout_rate": _rate(sum("timeout" in (r["jev_error"] or "").lower() for r in evaluated), len(evaluated))},
        "latency_ms": {"jev_api": timing(evaluated, "jev_api_latency_ms"),
                       "jev_wall": timing(evaluated, "jev_wall_latency_ms"),
                       "hybrid_router": timing(evaluated, "hybrid_latency_ms")},
        "usage_total_formal": dict(sum((Counter(r.get("usage") or {}) for r in evaluated), Counter())),
        "usage_total_all_calls": dict(sum((Counter(r.get("usage") or {}) for r in rows), Counter())),
        "response_models": dict(Counter(r["response_model"] for r in rows if r["response_model"])),
        "questionable_cases": [r["case_id"] for r in formal if r["label_status"] == "questionable"],
    }


def failure_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for row in rows:
        if row["label_status"] == "candidate":
            continue
        if (set(row["gold_routes"]) == set(row["selected_routes"])
                and set(row["gold_routes"]) == set(row["jev_routes"])
                and not row["jev_error"]):
            continue
        categories = []
        if row["label_status"] == "questionable":
            categories += ["label_ambiguity", "capability_mismatch"]
        if row["jev_error"]:
            categories += ["schema_failure" if "invalid" in row["jev_error"].lower() or "value" in row["jev_error"].lower() else "api_failure"]
        if row["fallback_reason"] == "low_confidence":
            categories.append("threshold_issue")
        if row["task"] == "multi_intent":
            categories.append("choice_strategy")
        elif set(row["gold_routes"]) != set(row["jev_routes"]):
            categories.append("model_reasoning_or_prompt_ambiguity")
        if set(row["gold_routes"]) != set(row["baseline_routes"]):
            categories.append("hybrid_router_error")
        output.append({"case_id": row["case_id"], "query": row["query"],
                       "gold_routes": row["gold_routes"], "jev_raw_structured_output": row["jev_structured_output"],
                       "structured_output_level": "validated DecisionClient response; SDK wire payload unavailable",
                       "jev_routes": row["jev_routes"], "selected_routes": row["selected_routes"],
                       "baseline_routes": row["baseline_routes"], "confidence": row["confidence"],
                       "score": row["probabilities"], "error_category": categories,
                       "capability_mismatch": row["questionable_reason"],
                       "prompt_ambiguity": "Candidate overlap in current Choice criteria; causal attribution not proven" if "model_reasoning_or_prompt_ambiguity" in categories else None,
                       "label_ambiguity": row["label_status"] == "questionable",
                       "schema_failure": "schema_failure" in categories,
                       "threshold_issue": "threshold_issue" in categories,
                       "model_reasoning_issue": "model_reasoning_or_prompt_ambiguity" in categories,
                       "downstream_capability_issue": row["label_status"] == "questionable",
                       "fallback_reason": row["fallback_reason"], "jev_error": row["jev_error"]})
    return output


async def run() -> Path:
    settings = get_settings()
    if not settings.jev_shadow_mode or settings.jev_model != "jev-latest":
        raise RuntimeError("Frozen replay requires JEV_SHADOW_MODE=true and JEV_MODEL=jev-latest")
    if not settings.typesafe_api_key:
        raise RuntimeError("missing_api_key")
    cases = read_jsonl(DATASET)
    router = create_hybrid_router(device="cpu")
    client = JevClient(settings)
    output = []
    source_hash = hashlib.sha256(DATASET.read_bytes()).hexdigest()
    for index, case in enumerate(cases, 1):
        baseline = RouteDecision()
        baseline_error = None
        start = perf_counter()
        try:
            baseline = await router.route(case["query"])
        except Exception as exc:
            baseline_error = type(exc).__name__
        hybrid_ms = round((perf_counter() - start) * 1000, 2)
        recorder = Recorder(client)
        start = perf_counter()
        selected = await compare_route(case["query"], baseline, settings=settings,
                                       client=recorder, trace_id=case["case_id"])
        wall_ms = round((perf_counter() - start) * 1000, 2)
        response = recorder.responses[-1] if recorder.responses else None
        answer = response.answers["route"] if response else None
        baseline_routes = sorted({task.route_key.value for task in baseline.tasks})
        selected_routes = sorted({task.route_key.value for task in selected.tasks})
        jev_routes = [answer.choice] if answer and answer.choice else []
        complex_route = not baseline.is_complete or len(baseline.tasks) != 1 or baseline.tasks[0].input_text.strip() != case["query"].strip() or bool(baseline.tasks[0].depends_on)
        fallback_reason = ("complex_existing_route" if complex_route else
                           "jev_error" if response is None else
                           "low_confidence" if answer.confidence is None or answer.confidence < settings.jev_router_confidence_threshold else
                           "shadow_mode")
        output.append({**case, "dataset_sha256": source_hash, "evaluated_at": datetime.now(UTC).isoformat(),
                       "requested_model": settings.jev_model, "response_model": response.response_model if response else None,
                       "router_confidence_threshold": settings.jev_router_confidence_threshold,
                       "shadow_mode": settings.jev_shadow_mode,
                       "baseline_routes": baseline_routes, "baseline_complete": baseline.is_complete,
                       "baseline_error": baseline_error, "selected_routes": selected_routes,
                       "jev_routes": jev_routes, "confidence": answer.confidence if answer else None,
                       "probabilities": answer.probabilities if answer else None,
                       "jev_structured_output": {"route": asdict(answer)} if answer else None,
                       "jev_error": recorder.error, "jev_calls": recorder.calls,
                       "fallback_reason": fallback_reason,
                       "usage": response.usage if response else None,
                       "jev_api_latency_ms": response.latency_ms if response else None,
                       "jev_wall_latency_ms": wall_ms, "hybrid_latency_ms": hybrid_ms})
        write_jsonl(RESULTS, output)
        print(f"{index}/{len(cases)} {case['case_id']} jev={jev_routes} selected={selected_routes}", flush=True)
    METRICS.write_text(json.dumps(summarize(output), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_jsonl(FAILURES, failure_rows(output))
    return RESULTS


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "run", "summarize"))
    args = parser.parse_args()
    if args.command == "prepare":
        print(prepare())
    elif args.command == "run":
        print(asyncio.run(run()))
    else:
        rows = read_jsonl(RESULTS)
        METRICS.write_text(json.dumps(summarize(rows), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        write_jsonl(FAILURES, failure_rows(rows))
        print(METRICS)


if __name__ == "__main__":
    main()
