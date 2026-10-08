"""Session/checkpoint/Journal caching against isolated PG and real Redis."""

from datetime import UTC, datetime
import json
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.base import empty_checkpoint
from psycopg.types.json import Jsonb
import pytest

from app.agent.cached_checkpointing import CachedPostgresSaver
from app.agent.checkpointing import AsyncCompatiblePostgresSaver, create_checkpoint_serializer
from app.agent.foundation.long_horizon.cached_postgres_store import CachedPostgresContinuityStore
from app.agent.foundation.long_horizon.history_writer import HistoryWriter
from app.agent.foundation.long_horizon.models import ContinuityIdentity, HandoffDraft, HandoffDraftV2, HistoryEventType, RolloverReason
from app.agent.foundation.long_horizon.postgres_store import PostgresContinuityStore
from app.agent.foundation.long_horizon.public_events import PublicText
from app.agent.foundation.long_horizon.service import LongHorizonContextService
from app.agent.foundation.routing import RouteKey
from app.agent.handoff.continuity import AgentTransferJournal, JournalCheckpointer, ProgressProjector
from app.agent.handoff.registry import AgentKey
from app.agent.handoff.state import new_run
from app.agent.session_cache import RedisSessionCache, SessionCacheConfig
from tests.context_loop_helpers import checkpoint, transfer
from tests.test_context_loop_postgres_recovery import database
from tests.test_redis_budget import redis_server

__all__ = ["database", "redis_server"]


@pytest.fixture
def hot(database, redis_server):
    cache = RedisSessionCache(SessionCacheConfig("redis://unused", 120, 20), client=redis_server)
    store = CachedPostgresContinuityStore(database, session_cache=cache)
    service = LongHorizonContextService(store)
    session = service.open_conversation("handoff:v1:session-cache-" + uuid4().hex)
    from app.agent.foundation.routing import RouteDecision, RouteTask
    plan = RouteDecision(is_complete=True, tasks=[RouteTask(task_id="task:one", route_key=RouteKey.WORLDLINE, input_text="音乐探索")])
    run = new_run("run:" + uuid4().hex, "音乐探索", plan, started_at=datetime.now(UTC), entry_agent=AgentKey.GENERAL_CHAT)
    identity = ContinuityIdentity(conversation_id=session.conversation_id, continuity_task_id=session.task_id,
        session_id=session.session_id, request_id="request:cache", graph_mode="handoff", run_id=run.run_id)
    writer = HistoryWriter(store)
    journal = AgentTransferJournal(writer)
    saver = CachedPostgresSaver(store._pool, session_cache=cache, serde=create_checkpoint_serializer())
    h = SimpleNamespace(cache=cache, store=store, service=service, session=session, run=run, identity=identity,
        writer=writer, journal=journal, checkpoint_saver=saver, saver=JournalCheckpointer(saver, journal),
        config={"configurable": {"thread_id": session.conversation_id, "session_id": session.session_id}}, messages=[])
    try:
        yield h
    finally:
        for row in store.sessions_for_task(session.task_id):
            redis_server.delete(cache.session_key(row.session_id), cache.view_key(row.session_id))
        store.close()


def sql_counter(h, monkeypatch):
    counter = Mock(wraps=h.store._pool.connection)
    monkeypatch.setattr(h.store._pool, "connection", counter)
    return counter


def test_record_miss_refill_then_hit_with_zero_pg_queries(hot, monkeypatch):
    h = hot
    count = sql_counter(h, monkeypatch)
    assert h.store.get_session(h.session.session_id) == h.session
    assert count.call_count > 0
    count.reset_mock()
    assert h.store.require_session(h.session.session_id) == h.session
    assert count.call_count == 0
    assert h.cache.client.type(h.cache.session_key(h.session.session_id)) == "hash"


