"""Redis hot counters with actual immutable PostgreSQL MODEL_USAGE receipts."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from psycopg import Error

from app.agent.foundation.long_horizon.accounting import DurableAccounting
from app.agent.foundation.long_horizon.history_writer import HistoryWriter
from app.agent.foundation.long_horizon.models import ContinuityIdentity
from app.agent.foundation.long_horizon.postgres_store import PostgresContinuityStore
from app.agent.foundation.long_horizon.service import LongHorizonContextService
from app.llm.budget import PostgresBudgetLedger, TokenUsage
from app.llm.redis_budget import RedisBudgetConfig, RedisBudgetLedger
from app.llm.runtime import accounted_call, agent_scope, request_scope
from scripts.reconcile_redis_budget import reconcile_active_runs, run_worker
from tests.test_context_loop_postgres_recovery import BASE, database
from tests.test_redis_budget import answer, book, redis_server

__all__ = ["database", "redis_server"]
pytestmark = pytest.mark.skipif(not BASE, reason="isolated PostgreSQL acceptance requires DATABASE_URL")


@pytest.fixture
def postgres_hot(database, redis_server):
    store = PostgresContinuityStore(database)
    session = LongHorizonContextService(store).open_conversation("redis-budget-" + uuid4().hex)
    identity = ContinuityIdentity(conversation_id=session.conversation_id, continuity_task_id=session.task_id,
        session_id=session.session_id, request_id="redis-acceptance", graph_mode="foundation", run_id=uuid4().hex)
    durable = DurableAccounting(HistoryWriter(store), identity, SimpleNamespace(check=lambda: None))
    adapter = RedisBudgetLedger(durable, config=RedisBudgetConfig("redis://test-only", ttl_seconds=300), client=redis_server)
    durable._budget_adapter = adapter
    assert isinstance(adapter.cold, PostgresBudgetLedger)
    yield store, durable, adapter
    redis_server.delete(*adapter.keys(identity.run_id).values())
    store.close()


@pytest.mark.asyncio
async def test_actual_postgres_flush_reopen_and_loss_replay(postgres_hot, database):
    store, durable, adapter = postgres_hot
    run = durable.identity.run_id
    ledger = book(durable)
    async def operation():
        return answer(20, 7)
    with request_scope(ledger):
        await accounted_call(operation, "hi", output_tokens=100, model="chat", billing_agent="jev")
        with agent_scope("curator"):
            await accounted_call(operation, "hi", output_tokens=100, model="chat")
    events = store.query_run_usage(durable.identity.continuity_task_id, run)
    snapshots = [event for event in events if event.payload.get("redis_snapshot") is not None]
    assert len(snapshots) == 1
    assert snapshots[0].payload["tokens"] == 54
    assert snapshots[0].payload["redis_snapshot"] == {"general_chat": 27, "curator": 27, "explorer": 0}
    assert adapter.get_task_total(run) == TokenUsage(40, 14, 0, 2)
    assert adapter.get_agent_total(run, "jev") == TokenUsage(20, 7, 0, 1)
    with pytest.raises(Error, match="immutable"):
        with store._pool.connection() as connection:
            connection.execute("DELETE FROM agent_history_event WHERE event_id=%s", (snapshots[0].event_id,))
    adapter.client.delete(*adapter.keys(run).values())
    reopened = PostgresContinuityStore(database)
    try:
        other = DurableAccounting(HistoryWriter(reopened), durable.identity, SimpleNamespace(check=lambda: None))
        replay = RedisBudgetLedger(other, config=adapter.config, client=adapter.client)
        assert replay.rebuild(run)["task"] == 54
        assert replay.counters(run, "general_chat") == (27, 54)
        assert replay.counters(run, "curator") == (27, 54)
        assert replay.get_task_total(run) == TokenUsage(40, 14, 0, 2)
    finally:
        reopened.close()


def test_periodic_worker_corrects_actual_postgres_drift(postgres_hot, caplog):
    store, durable, adapter = postgres_hot
    run = durable.identity.run_id
    adapter.add(run, "explorer", 30, 4, "chat")
    adapter.client.set(adapter.keys(run)["task"], 900)
    adapter.client.expire(adapter.keys(run)["task"], 30)
    result = reconcile_active_runs(store, adapter.client, adapter.config)
    assert result == {"checked": 1, "corrected": 1, "failed": 0}
    assert adapter.counters(run, "explorer") == (34, 34)
    assert 0 < adapter.client.ttl(adapter.keys(run)["task"]) <= 30
    assert "drift corrected" in caplog.text
    run_worker(store, adapter.client, adapter.config, once=True)
    intervals = []
    def finish(seconds):
        intervals.append(seconds)
        raise InterruptedError("controlled worker stop")
    with pytest.raises(InterruptedError):
        run_worker(store, adapter.client, adapter.config, sleep=finish)
    assert intervals == [60]


@pytest.mark.asyncio
async def test_unavailable_redis_preserves_postgres_success_and_refusal(postgres_hot, monkeypatch, caplog):
    from redis.exceptions import ConnectionError
    _, durable, adapter = postgres_hot
    def fail(*args, **kwargs):
        raise ConnectionError("test-only unavailable Redis")
    monkeypatch.setattr(adapter.client, "pipeline", fail)
    ledger = book(durable, l2=100, l3=200)
    assert ledger.hot_budget is None
    async def operation():
        return answer(20, 7)
    with request_scope(ledger):
        await accounted_call(operation, "hi", output_tokens=30, model="chat")
    assert adapter.get_task_total(durable.identity.run_id) == TokenUsage(20, 7, 0, 1)
    assert "using existing budget logic" in caplog.text
    from app.llm.runtime import RuntimeLimitError
    with pytest.raises(RuntimeLimitError, match="token_limit"):
        ledger.reserve(101)
    assert ledger.budget_trace[-1]["stage"] == "honest_degradation"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["read", "write"])
async def test_secondary_pg_flush_failure_keeps_primary_facts_and_cleans_scope(postgres_hot, monkeypatch, caplog, failure):
    from psycopg import OperationalError
    from app.llm.runtime import active_ledger
    _, durable, adapter = postgres_hot
    ledger = book(durable)
    original_events, original_write = durable.events, durable.write_usage
    def failed_events(*args):
        raise OperationalError("controlled cold read outage after primary settlement")
    def failed_write(body):
        if body.redis_snapshot is not None:
            raise OperationalError("controlled snapshot append outage")
        return original_write(body)
    async def operation():
        return answer(20, 7)
    with request_scope(ledger):
        await accounted_call(operation, "hi", output_tokens=100)
        if failure == "read":
            monkeypatch.setattr(durable, "events", failed_events)
        else:
            monkeypatch.setattr(durable, "write_usage", failed_write)
    assert active_ledger.get() is None
    assert "per-call receipts retained" in caplog.text
    monkeypatch.setattr(durable, "events", original_events)
    monkeypatch.setattr(durable, "write_usage", original_write)
    assert adapter.get_task_total(durable.identity.run_id) == TokenUsage(20, 7, 0, 1)
    assert adapter.rebuild(durable.identity.run_id)["task"] == 27
