"""Frozen-query, no-Subgraph V4 MainGraph decision benchmark."""

from __future__ import annotations

import argparse
import asyncio
import contextvars
import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from time import perf_counter
from typing import Any

from app.agent.decision.jev.client import JevClient
from app.agent.decision.jev.router_v4 import JevRouterCandidate
from app.agent.foundation.hybrid_routing import create_hybrid_router
from app.agent.foundation.llm_routing import runtime as llm_runtime
from app.agent.foundation.routing import RouteDecision, RoutingContext
from app.config.settings import get_settings

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "evals/datasets/router_e2e_blind_v4.jsonl"
RESULTS = ROOT / "evals/reports/jev_router_v4_e2e_results.jsonl"
METRICS = ROOT / "evals/reports/jev_router_v4_e2e_metrics.json"
FAILURES = ROOT / "evals/reports/jev_router_v4_failure_cases.jsonl"
FIRST_PASS_RESULTS = ROOT / "evals/reports/jev_router_v4_e2e_first_pass_results.jsonl"
SINGLE_GUARD = ROOT / "evals/datasets/single_blind_v3.jsonl"
SINGLE_GUARD_RESULTS = ROOT / "evals/reports/jev_router_v4_single_guard_results.jsonl"
SINGLE_GUARD_METRICS = ROOT / "evals/reports/jev_router_v4_single_guard_metrics.json"
DATASET_SHA256 = "216197f910ef0efc7acc44e790744ee0770bbac20f9d9f2e1420f9780a4fdf7c"
SINGLE_GUARD_SHA256 = "8ca3cf3aca26530a3b8bfdcc9b83b0828bede2ba5b7916eb6c6f5caaeaf0118e"
ROUTES = ("current_song", "tool_calling", "worldline", "general_chat")
_active: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar("v4_measure", default=None)


def read_cases() -> list[dict[str, Any]]:
    if hashlib.sha256(DATASET.read_bytes()).hexdigest() != DATASET_SHA256:
        raise RuntimeError("V4 E2E blind dataset changed after freeze")
    return [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
                            for row in rows), encoding="utf-8")


def decision_view(decision: RouteDecision) -> dict[str, Any]:
    ids = {task.task_id: index for index, task in enumerate(decision.tasks)}
    return {
        "routes": sorted({task.route_key.value for task in decision.tasks}),
        "tasks": [{"route": task.route_key.value, "fragment": task.input_text,
                   "depends_on": [ids[dependency] for dependency in task.depends_on]}
                  for task in decision.tasks],
        "clarify": decision.needs_clarification,
        "is_complete": decision.is_complete,
        "main_graph_schema_valid": True,
    }


class HybridUsage:
    """Count actual baseline/fallback provider calls without logging request content."""

    def __init__(self, router) -> None:
        self.router = router
        self.original_llm = llm_runtime.create_routing_response
        self.original_model = router._model.route

        async def counted_llm(*args, **kwargs):
            current = _active.get()
            if current is not None:
                current["hybrid_api_calls"] += 1
            result = await self.original_llm(*args, **kwargs)
            if current is not None and result.usage:
                current["hybrid_input_tokens"] += int(result.usage.input_tokens or 0)
                current["hybrid_output_tokens"] += int(result.usage.output_tokens or 0)
            return result

        def counted_model(text):
            current = _active.get()
            if current is not None:
                current["local_model_calls"] += 1
            return self.original_model(text)

        llm_runtime.create_routing_response = counted_llm
        router._model.route = counted_model

    def close(self) -> None:
        llm_runtime.create_routing_response = self.original_llm
        self.router._model.route = self.original_model


async def measure(operation) -> tuple[Any, dict[str, Any]]:
    counts = {"hybrid_api_calls": 0, "hybrid_input_tokens": 0,
              "hybrid_output_tokens": 0, "local_model_calls": 0}
    token = _active.set(counts)
    started = perf_counter()
    try:
        result = await operation()
        return result, {**counts, "latency_ms": round((perf_counter() - started) * 1000, 2),
                        "error_type": None}
    except Exception as exc:
        return None, {**counts, "latency_ms": round((perf_counter() - started) * 1000, 2),
                      "error_type": type(exc).__name__}
    finally:
        _active.reset(token)


