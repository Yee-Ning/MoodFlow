"""Replay frozen diagnostic inputs through the existing and Jev decision paths.

This module is evaluation only. It never changes the production shadow flag.
"""

from __future__ import annotations

import asyncio
import gzip
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.agent.decision.jev.client import DecisionResponse, JevClient
from app.agent.decision.jev.context_selector import SELECTABLE_TYPES, select_context
from app.agent.decision.jev.memory_filter import _question_id as memory_question_id, filter_memories
from app.agent.decision.jev.router import compare_route
from app.agent.foundation.context_engineering import ContextCollector, ContextSelector, POLICIES
from app.agent.foundation.hybrid_routing import create_hybrid_router
from app.agent.foundation.routing import RouteDecision, RouteKey
from app.config.settings import get_settings

ROOT = Path(__file__).resolve().parents[1]
DATASETS = ROOT / "evals" / "datasets"
REPORTS = ROOT / "evals" / "reports"
LOCAL = ROOT / "evals" / "local"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def prepare_router() -> Path:
    # Manual held-out rows only. manual_test duplicates test and is not counted twice.
    rows: dict[str, dict[str, Any]] = {}
    audit_path = ROOT / "artifacts" / "intent_model" / "error_analysis.jsonl"
    historical_failures = {row["id"] for row in read_jsonl(audit_path)} if audit_path.exists() else set()
    for split in ("test", "val", "manual_test"):
        for source in read_jsonl(ROOT / "data" / "intent" / "splits" / f"{split}.jsonl"):
            if source["source"] != "manual" or source["id"] in rows:
                continue
            rows[source["id"]] = {
                "case_id": source["id"], "query": source["text"],
                "recent_context": [],
                "gold_route": sorted({label.lower() for label in source["labels"]}),
                "label_source": f"data/intent/splits/{split}.jsonl",
                "tags": (["ambiguous"] if source["is_multi_intent"] else [])
                + (["boundary"] if source["is_hard_case"] else ["easy"])
                + (["historically_failed"] if source["id"] in historical_failures else []),
            }
    rows["foundation-e2e-current-song-followup"] = {
        "case_id": "foundation-e2e-current-song-followup", "query": "现在这首呢",
        "recent_context": [{"role": "user", "content": "分析这首歌"}],
        "gold_route": ["current_song"],
        "label_source": "tests/test_foundation_e2e_acceptance.py:test_current_song_normal_and_context_change",
        "tags": ["multi_turn", "boundary", "context_not_consumed_by_router"],
    }
    rows["foundation-e2e-general-chat-followup"] = {
        "case_id": "foundation-e2e-general-chat-followup", "query": "接着聊",
        "recent_context": [{"role": "user", "content": "你好"}],
        "gold_route": ["general_chat"],
        "label_source": "tests/test_foundation_e2e_acceptance.py:test_general_chat_normal_and_history",
        "tags": ["multi_turn", "boundary", "context_not_consumed_by_router"],
    }
    path = DATASETS / "jev_router_diagnostic_v1.jsonl"
    write_jsonl(path, list(rows.values()))
    return path


def prepare_context() -> Path:
    gold = {row["case_id"]: row for row in read_jsonl(DATASETS / "rag_eval_gold_v4.frozen.jsonl")}
    traces = {row["case_id"]: row for row in read_jsonl(
        REPORTS / "gold_v4_hybrid_shadow_trace_50_20260917.jsonl.gz")}
    selected = [row for row in gold.values() if row["expected_outcome"] == "answerable"][:29]
    selected += [next(row for row in gold.values() if row["expected_outcome"] == "insufficient_evidence")]
    rows: list[dict[str, Any]] = []
    for case in selected:
        trace = traces[case["case_id"]]
        evidence = [
            {"parent_id": item["parent_id"], "source_id": item.get("source_id"),
             "title": item.get("title", ""), "context": item.get("content", "")}
            for item in trace["final_context"]
        ]
        pool = ContextCollector().collect(
            query=case["query"], system="Answer using the available evidence.",
            rag_pack=SimpleNamespace(items=evidence),
        )
        relevant = set(case["relevant_parent_ids"])
        labels = {
            item.id: "required" if item.required else
            "useful" if item.metadata.get("evidence_id") in relevant else "irrelevant"
            for item in pool
        }
        rows.append({
            "case_id": case["case_id"], "query": case["query"],
            "source_trace": "gold_v4_hybrid_shadow_trace_50_20260917.jsonl.gz",
            "label_source": "rag_eval_gold_v4.frozen.jsonl:relevant_parent_ids",
            "gold_outcome": case["expected_outcome"],
            "items": [
                {"context_id": item.id, "source_type": item.type,
                 "content": item.content, "gold_label": labels[item.id],
                 "required": item.required, "token_count": item.token_count,
                 "metadata": item.metadata, "references": item.references}
                for item in pool
            ],
        })
    path = DATASETS / "jev_context_diagnostic_v1.jsonl"
    write_jsonl(path, rows)
    return path


