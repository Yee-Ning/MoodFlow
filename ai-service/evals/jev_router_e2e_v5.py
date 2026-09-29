"""Frozen V5 offline Router comparison; no candidate Subgraph dispatch."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean

from app.agent.decision.jev.client import JevClient
from app.agent.decision.jev.router_v5 import JevRouterV5
from app.agent.foundation.hybrid_routing import create_hybrid_router
from app.agent.foundation.routing import RoutingContext
from app.config.settings import get_settings
from evals.jev_router_e2e_v4 import (
    HybridUsage, measure, normalized_record, percentile, summarize, write_jsonl,
)

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "evals/datasets/router_e2e_blind_v5.jsonl"
RESULTS = ROOT / "evals/reports/jev_router_v5_e2e_results.jsonl"
METRICS = ROOT / "evals/reports/jev_router_v5_e2e_metrics.json"
FAILURES = ROOT / "evals/reports/jev_router_v5_failure_cases.jsonl"
DATASET_SHA256 = "24d190d630614a611f19ac2ea1b7fc61cbf529b4222dfe826ec1b2b64f5e6daf"


def read_cases():
    if hashlib.sha256(DATASET.read_bytes()).hexdigest() != DATASET_SHA256:
        raise RuntimeError("V5 Gold changed after freeze")
    return [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines()]


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _latencies(outputs):
    if not outputs:
        return {"count": 0, "average_ms": None, "p50_ms": None,
                "p95_ms": None, "p99_ms": None}
    times = [item["latency_ms"] for item in outputs]
    return {"count": len(times), "average_ms": round(mean(times), 2),
            "p50_ms": percentile(times, .5), "p95_ms": percentile(times, .95),
            "p99_ms": percentile(times, .99)}


def v5_metrics(rows, system):
    base = summarize(rows, system)
    outputs = [row[system] for row in rows]
    same = [row for row in rows if row["gold"]["facet"].startswith("same_route")
            or row["gold"]["facet"] in {"three_tool_actions", "three_tool_result_chain",
                                          "no_connector_same_route"}]
    correct_same = sum(row["gold"]["expected_tasks"] == row[system]["tasks"] for row in same)
    base["tasks"]["same_route_multi_exact"] = _ratio(correct_same, len(same))
    base["tasks"]["same_route_multi_count"] = len(same)
    base["reliability"] = {
        "fallback_count": sum(out["router_source"] == "legacy_hybrid_fallback" for out in outputs),
        "fallback_rate": _ratio(sum(out["router_source"] == "legacy_hybrid_fallback"
                                    for out in outputs), len(outputs)),
        "timeout_count": sum(out["fallback_reason"] == "timeout" for out in outputs),
        "api_failure_count": sum(out["fallback_reason"] == "api_failure" for out in outputs),
        "schema_failure_count": sum(out["fallback_reason"] == "schema_failure" for out in outputs),
        "router_exception_count": sum(out["error_type"] is not None for out in outputs),
    }
    base["performance"]["tokens_per_query"] = round(
        (base["performance"]["input_tokens"] + base["performance"]["output_tokens"])
        / len(outputs), 2,
    )
    base["performance"]["normal_jev"] = _latencies([
        out for out in outputs if out["router_source"] in {"jev_single", "jev_multi"}
    ])
    base["performance"]["fallback"] = _latencies([
        out for out in outputs if out["router_source"] == "legacy_hybrid_fallback"
    ])
    base["performance"]["classifier_hot_path_calls"] = sum(
        out.get("classifier_hot_path_calls", 0) for out in outputs
    )
    base["performance"]["classifier_fallback_calls"] = sum(
        out.get("classifier_fallback_calls", 0) for out in outputs
    )
    return base


def _output(result, operation, *, jev):
    output = normalized_record(result, operation, jev=jev)
    output["classifier_hot_path_calls"] = result.classifier_hot_path_calls if jev and result else (
        operation["local_model_calls"] if not jev else 0
    )
    output["classifier_fallback_calls"] = result.classifier_fallback_calls if jev and result else 0
    if jev and result:
        output["jev_latency_ms"] = result.jev_latency_ms
        output["orchestration_latency_ms"] = result.orchestration_latency_ms
        output["fallback_latency_ms"] = result.fallback_latency_ms
    return output


def summarize_results(rows):
    if len(rows) != len(read_cases()):
        raise RuntimeError("Incomplete V5 comparison")
    metrics = {"dataset_sha256": DATASET_SHA256,
               "evaluated_at": datetime.now(UTC).isoformat(),
               "hybrid": v5_metrics(rows, "hybrid"), "jev": v5_metrics(rows, "jev")}
    METRICS.write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    failures = []
    for row in rows:
        expected = row["gold"]
        actual = row["jev"]
        if (set(expected["expected_routes"]) != set(actual["routes"])
                or expected["expected_tasks"] != actual["tasks"]
                or expected["expected_clarify"] != actual["clarify"]):
            failures.append({"case_id": row["case_id"], "query": row["query"],
                             "facet": expected["facet"],
                             "expected_routes": expected["expected_routes"],
                             "actual_routes": actual["routes"],
                             "expected_tasks": expected["expected_tasks"],
                             "actual_tasks": actual["tasks"],
                             "expected_clarify": expected["expected_clarify"],
                             "actual_clarify": actual["clarify"],
                             "router_source": actual["router_source"],
                             "fallback_reason": actual["fallback_reason"],
                             "latency_ms": actual["latency_ms"]})
    write_jsonl(FAILURES, failures)
    return metrics


async def run():
    cases = read_cases()
    settings = get_settings()
    if (not settings.jev_shadow_mode or settings.jev_model != "jev-latest"
            or settings.jev_timeout_seconds != 5
            or settings.jev_router_confidence_threshold != .8
            or not settings.typesafe_api_key):
        raise RuntimeError("V5 benchmark requires frozen Jev settings and Shadow Mode")
    router = create_hybrid_router(device="cpu")
    recorder = HybridUsage(router)
    candidate = JevRouterV5(JevClient(settings), fallback_router=router)
    rows = []
    try:
        for index, case in enumerate(cases):
            context = RoutingContext.model_validate(case["context"])
            async def baseline():
                return await router.route(case["query"], context)

            async def contender():
                return await candidate.route_with_trace(case["query"], context)
            if index % 2:
                jev_result, jev_operation = await measure(contender)
                hybrid_result, hybrid_operation = await measure(baseline)
            else:
                hybrid_result, hybrid_operation = await measure(baseline)
                jev_result, jev_operation = await measure(contender)
            row = {"case_id": case["case_id"], "query": case["query"],
                   "gold": {key: case[key] for key in (
                       "expected_routes", "expected_tasks", "expected_clarify", "facet",
                       "unsupported_part",
                   )},
                   "hybrid": _output(hybrid_result, hybrid_operation, jev=False),
                   "jev": _output(jev_result, jev_operation, jev=True)}
            rows.append(row)
            write_jsonl(RESULTS, rows)
            print(f"{index + 1}/{len(cases)} {case['case_id']} "
                  f"source={row['jev']['router_source']} "
                  f"tasks={len(row['jev']['tasks'])}", flush=True)
    finally:
        recorder.close()
    summarize_results(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("run", "summarize"))
    args = parser.parse_args()
    if args.command == "run":
        asyncio.run(run())
    else:
        summarize_results([json.loads(line) for line in RESULTS.read_text(
            encoding="utf-8").splitlines()])


if __name__ == "__main__":
    main()
