"""Evaluation-only v2 replays built on the existing Jev shadow boundaries."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from typesafe_sdk import Noul

from app.agent.decision.jev.client import JevClient
from app.agent.decision.jev.memory_filter import _question_id as memory_question_id, filter_memories
from app.agent.decision.jev.router import compare_route
from app.agent.foundation.hybrid_routing import create_hybrid_router
from app.agent.foundation.routing import RouteDecision, RouteKey
from app.config.settings import get_settings
from evals.jev_shadow_v1 import (
    DATASETS, REPORTS, RecordingClient, read_jsonl, write_jsonl,
)

ROUTER_DATASET = DATASETS / "router_eval_v2.jsonl"
ROUTER_RESULTS = REPORTS / "router_eval_v2_results.jsonl"
MEMORY_DATASET = DATASETS / "memory_eval_v2.jsonl"
CONTEXT_DATASET = DATASETS / "context_eval_v2.jsonl"
MEMORY_RESULTS = REPORTS / "memory_eval_v2_results.jsonl"


def _settings():
    settings = get_settings()
    if not settings.jev_shadow_mode or settings.jev_model != "jev-latest":
        raise RuntimeError("v2 evaluation requires JEV_SHADOW_MODE=true and JEV_MODEL=jev-latest")
    return settings


def prepare_router_v2() -> Path:
    source = read_jsonl(DATASETS / "jev_router_diagnostic_v1.jsonl")
    rows = []
    for item in source:
        task = ("followup_analysis" if item["recent_context"] else
                "single_intent" if len(item["gold_route"]) == 1 else "multi_intent")
        rows.append({"case_id": item["case_id"], "query": item["query"],
                     "recent_context": item["recent_context"],
                     "gold_routes": item["gold_route"], "task": task,
                     "label_source": item["label_source"], "tags": item["tags"]})
    assert len(rows) == 47
    assert sum(row["task"] == "single_intent" for row in rows) == 31
    assert sum(row["task"] == "multi_intent" for row in rows) == 14
    write_jsonl(ROUTER_DATASET, rows)
    return ROUTER_DATASET


def _metadata(recorder: RecordingClient, timestamp: str) -> dict[str, Any]:
    response = recorder.responses[-1] if recorder.responses else None
    return {
        "requested_model": "jev-latest",
        "response_model": response.response_model if response else None,
        "evaluation_timestamp": timestamp,
        "response_timestamp": response.evaluation_timestamp if response else None,
        "jev_latency_ms": sum(r.latency_ms for r in recorder.responses) if recorder.responses else None,
    }


async def run_router_v2(limit: int | None = None) -> Path:
    settings = _settings()
    router = create_hybrid_router(device="cpu")
    client = JevClient(settings)
    timestamp = datetime.now(UTC).isoformat()
    rows: list[dict[str, Any]] = []
    questions = {
        RouteKey.CURRENT_SONG.value: Noul(instructions="Does this request ask to analyze the currently playing song or lyrics?"),
        RouteKey.TOOL_CALLING.value: Noul(instructions="Does this request ask to operate the music player or library?"),
        RouteKey.WORLDLINE.value: Noul(instructions="Does this request ask to research music knowledge or relationships?"),
        RouteKey.GENERAL_CHAT.value: Noul(instructions="Does this request ask for general conversation or explanation?"),
    }
    for case in read_jsonl(ROUTER_DATASET)[:limit]:
        baseline: RouteDecision | None = None
        baseline_error: str | None = None
        start = perf_counter()
        try:
            baseline = await router.route(case["query"])
        except Exception as exc:
            baseline_error = type(exc).__name__
        baseline_ms = round((perf_counter() - start) * 1000, 2)
        recorder = RecordingClient(client)
        jev_error: str | None = None
        scores: dict[str, float] | None = None
        confidence: float | None = None
        probabilities: dict[str, float] | None = None
        predicted: list[str] = []
        if case["task"] == "multi_intent":
            try:
                response = await recorder.decide({"query": case["query"][:4000]}, questions)
                scores = {key: response.answers[key].noul for key in questions}
                if any(value is None for value in scores.values()):
                    raise ValueError("missing Noul score")
                predicted = sorted(key for key, value in scores.items() if value is not None and value >= .5)
            except Exception as exc:
                jev_error = type(exc).__name__
        else:
            try:
                await compare_route(case["query"], baseline or RouteDecision(),
                                    settings=settings, client=recorder, trace_id=case["case_id"])
                if recorder.responses:
                    answer = recorder.responses[-1].answers["route"]
                    predicted = [answer.choice] if answer.choice else []
                    confidence = answer.confidence
                    probabilities = answer.probabilities
                else:
                    jev_error = "no_valid_response"
            except Exception as exc:
                jev_error = type(exc).__name__
        rows.append({**case, **_metadata(recorder, timestamp),
                     "baseline_routes": sorted({task.route_key.value for task in baseline.tasks}) if baseline else [],
                     "baseline_complete": baseline.is_complete if baseline else False,
                     "baseline_error": baseline_error,
                     "baseline_latency_ms": baseline_ms,
                     "jev_routes": predicted, "jev_confidence": confidence,
                     "jev_probabilities": probabilities, "jev_noul_scores": scores,
                     "jev_error": jev_error,
                     "recent_context_consumed": False,
                     "multi_intent_method": "four independent Noul questions; threshold=0.5"
                     if case["task"] == "multi_intent" else None})
        write_jsonl(ROUTER_RESULTS, rows)
    return ROUTER_RESULTS


async def run_memory_v2(limit: int | None = None) -> Path:
    from app.agent.memory.models import MemoryRetrievalRequest, MemoryRetrievalResult, RetrievedMemory

    settings = _settings()
    client = JevClient(settings)
    timestamp = datetime.now(UTC).isoformat()
    output: list[dict[str, Any]] = []
    for case in read_jsonl(MEMORY_DATASET)[:limit]:
        items = [RetrievedMemory.model_validate(row["retrieved"])
                 for row in case["candidate_memories"]]
        request = MemoryRetrievalRequest(user_id=case["user_id"], query=case["query"],
                                         scene=case["scene"], top_k=8)
        result = MemoryRetrievalResult(user_id=case["user_id"], items=items)
        recorder = RecordingClient(client)
        await filter_memories(request, result, settings=settings, client=recorder,
                              trace_id=case["case_id"])
        scores: dict[str, float] = {}
        for response in recorder.responses:
            for item in items:
                answer = response.answers.get(memory_question_id(item.memory_id))
                if answer and answer.noul is not None:
                    scores[item.memory_id] = answer.noul
        complete = len(scores) == len(items)
        selected = [item.memory_id for item in items if not complete or
                    scores[item.memory_id] >= settings.jev_memory_filter_threshold]
        response = recorder.responses[-1] if recorder.responses else None
        output.append({"case_id": case["case_id"], "query": case["query"],
                       "task": case["task"], "retrieval_mode": case["retrieval_mode"],
                       "gold_memory_ids": case["gold_memory_ids"],
                       "candidate_memories": [{key: row[key] for key in
                                              ("memory_id", "memory_type", "content", "relevance", "gold_useful")}
                                             for row in case["candidate_memories"]],
                       "baseline_selected_ids": [item.memory_id for item in items],
                       "jev_selected_ids": selected, "jev_scores": scores,
                       "old_keep_jev_keep_diff": [
                           {"memory_id": item.memory_id, "old_keep": True,
                            "jev_keep": item.memory_id in selected} for item in items],
                       "fallback_used": not complete,
                       "baseline_retrieval_latency_ms": case["baseline_retrieval_latency_ms"],
                       "jev_latency_ms": sum(r.latency_ms for r in recorder.responses) if recorder.responses else None,
                       "requested_model": "jev-latest",
                       "response_model": response.response_model if response else None,
                       "evaluation_timestamp": timestamp,
                       "response_timestamps": [r.evaluation_timestamp for r in recorder.responses],
                       "recent_context_consumed": False})
        write_jsonl(MEMORY_RESULTS, output)
    return MEMORY_RESULTS
