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
