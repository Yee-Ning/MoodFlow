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

    def test_context_loop_public_history_replay_and_conflict_are_durable(self):
        from concurrent.futures import ThreadPoolExecutor

        from app.agent.foundation.long_horizon.history_writer import HistoryWriter
        from app.agent.foundation.long_horizon.models import ContinuityIdentity, PublicTaskResult

        store = PostgresContinuityStore(self.database_url)
        service = LongHorizonContextService(store)
        session = service.start_task(f"handoff:v1:postgres:{uuid4().hex}")
        identity = ContinuityIdentity(
            conversation_id=session.conversation_id, continuity_task_id=session.task_id,
            session_id=session.session_id, request_id="request:postgres", graph_mode="handoff", run_id="run:postgres",
        )
        writer = HistoryWriter(store)
        result = PublicTaskResult(
            run_id=identity.run_id, route_task_id="route:one", route_key="worldline", status="success", content="public",
        )
        try:
            with ThreadPoolExecutor(max_workers=4) as executor:
                receipts = list(executor.map(lambda _: writer.task_result(identity, result), range(8)))
            self.assertEqual(len({item.event_id for item in receipts}), 1)
            self.assertEqual(len({item.event_seq for item in receipts}), 1)
            self.assertEqual(store.query_task_history(session.task_id, after_seq=receipts[0].event_seq), [])
            with self.assertRaisesRegex(ValueError, "key conflict"):
                writer.task_result(identity, result.model_copy(update={"content": "changed"}))
            self.assertEqual(store.get_history_event(receipts[0].event_id).payload["content"], "public")
            self.assertEqual(len(service.query_task_history(session.task_id, event_type=HistoryEventType.ERROR)), 1)
        finally:
            store.close()

    def test_context_loop_v2_transfer_checkpoint_and_rollover_survive_reopen(self):
        import asyncio
        from types import SimpleNamespace

        from app.agent.checkpointing import AsyncCompatiblePostgresSaver, create_checkpoint_serializer
        from app.agent.foundation.long_horizon.history_writer import HistoryWriter
        from app.agent.foundation.long_horizon.models import ContinuityIdentity, HandoffDraftV2, LiveSnapshot
        from app.agent.foundation.long_horizon.revalidation import RealityRevalidator
        from app.agent.foundation.long_horizon.recovery import RecoveryPlanner
        from app.agent.foundation.routing import RouteDecision, RouteKey, RouteTask
        from app.agent.handoff.continuity import AgentTransferJournal, JournalCheckpointer, ProgressProjector
        from app.agent.handoff.registry import AgentKey
        from app.agent.handoff.state import new_run
        from tests.context_loop_helpers import checkpoint, transfer

        store = PostgresContinuityStore(self.database_url)
        service = LongHorizonContextService(store)
        suffix = uuid4().hex
        session = service.start_task(f"handoff:v1:postgres:{suffix}")
        run = new_run(f"run:{suffix}", "探索音乐", RouteDecision(is_complete=True, tasks=[
            RouteTask(task_id="route:one", route_key=RouteKey.WORLDLINE, input_text="探索音乐"),
        ]), started_at=datetime.now(UTC), entry_agent=AgentKey.GENERAL_CHAT)
        identity = ContinuityIdentity(conversation_id=session.conversation_id, continuity_task_id=session.task_id,
                                      session_id=session.session_id, request_id="request:postgres", graph_mode="handoff", run_id=run.run_id)
        journal = AgentTransferJournal(HistoryWriter(store))
        try:
            with Connection.connect(self.database_url, autocommit=True, row_factory=dict_row, prepare_threshold=0) as connection:
                saver = JournalCheckpointer(AsyncCompatiblePostgresSaver(connection, serde=create_checkpoint_serializer()), journal)
                h = SimpleNamespace(store=store, service=service, session=session, run=run, identity=identity, journal=journal,
                                    saver=saver, config={"configurable": {"thread_id": identity.conversation_id, "session_id": identity.session_id}}, messages=[])
                asyncio.run(transfer(h, "curator"))
                snapshot = checkpoint(h)
                draft = ProgressProjector(service).project_progress(session, draft=HandoffDraftV2(goal="探索音乐", current_stage="执行中"))
                rolled = service.rollover(session.session_id, draft, RolloverReason.EXPLICIT_CHECKPOINT, automatic=True)
        finally:
            store.close()
        reopened = PostgresContinuityStore(self.database_url)
        try:
            projection = reopened.get_handoff(rolled.handoff.handoff_id)
            self.assertEqual(projection.agent_progress.last_agent, "curator")
            self.assertEqual(projection.agent_progress.accepted_hop_count, 1)
            self.assertEqual(reopened.active_session(session.task_id).previous_session_id, session.session_id)
            report = RealityRevalidator(reopened).revalidate(projection, LiveSnapshot(), snapshot)
            plan = RecoveryPlanner().decide(report, snapshot)
            self.assertEqual(plan.action, "resume_target")
            self.assertEqual(plan.resume_checkpoint_ref, snapshot.ref)
            with Connection.connect(self.database_url, autocommit=True, row_factory=dict_row, prepare_threshold=0) as connection:
                loaded = AsyncCompatiblePostgresSaver(connection, serde=create_checkpoint_serializer()).get_tuple(h.config)
                self.assertEqual(loaded.checkpoint["channel_values"]["handoff_run"]["run_id"], run.run_id)
        finally:
            reopened.close()

    def test_context_loop_cursor_cannot_pass_an_uncommitted_event_in_same_task(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event

        from app.agent.foundation.long_horizon.history_writer import HistoryWriter
        from app.agent.foundation.long_horizon.models import ContinuityIdentity, PublicTaskResult

        store = PostgresContinuityStore(self.database_url)
        service = LongHorizonContextService(store)
        session = service.start_task(f"handoff:v1:cursor:{uuid4().hex}")
        identity = ContinuityIdentity(conversation_id=session.conversation_id, continuity_task_id=session.task_id,
                                      session_id=session.session_id, request_id="request:cursor", graph_mode="handoff", run_id="run:cursor")
        writer = HistoryWriter(store)
        entered, release = Event(), Event()
        def first():
            with store.transaction():
                receipt = writer.task_result(identity, PublicTaskResult(run_id=identity.run_id, route_task_id="first", route_key="worldline", status="success"))
                entered.set()
                if not release.wait(10):
                    raise RuntimeError("test transaction release timed out")
            return receipt
        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                early = executor.submit(first)
                self.assertTrue(entered.wait(10))
                later = executor.submit(writer.task_result, identity, PublicTaskResult(run_id=identity.run_id, route_task_id="later", route_key="worldline", status="success"))
                self.assertFalse(later.done())
                self.assertEqual(store.query_task_history(session.task_id), [])
                release.set()
                early_receipt, later_receipt = early.result(timeout=10), later.result(timeout=10)
            self.assertLess(early_receipt.event_seq, later_receipt.event_seq)
            self.assertEqual([e.event_id for e in store.query_task_history(session.task_id, after_seq=early_receipt.event_seq)], [later_receipt.event_id])
        finally:
            release.set()
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
                "session_handoff",
                "reality_revalidation",
                "context_trace",
            }.issubset(set(state["history_types"]))
        )


if __name__ == "__main__":
    unittest.main()
