import pytest

from app.agent.foundation.user_profile import PostgresProfileStore
from app.agent.memory.postgres_store import PostgresMemoryStore
from app.agent.memory.projection import synchronize_profile
from app.agent.memory.service import MemoryService
from app.agent.postgres_migrations import run_business_migrations
from tests.test_context_loop_postgres_recovery import BASE, database
from tests.test_memory_hardening import artist

__all__ = ["database"]
pytestmark = pytest.mark.skipif(not BASE, reason="isolated PostgreSQL acceptance requires DATABASE_URL")


def test_profile_outbox_survives_restart_and_rebuilds_document(database):
    run_business_migrations(database, include_memory=True)
    store = PostgresMemoryStore(database)
    document = PostgresProfileStore(database)
    try:
        service = MemoryService(store)
        service.remember(user_id="u", source_thread_id="t", candidate=artist())
        pending = store.pending_profile_revision("u")
        assert pending and document.read("u") is None
        store.close()
        store = PostgresMemoryStore(database)
        assert store.pending_profile_revision("u") == pending
        assert synchronize_profile(store, document, "u")
        assert document.read("u").entries[0].value == "用户喜欢周杰伦"
        assert store.pending_profile_revision("u") is None
        assert document.read("other") is None
    finally:
        store.close()
        document.close()


def test_memory_fact_and_profile_outbox_rollback_together(database):
    run_business_migrations(database, include_memory=True)
    store = PostgresMemoryStore(database)
    try:
        original = store.mark_profile_dirty
        def unavailable(*args):
            original(*args)
            raise RuntimeError("interrupted")
        store.mark_profile_dirty = unavailable
        with pytest.raises(RuntimeError):
            MemoryService(store).remember(user_id="u", source_thread_id="t", candidate=artist())
        assert store.profile_records("u") == [] and store.pending_profile_revision("u") is None
    finally:
        store.close()


def test_message_receipt_is_durable_and_confirmations_count_distinct_messages(database):
    run_business_migrations(database, include_memory=True)
    store = PostgresMemoryStore(database)
    try:
        service = MemoryService(store)
        first = service.remember(user_id="u", source_thread_id="t", source_message_id="m", candidate=artist())
        store.close()
        store = PostgresMemoryStore(database)
        repeated = MemoryService(store).remember(user_id="u", source_thread_id="t", source_message_id="m", candidate=artist())
        assert repeated.action.value == "replayed" and repeated.memory.confirmations == 1
        assert repeated.memory.memory_id == first.memory.memory_id
        store.add_evidence = lambda _: (_ for _ in ()).throw(RuntimeError("interrupted"))
        with pytest.raises(RuntimeError):
            MemoryService(store).remember(user_id="u", source_thread_id="t", source_message_id="m2", candidate=artist())
        assert store.get_record("u", first.memory.memory_id).confirmations == 1
    finally:
        store.close()


def test_postgres_profile_scene_and_expiry_filters_match_local_store(database):
    from datetime import UTC, datetime, timedelta
    run_business_migrations(database, include_memory=True)
    store = PostgresMemoryStore(database)
    try:
        service = MemoryService(store)
        service.remember(user_id="u", source_thread_id="t", candidate=artist())
        service.remember(user_id="u", source_thread_id="t", candidate=artist().model_copy(update={"scene": "sleep"}))
        service.remember(user_id="u", source_thread_id="t", candidate=artist("expired").model_copy(
            update={"valid_until": datetime.now(UTC) - timedelta(days=1)}))
        assert len(store.list_active_profiles("u")) == 1
        assert len(store.list_active_profiles("u", scene="sleep")) == 2
        assert len(store.list_active_profiles("u", scene="workout")) == 1
    finally:
        store.close()


def test_postgres_forget_hides_old_profile_versions_and_preferences_survive_restart(database):
    run_business_migrations(database, include_memory=True)
    store, documents = PostgresMemoryStore(database), PostgresProfileStore(database)
    try:
        service = MemoryService(store)
        record = service.remember(user_id="u", source_thread_id="t", source_message_id="m", candidate=artist()).memory
        synchronize_profile(store, documents, "u")
        version = documents.read("u").version
        service.forget("u", record.memory_id)
        synchronize_profile(store, documents, "u")
        assert store.profile_records("u") == [] and documents.read("u", version=version).entries == ()
        service.set_enabled("u", False)
        store.close()
        store = PostgresMemoryStore(database)
        assert not store.preferences("u").enabled
        assert store.preferences("other").enabled
    finally:
        store.close()
        documents.close()


@pytest.mark.asyncio
async def test_postgres_job_restart_claim_fencing_and_durable_dispatch_limit(database):
    from app.agent.memory.jobs import MemoryJobWorker
    from app.agent.memory.models import MemoryJob
    from app.agent.memory.runtime import MemoryRuntime
    from types import SimpleNamespace
    from datetime import UTC, datetime, timedelta
    from unittest.mock import AsyncMock
    from tests.test_memory_jobs import response
    run_business_migrations(database, include_memory=True)
    store, documents = PostgresMemoryStore(database), PostgresProfileStore(database)
    try:
        runtime = MemoryRuntime(MemoryService(store), SimpleNamespace(), profile_store=documents)
        worker = MemoryJobWorker(runtime, provider=AsyncMock(return_value=response()))
        worker.start = lambda: None
        await worker.enqueue(user_id="u", source_thread_id="t", source_message_id="m", user_message="我喜欢周杰伦")
        store.close()
        store = PostgresMemoryStore(database)
        runtime = MemoryRuntime(MemoryService(store), SimpleNamespace(), profile_store=documents)
        worker = MemoryJobWorker(runtime, provider=AsyncMock(return_value=response()))
        assert await worker.process_one()
        assert store.profile_records("u")
        job = MemoryJob(job_id="fencing", user_id="u", source_thread_id="t", source_message_id="f",
            user_message="我喜欢周杰伦", source_digest="fixture", generation=0,
            budget={}, created_at=datetime.now(UTC))
        store.enqueue_job(job)
        claimed = store.claim_job("one")
        assert claimed.job_id == "fencing" and store.claim_job("two") is None
        with pytest.raises(ValueError, match="lease lost"):
            store.dispatch_job("fencing", "two")
        assert store.dispatch_job("fencing", "one") and store.dispatch_job("fencing", "one")
        assert not store.dispatch_job("fencing", "one")
        store._execute("UPDATE agent_memory_job SET lease_until=%s WHERE job_id=%s",
                       (datetime.now(UTC) - timedelta(seconds=1), "fencing"))
        assert store.claim_job("two").dispatches == 2
    finally:
        store.close()
        documents.close()