class RecordingClient:
    def __init__(self, delegate: JevClient):
        self.delegate = delegate
        self.responses: list[DecisionResponse] = []

    async def decide(self, state: Any, questions: Any) -> DecisionResponse:
        response = await self.delegate.decide(state, questions)
        self.responses.append(response)
        return response


def _run_metadata(recorder: RecordingClient, timestamp: str) -> dict[str, Any]:
    response = recorder.responses[-1] if recorder.responses else None
    return {
        "requested_model": response.requested_model if response else "jev-latest",
        "response_model": response.response_model if response else None,
        "evaluation_timestamp": timestamp,
        "response_timestamps": [r.evaluation_timestamp for r in recorder.responses],
        "latency_ms": sum(r.latency_ms for r in recorder.responses) if recorder.responses else None,
    }


async def run_router(limit: int | None = None) -> Path:
    settings = get_settings()
    if not settings.jev_shadow_mode or settings.jev_model != "jev-latest":
        raise RuntimeError("evaluation requires JEV_SHADOW_MODE=true and JEV_MODEL=jev-latest")
    router = create_hybrid_router(device="cpu")
    client = JevClient(settings)
    cases = read_jsonl(DATASETS / "jev_router_diagnostic_v1.jsonl")[:limit]
    timestamp = datetime.now(UTC).isoformat()
    output: list[dict[str, Any]] = []
    path = REPORTS / "jev_router_shadow_v1.jsonl"
    for case in cases:
        baseline: RouteDecision | None = None
        baseline_error: str | None = None
        started = asyncio.get_running_loop().time()
        try:
            baseline = await router.route(case["query"])
        except Exception as exc:
            baseline_error = type(exc).__name__
        baseline_latency = round((asyncio.get_running_loop().time() - started) * 1000, 2)
        recorder = RecordingClient(client)
        # The production comparator sees this same baseline RouteDecision.
        await compare_route(case["query"], baseline or RouteDecision(),
                            settings=settings, client=recorder, trace_id=case["case_id"])
        answer = recorder.responses[-1].answers.get("route") if recorder.responses else None
        existing = sorted({task.route_key.value for task in baseline.tasks}) if baseline else []
        jev = [answer.choice] if answer and answer.choice else []
        complex_route = (not baseline or not baseline.is_complete or len(baseline.tasks) != 1
                         or baseline.tasks[0].input_text.strip() != case["query"].strip()
                         or bool(baseline.tasks[0].depends_on))
        fallback_reason = ("baseline_error" if baseline_error else "complex_existing_route" if complex_route else
                           "jev_error" if answer is None else
                           "low_confidence" if answer.confidence is None or answer.confidence < settings.jev_router_confidence_threshold else None)
        output.append({**case, **_run_metadata(recorder, timestamp),
                       "decision_type": "router", "existing_route": existing,
                       "baseline_llm_model": settings.model,
                       "recent_context_consumed": False,
                       "jev_route": jev, "jev_confidence": answer.confidence if answer else None,
                       "jev_probabilities": answer.probabilities if answer else None,
                       "baseline_latency_ms": baseline_latency, "baseline_error": baseline_error,
                       "fallback_used": fallback_reason is not None,
                       "fallback_reason": fallback_reason})
        write_jsonl(path, output)
    return path


