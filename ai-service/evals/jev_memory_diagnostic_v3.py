"""Exact cosine versus HNSW diagnosis over temporary v2 synthetic memories."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from math import sqrt
from pathlib import Path
from time import perf_counter
from typing import Any

from app.agent.memory.embeddings import QwenMemoryEmbedder
from app.agent.memory.postgres_store import _vector_text
from evals.jev_shadow_v1 import DATASETS, LOCAL, REPORTS, read_jsonl, write_jsonl
from evals.jev_synthetic_memory_v2 import _delete_sql, _insert_sql, _psql_script, synthetic_memories
from evals.jev_targeted_v3 import split_for

QUERY_CACHE = LOCAL / "jev_memory_v3_query_vectors.jsonl"
EPISODE_CACHE = LOCAL / "jev_memory_v3_episode_vectors.jsonl"
DIAGNOSTIC = REPORTS / "memory_exact_hnsw_v3.jsonl"


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _batch_search(cases: list[dict[str, Any]], vectors: dict[str, list[float]],
                  mode: str, limit: int = 20) -> dict[str, list[str]]:
    if mode not in {"exact", "hnsw", "planner"}:
        raise ValueError("invalid search mode")
    setup = {"exact": "SET LOCAL enable_indexscan=off; SET LOCAL enable_bitmapscan=off;",
             "hnsw": "SET LOCAL enable_seqscan=off; SET LOCAL enable_bitmapscan=off; "
                     "SET LOCAL enable_sort=off; SET LOCAL hnsw.ef_search=100;",
             "planner": ""}[mode]
    sql = ["BEGIN;", setup]
    for case in cases:
        vector = _literal(_vector_text(vectors[case["case_id"]]) or "[]")
        user = _literal(case["user_id"])
        scene = case.get("scene")
        scene_clause = (" AND (structured_content->>'scene' IS NULL OR "
                        f"structured_content->>'scene'={_literal(scene)})") if scene else ""
        sql.append(
            "SELECT row_to_json(result) FROM (SELECT "
            f"{_literal(case['case_id'])} AS case_id, memory_id, "
            f"embedding <=> {vector}::vector AS distance "
            "FROM agent_long_term_memory "
            f"WHERE user_id={user} AND memory_type='episodic' AND status='active' "
            f"AND embedding IS NOT NULL{scene_clause} "
            f"ORDER BY embedding <=> {vector}::vector LIMIT {limit}) result;"
        )
    sql.append("COMMIT;")
    process = subprocess.run(
        ["docker", "exec", "-i", "moodflow-postgres", "psql", "-U", "root",
         "-d", "moodflow_agent", "-X", "-A", "-t", "-q", "-v", "ON_ERROR_STOP=1"],
        input="\n".join(sql), capture_output=True, text=True, encoding="utf-8", timeout=240,
        check=False,
    )
    if process.returncode:
        raise RuntimeError(f"{mode} pgvector diagnostic failed: {process.stderr[-500:]}")
    rows: dict[str, list[str]] = {case["case_id"]: [] for case in cases}
    for line in process.stdout.splitlines():
        if line.startswith("{"):
            item = json.loads(line)
            rows[item["case_id"]].append(item["memory_id"])
    return rows


def run_memory_exact_hnsw_v3() -> Path:
    """Use actual PostgreSQL vector operators; clean only dedicated synthetic IDs."""
    memories = synthetic_memories()
    cases = [case for case in read_jsonl(DATASETS / "memory_eval_v2.jsonl")
             if case["retrieval_mode"] == "default"]
    splits = split_for("memory")
    embedder = QwenMemoryEmbedder()
    episodes = [row for row in memories if row["memory_type"] == "episodic"]
    episode_cache = {row["memory_id"]: row["vector"] for row in read_jsonl(EPISODE_CACHE)} if EPISODE_CACHE.exists() else {}
    if len(episode_cache) != len(episodes):
        episode_vectors = embedder.embed_documents([row["content"] for row in episodes])
        write_jsonl(EPISODE_CACHE, [{"memory_id": row["memory_id"], "vector": vector}
                                  for row, vector in zip(episodes, episode_vectors, strict=True)])
    else:
        episode_vectors = [episode_cache[row["memory_id"]] for row in episodes]
    if len(episode_vectors) != len(episodes):
        raise ValueError("episodic embedding count mismatch")
    corpus_vectors = {row["memory_id"]: vector for row, vector in zip(episodes, episode_vectors, strict=True)}
    episode_norms = [sqrt(sum(value * value for value in vector)) for vector in episode_vectors]
    cache = {row["case_id"]: row["vector"] for row in read_jsonl(QUERY_CACHE)} if QUERY_CACHE.exists() else {}
    for case in cases:
        if case["case_id"] not in cache:
            cache[case["case_id"]] = embedder.embed_query(case["query"])
            write_jsonl(QUERY_CACHE, [{"case_id": key, "vector": value} for key, value in cache.items()])
    output: list[dict[str, Any]] = []
    _psql_script(_insert_sql(memories, corpus_vectors))
    try:
        timings = {}
        results = {}
        for mode in ("exact", "planner", "hnsw"):
            start = perf_counter()
            results[mode] = _batch_search(cases, cache, mode)
            timings[mode] = round((perf_counter() - start) * 1000 / len(cases), 2)
        timestamp = datetime.now(UTC).isoformat()
        for case in cases:
            cid = case["case_id"]
            output.append({"case_id": cid, "split": splits[cid], "query": case["query"],
                           "target_id": case["gold_memory_ids"][0],
                           "baseline_top8_ids": [item["memory_id"] for item in case["candidate_memories"]],
                           "exact_ids": results["exact"][cid],
                           "planner_ids": results["planner"][cid],
                           "forced_hnsw_ids": results["hnsw"][cid],
                           "query_vector_dim": len(cache[cid]),
                           "document_vector_dim": len(episode_vectors[0]),
                           "query_vector_norm": sqrt(sum(value * value for value in cache[cid])),
                           "document_vector_norm_min": min(episode_norms),
                           "document_vector_norm_max": max(episode_norms),
                           "batch_avg_query_ms": timings,
                           "evaluation_timestamp": timestamp})
        write_jsonl(DIAGNOSTIC, output)
    finally:
        _psql_script(_delete_sql())
    return DIAGNOSTIC
