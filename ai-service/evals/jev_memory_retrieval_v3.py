"""Same-query legacy versus lightweight hybrid MemoryRetriever replay."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from app.agent.memory.models import MemoryRecord, MemoryRetrievalRequest
from app.agent.memory.retriever import MemoryRetriever
from evals.jev_memory_diagnostic_v3 import EPISODE_CACHE, QUERY_CACHE
from evals.jev_shadow_v1 import DATASETS, REPORTS, DockerPsqlReadOnlyStore, read_jsonl, write_jsonl
from evals.jev_synthetic_memory_v2 import _delete_sql, _insert_sql, _psql_script, synthetic_memories
from evals.jev_targeted_v3 import split_for

K_VALUES = (3, 5, 8, 10, 20)
DEV_RESULTS = REPORTS / "memory_retrieval_dev_v3.jsonl"
TEST_RESULTS = REPORTS / "memory_retrieval_test_v3.jsonl"
CALIBRATION = REPORTS / "memory_retrieval_calibration_v3.jsonl"


class LegacyMemoryRetriever(MemoryRetriever):
    """Frozen pre-v3 ranking, without changing the production store or embedding path."""

    @staticmethod
    def _profile_relevance(memory: MemoryRecord, request: MemoryRetrievalRequest) -> float:
        haystack = " ".join([memory.memory_key, memory.content,
                             str(memory.structured_content.get("scene") or "")]).casefold()
        terms = [term.casefold() for term in (request.intent, request.scene) if term and term.strip()]
        if not terms:
            return .75
        return 1.0 if any(term in haystack for term in terms) else .65

    @staticmethod
    def _episode_relevance(memory: MemoryRecord, relevance: float,
                           request: MemoryRetrievalRequest) -> float:
        del memory, request
        return relevance


class CachedEmbedder:
    def __init__(self) -> None:
        self.vectors = {row["case_id"]: row["vector"] for row in read_jsonl(QUERY_CACHE)}
        self.by_query = {row["query"]: self.vectors[row["case_id"]]
                         for row in read_jsonl(DATASETS / "memory_eval_v2.jsonl")
                         if row["retrieval_mode"] == "default"}

    def embed_query(self, query: str) -> list[float]:
        return self.by_query[query]


def run_memory_retrieval_v3(split: str) -> Path:
    if split not in {"dev", "test"}:
        raise ValueError("split must be dev or test")
    memories = synthetic_memories()
    episode_vectors = {row["memory_id"]: row["vector"] for row in read_jsonl(EPISODE_CACHE)}
    if len(episode_vectors) != 30:
        raise ValueError("frozen episodic embeddings are required")
    cases = [case for case in read_jsonl(DATASETS / "memory_eval_v2.jsonl")
             if case["retrieval_mode"] == "default"
             and split_for("memory")[case["case_id"]] == split]
    store = DockerPsqlReadOnlyStore()
    embedder = CachedEmbedder()
    retrievers = {"legacy": LegacyMemoryRetriever(store, embedder=embedder),
                  "hybrid": MemoryRetriever(store, embedder=embedder)}
    path = DEV_RESULTS if split == "dev" else TEST_RESULTS
    timestamp = datetime.now(UTC).isoformat()
    output: list[dict[str, Any]] = []
    _psql_script(_insert_sql(memories, episode_vectors))
    try:
        for case in cases:
            for k in K_VALUES:
                request = MemoryRetrievalRequest(user_id=case["user_id"], query=case["query"],
                                                 scene=case["scene"], top_k=k)
                for strategy, retriever in retrievers.items():
                    start = perf_counter()
                    result = retriever.retrieve(request)
                    elapsed = (perf_counter() - start) * 1000
                    output.append({"case_id": case["case_id"], "split": split,
                                   "query": case["query"], "target_id": case["gold_memory_ids"][0],
                                   "strategy": strategy, "top_k": k,
                                   "candidate_ids": [item.memory_id for item in result.items],
                                   "candidates": [item.model_dump(mode="json") for item in result.items],
                                   "retrieval_latency_ms": round(elapsed, 2),
                                   "evaluation_timestamp": timestamp})
            write_jsonl(path, output)
    finally:
        _psql_script(_delete_sql())
    return path


def calibrate_memory_retrieval_v3() -> Path:
    rows = read_jsonl(DEV_RESULTS)
    if len(rows) != 51 * len(K_VALUES) * 2:
        raise ValueError("complete Memory dev replay required")
    candidates = []
    for k in K_VALUES:
        for strategy in ("legacy", "hybrid"):
            group = [row for row in rows if row["top_k"] == k and row["strategy"] == strategy]
            hits = sum(row["target_id"] in row["candidate_ids"] for row in group)
            candidates.append({"strategy": strategy, "top_k": k, "cases": len(group),
                               "hits": hits, "recall": hits / len(group),
                               "mean_candidates": sum(len(row["candidate_ids"]) for row in group) / len(group)})
    write_jsonl(CALIBRATION, candidates)
    return CALIBRATION