def test_session_close_invalidates_after_commit_and_rollback_retains_cache(hot):
    h = hot
    key = h.cache.session_key(h.session.session_id)
    assert h.store.get_session(h.session.session_id).closed_at is None
    with pytest.raises(RuntimeError):
        with h.store.transaction():
            h.store.close_session(h.session.session_id, datetime.now(UTC).isoformat())
            assert h.store.get_session(h.session.session_id).closed_at is not None
            assert json.loads(h.cache.client.hget(key, "record:v1"))["closed_at"] is None
            raise RuntimeError("rollback")
    assert h.store.get_session(h.session.session_id).closed_at is None
    closed = h.store.close_session(h.session.session_id, datetime.now(UTC).isoformat())
    assert h.cache.client.hget(key, "record:v1") is None
    assert h.store.get_session(h.session.session_id) == closed


@pytest.mark.parametrize("legacy,version", [(False, 1), (False, 2), (True, 1), (True, 2)])
def test_archive_cache_preserves_exact_materialization_and_old_table(hot, monkeypatch, legacy, version):
    h = hot
    kind = HandoffDraft if version == 1 else HandoffDraftV2
    projection = h.service.build_handoff(h.session.session_id, kind(goal="音乐探索", current_stage="待执行", summary="公开摘要"))
    if legacy:
        h.store._execute("INSERT INTO agent_handoff(handoff_id,task_id,session_id,created_at,body) VALUES(%s,%s,%s,%s,%s)",
            (projection.handoff_id, projection.task_id, projection.session_id, projection.created_at, Jsonb(projection.model_dump(mode="json"))))
    else:
        h.store.put_handoff(projection)
    count = sql_counter(h, monkeypatch)
    first = h.store.read_session_projection(h.session.session_id, projection.handoff_id)
    assert count.call_count > 0 and type(first) is type(projection)
    count.reset_mock()
    assert h.store.read_session_projection(h.session.session_id, projection.handoff_id).model_dump(mode="json") == projection.model_dump(mode="json")
    assert count.call_count == 0
    body = h.store._one("SELECT body FROM agent_handoff WHERE handoff_id=%s", (projection.handoff_id,))["body"]
    assert body == (projection.model_dump(mode="json") if legacy else {
        **body, "storage_schema": "history-pointer:v1"})
    assert ("goal" in body) == legacy


@pytest.mark.asyncio
async def test_progress_hits_zero_pg_and_new_proposal_cannot_fake_committed_hop(hot, monkeypatch):
    h = hot
    projector = ProgressProjector(h.service)
    draft = HandoffDraftV2(goal="音乐探索", current_stage="执行中")
    first = projector.project_progress(h.session, draft=draft)
    count = sql_counter(h, monkeypatch)
    assert projector.project_progress(h.session, draft=draft) == first
    assert count.call_count == 0
    await transfer(h, "curator")
    proposed = projector.project_progress(h.session, draft=draft)
    assert proposed.agent_progress.accepted_hop_count == 0
    assert proposed.agent_progress.transfer_phase == "proposed"
    checkpoint(h)
    committed = projector.project_progress(h.session, draft=draft)
    assert committed.agent_progress.accepted_hop_count == 1
    assert committed.agent_progress.recent_run_chain == ("general_chat", "curator")
    count.reset_mock()
    assert projector.project_progress(h.session, draft=draft) == committed
    assert count.call_count == 0


def test_journal_rollback_keeps_view_then_commit_evicts_it(hot):
    h = hot
    projector = ProgressProjector(h.service)
    draft = HandoffDraftV2(goal="探索", current_stage="执行中")
    projector.project_progress(h.session, draft=draft)
    key = h.cache.view_key(h.session.session_id)
    before = h.cache.client.hgetall(key)
    with pytest.raises(RuntimeError):
        with h.store.transaction():
            h.writer.append_public_event(h.identity, "rollback", HistoryEventType.USER_MESSAGE, PublicText(text="公开新消息"))
            assert h.cache.client.hgetall(key) == before
            raise RuntimeError("rollback")
    assert h.cache.client.hgetall(key) == before
    h.writer.append_public_event(h.identity, "commit", HistoryEventType.USER_MESSAGE, PublicText(text="公开新消息"))
    assert set(h.cache.client.hgetall(key)) == {"__generation"}


