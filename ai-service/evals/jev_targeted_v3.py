"""Frozen v3 splits and one-request multi-label Jev Router shadow replay."""

from __future__ import annotations

import random
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from typesafe_sdk import Noul

from app.agent.decision.jev.client import JevClient
from app.agent.foundation.hybrid_routing import create_hybrid_router
from app.agent.foundation.routing import RouteKey
from evals.jev_shadow_v1 import DATASETS, REPORTS, RecordingClient, read_jsonl, write_jsonl
from evals.jev_shadow_v2 import _settings

SPLITS = DATASETS / "jev_targeted_v3_splits.jsonl"
ROUTER_RESULTS = REPORTS / "router_multilabel_v3_results.jsonl"
ROUTER_CALIBRATION = REPORTS / "router_multilabel_v3_calibration.jsonl"
ROUTES = tuple(key.value for key in RouteKey)
QUESTIONS = {
    RouteKey.CURRENT_SONG.value: Noul(instructions=(
        "Does the user independently ask to analyze, explain, or discuss the specific currently "
        "playing song, its lyrics, or its theme? Mere mention of a song while requesting an action "
        "or general music knowledge is not enough.")),
    RouteKey.TOOL_CALLING.value: Noul(instructions=(
        "Does the user independently request an executable music player or library action, "
        "including playing, searching, saving, favoriting, or changing playback? Negated or "
        "hypothetical actions do not count.")),
    RouteKey.WORLDLINE.value: Noul(instructions=(
        "Does the user independently request researched music facts, artist history, musical "
        "relationships, or factual world knowledge? A reference to an artist in another task "
        "alone does not count.")),
    RouteKey.GENERAL_CHAT.value: Noul(instructions=(
        "Does the user independently ask for conversation, opinion, or a general explanation "
        "that is not the specific current song, an executable player action, or music research? "
        "Do not mark this as a catch-all when one of those other intents fully covers the request.")),
}


def prepare_splits() -> Path:
    """Freeze stratified case assignments before score calibration."""
    router = read_jsonl(DATASETS / "router_eval_v2.jsonl")
    memory = read_jsonl(DATASETS / "memory_eval_v2.jsonl")
    context = read_jsonl(DATASETS / "context_eval_v2.jsonl")
    rows: list[dict[str, str]] = []
    rng = random.Random(20260924)
    for task in ("single_intent", "multi_intent", "followup_analysis"):
        cases = [case for case in router if case["task"] == task]
        rng.shuffle(cases)
        for index, case in enumerate(cases):
            rows.append({"component": "router", "case_id": case["case_id"],
                         "split": "dev" if index < len(cases) // 2 else "test",
                         "stratum": task})
    for case in memory:
        target = case["gold_memory_ids"][0]
        number = int(target.rsplit("-", 1)[1])
        rows.append({"component": "memory", "case_id": case["case_id"],
                     "split": "dev" if number % 2 else "test",
                     "stratum": target.rsplit("-", 1)[0]})
    for case in context:
        index = int(case["case_id"].rsplit("-", 1)[1])
        rows.append({"component": "context", "case_id": case["case_id"],
                     "split": "dev" if index % 2 else "test",
                     "stratum": case["family"]})
    if len(rows) != 47 + 130 + 30 or len({(r["component"], r["case_id"]) for r in rows}) != len(rows):
        raise ValueError("v3 split does not cover exactly the frozen v2 cases")
    write_jsonl(SPLITS, rows)
    return SPLITS


def split_for(component: str) -> dict[str, str]:
    return {row["case_id"]: row["split"] for row in read_jsonl(SPLITS)
            if row["component"] == component}


def select_routes(scores: dict[str, float], thresholds: dict[str, float], max_routes: int) -> list[str]:
    if not 1 <= max_routes <= len(ROUTES):
        raise ValueError("max_routes out of range")
    if set(scores) != set(ROUTES) or set(thresholds) != set(ROUTES):
        raise ValueError("all RouteKey scores and thresholds are required")
    if any(not 0 <= value <= 1 for value in scores.values()) or any(
        not 0 <= value <= 1 for value in thresholds.values()
    ):
        raise ValueError("probability or threshold out of range")
    eligible = sorted((route for route in ROUTES if scores[route] >= thresholds[route]),
                      key=lambda route: (-scores[route], route))
    return sorted(eligible[:max_routes])


