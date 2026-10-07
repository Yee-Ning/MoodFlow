"""Real Redis Lua/transaction acceptance; no fakeredis or weakened old gates."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import os
import shutil
import socket
import subprocess
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage
from pydantic import ValidationError

from app.agent.foundation.long_horizon.accounting import DurableAccounting, active_accounting
from app.agent.foundation.long_horizon.public_events import PublicUsage
from app.agent.handoff.registry import AgentKey
from app.agent.handoff.state import AgentUsage, RunLimits
from app.llm.budget import HistoryBudgetLedger, TokenUsage
from app.llm.redis_budget import RedisBudgetConfig, RedisBudgetLedger, RedisBudgetUnavailable, SCRIPT
from app.llm.runtime import RequestLedger, RuntimeLimitError, accounted_call, agent_scope, request_scope
from tests.context_loop_helpers import harness


@pytest.fixture(scope="session")
def redis_server(tmp_path_factory):
    from redis import Redis
    from redis.exceptions import RedisError
    configured = os.getenv("BUDGET_TEST_REDIS_URL")
    if configured:
        client = Redis.from_url(configured, decode_responses=True)
        assert client.connection_pool.connection_kwargs["host"] in {"127.0.0.1", "localhost"}, "acceptance requires loopback Redis"
        client.ping()
        yield client
        client.close()
        return
    binary = shutil.which("redis-server")
    assert binary, "Redis acceptance requires redis-server or isolated BUDGET_TEST_REDIS_URL"
    # Resolve Windows package-manager links so adjacent runtime DLLs can load.
    binary = str(Path(binary).resolve())
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    directory = tmp_path_factory.mktemp("redis-budget")
    process = subprocess.Popen([binary, "--bind", "127.0.0.1", "--port", str(port),
                                "--save", "", "--appendonly", "no"],
                               cwd=directory, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    client = Redis(host="127.0.0.1", port=port, decode_responses=True,
                   socket_timeout=1, socket_connect_timeout=1)
    try:
        for _ in range(100):
            try:
                client.ping()
                break
            except RedisError:
                if process.poll() is not None:
                    pytest.fail("isolated Redis process exited before readiness")
                time.sleep(0.05)
        else:
            pytest.fail("isolated Redis failed readiness")
        yield client
    finally:
        client.close()
        process.terminate()
        process.wait(timeout=10)


@pytest.fixture
def hot(tmp_path, redis_server):
    h = harness(tmp_path / "budget.db", suffix=uuid4().hex)
    durable = DurableAccounting(h.writer, h.identity, SimpleNamespace(check=lambda: None))
    adapter = RedisBudgetLedger(durable, config=RedisBudgetConfig("redis://unused", ttl_seconds=300), client=redis_server)
    durable._budget_adapter = adapter
    adapter.rebuild(h.run.run_id)
    yield h, durable, adapter
    redis_server.delete(*adapter.keys(h.run.run_id).values())


def book(durable, *, l2=16000, l3=32000):
    token = active_accounting.set(durable)
    try:
        return RequestLedger(tuple(AgentUsage(agent=key) for key in AgentKey), AgentKey.GENERAL_CHAT,
                             RunLimits(agent_token_budget=l2, run_token_budget=l3),
                             deadline=30, run_id=durable.identity.run_id)
    finally:
        active_accounting.reset(token)


def answer(left, right):
    return AIMessage(content="完成", usage_metadata={"input_tokens": left, "output_tokens": right,
                                                   "total_tokens": left + right})


def test_protocol_split_models_and_scope_match_cold_adapter(hot):
    h, _, adapter = hot
    run = h.run.run_id
    adapter.add(run, "curator", 100, 10, "chat")
    adapter.add(run, "jev", 20, 2, "jev", estimated=True)
    assert adapter.get_task_total(run) == TokenUsage(120, 12, 22, 2)
    assert adapter.get_agent_total(run, "jev") == TokenUsage(20, 2, 22, 1)
    assert adapter.get_model_breakdown(run) == {"chat": TokenUsage(100, 10, 0, 1), "jev": TokenUsage(20, 2, 22, 1)}
    assert adapter.counters(run, "general_chat") == (22, 132)
    assert adapter.counters(run, "curator") == (110, 132)
    with pytest.raises(ValueError, match="scope"):
        adapter.get_task_total("another-run")
    with pytest.raises(ValidationError):
        adapter.add(run, "curator", -1, 0, "chat")
    with pytest.raises(ValueError, match="unknown"):
        adapter.reserve(run, "jev", 1, 300, 500)


def test_lua_multi_thread_reservations_never_exceed_either_limit(hot):
    h, _, adapter = hot
    run = h.run.run_id
    owners = ("general_chat", "curator", "explorer")
    def reserve(index):
        owner = owners[index % len(owners)]
        result, _ = adapter.reserve(run, owner, 10, 200, 500)
        return owner, result
    with ThreadPoolExecutor(max_workers=24) as pool:
        results = list(pool.map(reserve, range(240)))
    assert sum(result == "accepted" for _, result in results) == 50
    assert {result for _, result in results} <= {"accepted", "l2", "l3"}
    accepted_by_owner = {owner: sum(agent == owner and result == "accepted" for agent, result in results) * 10
                         for owner in owners}
    for owner in owners:
        assert adapter.counters(run, owner) == (accepted_by_owner[owner], 500)
        assert accepted_by_owner[owner] <= 200
    assert sum(accepted_by_owner.values()) == 500


@pytest.mark.parametrize("level", ["l2", "l3"])
def test_cross_ledger_rejection_signals_and_existing_degradation(hot, level):
    h, durable, adapter = hot
    first = book(durable, l2=100, l3=200)
    # Another process view, independently instantiated before the reservation.
    second_durable = DurableAccounting(h.writer, h.identity, SimpleNamespace(check=lambda: None))
    second_durable._budget_adapter = RedisBudgetLedger(durable, config=adapter.config, client=adapter.client)
    second = book(second_durable, l2=100, l3=200)
    first.reserve(100)
    durable.reserve(first, 100)
    if level == "l3":
        with agent_scope("curator"):
            first.reserve(100)
            durable.reserve(first, 100)
    before = adapter.counters(h.run.run_id, "general_chat")
    with pytest.raises(RuntimeLimitError, match="token_limit"):
        second.reserve(1)
    assert adapter.counters(h.run.run_id, "general_chat") == before
    stages = (["truncate_history", "summarize_history", "stop_sources", "honest_degradation"]
              if level == "l2" else ["task_degradation", "refuse"])
    assert [event["stage"] for event in second.budget_trace] == stages
    assert all(event["level"] == level for event in second.budget_trace)
    assert [event.budget_stage for event in durable.events(h.run.run_id) if event.phase == "budget"] == stages
    assert ("general_chat" in second.source_stops) == (level == "l2")


def test_delta_refund_overshoot_duplicate_and_ttl(hot):
    h, _, adapter = hot
    run = h.run.run_id
    result, call = adapter.reserve(run, "curator", 120, 200, 300)
    assert result == "accepted"
    assert adapter.reserve(run, "curator", 120, 200, 300, call_id=call) == ("accepted", call)
    adapter.settle(run, "curator", call, 27)
    adapter.settle(run, "curator", call, 27)
    assert adapter.counters(run, "curator") == (27, 27)
    with pytest.raises(ValueError, match="conflicting"):
        adapter.settle(run, "curator", call, 28)
    _, next_call = adapter.reserve(run, "curator", 10, 200, 300)
    adapter.settle(run, "curator", next_call, 23)
    assert adapter.counters(run, "curator") == (50, 50)
    for key in adapter.keys(run).values():
        if adapter.client.exists(key):
            assert 0 < adapter.client.ttl(key) <= 300
    with pytest.raises(ValueError):
        adapter.reserve(run, "curator", -1, 200, 300)


@pytest.mark.asyncio
async def test_end_flush_is_one_settlement_and_full_eviction_replays_exactly(hot):
    h, durable, adapter = hot
    ledger = book(durable)
    with request_scope(ledger):
        await accounted_call(AsyncMock(return_value=answer(20, 7)), "hi", output_tokens=100, model="chat")
        with agent_scope("explorer"):
            await accounted_call(AsyncMock(return_value=answer(30, 3)), "hi", output_tokens=100, model="reasoner")
    assert adapter.counters(h.run.run_id, "general_chat") == (27, 60)
    snapshots = [event for event in durable.events(h.run.run_id) if event.redis_snapshot is not None]
    assert len(snapshots) == 1
    assert snapshots[0].phase == "settled" and snapshots[0].tokens == 60
    assert snapshots[0].redis_snapshot == {"general_chat": 27, "curator": 0, "explorer": 33}
    durable.flush_budget()
    assert len([event for event in durable.events(h.run.run_id) if event.redis_snapshot is not None]) == 1
    assert adapter.get_task_total(h.run.run_id) == TokenUsage(50, 10, 0, 2)
    adapter.client.delete(*adapter.keys(h.run.run_id).values())
    rebuilt = adapter.rebuild(h.run.run_id)
    assert rebuilt["general_chat"] == 27 and rebuilt["explorer"] == 33 and rebuilt["task"] == 60
    assert adapter.get_task_total(h.run.run_id) == TokenUsage(50, 10, 0, 2)


def test_reconcile_keeps_inflight_and_unknown_cost_reserves_and_warns(hot, caplog):
    h, durable, adapter = hot
    run = h.run.run_id
    _, orphan = adapter.reserve(run, "curator", 40, 100, 200)
    ledger = book(durable, l2=100, l3=200)
    ledger.reserve(80)
    call = durable.reserve(ledger, 80)
    assert adapter.counters(run, "general_chat") == (80, 120)
    adapter.client.set(adapter.keys(run)["task"], 1)
    result = adapter.reconcile(run)
    assert result["drift"] is True and result["task"] == 120
    assert "drift corrected" in caplog.text
    adapter.settle(run, "curator", orphan, 10)
    assert adapter.reconcile(run)["task"] == 90
    # Full loss can replay dispatched cold reserves; orphan had not been dispatched.
    adapter.client.delete(*adapter.keys(run).values())
    assert adapter.rebuild(run)["task"] == 80
    adapter.settle(run, "general_chat", call, 20)
    assert adapter.counters(run, "general_chat") == (20, 20)


@pytest.mark.asyncio
@pytest.mark.parametrize("ambiguous", [False, True])
async def test_redis_failure_falls_back_to_original_gate_and_cold_receipts(hot, monkeypatch, caplog, ambiguous):
    from redis.exceptions import ConnectionError
    h, durable, adapter = hot
    ledger = book(durable, l2=100, l3=200)
    evaluate = adapter.client.eval
    def fail(*args, **kwargs):
        if ambiguous:
            evaluate(*args, **kwargs)  # Simulate successful mutation with lost reply.
        raise ConnectionError("test-only outage")
    monkeypatch.setattr(adapter.client, "eval", fail)
    operation = AsyncMock(return_value=answer(20, 7))
    with request_scope(ledger):
        await accounted_call(operation, "hi", output_tokens=30)
    assert operation.await_count == 1
    assert ledger.hot_budget is None
    assert adapter.get_task_total(h.run.run_id) == TokenUsage(20, 7, 0, 1)
    assert adapter.counters(h.run.run_id, "general_chat") == (27, 27)
    assert "using existing budget logic" in caplog.text
    with pytest.raises(RuntimeLimitError, match="token_limit"):
        ledger.reserve(101)


@pytest.mark.asyncio
async def test_same_amount_calls_settle_out_of_order_without_mismatching_receipts(hot):
    h, durable, adapter = hot
    ledger = book(durable)
    started, release = asyncio.Event(), asyncio.Event()
    async def slow():
        started.set()
        await release.wait()
        return answer(20, 10)
    with request_scope(ledger):
        task = asyncio.create_task(accounted_call(slow, "hi", output_tokens=100, model="slow"))
        await started.wait()
        reservation = ledger.reserved
        await accounted_call(AsyncMock(return_value=answer(8, 2)), "hi", output_tokens=100, model="fast")
        assert adapter.reconcile(h.run.run_id)["task"] == reservation + 10
        release.set()
        await task
    events = {event.call_id: event for event in durable.events(h.run.run_id)
              if event.phase == "settled" and event.redis_snapshot is None}
    for call_id, encoded in adapter.client.hgetall(adapter.keys(h.run.run_id)["receipts"]).items():
        assert json.loads(encoded)["tokens"] == events[call_id].tokens
    assert adapter.counters(h.run.run_id, "general_chat") == (40, 40)


@pytest.mark.asyncio
async def test_shared_actual_overshoot_preserves_bill_and_uses_existing_l3_refusal(hot):
    h, durable, adapter = hot
    ledger = book(durable, l2=100, l3=100)
    adapter.reserve(h.run.run_id, "explorer", 20, 100, 100)
    operation = AsyncMock(return_value=answer(85, 5))
    with pytest.raises(RuntimeLimitError, match="token_limit"):
        with request_scope(ledger):
            await accounted_call(operation, "hi", output_tokens=30, model="overshoot")
    assert operation.await_count == 1
    assert adapter.get_task_total(h.run.run_id) == TokenUsage(85, 5, 0, 1)
    assert adapter.counters(h.run.run_id, "general_chat") == (90, 110)
    assert [event["stage"] for event in ledger.budget_trace] == ["task_degradation", "refuse"]
    assert all(event["level"] == "l3" for event in ledger.budget_trace)


def test_disabled_missing_url_or_dependency_preserve_adapter(hot, monkeypatch, caplog):
    h, _, _ = hot
    def fresh():
        return DurableAccounting(h.writer, h.identity, SimpleNamespace(check=lambda: None))
    monkeypatch.delenv("BUDGET_LEDGER_BACKEND", raising=False)
    assert type(fresh().budget_ledger) is HistoryBudgetLedger
    monkeypatch.setenv("BUDGET_LEDGER_BACKEND", "redis")
    monkeypatch.delenv("REDIS_URL", raising=False)
    assert type(fresh().budget_ledger) is HistoryBudgetLedger
    assert "REDIS_URL missing" in caplog.text
    monkeypatch.setenv("REDIS_URL", "redis://test-only.invalid")
    def missing(_):
        raise ImportError("optional dependency absent")
    monkeypatch.setattr("app.llm.redis_budget.redis_client", missing)
    assert type(fresh().budget_ledger) is HistoryBudgetLedger


def test_config_selects_env_connection_and_respects_task_lifetime(hot, monkeypatch):
    h, _, adapter = hot
    port = adapter.client.connection_pool.connection_kwargs["port"]
    monkeypatch.setenv("BUDGET_LEDGER_BACKEND", "redis")
    monkeypatch.setenv("REDIS_URL", f"redis://127.0.0.1:{port}/0")
    monkeypatch.setenv("BUDGET_REDIS_TTL_SECONDS", "30")
    durable = DurableAccounting(h.writer, h.identity, SimpleNamespace(check=lambda: None))
    ledger = book(durable)
    assert isinstance(durable.budget_ledger, RedisBudgetLedger)
    assert ledger.hot_budget.ttl == 120  # Never expires before the allowed run lifetime.
    ledger.reserve(40)
    durable.reserve(ledger, 40)
    assert ledger.hot_budget.counters(h.run.run_id, "general_chat") == (40, 40)
    assert 0 < ledger.hot_budget.client.ttl(ledger.hot_budget.keys(h.run.run_id)["task"]) <= 120
    monkeypatch.setenv("BUDGET_REDIS_TTL_SECONDS", "0")
    with pytest.raises(ValueError, match="positive"):
        RedisBudgetConfig.configured()


def test_script_does_not_mutate_on_rejection_and_keys_share_hash_tag(hot):
    h, _, adapter = hot
    keys = adapter.keys(h.run.run_id)
    assert {key[key.index("{"):key.index("}") + 1] for key in keys.values()} == {"{" + h.run.run_id + "}"}
    adapter.reserve(h.run.run_id, "curator", 100, 100, 200)
    before = {key: adapter.client.dump(key) for key in keys.values()}
    assert adapter.client.eval(SCRIPT, 3, keys["curator"], keys["task"], keys["receipts"],
                               1, 100, 200, "rejected", "{}", 300) == "l2"
    assert {key: adapter.client.dump(key) for key in keys.values()} == before


def test_partial_eviction_and_saved_conservative_usage(hot):
    h, _, adapter = hot
    adapter.add(h.run.run_id, "general_chat", 10, 2, "chat")
    adapter.client.delete(adapter.keys(h.run.run_id)["general_chat"])
    assert adapter.counters(h.run.run_id, "general_chat") == (12, 12)
    adapter.reconcile(h.run.run_id, minimum={"general_chat": 40})
    assert adapter.counters(h.run.run_id, "general_chat") == (40, 40)
    assert adapter.reserve(h.run.run_id, "general_chat", 1, 40, 100)[0] == "l2"
    adapter.flush(h.run.run_id)
    adapter.client.delete(*adapter.keys(h.run.run_id).values())
    fresh = RedisBudgetLedger(adapter.accounting, config=adapter.config, client=adapter.client)
    assert fresh.rebuild(h.run.run_id)["task"] == 40


def test_lost_settlement_reply_cannot_refund_twice(hot, monkeypatch):
    from redis.exceptions import ConnectionError
    h, _, adapter = hot
    run = h.run.run_id
    _, call_id = adapter.reserve(run, "curator", 100, 200, 300)
    make_pipeline = adapter.client.pipeline
    dropped = False
    def pipeline(*args, **kwargs):
        pipe = make_pipeline(*args, **kwargs)
        execute = pipe.execute
        def lose_reply(*args, **kwargs):
            nonlocal dropped
            result = execute(*args, **kwargs)
            if not dropped:
                dropped = True
                raise ConnectionError("committed transaction reply lost")
            return result
        pipe.execute = lose_reply
        return pipe
    monkeypatch.setattr(adapter.client, "pipeline", pipeline)
    with pytest.raises(RedisBudgetUnavailable):
        adapter.settle(run, "curator", call_id, 20)
    adapter.settle(run, "curator", call_id, 20)
    assert adapter.counters(run, "curator") == (20, 20)


def test_snapshot_validation_rejects_negative_mismatched_or_reserved_document():
    valid = dict(run_id="run", agent="general_chat", call_id="redis-flush-one", phase="settled", tokens=3,
                 redis_snapshot={"general_chat": 1, "curator": 2, "explorer": 0})
    assert PublicUsage(**valid).tokens == 3
    for changed in ({"tokens": 4}, {"phase": "reserved"},
                    {"redis_snapshot": {"general_chat": -1, "curator": 4, "explorer": 0}}):
        with pytest.raises(ValidationError):
            PublicUsage(**{**valid, **changed})


def test_reconcile_outage_raises_explicit_unavailable(hot, monkeypatch):
    from redis.exceptions import ConnectionError
    h, _, adapter = hot
    def fail(*args, **kwargs):
        raise ConnectionError("test-only outage")
    monkeypatch.setattr(adapter.client, "pipeline", fail)
    with pytest.raises(RedisBudgetUnavailable):
        adapter.rebuild(h.run.run_id)