def test_projection_cache_key_covers_draft_cursor_budget_and_policy(hot, monkeypatch):
    h = hot
    projector = ProgressProjector(h.service)
    draft = HandoffDraftV2(goal="探索", current_stage="执行中")
    projector.project_progress(h.session, draft=draft)
    count = sql_counter(h, monkeypatch)
    for options in ({"draft": draft.model_copy(update={"goal": "另一个目标"})},
                    {"draft": draft, "event_cursor": 1}, {"draft": draft, "projection_budget": 1500}):
        count.reset_mock()
        projector.project_progress(h.session, **options)
        assert count.call_count > 0
    h.service.policy = h.service.policy.model_copy(update={"handoff_token_budget": 1400})
    count.reset_mock()
    projector.project_progress(h.session, draft=draft)
    assert count.call_count > 0


def save_working(h, text):
    cp = empty_checkpoint()
    cp["channel_values"] = {"messages": [HumanMessage(content=text)], "working": {"stage": text}}
    versions = {"messages": cp["id"], "working": cp["id"]}
    cp["channel_versions"] = versions
    return h.checkpoint_saver.put(h.config, cp, {"source": "input", "step": 0}, versions)


def latest(h):
    return {**h.config, "configurable": {key: value for key, value in h.config["configurable"].items() if key != "checkpoint_id"}}


@pytest.mark.asyncio
async def test_working_state_cache_hit_matches_pg_and_writes_evict(hot, monkeypatch):
    h = hot
    pinned = save_working(h, "first")
    original = AsyncCompatiblePostgresSaver(h.store._pool, serde=create_checkpoint_serializer()).get_tuple(latest(h))
    count = sql_counter(h, monkeypatch)
    assert await h.checkpoint_saver.aget_tuple(latest(h)) == original
    assert count.call_count > 0
    count.reset_mock()
    assert await h.checkpoint_saver.aget_tuple(latest(h)) == original
    assert count.call_count == 0
    save_working(h, "second")
    assert h.checkpoint_saver.get_tuple(latest(h)).checkpoint["channel_values"]["working"] == {"stage": "second"}
    count.reset_mock()
    assert h.checkpoint_saver.get_tuple(pinned).checkpoint["channel_values"]["working"] == {"stage": "first"}
    assert count.call_count > 0  # Pinned commit evidence is never served from cache.


def test_pending_writes_and_delete_thread_evict_working_state(hot):
    h = hot
    pinned = save_working(h, "first")
    assert not h.checkpoint_saver.get_tuple(latest(h)).pending_writes
    h.checkpoint_saver.put_writes(pinned, [("working", {"stage": "pending"})], "task:writes")
    pending = h.checkpoint_saver.get_tuple(latest(h))
    assert pending.pending_writes == [("task:writes", "working", {"stage": "pending"})]
    h.checkpoint_saver.delete_thread(h.session.conversation_id)
    assert h.checkpoint_saver.get_tuple(latest(h)) is None


def test_context_pack_cached_and_uncached_projection_inputs_identical(hot, monkeypatch):
    h = hot
    rolled = h.service.rollover(h.session.session_id, HandoffDraft(goal="音乐探索", current_stage="继续", summary="公开摘要"),
                                RolloverReason.EXPLICIT_CHECKPOINT)
    args = dict(session_id=rolled.new_session.session_id, query="继续探索", system="音乐助手", live_state={})
    first = h.service.build_active_context(RouteKey.GENERAL_CHAT, **args)
    # cold_start intentionally records live revalidation/trace; those PG writes
    # must remain. Only source reads, never this side effect, are cached.
    h.store.read_session_projection(rolled.new_session.session_id, rolled.handoff.handoff_id)
    loader = Mock(side_effect=AssertionError("projection should come from Redis"))
    monkeypatch.setattr(h.store, "get_handoff", loader)
    second = h.service.build_active_context(RouteKey.GENERAL_CHAT, **args)
    assert first.messages == second.messages and first.items == second.items
    assert first.total_tokens == second.total_tokens and first.manifest == second.manifest
    loader.assert_not_called()


