"""Deterministic offline memory policy/recall evaluation; no model/API credentials."""

import argparse
import json
from pathlib import Path
from statistics import mean
from time import perf_counter

from app.agent.memory.models import ExtractedMemory, MemoryRetrievalRequest
from app.agent.memory.policy import MemoryAdmissionError, validate_candidate
from app.agent.memory.retriever import MemoryRetriever
from app.agent.memory.service import MemoryService
from app.agent.memory.store import InMemoryMemoryStore

DATASET = Path(__file__).resolve().parents[1] / "evals" / "datasets" / "memory_policy_v1.json"


def evaluate(path=DATASET):
    cases = json.loads(Path(path).read_text(encoding="utf-8"))
    false_positive = false_negative = admitted = 0
    positive = sum(case["expected"] for case in cases)
    durations = []
    for case in cases:
        started = perf_counter()
        candidate = ExtractedMemory.model_validate(case["candidate"])
        try:
            validate_candidate(candidate, user_message=case["message"])
            accepted = True
        except MemoryAdmissionError:
            accepted = False
        admitted += accepted
        false_positive += accepted and not case["expected"]
        false_negative += not accepted and case["expected"]
        durations.append((perf_counter() - started) * 1000)
    store = InMemoryMemoryStore()
    service = MemoryService(store)
    base = ExtractedMemory.model_validate(cases[0]["candidate"])
    service.remember(user_id="fixture", source_thread_id="fixture", candidate=base)
    retriever = MemoryRetriever(store)
    queries = [("推荐周杰伦的歌", True), ("推荐适合通勤的歌", True), ("解释天气变化", False)]
    hits = sum(bool(retriever.retrieve(MemoryRetrievalRequest(user_id="fixture", query=query)).items) == expected
               for query, expected in queries)
    updated = base.model_copy(update={"preference": "dislike", "value": "disliked", "content": "用户不喜欢周杰伦",
                                      "evidence_quote": "我不喜欢周杰伦"})
    service.remember(user_id="fixture", source_thread_id="fixture", candidate=updated, user_message="我不喜欢周杰伦")
    current = store.list_active_profiles("fixture")
    return {
        "evaluation_kind": "offline_synthetic_policy_and_recall",
        "live_model_accuracy_measured": False,
        "admission_cases": len(cases), "admitted": admitted,
        "false_positive_rate": false_positive / max(1, len(cases) - positive),
        "false_negative_rate": false_negative / max(1, positive),
        "recall_hit_at_3": hits / len(queries),
        "conflict_resolution_accuracy": int(len(current) == 1 and current[0].structured_content["preference"] == "dislike"),
        "mean_admission_ms": round(mean(durations), 3), "p95_admission_ms": round(sorted(durations)[int(.95*(len(durations)-1))], 3),
        "note": "Synthetic fixture results do not measure live extraction model quality or real user correction rates.",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = evaluate()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return int(result["false_positive_rate"] != 0 or result["false_negative_rate"] != 0 or result["recall_hit_at_3"] != 1)


if __name__ == "__main__":
    raise SystemExit(main())