def normalized_record(result, operation: dict[str, Any], *, jev: bool) -> dict[str, Any]:
    trace = result if jev else None
    decision = trace.decision if jev and trace else result
    view = decision_view(RouteDecision.model_validate(decision.model_dump())) if decision else {
        "routes": [], "tasks": [], "clarify": False, "is_complete": False,
        "main_graph_schema_valid": False,
    }
    jev_usage = trace.usage if trace else {}
    usage = {
        "input_tokens": operation["hybrid_input_tokens"] + jev_usage.get("input_tokens", 0),
        "output_tokens": operation["hybrid_output_tokens"] + jev_usage.get("output_tokens", 0),
    }
    return {
        **view,
        "router_source": trace.router_source if trace else "hybrid_control",
        "fallback_reason": trace.fallback_reason if trace else None,
        "latency_ms": operation["latency_ms"],
        "api_calls": operation["hybrid_api_calls"] + (trace.api_calls if trace else 0),
        "local_model_calls": operation["local_model_calls"],
        "usage": usage,
        "response_model": trace.response_model if trace else None,
        "scores": trace.scores if trace else None,
        "confidence": trace.confidence if trace else None,
        "error_type": operation["error_type"],
    }


def percentile(values: list[float], percentile_value: float) -> float:
    sorted_values = sorted(values)
    position = (len(sorted_values) - 1) * percentile_value
    low = int(position)
    high = min(low + 1, len(sorted_values) - 1)
    return round(sorted_values[low] + (sorted_values[high] - sorted_values[low]) * (position - low), 2)


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def summarize(rows: list[dict[str, Any]], system: str) -> dict[str, Any]:
    route_counts = {route: Counter(tp=0, fp=0, fn=0) for route in ROUTES}
    exact = over = under = task_count = task_route = fragment = dependency = ordering = full = 0
    task_denominator = clarification_tp = clarification_fp = clarification_fn = 0
    reliability = Counter()
    outputs = []
    for row in rows:
        gold, output = row["gold"], row[system]
        outputs.append(output)
        expected, actual = set(gold["expected_routes"]), set(output["routes"])
        exact += expected == actual
        over += bool(actual - expected)
        under += bool(expected - actual)
        for route in ROUTES:
            route_counts[route]["tp"] += route in expected and route in actual
            route_counts[route]["fp"] += route not in expected and route in actual
            route_counts[route]["fn"] += route in expected and route not in actual
        gold_tasks, tasks = gold["expected_tasks"], output["tasks"]
        task_count += len(gold_tasks) == len(tasks)
        ordering += [task["route"] for task in gold_tasks] == [task["route"] for task in tasks]
        task_denominator += len(gold_tasks)
        for expected_task, task in zip(gold_tasks, tasks):
            task_route += expected_task["route"] == task["route"]
            fragment += expected_task["fragment"] == task["fragment"]
            dependency += expected_task["depends_on"] == task["depends_on"]
        gold_clarify, predicted_clarify = gold["expected_clarify"], output["clarify"]
        clarification_tp += gold_clarify and predicted_clarify
        clarification_fp += not gold_clarify and predicted_clarify
        clarification_fn += gold_clarify and not predicted_clarify
        full += (gold_clarify == predicted_clarify and gold_tasks == tasks
                 and expected == actual and output["main_graph_schema_valid"])
        reason = output["fallback_reason"]
        if reason:
            reliability["fallback"] += 1
            if "timeout" in reason.lower():
                reliability["timeout"] += 1
            elif reason in {"ValueError", "ValidationError", "KeyError", "TypeError",
                            "AttributeError", "invalid_choice", "invalid_noul",
                            "invalid_fragment_choice"}:
                reliability["parse_schema_failure"] += 1
            elif reason == "route_fragment_conflict":
                reliability["task_construction_failure"] += 1
            else:
                reliability["api_failure"] += 1
        if output["error_type"]:
            reliability["router_exception"] += 1
    total_tp = sum(counts["tp"] for counts in route_counts.values())
    total_fp = sum(counts["fp"] for counts in route_counts.values())
    total_fn = sum(counts["fn"] for counts in route_counts.values())
    per_route = {}
    for route, counts in route_counts.items():
        precision = _ratio(counts["tp"], counts["tp"] + counts["fp"])
        recall = _ratio(counts["tp"], counts["tp"] + counts["fn"])
        per_route[route] = {"support": counts["tp"] + counts["fn"],
                            "precision": precision, "recall": recall,
                            "f1": _ratio(2 * precision * recall, precision + recall)}
    micro_p = _ratio(total_tp, total_tp + total_fp)
    micro_r = _ratio(total_tp, total_tp + total_fn)
    latencies = [output["latency_ms"] for output in outputs]
    return {
        "cases": len(rows),
        "routing": {"exact": _ratio(exact, len(rows)), "exact_count": exact,
                    "micro_precision": micro_p, "micro_recall": micro_r,
                    "micro_f1": _ratio(2 * micro_p * micro_r, micro_p + micro_r),
                    "macro_f1": round(mean(item["f1"] for item in per_route.values()), 4),
                    "over_count": over, "under_count": under, "per_route": per_route},
        "tasks": {"full_decision_exact": _ratio(full, len(rows)), "full_decision_exact_count": full,
                  "task_count_accuracy": _ratio(task_count, len(rows)),
                  "task_route_accuracy": _ratio(task_route, task_denominator),
                  "fragment_accuracy": _ratio(fragment, task_denominator),
                  "dependency_accuracy": _ratio(dependency, task_denominator),
                  "ordering_accuracy": _ratio(ordering, len(rows)),
                  "clarify_precision": _ratio(clarification_tp, clarification_tp + clarification_fp),
                  "clarify_recall": _ratio(clarification_tp, clarification_tp + clarification_fn)},
        "reliability": {**reliability, "fallback_rate": _ratio(reliability["fallback"], len(rows))},
        "performance": {"average_ms": round(mean(latencies), 2),
                        "p50_ms": percentile(latencies, .5),
                        "p95_ms": percentile(latencies, .95),
                        "p99_ms": percentile(latencies, .99),
                        "api_calls_total": sum(output["api_calls"] for output in outputs),
                        "api_calls_per_query": _ratio(sum(output["api_calls"] for output in outputs), len(rows)),
                        "input_tokens": sum(output["usage"]["input_tokens"] for output in outputs),
                        "output_tokens": sum(output["usage"]["output_tokens"] for output in outputs)},
    }


