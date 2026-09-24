"""Jev Memory Filter shadow replay on the frozen v3 retrieval candidates."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from app.agent.decision.jev.client import JevClient
from app.agent.decision.jev.memory_filter import _question_id, filter_memories
from app.agent.memory.models import MemoryRetrievalRequest, MemoryRetrievalResult, RetrievedMemory
from evals.jev_memory_retrieval_v3 import DEV_RESULTS, TEST_RESULTS
from evals.jev_shadow_v1 import DATASETS, REPORTS, RecordingClient, read_jsonl, write_jsonl
from evals.jev_shadow_v2 import _settings

DEV_FILTER = REPORTS / "memory_filter_dev_v3.jsonl"
TEST_FILTER = REPORTS / "memory_filter_test_v3.jsonl"
CALIBRATION = REPORTS / "memory_filter_calibration_v3.jsonl"
TEST_DECISIONS = REPORTS / "memory_filter_test_decisions_v3.jsonl"
BOUNDARY_AUDIT = REPORTS / "memory_episodic_boundary_audit_v3.jsonl"


async def run_memory_filter_v3(split: str) -> Path:
    if split not in {"dev", "test"}:
        raise ValueError("invalid split")
    settings = _settings()
    source = DEV_RESULTS if split == "dev" else TEST_RESULTS
    path = DEV_FILTER if split == "dev" else TEST_FILTER
    retrieval = [row for row in read_jsonl(source)
                 if row["strategy"] == "hybrid" and row["top_k"] == 8]
    cases = {row["case_id"]: row for row in read_jsonl(DATASETS / "memory_eval_v2.jsonl")
             if row["retrieval_mode"] == "default"}
    if len(retrieval) != (51 if split == "dev" else 49):
        raise ValueError("complete frozen retrieval set required")
    client = JevClient(settings)
    timestamp = datetime.now(UTC).isoformat()
    output: list[dict[str, Any]] = []
    for row in retrieval:
        case = cases[row["case_id"]]
        items = [RetrievedMemory.model_validate(item) for item in row["candidates"]]
        request = MemoryRetrievalRequest(user_id=case["user_id"], query=case["query"],
                                         scene=case["scene"], top_k=8)
        result = MemoryRetrievalResult(user_id=case["user_id"], items=items)
        recorder = RecordingClient(client)
        start = perf_counter()
        await filter_memories(request, result, settings=settings, client=recorder,
                              trace_id=row["case_id"])
        total_ms = (perf_counter() - start) * 1000
        scores = {}
        for response in recorder.responses:
            for item in items:
                answer = response.answers.get(_question_id(item.memory_id))
                if answer and answer.noul is not None:
                    scores[item.memory_id] = answer.noul
        response = recorder.responses[-1] if recorder.responses else None
        output.append({"case_id": row["case_id"], "split": split,
                       "query": case["query"], "target_id": row["target_id"],
                       "candidate_ids": row["candidate_ids"], "jev_scores": scores,
                       "fallback_needed": len(scores) != len(items),
                       "retrieval_latency_ms": row["retrieval_latency_ms"],
                       "jev_api_ms": sum(r.latency_ms for r in recorder.responses)
                       if recorder.responses else None,
                       "jev_total_ms": round(total_ms, 2),
                       "jev_request_count": len(recorder.responses),
                       "requested_model": settings.jev_model,
                       "response_model": response.response_model if response else None,
                       "evaluation_timestamp": timestamp})
        write_jsonl(path, output)
    return path


def _metrics(rows: list[dict[str, Any]], threshold: float) -> dict[str, Any]:
    gold = selected_gold = selected_total = negatives = selected_negatives = 0
    for row in rows:
        if row["target_id"] not in row["candidate_ids"]:
            raise ValueError("retrieval miss must not be counted as Filter false drop")
        if row["fallback_needed"]:
            selected = set(row["candidate_ids"])
        else:
            selected = {mid for mid, score in row["jev_scores"].items() if score >= threshold}
        gold += 1
        selected_gold += row["target_id"] in selected
        selected_total += len(selected)
        negatives += len(row["candidate_ids"]) - 1
        selected_negatives += len(selected - {row["target_id"]})
    return {"cases": len(rows), "key_recall": selected_gold / gold,
            "precision": selected_gold / selected_total if selected_total else 0,
            "false_drop": gold - selected_gold,
            "false_keep_rate": selected_negatives / negatives,
            "candidate_reduction": 1 - selected_total / sum(len(row["candidate_ids"]) for row in rows)}


def calibrate_memory_filter_v3() -> Path:
    rows = read_jsonl(DEV_FILTER)
    if len(rows) != 51:
        raise ValueError("complete Memory dev filter replay required")
    candidates = [{"threshold": threshold, **_metrics(rows, threshold)}
                  for threshold in (.35, .4, .45, .5, .55)]
    candidates.sort(key=lambda item: (-item["key_recall"], -item["precision"],
                                      -item["threshold"]))
    write_jsonl(CALIBRATION, candidates)
    return CALIBRATION


def decide_memory_filter_test_v3() -> Path:
    threshold = read_jsonl(CALIBRATION)[0]["threshold"]
    rows = read_jsonl(TEST_FILTER)
    if len(rows) != 49:
        raise ValueError("complete Memory test filter replay required")
    output = []
    for row in rows:
        for policy, value in (("configured", .5), ("dev_calibrated", threshold)):
            selected = (row["candidate_ids"] if row["fallback_needed"] else
                        [mid for mid in row["candidate_ids"] if row["jev_scores"][mid] >= value])
            output.append({**row, "policy": policy, "threshold": value,
                           "jev_selected_ids": selected,
                           "target_kept": row["target_id"] in selected})
    write_jsonl(TEST_DECISIONS, output)
    return TEST_DECISIONS


async def audit_episodic_boundary_v3(repeats: int = 3) -> Path:
    """Repeat only the known dev boundary item with its original candidate set."""
    if not 1 <= repeats <= 5:
        raise ValueError("bounded repeat count required")
    settings = _settings()
    original = next(row for row in read_jsonl(REPORTS / "memory_eval_v2_results.jsonl")
                    if row["case_id"] == "memory-v2-episodic_only_probe-120")
    source = next(row for row in read_jsonl(DATASETS / "memory_eval_v2.jsonl")
                  if row["case_id"] == original["case_id"])
    items = [RetrievedMemory.model_validate(row["retrieved"])
             for row in source["candidate_memories"]]
    request = MemoryRetrievalRequest(user_id=source["user_id"], query=source["query"],
                                     scene=source["scene"], top_k=8)
    result = MemoryRetrievalResult(user_id=source["user_id"], items=items)
    target_id = source["gold_memory_ids"][0]
    output = [{"case_id": original["case_id"], "iteration": "v2_original",
               "target_id": target_id, "score": original["jev_scores"][target_id],
               "response_model": original["response_model"],
               "evaluation_timestamp": original["evaluation_timestamp"],
               "candidate_ids": original["baseline_selected_ids"]}]
    client = JevClient(settings)
    for index in range(repeats):
        recorder = RecordingClient(client)
        await filter_memories(request, result, settings=settings, client=recorder,
                              trace_id=f"jev-v3-boundary-{index}")
        response = recorder.responses[-1]
        answer = response.answers[_question_id(target_id)]
        output.append({"case_id": original["case_id"], "iteration": index + 1,
                       "target_id": target_id, "score": answer.noul,
                       "response_model": response.response_model,
                       "requested_model": settings.jev_model,
                       "evaluation_timestamp": datetime.now(UTC).isoformat(),
                       "api_latency_ms": response.latency_ms,
                       "candidate_ids": [item.memory_id for item in items]})
        write_jsonl(BOUNDARY_AUDIT, output)
    return BOUNDARY_AUDIT