async def run_context(limit: int | None = None) -> Path:
    settings = get_settings()
    if not settings.jev_shadow_mode or settings.jev_model != "jev-latest":
        raise RuntimeError("evaluation requires JEV_SHADOW_MODE=true and JEV_MODEL=jev-latest")
    client = JevClient(settings)
    cases = read_jsonl(DATASETS / "jev_context_diagnostic_v1.jsonl")[:limit]
    timestamp = datetime.now(UTC).isoformat()
    output: list[dict[str, Any]] = []
    path = REPORTS / "jev_context_shadow_v1.jsonl"
    for case in cases:
        from app.agent.foundation.context_engineering import ContextItem
        items = [ContextItem(id=row["context_id"], type=row["source_type"],
                             source=row["source_type"], content=row["content"],
                             required=row["required"], token_count=row["token_count"],
                             metadata=row["metadata"], references=row["references"])
                 for row in case["items"]]
        selector = ContextSelector()
        clock = asyncio.get_running_loop()
        baseline_started = clock.time()
        baseline = selector.build(items, POLICIES[RouteKey.WORLDLINE])
        baseline_latency = round((clock.time() - baseline_started) * 1000, 2)
        recorder = RecordingClient(client)
        await select_context(items, case["query"], settings=settings, client=recorder,
                             trace_id=case["case_id"])
        scores: dict[str, float] = {}
        for response in recorder.responses:
            for item in items:
                from app.agent.decision.jev.context_selector import _question_id
                answer = response.answers.get(_question_id(item.id))
                if answer and answer.noul is not None:
                    scores[item.id] = answer.noul
        complete = len(scores) == sum(not item.required and item.type in SELECTABLE_TYPES for item in items)
        proposed_items = [item for item in items if item.required or item.type not in SELECTABLE_TYPES
                          or not complete or scores[item.id] >= settings.jev_context_select_threshold]
        enhanced_started = clock.time()
        enhanced = selector.build(proposed_items, POLICIES[RouteKey.WORLDLINE])
        enhanced_select_latency = round((clock.time() - enhanced_started) * 1000, 2)
        output.append({"case_id": case["case_id"], "query": case["query"],
                       "decision_type": "context_select", **_run_metadata(recorder, timestamp),
                       "candidate_ids": [item.id for item in items],
                       "gold_labels": {row["context_id"]: row["gold_label"] for row in case["items"]},
                       "baseline_selected_ids": [item.id for item in baseline.items],
                       "jev_preselect_ids": [item.id for item in proposed_items],
                       "jev_selected_ids": [item.id for item in enhanced.items],
                       "baseline_tokens": baseline.total_tokens, "jev_tokens": enhanced.total_tokens,
                       "baseline_latency_ms": baseline_latency,
                       "enhanced_select_latency_ms": enhanced_select_latency,
                       "jev_scores": scores, "fallback_used": not complete,
                       "fallback_reason": None if complete else "partial_or_failed_decision"})
        write_jsonl(path, output)
    return path


MEMORY_QUERIES = (
    ("artist", "我平时喜欢哪位歌手？", {"artist": True, "live": False, "slow": False}),
    ("no_live", "以后推荐歌曲可以推荐现场版吗？", {"artist": False, "live": True, "slow": False}),
    ("slow_workout", "健身时我对慢歌有什么反应？", {"artist": False, "live": False, "slow": True}),
    ("workout_recs", "给我推荐适合健身的歌曲，不要现场版", {"artist": "uncertain", "live": True, "slow": True}),
    ("general_recs", "按我的偏好推荐几首歌", {"artist": True, "live": True, "slow": "uncertain"}),
    ("beethoven", "贝多芬第五交响曲的首演是哪一年？", {"artist": False, "live": False, "slow": False}),
    ("lyrics", "分析当前播放歌曲的歌词", {"artist": False, "live": False, "slow": False}),
    ("concert", "推荐周杰伦的现场版歌曲", {"artist": True, "live": True, "slow": False}),
    ("fast_workout", "健身时请给我节奏快的歌", {"artist": "uncertain", "live": "uncertain", "slow": True}),
    ("recap", "总结一下你记得的我的音乐偏好", {"artist": True, "live": True, "slow": True}),
)


def _memory_kind(content: str) -> str:
    # These three phrases are the current local demo corpus. Unknown content is
    # excluded rather than assigned a guessed relevance label.
    lookup = {"用户喜欢周杰伦": "artist", "用户要求以后不要推荐现场版": "live",
              "健身推荐里连续跳过了慢歌": "slow"}
    return lookup.get(" ".join(content.split()), "unknown")