async def run() -> None:
    cases = read_cases()
    settings = get_settings()
    if (not settings.jev_shadow_mode or settings.jev_model != "jev-latest"
            or settings.jev_timeout_seconds != 5 or not settings.typesafe_api_key):
        raise RuntimeError("V4 benchmark requires frozen Jev alias/config, API key and Shadow Mode")
    router = create_hybrid_router(device="cpu")
    recorder = HybridUsage(router)
    candidate = JevRouterCandidate(JevClient(settings), fallback_router=router)
    rows: list[dict[str, Any]] = []
    try:
        for index, case in enumerate(cases):
            context = RoutingContext.model_validate(case["context"])
            async def baseline_operation():
                return await router.route(case["query"], context)

            async def candidate_operation():
                return await candidate.route_with_trace(case["query"], context)
            if index % 2:
                candidate_result, candidate_ops = await measure(candidate_operation)
                baseline_result, baseline_ops = await measure(baseline_operation)
            else:
                baseline_result, baseline_ops = await measure(baseline_operation)
                candidate_result, candidate_ops = await measure(candidate_operation)
            row = {"case_id": case["case_id"], "query": case["query"],
                   "gold": {key: case[key] for key in ("expected_routes", "expected_tasks",
                                                            "expected_clarify", "facet")},
                   "hybrid": normalized_record(baseline_result, baseline_ops, jev=False),
                   "jev": normalized_record(candidate_result, candidate_ops, jev=True)}
            rows.append(row)
            write_jsonl(RESULTS, rows)
            print(f"{index + 1}/{len(cases)} {case['case_id']} hybrid={row['hybrid']['routes']} "
                  f"jev={row['jev']['routes']} source={row['jev']['router_source']}", flush=True)
    finally:
        recorder.close()
    summarize_results(rows)


