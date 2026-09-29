"""Evaluation-only Hybrid versus independent-route Jev V3 benchmark."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean, pstdev
from time import perf_counter
from typing import Any

from typesafe_sdk import Choice, Noul, Score

from app.agent.decision.jev.capability_criteria import (
    CONTRACT_PATH, ROUTER_QUESTION_INSTRUCTIONS, router_criteria,
)
from app.agent.decision.jev.client import JevClient
from app.agent.foundation.hybrid_routing import create_hybrid_router
from app.agent.foundation.llm_routing import runtime as llm_runtime
from app.config.settings import get_settings
from evals.jev_shadow_v1 import read_jsonl, write_jsonl

ROOT = Path(__file__).resolve().parents[1]
DATASETS = ROOT / "evals/datasets"
REPORTS = ROOT / "evals/reports"
DEV = DATASETS / "multi_dev_v3.jsonl"
BLIND = DATASETS / "multi_blind_test_v3.jsonl"
SINGLE = DATASETS / "single_blind_v3.jsonl"
DEV_RESULTS = REPORTS / "jev_multi_dev_v3_results.jsonl"
DEV_ABLATION = REPORTS / "jev_multi_strategy_ablation_v3.json"
FROZEN_CONFIG = ROOT / "evals/config/jev_multi_frozen_strategy_v3.json"
BLIND_RESULTS = REPORTS / "jev_multi_blind_v3_results.jsonl"
BLIND_METRICS = REPORTS / "jev_multi_head_to_head_v3_metrics.json"
FAILURES = REPORTS / "jev_multi_failure_cases_v3.jsonl"
SINGLE_RESULTS = REPORTS / "jev_single_blind_v3_results.jsonl"
SINGLE_METRICS = REPORTS / "jev_single_blind_v3_metrics.json"
HASHES = {
    "dev": "4d5eba1b34142593fde46997e355fdea62885f43b48de3988d7f81129174eb0c",
    "blind": "137e9e3c479b8f1d3a309fbf1e03142c79b55dc4933d34f55bcb600c78dedc94",
    "single": "8ca3cf3aca26530a3b8bfdcc9b83b0828bede2ba5b7916eb6c6f5caaeaf0118e",
    "contract": "238d373ff43db0eea72dae17525525276a666c3819f959de8925206b0915a97c",
}
ROUTES = ("current_song", "tool_calling", "worldline", "general_chat")
PARSE_ERRORS = {"ValueError", "ValidationError", "KeyError", "TypeError", "AttributeError"}
SCORE_LEVELS = (
    "No separate supported intent for this route. The route is absent or negated.",
    "This route is mentioned, hypothetical, underspecified, or ambiguous; a separate supported task is not established.",
)
NOUL_FALSE = "No distinct affirmative supported task for this route; mere mentions, negations and unresolved entities do not count."
HEAD_INSTRUCTIONS = "Does this request contain a distinct affirmative supported intent for {route}? Judge this route independently of the other three; a separate unsupported request does not create a supported route."


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def frozen_cases(path: Path, name: str) -> list[dict[str, Any]]:
    if sha256(path) != HASHES[name] or sha256(CONTRACT_PATH) != HASHES["contract"]:
        raise RuntimeError(f"{name} dataset or Contract changed after freeze")
    return read_jsonl(path)


def frozen_settings():
    settings = get_settings()
    if (settings.jev_model != "jev-latest" or settings.jev_timeout_seconds != 5
            or settings.jev_router_confidence_threshold != .8 or not settings.jev_shadow_mode):
        raise RuntimeError("Jev alias, timeout, Single threshold or Shadow Mode differs from freeze")
    if not settings.typesafe_api_key:
        raise RuntimeError("missing Jev API key")
    return settings


def multi_questions(mode: str) -> dict[str, Noul | Score]:
    criteria = router_criteria()
    if list(criteria) != list(ROUTES):
        raise RuntimeError("route order differs from frozen Contract projection")
    if mode == "noul":
        return {route: Noul(instructions=HEAD_INSTRUCTIONS.format(route=route),
                            criteria={"true": description, "false": NOUL_FALSE})
                for route, description in criteria.items()}
    if mode == "score":
        return {route: Score(instructions=HEAD_INSTRUCTIONS.format(route=route),
                             criteria=[*SCORE_LEVELS, "Distinct supported task for this route: " + description])
                for route, description in criteria.items()}
    raise ValueError("unknown multi head")


def _error(exc: Exception) -> dict[str, str]:
    error_type = str(exc) if type(exc).__name__ == "JevDecisionError" else type(exc).__name__
    return {"error_category": "parse_schema_failure" if error_type in PARSE_ERRORS else "api_failure",
            "error_type": error_type}


async def call_jev_multi(client: JevClient, query: str, mode: str) -> dict[str, Any]:
    started = perf_counter()
    try:
        response = await client.decide({"query": query[:4000]}, multi_questions(mode))
        if set(response.answers) != set(ROUTES):
            raise ValueError("missing route evidence")
        evidence = {}
        for route in ROUTES:
            answer = response.answers[route]
            if mode == "noul":
                if answer.kind != "noul" or answer.noul is None or not 0 <= answer.noul <= 1:
                    raise ValueError("invalid Noul route evidence")
                evidence[route] = {"noul_probability": answer.noul}
            else:
                distribution = answer.probabilities or {}
                if (answer.kind != "score" or set(distribution) != {"0", "1", "2"}
                        or any(not 0 <= value <= 1 for value in distribution.values())
                        or not .98 <= sum(distribution.values()) <= 1.02
                        or answer.score is None or not 0 <= answer.score <= 2):
                    raise ValueError("invalid Score route evidence")
                evidence[route] = {"rubric_score": answer.score,
                                   "confidence": answer.confidence,
                                   "probabilities": distribution}
        scores = {route: (evidence[route]["noul_probability"] if mode == "noul"
                          else evidence[route]["probabilities"]["2"])
                  for route in ROUTES}
        return {"scores": scores, "evidence": evidence, "response_model": response.response_model,
                "api_latency_ms": response.latency_ms,
                "wall_latency_ms": round((perf_counter() - started) * 1000, 2),
                "usage": response.usage, "api_calls": 1, "error_category": None,
                "error_type": None, "timestamp": response.evaluation_timestamp}
    except Exception as exc:
        return {"scores": None, "evidence": None, "response_model": None,
                "api_latency_ms": None, "wall_latency_ms": round((perf_counter() - started) * 1000, 2),
                "usage": None, "api_calls": 1, **_error(exc),
                "timestamp": datetime.now(UTC).isoformat()}


class HybridRecorder:
    """Observe the unmodified production router and its existing provider call."""

    def __init__(self):
        self.router = create_hybrid_router(device="cpu")
        self.original_llm = llm_runtime.create_routing_response
        self.original_model = self.router._model.route
        self.active: dict[str, Any] | None = None

        async def tracked_llm(input_items, instructions, schema):
            assert self.active is not None
            self.active["llm_api_calls"] += 1
            response = await self.original_llm(input_items, instructions, schema)
            usage = response.usage
            if usage:
                self.active["usage"]["input_tokens"] += int(usage.input_tokens or 0)
                self.active["usage"]["output_tokens"] += int(usage.output_tokens or 0)
            return response

        def tracked_model(text):
            assert self.active is not None
            self.active["local_model_calls"] += 1
            return self.original_model(text)

        llm_runtime.create_routing_response = tracked_llm
        self.router._model.route = tracked_model

    async def route(self, query: str) -> dict[str, Any]:
        self.active = {"llm_api_calls": 0, "local_model_calls": 0,
                       "usage": Counter({"input_tokens": 0, "output_tokens": 0})}
        started = perf_counter()
        try:
            result = await self.router.route(query)
            routes = sorted({task.route_key.value for task in result.tasks})
            return {"predicted_routes": routes, "is_complete": result.is_complete,
                    "needs_clarification": result.needs_clarification,
                    "task_count": len(result.tasks),
                    "dependency_count": sum(len(task.depends_on) for task in result.tasks),
                    "fallback_reason": "needs_clarification" if result.needs_clarification else
                                       "incomplete" if not result.is_complete else
                                       "llm_escalation" if self.active["llm_api_calls"] else None,
                    "wall_latency_ms": round((perf_counter() - started) * 1000, 2),
                    "usage": dict(self.active["usage"]),
                    "api_calls": self.active["llm_api_calls"],
                    "local_model_calls": self.active["local_model_calls"],
                    "error_category": None, "error_type": None}
        except Exception as exc:
            return {"predicted_routes": [], "is_complete": False,
                    "needs_clarification": False, "task_count": 0,
                    "dependency_count": 0, "fallback_reason": "router_exception",
                    "wall_latency_ms": round((perf_counter() - started) * 1000, 2),
                    "usage": dict(self.active["usage"]), "api_calls": self.active["llm_api_calls"],
                    "local_model_calls": self.active["local_model_calls"],
                    "error_category": "router_failure", "error_type": type(exc).__name__}
        finally:
            self.active = None

    def close(self) -> None:
        llm_runtime.create_routing_response = self.original_llm
        self.router._model.route = self.original_model


def select(scores: dict[str, float] | None, config: dict[str, Any]) -> list[str]:
    if scores is None:
        return []
    if set(scores) != set(ROUTES) or any(not math.isfinite(s) or not 0 <= s <= 1
                                          for s in scores.values()):
        raise ValueError("invalid independent route scores")
    family = config["family"]
    if family in {"global_threshold", "native_noul"}:
        threshold = .5 if family == "native_noul" else config["threshold"]
        picked = {route for route, score in scores.items() if score >= threshold}
    elif family == "per_route_threshold":
        thresholds = config["thresholds"]
        if set(thresholds) != set(ROUTES):
            raise ValueError("incomplete per-route thresholds")
        picked = {route for route, score in scores.items() if score >= thresholds[route]}
    elif family == "top_k_min_score":
        ranked = sorted(ROUTES, key=lambda route: (-scores[route], ROUTES.index(route)))
        picked = {route for route in ranked[:config["k"]]
                  if scores[route] >= config["minimum_score"]}
    else:
        raise ValueError("unknown multi selection family")
    return [route for route in ROUTES if route in picked]


def _ratio(n: int, d: int) -> float:
    return round(n / d, 4) if d else 0.0


def route_metrics(rows: list[dict[str, Any]], predicted: list[list[str]]) -> dict[str, Any]:
    if len(rows) != len(predicted) or not rows:
        raise ValueError("metrics require equal nonempty Gold and prediction arrays")
    stats = {route: Counter({"tp": 0, "fp": 0, "fn": 0}) for route in ROUTES}
    tp = fp = fn = exact = over = under = count_error = abs_count_error = 0
    for row, routes in zip(rows, predicted, strict=True):
        gold, guess = set(row["gold_routes"]), set(routes)
        if not gold or not gold <= set(ROUTES) or not guess <= set(ROUTES):
            raise ValueError("invalid route set in metric input")
        exact += gold == guess
        over += bool(guess - gold)
        under += bool(gold - guess)
        count_error += len(gold) != len(guess)
        abs_count_error += abs(len(gold) - len(guess))
        tp += len(gold & guess)
        fp += len(guess - gold)
        fn += len(gold - guess)
        for route in ROUTES:
            stats[route]["tp"] += route in gold and route in guess
            stats[route]["fp"] += route not in gold and route in guess
            stats[route]["fn"] += route in gold and route not in guess
    precision, recall = _ratio(tp, tp + fp), _ratio(tp, tp + fn)
    by_route = {}
    for route, counts in stats.items():
        p, r = _ratio(counts["tp"], counts["tp"] + counts["fp"]), _ratio(counts["tp"], counts["tp"] + counts["fn"])
        by_route[route] = {"support": counts["tp"] + counts["fn"], "precision": p,
                           "recall": r, "f1": round(2 * p * r / (p + r), 4) if p + r else 0.0,
                           **dict(counts)}
    return {"count": len(rows), "exact_count": exact, "exact_match": _ratio(exact, len(rows)),
            "micro_precision": precision, "micro_recall": recall,
            "micro_f1": round(2 * precision * recall / (precision + recall), 4) if precision + recall else 0.0,
            "macro_f1": round(mean(item["f1"] for item in by_route.values()), 4),
            "per_route": by_route, "over_routing_count": over,
            "over_routing_rate": _ratio(over, len(rows)),
            "under_routing_count": under, "under_routing_rate": _ratio(under, len(rows)),
            "route_count_error_count": count_error,
            "route_count_error_rate": _ratio(count_error, len(rows)),
            "mean_absolute_route_count_error": round(abs_count_error / len(rows), 4),
            "tp": tp, "fp": fp, "fn": fn}


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * p
    low, high = int(position), min(int(position) + 1, len(ordered) - 1)
    return round(ordered[low] + (ordered[high] - ordered[low]) * (position - low), 2)


def timing(outputs: list[dict[str, Any]], field: str) -> dict[str, float | int | None]:
    values = [float(row[field]) for row in outputs if row.get(field) is not None]
    return {"count": len(values), "average": round(mean(values), 2) if values else None,
            "p50": percentile(values, .5), "p95": percentile(values, .95),
            "p99": percentile(values, .99)}


def operational_metrics(outputs: list[dict[str, Any]], *, jev: bool) -> dict[str, Any]:
    usage = Counter()
    for output in outputs:
        usage.update({key: value for key, value in (output.get("usage") or {}).items()
                      if key in {"input_tokens", "output_tokens"} and isinstance(value, int)})
    calls = sum(output.get("api_calls", 0) for output in outputs)
    return {"wall_latency_ms": timing(outputs, "wall_latency_ms"),
            "api_latency_ms": timing(outputs, "api_latency_ms") if jev else None,
            "api_calls_total": calls, "api_calls_per_query": round(calls / len(outputs), 4),
            "local_model_calls_total": None if jev else sum(o["local_model_calls"] for o in outputs),
            "token_usage": dict(usage),
            "parse_schema_failures": sum(o.get("error_category") == "parse_schema_failure" for o in outputs),
            "api_failures": sum(o.get("error_category") == "api_failure" for o in outputs),
            "timeout_failures": sum("timeout" in (o.get("error_type") or "").lower() for o in outputs),
            "router_failures": sum(o.get("error_category") == "router_failure" for o in outputs),
            "fallback_or_clarification_count": sum(bool(o.get("fallback_reason")) for o in outputs),
            "llm_escalation_count": sum(o.get("fallback_reason") == "llm_escalation" for o in outputs),
            "clarification_count": sum(o.get("fallback_reason") == "needs_clarification" for o in outputs),
            "incomplete_count": sum(o.get("is_complete") is False for o in outputs) if not jev else None,
            "response_models": dict(Counter(o["response_model"] for o in outputs
                                            if o.get("response_model")))}


def _utility(metrics: dict[str, Any]) -> float:
    return round(.45 * metrics["exact_match"] + .35 * metrics["micro_f1"]
                 - .1 * metrics["over_routing_rate"] - .1 * metrics["under_routing_rate"], 6)


def evaluate_config(rows: list[dict[str, Any]], mode: str,
                    config: dict[str, Any]) -> dict[str, Any]:
    predictions = [select(row[mode]["scores"], config) for row in rows]
    metrics = route_metrics(rows, predictions)
    return {"mode": mode, "config": config, "metrics": metrics, "utility": _utility(metrics)}


def candidate_configs(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grid = [round(n / 20, 2) for n in range(2, 19)]
    candidates = []
    for mode in ("score", "noul"):
        global_results = [evaluate_config(rows, mode, {"family": "global_threshold", "threshold": threshold})
                          for threshold in grid]
        candidates += global_results
        for start in sorted(global_results, key=lambda r: (-r["utility"], -r["metrics"]["exact_match"]))[:3]:
            thresholds = {route: start["config"]["threshold"] for route in ROUTES}
            for _ in range(2):
                for route in ROUTES:
                    options = []
                    for value in grid:
                        test = {**thresholds, route: value}
                        options.append(evaluate_config(rows, mode,
                                                       {"family": "per_route_threshold", "thresholds": test}))
                    best = max(options, key=lambda r: (r["utility"], r["metrics"]["exact_match"],
                                                       -sum(abs(v - .5) for v in r["config"]["thresholds"].values())))
                    thresholds = best["config"]["thresholds"]
            candidates.append(evaluate_config(rows, mode,
                                              {"family": "per_route_threshold", "thresholds": thresholds}))
        for k in (2, 3, 4):
            for value in grid:
                candidates.append(evaluate_config(rows, mode,
                                                  {"family": "top_k_min_score", "k": k,
                                                   "minimum_score": value}))
    candidates.append(evaluate_config(rows, "noul", {"family": "native_noul"}))
    return candidates


def tune(rows: list[dict[str, Any]]) -> dict[str, Any]:
    cases = frozen_cases(DEV, "dev")
    if (len(rows) != len(cases) or {row["case_id"] for row in rows} !=
            {row["case_id"] for row in cases}):
        raise RuntimeError("dev result coverage differs from frozen corpus")
    candidates = candidate_configs(rows)
    best_by_family = {}
    for family in ("global_threshold", "per_route_threshold", "top_k_min_score", "native_noul"):
        choices = [item for item in candidates if item["config"]["family"] == family]
        best_by_family[family] = max(choices, key=lambda r: (r["utility"], r["metrics"]["exact_match"],
                                                               r["metrics"]["micro_f1"]))
    pareto = [item for item in candidates if not any(
        other is not item and
        other["metrics"]["exact_match"] >= item["metrics"]["exact_match"] and
        other["metrics"]["micro_f1"] >= item["metrics"]["micro_f1"] and
        other["metrics"]["over_routing_rate"] <= item["metrics"]["over_routing_rate"] and
        other["metrics"]["under_routing_rate"] <= item["metrics"]["under_routing_rate"] and
        (other["metrics"]["exact_match"] > item["metrics"]["exact_match"] or
         other["metrics"]["micro_f1"] > item["metrics"]["micro_f1"] or
         other["metrics"]["over_routing_rate"] < item["metrics"]["over_routing_rate"] or
         other["metrics"]["under_routing_rate"] < item["metrics"]["under_routing_rate"])
        for other in candidates)]
    chosen = max(pareto, key=lambda r: (r["utility"], r["metrics"]["exact_match"],
                                        -r["metrics"]["route_count_error_rate"]))
    baseline = route_metrics(rows, [row["hybrid"]["predicted_routes"] for row in rows])
    summary = {"dataset_sha256": HASHES["dev"], "contract_sha256": HASHES["contract"],
               "selection_rule": "maximize .45 exact + .35 micro_f1 - .10 over - .10 under among nondominated dev candidates",
               "candidate_count": len(candidates), "pareto_count": len(pareto),
               "hybrid_control": baseline, "best_by_family": best_by_family,
               "chosen": chosen, "dev_results_sha256": sha256(DEV_RESULTS)}
    DEV_ABLATION.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    FROZEN_CONFIG.write_text(json.dumps({"mode": chosen["mode"], "config": chosen["config"],
                                        "dev_utility": chosen["utility"],
                                        "dev_metrics": chosen["metrics"],
                                        "dev_dataset_sha256": HASHES["dev"],
                                        "blind_dataset_sha256": HASHES["blind"],
                                        "contract_sha256": HASHES["contract"],
                                        "dev_results_sha256": sha256(DEV_RESULTS),
                                        "frozen_at": datetime.now(UTC).isoformat()},
                                       ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def load_config() -> dict[str, Any]:
    config = json.loads(FROZEN_CONFIG.read_text(encoding="utf-8"))
    if (config["dev_dataset_sha256"] != HASHES["dev"] or
            config["blind_dataset_sha256"] != HASHES["blind"] or
            config["contract_sha256"] != HASHES["contract"] or
            config["dev_results_sha256"] != sha256(DEV_RESULTS)):
        raise RuntimeError("frozen Multi configuration or source changed")
    return config


async def run_dev() -> None:
    cases = frozen_cases(DEV, "dev")
    settings = frozen_settings()
    client = JevClient(settings)
    existing = read_jsonl(DEV_RESULTS) if DEV_RESULTS.exists() else []
    output = list(existing)
    seen = {row["case_id"] for row in output}
    hybrid = HybridRecorder()
    try:
        for index, case in enumerate(cases, 1):
            if case["case_id"] in seen:
                continue
            old = await hybrid.route(case["query"])
            if index % 2:
                score = await call_jev_multi(client, case["query"], "score")
                noul = await call_jev_multi(client, case["query"], "noul")
            else:
                noul = await call_jev_multi(client, case["query"], "noul")
                score = await call_jev_multi(client, case["query"], "score")
            output.append({**case, "dataset_sha256": HASHES["dev"],
                           "hybrid": old, "score": score, "noul": noul})
            write_jsonl(DEV_RESULTS, output)
            print(f"dev {index}/{len(cases)} {case['case_id']} hybrid={old['predicted_routes']} score={score['scores']} noul={noul['scores']}", flush=True)
    finally:
        hybrid.close()


async def run_blind() -> None:
    cases = frozen_cases(BLIND, "blind")
    config = load_config()
    settings = frozen_settings()
    client = JevClient(settings)
    existing = read_jsonl(BLIND_RESULTS) if BLIND_RESULTS.exists() else []
    output = list(existing)
    seen = {(row["round"], row["case_id"]) for row in output}
    hybrid = HybridRecorder()
    try:
        for round_number in range(1, 4):
            for index, case in enumerate(cases, 1):
                if (round_number, case["case_id"]) in seen:
                    continue
                if (round_number + index) % 2:
                    old = await hybrid.route(case["query"])
                    jev = await call_jev_multi(client, case["query"], config["mode"])
                else:
                    jev = await call_jev_multi(client, case["query"], config["mode"])
                    old = await hybrid.route(case["query"])
                predicted = select(jev["scores"], config["config"])
                output.append({**case, "dataset_sha256": HASHES["blind"],
                               "frozen_config_sha256": sha256(FROZEN_CONFIG),
                               "round": round_number, "hybrid": old,
                               "jev": {**jev, "predicted_routes": predicted}})
                write_jsonl(BLIND_RESULTS, output)
                print(f"blind {round_number}:{index}/{len(cases)} {case['case_id']} hybrid={old['predicted_routes']} jev={predicted}", flush=True)
    finally:
        hybrid.close()


def _summary_stats(values: list[float]) -> dict[str, float]:
    return {"mean": round(mean(values), 4), "min": min(values), "max": max(values),
            "std": round(pstdev(values), 4)}


def summarize_blind(rows: list[dict[str, Any]]) -> dict[str, Any]:
    cases = frozen_cases(BLIND, "blind")
    config = load_config()
    if (len(rows) != len(cases) * 3 or
            {(r["round"], r["case_id"]) for r in rows} !=
            {(round_number, c["case_id"]) for round_number in range(1, 4) for c in cases} or
            any(r["frozen_config_sha256"] != sha256(FROZEN_CONFIG) for r in rows)):
        raise RuntimeError("blind results do not cover exactly three frozen repetitions")
    by_id = {case["case_id"]: case for case in cases}
    metrics = {}
    for name in ("hybrid", "jev"):
        per_round = []
        for round_number in range(1, 4):
            subset = [row for row in rows if row["round"] == round_number]
            per_round.append(route_metrics(subset, [row[name]["predicted_routes"] for row in subset]))
        all_outputs = [row[name] for row in rows]
        flips = []
        for case_id in by_id:
            predictions = {tuple(row[name]["predicted_routes"]) for row in rows if row["case_id"] == case_id}
            if len(predictions) > 1:
                flips.append({"case_id": case_id, "unique_route_sets": [list(x) for x in sorted(predictions)]})
        true_multi = [row for row in rows if row["cohort"] == "true_multi"]
        single_guards = [row for row in rows if row["facet"].startswith("single_guard")]
        partial = [row for row in rows if row["unroutable_part"]]
        metrics[name] = {"per_round": per_round,
                         "repeat_stats": {key: _summary_stats([m[key] for m in per_round])
                                          for key in ("exact_match", "micro_f1", "macro_f1",
                                                      "over_routing_rate", "under_routing_rate",
                                                      "route_count_error_rate")},
                         "case_route_flips": flips, "case_route_flip_count": len(flips),
                         "operations": operational_metrics(all_outputs, jev=name == "jev"),
                         "true_multi": route_metrics(true_multi, [r[name]["predicted_routes"] for r in true_multi]),
                         "single_guard": route_metrics(single_guards, [r[name]["predicted_routes"] for r in single_guards]),
                         "partial_or_ambiguous": route_metrics(partial, [r[name]["predicted_routes"] for r in partial])}
    hybrid_tokens = sum(metrics["hybrid"]["operations"]["token_usage"].values())
    jev_tokens = sum(metrics["jev"]["operations"]["token_usage"].values())
    return {"blind_dataset_sha256": HASHES["blind"],
            "frozen_config_sha256": sha256(FROZEN_CONFIG),
            "selection": {"mode": config["mode"], "config": config["config"]},
            "rounds": 3, "cases_per_round": len(cases), "comparison": metrics,
            "relative_cost_proxy": {"method": "equal price per 1k input/output tokens; excludes local compute and provider-specific pricing",
                                    "jev_to_hybrid_token_ratio": round(jev_tokens / hybrid_tokens, 4) if hybrid_tokens else None,
                                    "hybrid_total_tokens": hybrid_tokens,
                                    "jev_total_tokens": jev_tokens}}


def failure_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for row in rows:
        gold = set(row["gold_routes"])
        h, j = row["hybrid"], row["jev"]
        h_guess, j_guess = set(h["predicted_routes"]), set(j["predicted_routes"])
        if (h_guess == gold and j_guess == gold and not h["error_category"]
                and not j["error_category"] and not row["unroutable_part"]):
            continue
        output.append({"round": row["round"], "case_id": row["case_id"],
                       "query": row["query"], "gold_routes": row["gold_routes"],
                       "cohort": row["cohort"], "facet": row["facet"],
                       "unroutable_part": row["unroutable_part"],
                       "hybrid_routes": h["predicted_routes"],
                       "jev_routes": j["predicted_routes"],
                       "hybrid_missing": sorted(gold - h_guess), "hybrid_extra": sorted(h_guess - gold),
                       "jev_missing": sorted(gold - j_guess), "jev_extra": sorted(j_guess - gold),
                       "hybrid_error": h["error_category"], "jev_error": j["error_category"],
                       "hybrid_fallback": h["fallback_reason"],
                       "jev_route_scores": j["scores"], "jev_route_evidence": j["evidence"]})
    return output


def summarize_single(rows: list[dict[str, Any]]) -> dict[str, Any]:
    cases = frozen_cases(SINGLE, "single")
    if len(rows) != len(cases) or {r["case_id"] for r in rows} != {c["case_id"] for c in cases}:
        raise RuntimeError("single blind coverage mismatch")
    from evals.jev_router_baseline_v1 import single_metrics
    classification = single_metrics([{"gold_routes": row["gold_routes"],
                                      "predicted_routes": row["jev"]["predicted_routes"]}
                                     for row in rows], "predicted_routes")
    matrix = classification["confusion_matrix"]
    outputs = [row["jev"] for row in rows]
    return {"dataset_sha256": HASHES["single"], "question_type": "Choice",
            "criteria_source": "unchanged capability_criteria.py V2",
            **classification,
            "general_chat_worldline_confusion": matrix["general_chat"]["worldline"] + matrix["worldline"]["general_chat"],
            "low_confidence_count": sum(o["confidence"] is not None and o["confidence"] < .8 for o in outputs),
            "low_confidence_case_ids": [row["case_id"] for row in rows
                                        if row["jev"]["confidence"] is not None and row["jev"]["confidence"] < .8],
            "operations": operational_metrics(outputs, jev=True)}


async def run_single() -> None:
    cases = frozen_cases(SINGLE, "single")
    settings = frozen_settings()
    client = JevClient(settings)
    existing = read_jsonl(SINGLE_RESULTS) if SINGLE_RESULTS.exists() else []
    output = list(existing)
    seen = {row["case_id"] for row in output}
    for index, case in enumerate(cases, 1):
        if case["case_id"] in seen:
            continue
        started = perf_counter()
        try:
            response = await client.decide(
                {"query": case["query"][:4000]},
                {"route": Choice(instructions=ROUTER_QUESTION_INSTRUCTIONS,
                                 criteria=router_criteria())},
            )
            answer = response.answers["route"]
            jev = {"predicted_routes": [answer.choice] if answer.choice else [],
                   "confidence": answer.confidence, "probabilities": answer.probabilities,
                   "response_model": response.response_model,
                   "api_latency_ms": response.latency_ms,
                   "wall_latency_ms": round((perf_counter() - started) * 1000, 2),
                   "usage": response.usage, "api_calls": 1,
                   "error_category": None, "error_type": None}
        except Exception as exc:
            jev = {"predicted_routes": [], "confidence": None, "probabilities": None,
                   "response_model": None, "api_latency_ms": None,
                   "wall_latency_ms": round((perf_counter() - started) * 1000, 2),
                   "usage": None, "api_calls": 1, **_error(exc)}
        output.append({**case, "dataset_sha256": HASHES["single"], "jev": jev})
        write_jsonl(SINGLE_RESULTS, output)
        print(f"single {index}/{len(cases)} {case['case_id']} jev={jev['predicted_routes']}", flush=True)
    SINGLE_METRICS.write_text(json.dumps(summarize_single(output), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("dev-run", "dev-tune", "blind-run",
                                            "blind-summarize", "single-run", "single-summarize"))
    args = parser.parse_args()
    if args.command == "dev-run":
        asyncio.run(run_dev())
    elif args.command == "dev-tune":
        tune(read_jsonl(DEV_RESULTS))
        print(FROZEN_CONFIG)
    elif args.command == "blind-run":
        asyncio.run(run_blind())
    elif args.command == "blind-summarize":
        rows = read_jsonl(BLIND_RESULTS)
        BLIND_METRICS.write_text(json.dumps(summarize_blind(rows), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        write_jsonl(FAILURES, failure_rows(rows))
        print(BLIND_METRICS)
    elif args.command == "single-run":
        asyncio.run(run_single())
    else:
        SINGLE_METRICS.write_text(json.dumps(summarize_single(read_jsonl(SINGLE_RESULTS)),
                                             ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(SINGLE_METRICS)


if __name__ == "__main__":
    main()
