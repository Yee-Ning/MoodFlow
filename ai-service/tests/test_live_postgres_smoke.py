"""Opt-in, non-destructive smoke checks against the existing development database.

Run with RUN_LIVE_POSTGRES_SMOKE=1. Test records use unique smoke-prefixed IDs;
this suite never creates/drops databases or deletes pre-existing data.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from uuid import uuid4

from psycopg import Connection
from fastapi.testclient import TestClient

from app.agent.foundation.context_engineering import ContextPolicy, POLICIES, build_context
from app.agent.foundation.routing import RouteKey
from app.agent.memory.models import (
    EvidenceStrength,
    ExtractedMemory,
    MemoryRetrievalRequest,
    MemoryType,
)
from app.agent.memory.embeddings import QwenMemoryEmbedder
from app.agent.memory.postgres_store import PostgresMemoryStore
from app.agent.memory.retriever import MemoryRetriever
from app.agent.memory.service import MemoryService
from app.config.settings import load_runtime_environment
from app.main import app
from app.agent.checkpointing import checkpoint_backend
from app.agent.foundation.long_horizon.runtime import persistence_backend
from app.agent.memory.runtime import get_memory_runtime, memory_backend


class SmokeEmbedder:
    dimension = 1024

    def embed_documents(self, texts):
        return [self.embed_query(text) for text in texts]

    def embed_query(self, text):
        vector = [0.0] * self.dimension
        vector[0] = 1.0 if "健身" in text else 0.1
        vector[1] = 1.0 if "慢歌" in text else 0.1
        return vector


@unittest.skipUnless(
    os.getenv("RUN_LIVE_POSTGRES_SMOKE") == "1",
    "set RUN_LIVE_POSTGRES_SMOKE=1 to write smoke records to the development DB",
)
class LivePostgresSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        load_runtime_environment()
        cls.database_url = os.environ["DATABASE_URL"]
        with Connection.connect(cls.database_url) as connection:
            actual = connection.execute(
                "SELECT datcollversion = pg_database_collation_actual_version(oid) "
                "FROM pg_database WHERE datname = current_database()"
            ).fetchone()
            if not actual or not actual[0]:
                raise RuntimeError("database collation mismatch; smoke writes stopped")

    def test_application_lifespan_uses_postgres_backends(self) -> None:
        with TestClient(app) as client:
            self.assertEqual(client.get("/health").status_code, 200)
            self.assertEqual(checkpoint_backend(), "postgres")
            self.assertEqual(persistence_backend(), "postgres")
            self.assertEqual(memory_backend(), "postgres")
            self.assertIsInstance(get_memory_runtime().service.store, PostgresMemoryStore)

    def test_checkpoint_history_and_handoff_survive_process_restart(self) -> None:
        suffix = uuid4().hex
        conversation_id = f"smoke:conversation:{suffix}"
        task_id = f"smoke:task:{suffix}"
        environment = {
            **os.environ,
            "DATABASE_URL": self.database_url,
            "CHECKPOINT_BACKEND": "postgres",
            "PERSISTENCE_BACKEND": "postgres",
        }
        environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])

        def run(mode: str) -> dict:
            result = subprocess.run(
                [
                    sys.executable,
                    "tests/fixtures/postgres_restart_worker.py",
                    mode,
                    conversation_id,
                    task_id,
                ],
                env=environment,
                capture_output=True,
                text=True,
                check=True,
            )
            return json.loads(result.stdout.strip().splitlines()[-1])

        created = run("create")
        recovered = run("recover")
        self.assertEqual(recovered["handoff_id"], created["handoff_id"])
        self.assertEqual(recovered["session_count"], 2)
        self.assertEqual(recovered["artifact_count"], 1)
        self.assertTrue(recovered["checkpoint_distinct_from_session"])
        self.assertTrue(recovered["context_within_budget"])
        self.assertEqual(
            recovered["continued_steps"],
            ["router:initial", "retrieval:initial", "router:continued", "retrieval:continued"],
        )
        self.assertTrue(
            {"route_decision", "retrieval", "handoff"}.issubset(
                recovered["history_types"]
            )
        )

    def test_memory_cross_thread_isolation_sql_vector_and_context_budget(self) -> None:
        suffix = uuid4().hex
        user_id = f"smoke:user:{suffix}"
        other_user_id = f"smoke:other:{suffix}"
        source_thread_id = f"smoke:thread:one:{suffix}"
        embedder = SmokeEmbedder()
        store = PostgresMemoryStore(self.database_url)
        try:
            service = MemoryService(store, embedder=embedder)
            artist = ExtractedMemory(
                memory_type=MemoryType.PROFILE,
                category="artist",
                subject="周杰伦",
                preference="like",
                value="liked",
                content="用户喜欢周杰伦",
                evidence_quote="我喜欢周杰伦",
                evidence_strength=EvidenceStrength.EXPLICIT,
                confidence=0.98,
                importance=0.9,
            )
            avoid_live = ExtractedMemory(
                memory_type=MemoryType.PROFILE,
                category="format",
                subject="live_version",
                preference="avoid",
                value="avoid",
                content="用户要求以后不要推荐现场版",
                evidence_quote="以后不要推荐现场版",
                evidence_strength=EvidenceStrength.EXPLICIT,
                confidence=0.98,
                importance=0.9,
            )
            episode = ExtractedMemory(
                memory_type=MemoryType.EPISODIC,
                category="recommendation_feedback",
                subject="slow_songs",
                value="repeated_skip",
                content="健身推荐里连续跳过了慢歌",
                evidence_quote="健身推荐里连续跳过了慢歌",
                evidence_strength=EvidenceStrength.REPEATED_BEHAVIOR,
                confidence=0.9,
                importance=0.8,
                scene="workout",
            )
            for candidate in (artist, avoid_live, episode, artist):
                service.remember(
                    user_id=user_id,
                    source_thread_id=source_thread_id,
                    candidate=candidate,
                )
        finally:
            store.close()

        # A newly opened repository represents a subsequent thread/process.
        reopened = PostgresMemoryStore(self.database_url)
        try:
            retriever = MemoryRetriever(reopened, embedder=embedder)
            profiles = retriever.retrieve(
                MemoryRetrievalRequest(
                    user_id=user_id,
                    query="新线程推荐音乐",
                    memory_type=MemoryType.PROFILE,
                    top_k=10,
                )
            )
            self.assertEqual(len(profiles.items), 2)
            self.assertEqual(
                {item.structured_content["subject"] for item in profiles.items},
                {"周杰伦", "live_version"},
            )
            self.assertEqual(
                reopened.get_active(user_id, MemoryType.PROFILE, artist.memory_key).source_thread_id,
                source_thread_id,
            )
            self.assertEqual(
                retriever.retrieve(
                    MemoryRetrievalRequest(
                        user_id=other_user_id,
                        query="周杰伦 健身慢歌",
                        top_k=10,
                    )
                ).items,
                [],
            )

            episodes = retriever.retrieve(
                MemoryRetrievalRequest(
                    user_id=user_id,
                    query="健身时不要慢歌",
                    scene="workout",
                    memory_type=MemoryType.EPISODIC,
                )
            )
            self.assertEqual(len(episodes.items), 1)
            self.assertIn("慢歌", episodes.items[0].content)
            with Connection.connect(self.database_url) as connection:
                row = connection.execute(
                    "SELECT vector_dims(embedding), count(*) OVER () "
                    "FROM agent_long_term_memory "
                    "WHERE user_id=%s AND memory_type='episodic'",
                    (user_id,),
                ).fetchone()
            self.assertEqual(row, (1024, 1))

            policy = ContextPolicy(
                node=RouteKey.GENERAL_CHAT,
                total_input_budget=180,
                reserved_output_tokens=0,
                safety_margin=0,
                source_budgets={"long_term_memory": 1},
                allowed_types=POLICIES[RouteKey.GENERAL_CHAT].allowed_types,
            )
            context = build_context(
                RouteKey.GENERAL_CHAT,
                policy=policy,
                system="rules",
                query="推荐音乐",
                long_term_memory=[
                    item.model_dump(mode="json")
                    for item in [*profiles.items, *episodes.items]
                ],
            )
            self.assertLessEqual(context.total_tokens, context.budget)
            self.assertTrue(
                any(
                    entry.item_id.startswith("long_term_memory:")
                    and entry.reason == "long_term_memory_budget_exceeded"
                    for entry in context.trace.entries
                )
            )
        finally:
            reopened.close()

    def test_real_qwen_embedding_pgvector_roundtrip(self) -> None:
        suffix = uuid4().hex
        user_id = f"smoke:qwen:{suffix}"
        embedder = QwenMemoryEmbedder()
        store = PostgresMemoryStore(self.database_url)
        try:
            MemoryService(store, embedder=embedder).remember(
                user_id=user_id,
                source_thread_id=f"smoke:thread:qwen:{suffix}",
                candidate=ExtractedMemory(
                    memory_type=MemoryType.EPISODIC,
                    category="recommendation_feedback",
                    subject="workout_slow_songs",
                    value="repeated_skip",
                    content="健身推荐里连续跳过了慢歌",
                    evidence_quote="健身推荐里连续跳过了慢歌",
                    evidence_strength=EvidenceStrength.REPEATED_BEHAVIOR,
                    confidence=0.9,
                    importance=0.8,
                    scene="workout",
                ),
            )
            retrieved = MemoryRetriever(store, embedder=embedder).retrieve(
                MemoryRetrievalRequest(
                    user_id=user_id,
                    query="健身时不想听慢歌",
                    scene="workout",
                    memory_type=MemoryType.EPISODIC,
                )
            )
            self.assertEqual(len(retrieved.items), 1)
            self.assertIn("慢歌", retrieved.items[0].content)
            with Connection.connect(self.database_url) as connection:
                dimensions = connection.execute(
                    "SELECT vector_dims(embedding) FROM agent_long_term_memory "
                    "WHERE user_id=%s AND memory_type='episodic'",
                    (user_id,),
                ).fetchone()
            self.assertEqual(dimensions, (1024,))
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