def summarize_results(rows: list[dict[str, Any]]) -> None:
    if len(rows) != len(read_cases()):
        raise RuntimeError("V4 results do not cover frozen dataset")
    metrics = {"dataset_sha256": DATASET_SHA256,
               "evaluated_at": datetime.now(UTC).isoformat(),
               "hybrid": summarize(rows, "hybrid"), "jev": summarize(rows, "jev")}
    METRICS.write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    failures = failure_rows(rows, "post_fix_repeat")
    if FIRST_PASS_RESULTS.exists():
        first = [json.loads(line) for line in FIRST_PASS_RESULTS.read_text(encoding="utf-8").splitlines()]
        if len(first) != len(rows):
            raise RuntimeError("first-pass V4 results are incomplete")
        failures = failure_rows(first, "first_blind") + failures
    write_jsonl(FAILURES, failures)


def failure_rows(rows: list[dict[str, Any]], phase: str) -> list[dict[str, Any]]:
    failures = []
    for row in rows:
        gold, candidate = row["gold"], row["jev"]
        route_error = set(gold["expected_routes"]) != set(candidate["routes"])
        task_error = gold["expected_tasks"] != candidate["tasks"]
        clarify_error = gold["expected_clarify"] != candidate["clarify"]
        if route_error or task_error or clarify_error:
            failures.append({
                "phase": phase, "case_id": row["case_id"], "query": row["query"],
                "facet": gold["facet"], "expected_routes": gold["expected_routes"],
                "actual_routes": candidate["routes"],
                "expected_tasks": gold["expected_tasks"], "actual_tasks": candidate["tasks"],
                "expected_clarify": gold["expected_clarify"],
                "actual_clarify": candidate["clarify"], "route_error": route_error,
                "task_error": task_error, "clarify_error": clarify_error,
                "router_source": candidate["router_source"],
                "fallback_reason": candidate["fallback_reason"],
                "latency_ms": candidate["latency_ms"],
            })
    return failures


async def run_single_guard() -> None:
    """Regression only: never feed these historic blind results into V4 tuning."""
    if hashlib.sha256(SINGLE_GUARD.read_bytes()).hexdigest() != SINGLE_GUARD_SHA256:
        raise RuntimeError("V3 Single blind changed after freeze")
    settings = get_settings()
    if not settings.jev_shadow_mode or not settings.typesafe_api_key:
        raise RuntimeError("single regression requires Jev key and Shadow Mode")
    cases = [json.loads(line) for line in SINGLE_GUARD.read_text(encoding="utf-8").splitlines()]
    candidate = JevRouterCandidate(JevClient(settings))
    rows = []
    for index, case in enumerate(cases, 1):
        context = RoutingContext(has_current_song=True, current_song_id=101)
        result, operation = await measure(lambda: candidate.route_with_trace(case["query"], context))
        output = normalized_record(result, operation, jev=True)
        rows.append({"case_id": case["case_id"], "query": case["query"],
                     "gold_routes": case["gold_routes"], "candidate": output})
        write_jsonl(SINGLE_GUARD_RESULTS, rows)
        print(f"single {index}/{len(cases)} {case['case_id']} {output['routes']}", flush=True)
    correct = sum(row["gold_routes"] == row["candidate"]["routes"] for row in rows)
    SINGLE_GUARD_METRICS.write_text(json.dumps({
        "dataset_sha256": SINGLE_GUARD_SHA256,
        "purpose": "untuned regression of frozen Single Choice through V4 task path",
        "cases": len(rows), "route_exact_count": correct,
        "route_exact": _ratio(correct, len(rows)),
        "clarify_count": sum(row["candidate"]["clarify"] for row in rows),
        "fallback_count": sum(row["candidate"]["router_source"] == "hybrid_fallback" for row in rows),
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("run", "summarize", "single-regression"))
    args = parser.parse_args()
    if args.command == "run":
        asyncio.run(run())
    elif args.command == "summarize":
        rows = [json.loads(line) for line in RESULTS.read_text(encoding="utf-8").splitlines()]
        summarize_results(rows)
    else:
        asyncio.run(run_single_guard())


if __name__ == "__main__":
    main()
