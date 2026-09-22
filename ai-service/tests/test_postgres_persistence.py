from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from psycopg import Connection, Error, sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row

from app.agent.foundation.long_horizon.models import (
    HandoffDraft,
    HistoryEventType,
    HistoryPointer,
    RolloverReason,
    TaskRecord,
)
from app.agent.foundation.long_horizon.postgres_store import PostgresContinuityStore
from app.agent.foundation.long_horizon.service import LongHorizonContextService
from app.agent.memory.models import (
    EvidenceStrength,
    ExtractedMemory,
    MemoryRetrievalRequest,
    MemoryType,
)
from app.agent.memory.postgres_store import PostgresMemoryStore
from app.agent.memory.retriever import MemoryRetriever
from app.agent.memory.service import MemoryService
from app.agent.postgres_migrations import postgres_supports_pgvector, run_all_migrations

BASE_DATABASE_URL = os.getenv("DATABASE_URL", "").strip()


@unittest.skipUnless(BASE_DATABASE_URL, "DATABASE_URL is required for PostgreSQL E2E")
class PostgresPersistenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database_name = f"moodflow_test_{uuid4().hex[:16]}"
        cls.base_parameters = conninfo_to_dict(BASE_DATABASE_URL)
        with Connection.connect(BASE_DATABASE_URL, autocommit=True) as connection:
            connection.execute(
                sql.SQL("CREATE DATABASE {}").format(
                    sql.Identifier(cls.database_name)
                )
            )
        cls.database_url = make_conninfo(
            **{**cls.base_parameters, "dbname": cls.database_name}
        )
        cls.has_pgvector = postgres_supports_pgvector(cls.database_url)
        run_all_migrations(cls.database_url, include_memory=cls.has_pgvector)

    @classmethod
    def tearDownClass(cls):
        with Connection.connect(
            BASE_DATABASE_URL,
            autocommit=True,
            row_factory=dict_row,
        ) as connection:
            connection.execute(
                """
                SELECT pg_terminate_backend(pid)
                FROM pg_stat_activity
                WHERE datname=%s AND pid <> pg_backend_pid()
                """,
                (cls.database_name,),
            )
            connection.execute(
                sql.SQL("DROP DATABASE IF EXISTS {}").format(
                    sql.Identifier(cls.database_name)
                )
            )

    def test_business_store_persists_all_continuity_records(self):
        self.assertEqual(
            run_all_migrations(
                self.database_url, include_memory=self.has_pgvector
            ),
            [],
        )
        store = PostgresContinuityStore(self.database_url)
        service = LongHorizonContextService(store)
        suffix = uuid4().hex
        session = service.start_task(
            f"conversation:{suffix}", task_id=f"task:{suffix}"
        )
        event = service.record_event(
            task_id=session.task_id,
            session_id=session.session_id,
            event_type=HistoryEventType.USER_MESSAGE,
            node="input",
            source="postgres_test",
            payload={"text": "persist me"},
        )
        artifact = service.create_artifact(
            task_id=session.task_id,
            session_id=session.session_id,
            artifact_type="test",
            payload={"rows": [1, 2]},
            projection={"count": 2},
        )
        rollover = service.rollover(
            session.session_id,
            HandoffDraft(
                goal="persist",
                constraints=["keep"],
                constraint_refs={
                    "keep": [
                        HistoryPointer(kind="event", target_id=event.event_id)
                    ]
                },
                current_stage="stored",
                next_actions=["recover"],
                artifact_refs=[artifact.ref],
            ),
            RolloverReason.EXPLICIT_CHECKPOINT,
        )
        store.close()

        reopened = PostgresContinuityStore(self.database_url)
        self.assertEqual(
            reopened.get_history_event(event.event_id).payload["text"],
            "persist me",
        )
        self.assertEqual(reopened.get_artifact(artifact.ref).projection["count"], 2)
        self.assertEqual(
            reopened.get_handoff(rollover.handoff.handoff_id).handoff_id,
            rollover.handoff.handoff_id,
        )
        self.assertEqual(
            reopened.active_session(session.task_id).session_id,
            rollover.new_session.session_id,
        )
        with Connection.connect(self.database_url, autocommit=True) as connection:
            with self.assertRaises(Error):
                connection.execute(
                    "UPDATE agent_history_event SET source='changed' "
                    "WHERE event_id=%s",
                    (event.event_id,),
                )
        reopened.close()

    def test_store_transaction_rolls_back_partial_writes(self):
        store = PostgresContinuityStore(self.database_url)
        suffix = uuid4().hex
        task_id = f"task:rollback:{suffix}"
        with self.assertRaisesRegex(RuntimeError, "force rollback"):
            with store.transaction():
                store.create_task(
                    TaskRecord(
                        task_id=task_id,
                        conversation_id=f"conversation:rollback:{suffix}",
                        created_at=datetime.now(UTC),
                    )
                )
                raise RuntimeError("force rollback")
        self.assertIsNone(store.get_task(task_id))
        store.close()

    def test_pgvector_memory_persists_and_isolates_users(self):
        if not self.has_pgvector:
            self.skipTest("PostgreSQL server does not provide pgvector")

        class Embedder:
            dimension = 1024

            def embed_documents(self, texts):
                return [self._vector(text) for text in texts]

            def embed_query(self, text):
                return self._vector(text)

            @staticmethod
            def _vector(text):
                vector = [0.0] * 1024
                vector[0] = 1.0 if "健身" in text else 0.1
                vector[1] = 1.0 if "慢歌" in text else 0.1
                return vector

        store = PostgresMemoryStore(self.database_url)
        embedder = Embedder()
        service = MemoryService(store, embedder=embedder)
        service.remember(
            user_id="postgres-user-a",
            source_thread_id="postgres-thread-a",
            candidate=ExtractedMemory(
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
            ),
        )
        retriever = MemoryRetriever(store, embedder=embedder)
        found = retriever.retrieve(
            MemoryRetrievalRequest(
                user_id="postgres-user-a",
                query="健身时不要慢歌",
                memory_type=MemoryType.EPISODIC,
            )
        )
        isolated = retriever.retrieve(
            MemoryRetrievalRequest(
                user_id="postgres-user-b",
                query="健身时不要慢歌",
                memory_type=MemoryType.EPISODIC,
            )
        )
        self.assertEqual(len(found.items), 1)
        self.assertEqual(isolated.items, [])
        store.close()

    def run_worker(
        self,
        environment: dict[str, str],
        mode: str,
        conversation_id: str,
        task_id: str,
    ) -> subprocess.CompletedProcess[str]:
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
        )
        if result.returncode:
            self.fail(f"restart worker failed:\n{result.stderr}")
        return result

    def test_restart_recovers_checkpoint_and_context_in_new_process(self):
        suffix = uuid4().hex
        conversation_id = f"conversation:restart:{suffix}"
        task_id = f"task:restart:{suffix}"
        environment = {
            **os.environ,
            "DATABASE_URL": self.database_url,
            "CHECKPOINT_BACKEND": "postgres",
            "PERSISTENCE_BACKEND": "postgres",
        }
        project_root = str(Path(__file__).resolve().parents[1])
        environment["PYTHONPATH"] = os.pathsep.join(
            filter(None, [project_root, environment.get("PYTHONPATH")])
        )
        created = self.run_worker(
            environment, "create", conversation_id, task_id
        )
        created_state = json.loads(created.stdout.strip().splitlines()[-1])
        recovered = self.run_worker(
            environment, "recover", conversation_id, task_id
        )
        state = json.loads(recovered.stdout.strip().splitlines()[-1])

        self.assertEqual(state["session_count"], 2)
        self.assertEqual(state["handoff_id"], created_state["handoff_id"])
        self.assertEqual(state["artifact_count"], 1)
        self.assertEqual(state["changed_fields"], ["current_song_id"])
        self.assertTrue(state["checkpoint_distinct_from_session"])
        self.assertTrue(state["context_within_budget"])
        self.assertEqual(
            state["before_steps"],
            ["router:initial", "retrieval:initial"],
        )
        self.assertEqual(
            state["continued_steps"],
            [
                "router:initial",
                "retrieval:initial",
                "router:continued",
                "retrieval:continued",
            ],
        )
        self.assertTrue(
            {
                "route_decision",
                "retrieval",
                "handoff",
                "reality_revalidation",
                "context_trace",
            }.issubset(set(state["history_types"]))
        )


if __name__ == "__main__":
    unittest.main()