class DockerPsqlReadOnlyStore:
    """Read the same pgvector tables over local Docker's trusted Unix socket.

    Used only when the host DATABASE_URL cannot authenticate. Ranking still runs
    in the application's MemoryRetriever; PostgreSQL still performs vector search.
    """

    def __init__(self, container: str = "moodflow-postgres"):
        self.container = container

    @staticmethod
    def _quote(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    def _rows(self, query: str) -> list[dict[str, Any]]:
        import subprocess

        process = subprocess.run(
            ["docker", "exec", self.container, "psql", "-U", "root", "-d", "moodflow_agent",
             "-X", "-A", "-t", "-v", "ON_ERROR_STOP=1", "-c",
             f"SELECT row_to_json(result) FROM ({query}) AS result"],
            capture_output=True, text=True, encoding="utf-8", check=False, timeout=30,
        )
        if process.returncode:
            raise RuntimeError("Docker PostgreSQL read-only query failed")
        return [json.loads(line) for line in process.stdout.splitlines() if line.strip()]

    def user_ids(self) -> list[str]:
        rows = self._rows(
            "SELECT user_id FROM agent_long_term_memory WHERE status='active' "
            "GROUP BY user_id HAVING COUNT(*) FILTER (WHERE memory_type='profile') >= 2 "
            "ORDER BY user_id")
        return [str(row["user_id"]) for row in rows]

    def list_active_profiles(self, user_id: str, *, scene: str | None = None,
                             limit: int = 50) -> list[Any]:
        from app.agent.memory.postgres_store import PostgresMemoryStore

        scene_clause = (f" AND (structured_content->>'scene' IS NULL OR "
                        f"structured_content->>'scene'={self._quote(scene)})") if scene else ""
        rows = self._rows(
            f"SELECT {PostgresMemoryStore._columns()} FROM agent_long_term_memory "
            f"WHERE user_id={self._quote(user_id)} AND memory_type='profile' "
            f"AND status='active'{scene_clause} ORDER BY importance DESC, updated_at DESC "
            f"LIMIT {int(limit)}")
        return [PostgresMemoryStore._record(row) for row in rows]

    def has_active_episodes(self, user_id: str) -> bool:
        rows = self._rows(
            "SELECT EXISTS(SELECT 1 FROM agent_long_term_memory "
            f"WHERE user_id={self._quote(user_id)} AND memory_type='episodic' "
            "AND status='active' AND embedding IS NOT NULL) AS found")
        return bool(rows and rows[0]["found"])

    def search_active_episodes(self, user_id: str, embedding: list[float], *,
                               scene: str | None = None, limit: int = 20) -> list[Any]:
        from app.agent.memory.postgres_store import PostgresMemoryStore, _vector_text

        vector = self._quote(_vector_text(embedding) or "[]")
        scene_clause = (f" AND (structured_content->>'scene' IS NULL OR "
                        f"structured_content->>'scene'={self._quote(scene)})") if scene else ""
        rows = self._rows(
            f"SELECT {PostgresMemoryStore._columns()}, "
            f"1 - (embedding <=> {vector}::vector) AS similarity "
            f"FROM agent_long_term_memory WHERE user_id={self._quote(user_id)} "
            "AND memory_type='episodic' AND status='active' AND embedding IS NOT NULL"
            f"{scene_clause} ORDER BY embedding <=> {vector}::vector LIMIT {int(limit)}")
        return [(PostgresMemoryStore._record(row), max(0.0, min(1.0, float(row["similarity"]))))
                for row in rows]

    def close(self) -> None:
        pass


def prepare_memory() -> Path:
    import hashlib
    import psycopg
    from dotenv import dotenv_values

    from app.agent.memory.embeddings import QwenMemoryEmbedder
    from app.agent.memory.models import MemoryRetrievalRequest
    from app.agent.memory.postgres_store import PostgresMemoryStore
    from app.agent.memory.retriever import MemoryRetriever

    database_url = dotenv_values(ROOT / ".env").get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required for pgvector memory evaluation")
    try:
        with psycopg.connect(database_url, connect_timeout=5) as connection:
            user_ids = [row[0] for row in connection.execute(
                "SELECT user_id FROM agent_long_term_memory WHERE status='active' "
                "GROUP BY user_id HAVING COUNT(*) FILTER (WHERE memory_type='profile') >= 2 "
                "ORDER BY user_id")]
        store = PostgresMemoryStore(database_url)
        transport = "PostgresMemoryStore"
    except psycopg.OperationalError:
        store = DockerPsqlReadOnlyStore()
        user_ids = store.user_ids()
        transport = "Docker psql read-only transport"
    embedder = QwenMemoryEmbedder()

    class CachedEmbedder:
        def __init__(self):
            self.cache: dict[str, list[float]] = {}

        def embed_query(self, query: str) -> list[float]:
            if query not in self.cache:
                self.cache[query] = embedder.embed_query(query)
            return self.cache[query]

    retriever = MemoryRetriever(store, embedder=CachedEmbedder())
    rows: list[dict[str, Any]] = []
    try:
        for user_id in user_ids[:3]:
            alias = hashlib.sha256(str(user_id).encode()).hexdigest()[:12]
            for name, query, labels in MEMORY_QUERIES:
                request = MemoryRetrievalRequest(user_id=str(user_id), query=query, top_k=3)
                result = retriever.retrieve(request)
                candidates = []
                for item in result.items:
                    kind = _memory_kind(item.content)
                    candidates.append({"memory_id": item.memory_id,
                                       "memory_text": " ".join(item.content.split()),
                                       "gold_useful": labels.get(kind, "uncertain"),
                                       "retrieved": item.model_dump(mode="json")})
                rows.append({"case_id": f"memory-{alias}-{name}", "query": query,
                             "user_alias": alias, "candidates": candidates,
                             "label_source": "evaluator-curated local demo memory patterns",
                             "retrieval_transport": transport})
    finally:
        store.close()
    path = LOCAL / "jev_memory_diagnostic_v1.jsonl"
    write_jsonl(path, rows)
    return path


async def run_memory(limit: int | None = None) -> Path:
    from app.agent.memory.models import MemoryRetrievalRequest, MemoryRetrievalResult, RetrievedMemory

    settings = get_settings()
    if not settings.jev_shadow_mode or settings.jev_model != "jev-latest":
        raise RuntimeError("evaluation requires JEV_SHADOW_MODE=true and JEV_MODEL=jev-latest")
    cases = read_jsonl(LOCAL / "jev_memory_diagnostic_v1.jsonl")[:limit]
    client = JevClient(settings)
    timestamp = datetime.now(UTC).isoformat()
    path = LOCAL / "jev_memory_shadow_v1.jsonl"
    output = []
    for case in cases:
        items = [RetrievedMemory.model_validate(row["retrieved"]) for row in case["candidates"]]
        request = MemoryRetrievalRequest(user_id=case["user_alias"], query=case["query"], top_k=3)
        result = MemoryRetrievalResult(user_id=case["user_alias"], items=items)
        recorder = RecordingClient(client)
        await filter_memories(request, result, settings=settings, client=recorder,
                              trace_id=case["case_id"])
        scores = {}
        for response in recorder.responses:
            for item in items:
                answer = response.answers.get(memory_question_id(item.memory_id))
                if answer and answer.noul is not None:
                    scores[item.memory_id] = answer.noul
        complete = len(scores) == len(items)
        selected = [item.memory_id for item in items if not complete or
                    scores[item.memory_id] >= settings.jev_memory_filter_threshold]
        output.append({"case_id": case["case_id"], "query": case["query"],
                       "decision_type": "memory_filter", **_run_metadata(recorder, timestamp),
                       "retrieval_transport": case["retrieval_transport"],
                       "candidates": [{key: row[key] for key in ("memory_id", "memory_text", "gold_useful")}
                                      for row in case["candidates"]],
                       "baseline_selected_ids": [item.memory_id for item in items],
                       "jev_selected_ids": selected, "jev_scores": scores,
                       "old_keep_jev_keep_diff": [
                           {"memory_id": item.memory_id, "old_keep": True,
                            "jev_keep": item.memory_id in selected} for item in items],
                       "fallback_used": not complete,
                       "fallback_reason": None if complete else "partial_or_failed_decision"})
        write_jsonl(path, output)
    return path