def test_redis_loss_rebuilds_records_checkpoints_and_projection_from_pg(hot):
    h = hot
    save_working(h, "active")
    draft = HandoffDraftV2(goal="探索", current_stage="执行中")
    projector = ProgressProjector(h.service)
    record = h.store.get_session(h.session.session_id)
    working = h.checkpoint_saver.get_tuple(latest(h))
    view = projector.project_progress(h.session, draft=draft)
    h.cache.client.delete(h.cache.session_key(h.session.session_id), h.cache.view_key(h.session.session_id))
    assert h.store.get_session(h.session.session_id) == record
    assert h.checkpoint_saver.get_tuple(latest(h)) == working
    assert projector.project_progress(h.session, draft=draft) == view


def test_redis_outage_falls_back_and_failed_invalidation_is_retried(hot, monkeypatch, caplog):
    from redis.exceptions import ConnectionError
    h = hot
    h.store.get_session(h.session.session_id)
    pipeline = h.cache.client.pipeline
    monkeypatch.setattr(h.cache.client, "pipeline", Mock(side_effect=ConnectionError("synthetic-private-url")))
    try:
        assert h.store.get_session(h.session.session_id) == h.session
        closed = h.store.close_session(h.session.session_id, datetime.now(UTC).isoformat())
    finally:
        monkeypatch.setattr(h.cache.client, "pipeline", pipeline)
    assert h.store.get_session(h.session.session_id) == closed
    assert "reading PostgreSQL" in caplog.text and "synthetic-private-url" not in caplog.text


def test_cache_adapter_preserves_postgres_budget_store_identity(hot):
    from app.agent.foundation.long_horizon.accounting import DurableAccounting
    from app.llm.budget import PostgresBudgetLedger
    h = hot
    assert isinstance(h.store, PostgresContinuityStore)
    accounting = DurableAccounting(h.writer, h.identity, SimpleNamespace(check=lambda: None))
    assert PostgresBudgetLedger(accounting).get_task_total(h.run.run_id).total_tokens == 0


def test_redis_outage_covers_working_state_and_committed_view(hot, monkeypatch, caplog):
    from redis.exceptions import ConnectionError
    h = hot
    save_working(h, "active")
    working = h.checkpoint_saver.get_tuple(latest(h))
    projector = ProgressProjector(h.service)
    draft = HandoffDraftV2(goal="探索", current_stage="执行中")
    view = projector.project_progress(h.session, draft=draft)
    monkeypatch.setattr(h.cache.client, "pipeline", Mock(side_effect=ConnectionError("synthetic-private-url")))
    assert h.checkpoint_saver.get_tuple(latest(h)) == working
    assert projector.project_progress(h.session, draft=draft) == view
    assert "reading PostgreSQL" in caplog.text and "synthetic-private-url" not in caplog.text


def test_wrong_session_record_is_rejected_and_unknown_session_not_cached(hot, caplog):
    h = hot
    key = h.cache.session_key(h.session.session_id)
    h.cache.client.hset(key, "record:v1", h.session.model_copy(update={"session_id": "session:foreign"}).model_dump_json())
    assert h.store.get_session(h.session.session_id) == h.session
    assert "invalid document" in caplog.text
    unknown = "session:" + uuid4().hex
    assert h.store.get_session(unknown) is None
    assert not h.cache.client.exists(h.cache.session_key(unknown))


def test_runtime_config_selects_cache_only_for_postgres(hot, monkeypatch):
    from app.agent.foundation.long_horizon import runtime
    h = hot
    monkeypatch.setenv("PERSISTENCE_BACKEND", "postgres")
    monkeypatch.setenv("DATABASE_URL", h.store._pool.conninfo)
    monkeypatch.setattr(runtime, "_persistent_store_factory", None)
    monkeypatch.setattr("app.agent.session_cache.configured_session_cache", lambda: h.cache)
    runtime.get_long_horizon_service.cache_clear()
    try:
        assert isinstance(runtime.get_long_horizon_service().store, CachedPostgresContinuityStore)
    finally:
        runtime.close_long_horizon_service()
    monkeypatch.setattr("app.agent.session_cache.configured_session_cache", lambda: None)
    try:
        assert type(runtime.get_long_horizon_service().store) is PostgresContinuityStore
    finally:
        runtime.close_long_horizon_service()