async def run_router_multilabel_v3(limit: int | None = None) -> Path:
    settings = _settings()
    split = split_for("router")
    router = create_hybrid_router(device="cpu")
    client = JevClient(settings)
    timestamp = datetime.now(UTC).isoformat()
    output: list[dict[str, Any]] = []
    for case in read_jsonl(DATASETS / "router_eval_v2.jsonl")[:limit]:
        layer_ms = {"rule": 0.0, "model": 0.0, "llm": 0.0}
        original_rule = router._rule.route
        original_model = router._model.route
        original_llm = router._llm.route

        def timed_rule(*args: Any, **kwargs: Any) -> Any:
            start = perf_counter()
            try:
                return original_rule(*args, **kwargs)
            finally:
                layer_ms["rule"] += (perf_counter() - start) * 1000

        def timed_model(*args: Any, **kwargs: Any) -> Any:
            start = perf_counter()
            try:
                return original_model(*args, **kwargs)
            finally:
                layer_ms["model"] += (perf_counter() - start) * 1000

        async def timed_llm(*args: Any, **kwargs: Any) -> Any:
            start = perf_counter()
            try:
                return await original_llm(*args, **kwargs)
            finally:
                layer_ms["llm"] += (perf_counter() - start) * 1000

        router._rule.route = timed_rule
        router._model.route = timed_model
        router._llm.route = timed_llm
        baseline_error = None
        start = perf_counter()
        try:
            baseline = await router.route(case["query"])
            baseline_routes = sorted({task.route_key.value for task in baseline.tasks})
            baseline_complete = baseline.is_complete
        except Exception as exc:
            baseline_error = type(exc).__name__
            baseline_routes = []
            baseline_complete = False
        baseline_total_ms = (perf_counter() - start) * 1000
        router._rule.route = original_rule
        router._model.route = original_model
        router._llm.route = original_llm

        recorder = RecordingClient(client)
        jev_error = None
        scores = None
        start = perf_counter()
        try:
            response = await recorder.decide({"query": case["query"][:4000]}, QUESTIONS)
            scores = {route: response.answers[route].noul for route in ROUTES}
            if any(value is None for value in scores.values()):
                raise ValueError("missing Noul score")
        except Exception as exc:
            jev_error = type(exc).__name__
            scores = None
        jev_total_ms = (perf_counter() - start) * 1000
        response = recorder.responses[-1] if recorder.responses else None
        output.append({**case, "split": split[case["case_id"]],
                       "baseline_routes": baseline_routes,
                       "baseline_complete": baseline_complete,
                       "baseline_error": baseline_error,
                       "baseline_total_ms": round(baseline_total_ms, 2),
                       "baseline_layer_ms": {k: round(v, 2) for k, v in layer_ms.items()},
                       "jev_scores": scores, "jev_error": jev_error,
                       "jev_api_ms": response.latency_ms if response else None,
                       "jev_total_ms": round(jev_total_ms, 2),
                       "jev_request_count": len(recorder.responses),
                       "requested_model": settings.jev_model,
                       "response_model": response.response_model if response else None,
                       "evaluation_timestamp": timestamp,
                       "response_timestamp": response.evaluation_timestamp if response else None,
                       "recent_context_consumed": False})
        write_jsonl(ROUTER_RESULTS, output)
    return ROUTER_RESULTS


def _scores(rows: list[dict[str, Any]], threshold: float, chat_delta: float,
            max_routes: int) -> tuple[float, float, int]:
    singles = [row for row in rows if row["task"] == "single_intent" and row["jev_scores"]]
    multis = [row for row in rows if row["task"] == "multi_intent" and row["jev_scores"]]
    thresholds = {route: threshold + (chat_delta if route == RouteKey.GENERAL_CHAT.value else 0)
                  for route in ROUTES}
    correct = sum(select_routes(row["jev_scores"], thresholds, max_routes) == row["gold_routes"]
                  for row in singles)
    tp = fp = fn = exact = 0
    for row in multis:
        predicted = set(select_routes(row["jev_scores"], thresholds, max_routes))
        gold = set(row["gold_routes"])
        tp += len(predicted & gold)
        fp += len(predicted - gold)
        fn += len(gold - predicted)
        exact += predicted == gold
    single_accuracy = correct / len(singles) if singles else 0
    multi_f1 = 2 * tp / (2 * tp + fp + fn) if tp + fp + fn else 0
    return single_accuracy, multi_f1, exact


def calibrate_router_v3() -> Path:
    dev = [row for row in read_jsonl(ROUTER_RESULTS) if row["split"] == "dev"]
    if len(dev) < 20 or any(row["jev_scores"] is None for row in dev):
        raise ValueError("complete Router dev replay required before calibration")
    candidates = []
    for step in range(4, 17):
        threshold = step * .05
        for chat_delta in (0.0, .1, .2):
            for max_routes in (2, 3, 4):
                single, multi, exact = _scores(dev, threshold, chat_delta, max_routes)
                candidates.append({"threshold": round(threshold, 2),
                                   "general_chat_delta": chat_delta,
                                   "max_routes": max_routes,
                                   "single_accuracy": single, "multi_micro_f1": multi,
                                   "multi_exact": exact,
                                   "objective": (single + multi) / 2})
    candidates.sort(key=lambda row: (-row["objective"], -row["multi_exact"],
                                      row["max_routes"], -row["threshold"],
                                      row["general_chat_delta"]))
    write_jsonl(ROUTER_CALIBRATION, candidates)
    return ROUTER_CALIBRATION