@pytest.mark.asyncio
async def test_checkpoint_startup_selects_optional_cache_without_changing_saver_semantics(hot, monkeypatch):
    from app.agent import checkpointing
    h = hot
    monkeypatch.setenv("CHECKPOINT_BACKEND", "postgres")
    monkeypatch.setenv("DATABASE_URL", h.store._pool.conninfo)
    monkeypatch.setattr(checkpointing, "_postgres_checkpointer", None)
    monkeypatch.setattr(checkpointing, "_postgres_pool", None)
    monkeypatch.setattr(checkpointing, "_persistent_checkpointer_factory", None)
    monkeypatch.setattr("app.agent.session_cache.configured_session_cache", lambda: h.cache)
    try:
        await checkpointing.initialize_runtime_checkpointer()
        assert isinstance(checkpointing.create_runtime_checkpointer(), CachedPostgresSaver)
    finally:
        await checkpointing.close_runtime_checkpointer()
    monkeypatch.setattr("app.agent.session_cache.configured_session_cache", lambda: None)
    try:
        await checkpointing.initialize_runtime_checkpointer()
        assert type(checkpointing.create_runtime_checkpointer()) is AsyncCompatiblePostgresSaver
    finally:
        await checkpointing.close_runtime_checkpointer()


@pytest.mark.asyncio
async def test_execution_reads_latest_pg_when_writer_dies_before_cache_eviction(hot, monkeypatch):
    from app.agent.foundation.long_horizon.coordinator import ContinuityCheckpointer
    h = hot
    save_working(h, "first")
    old = h.checkpoint_saver.get_tuple(latest(h))
    cold = AsyncCompatiblePostgresSaver(h.store._pool, serde=create_checkpoint_serializer())
    cp = empty_checkpoint()
    cp["channel_values"] = {"messages": [HumanMessage(content="second")], "working": {"stage": "second"}}
    versions = {key: cp["id"] for key in cp["channel_values"]}
    cp["channel_versions"] = versions
    # A durable commit with no corresponding eviction (producer death).
    cold.put(h.config, cp, {"source": "input", "step": 0}, versions)
    restarted_cache = RedisSessionCache(h.cache.config, client=h.cache.client)
    restarted = CachedPostgresSaver(h.store._pool, session_cache=restarted_cache, serde=create_checkpoint_serializer())
    assert restarted.get_tuple(latest(h)) == old  # Ordinary hot reads are TTL-bounded.
    count = sql_counter(h, monkeypatch)
    execution = ContinuityCheckpointer(restarted, SimpleNamespace())
    assert execution.get_tuple(latest(h)).checkpoint["channel_values"]["working"] == {"stage": "second"}
    assert (await execution.aget_tuple(latest(h))).checkpoint["channel_values"]["working"] == {"stage": "second"}
    assert count.call_count > 0


def test_recovery_projection_bypasses_stale_hot_view(hot, monkeypatch):
    from app.agent.session_cache import authoritative_session_evidence
    h = hot
    projector = ProgressProjector(h.service)
    draft = HandoffDraftV2(goal="探索", current_stage="执行中")
    before = projector.project_progress(h.session, draft=draft)
    assert before.agent_progress.transfer_phase == "unknown"
    field = next(key for key in h.cache.client.hkeys(h.cache.view_key(h.session.session_id)) if key.startswith("progress:"))
    stale = before.model_copy(update={"summary": "旧的缓存视图"})
    h.cache.client.hset(h.cache.view_key(h.session.session_id), field, stale.model_dump_json())
    assert projector.project_progress(h.session, draft=draft).summary == "旧的缓存视图"
    count = sql_counter(h, monkeypatch)

    @authoritative_session_evidence
    def recovery_read():
        return projector.project_progress(h.session, draft=draft)

    assert recovery_read() == before and count.call_count > 0
