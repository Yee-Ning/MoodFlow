"""P2 acceptance against a real loopback Redis; no fake Lua implementation."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from threading import Barrier
from time import sleep
from types import SimpleNamespace
from unittest.mock import Mock
from unittest.mock import AsyncMock
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage
import pytest
from redis.exceptions import ConnectionError

from app.agent.dedup_locks import LocalDedupLocks, RedisDedupLocks, configured_dedup_locks
from app.agent.foundation import derived_cache
from app.agent.foundation.context_engineering import POLICIES, build_context, build_context_async
from app.agent.foundation.profile_cache import CachedProfileStore, RedisProfileCache
from app.agent.foundation.routing import RouteKey
from app.agent.foundation.user_profile import ProfileWriter, SQLiteProfileStore
from app.agent.redis_reuse import RedisReuseConfig
from app.rag.chunk.token_counter import CallableTokenCounter, UnicodeHeuristicTokenCounter
from tests.test_context_entry_phase1 import entry
from tests.test_redis_budget import redis_server

__all__ = ["redis_server"]


@pytest.fixture
def derived(redis_server, monkeypatch):
    cache = derived_cache.RedisDerivedCache(RedisReuseConfig("redis://unused", 60), client=redis_server)
    monkeypatch.setattr(derived_cache, "configured_derived_cache", lambda: cache)
    return cache


def inputs(user=None):
    return {"query": "解释音乐", "system": "rules", "messages": [HumanMessage(content="earlier"),
        AIMessage(content="GENERATED-TEXT-MUST-NOT-BE-CACHED")],
        "continuity_context": {"user_id": user or uuid4().hex, "revalidation": {"ok": True}},
        "intent": {"routes": ["general_chat"]}}


def test_repeated_derived_input_has_zero_token_or_dedup_recomputation(derived, monkeypatch):
    from app.agent.foundation import context_engineering
    original_count = UnicodeHeuristicTokenCounter.count
    count = Mock(side_effect=lambda text: original_count(UnicodeHeuristicTokenCounter(), text))
    monkeypatch.setattr(UnicodeHeuristicTokenCounter, "count", lambda self, text: count(text))
    dedup = Mock(wraps=context_engineering._compute_dedup_keys)
    monkeypatch.setattr(context_engineering, "_compute_dedup_keys", dedup)
    values = inputs()
    first = build_context(RouteKey.GENERAL_CHAT, **values)
    assert count.call_count > 0 and dedup.call_count > 0
    count.reset_mock()
    dedup.reset_mock()
    assert build_context(RouteKey.GENERAL_CHAT, **values) == first
    count.assert_not_called()
    dedup.assert_not_called()
    keys = list(derived.client.scan_iter(match="contextpack:derived:" + values["continuity_context"]["user_id"] + ":*"))
    assert len(keys) == 2
    for key in keys:
        raw = derived.client.get(key)
        assert "GENERATED-TEXT-MUST-NOT-BE-CACHED" not in raw
        assert "rules" not in raw and "earlier" not in raw
        assert set(json.loads(raw)) == {"counts", "dedup"}
        assert 0 < derived.client.ttl(key) <= 60


@pytest.mark.parametrize("changed", ["query", "system", "message", "profile", "intent", "policy", "metadata", "source_audit", "user"])
def test_every_changed_input_addresses_a_new_derivation(derived, changed):
    values = inputs()
    build_context(RouteKey.GENERAL_CHAT, **values)
    old_keys = set(derived.client.scan_iter(match="contextpack:derived:*"))
    values = deepcopy(values)
    if changed in {"query", "system"}:
        values[changed] += " different"
    elif changed == "message":
        values["messages"].append(HumanMessage(content="new message"))
    elif changed == "profile":
        values["continuity_context"]["user_profile"] = {"version": 2}
    elif changed == "intent":
        values["intent"]["routes"] = ["worldline"]
    elif changed == "policy":
        values["policy"] = POLICIES[RouteKey.GENERAL_CHAT].model_copy(update={"safety_margin": 257})
    elif changed == "metadata":
        values["messages"][0] = HumanMessage(content="earlier", additional_kwargs={"source_revision": 2})
    elif changed == "source_audit":
        values["continuity_context"]["entry_source_audit"] = {"rag": {"model_usage": [], "collection": None}}
    else:
        values["continuity_context"]["user_id"] = uuid4().hex
    result = build_context(RouteKey.GENERAL_CHAT, **values)
    if changed == "source_audit":
        assert result.manifest["sources"]["rag"]["model_usage"] == []
        assert result.manifest["sources"]["rag"]["collection"] is None
    assert set(derived.client.scan_iter(match="contextpack:derived:*")) - old_keys


def test_derived_cache_custom_counter_and_anonymous_calls_bypass(derived):
    values = inputs()
    before = set(derived.client.scan_iter(match="contextpack:derived:*"))
    values["continuity_context"] = {}
    build_context(RouteKey.GENERAL_CHAT, **values)
    values["continuity_context"]["user_id"] = uuid4().hex
    counter = Mock(return_value=1)
    build_context(RouteKey.GENERAL_CHAT, counter=CallableTokenCounter(counter), **values)
    assert counter.call_count > 0
    assert set(derived.client.scan_iter(match="contextpack:derived:*")) == before


@pytest.mark.asyncio
async def test_async_derivation_users_and_active_manifests_remain_isolated(derived):
    values = [inputs(), inputs()]
    async def run(arguments):
        first = await build_context_async(RouteKey.GENERAL_CHAT, **arguments)
        second = await build_context_async(RouteKey.GENERAL_CHAT, **arguments)
        assert first == second
        return first
    result = await asyncio.gather(*(run(arguments) for arguments in values))
    assert result[0].messages == result[1].messages
    assert derived_cache._user.get() is None and derived_cache._batch.get() is None
    for arguments in values:
        assert len(list(derived.client.scan_iter(match="contextpack:derived:" + arguments["continuity_context"]["user_id"] + ":*"))) == 2


@pytest.mark.asyncio
async def test_cache_hits_still_execute_jev_selection(derived, monkeypatch):
    async def select(items, *args, **kwargs):
        return items
    selection = AsyncMock(side_effect=select)
    monkeypatch.setattr("app.config.settings.get_settings", lambda: SimpleNamespace(jev_enabled=True, jev_context_select_enabled=True))
    monkeypatch.setattr("app.agent.decision.jev.context_selector.select_context", selection)
    values = inputs()
    client = Mock()
    first = await build_context_async(RouteKey.GENERAL_CHAT, jev_client=client, **values)
    assert await build_context_async(RouteKey.GENERAL_CHAT, jev_client=client, **values) == first
    assert selection.await_count == 2


def test_derived_cache_fault_keeps_original_result_and_warns(derived, monkeypatch, caplog):
    values = inputs()
    expected = build_context(RouteKey.GENERAL_CHAT, **values)
    monkeypatch.setattr(derived.client, "get", Mock(side_effect=ConnectionError("private-fixture")))
    monkeypatch.setattr(derived.client, "set", Mock(side_effect=ConnectionError("private-fixture")))
    assert build_context(RouteKey.GENERAL_CHAT, **values) == expected
    assert "derived cache unavailable" in caplog.text and "private-fixture" not in caplog.text


def test_derived_cache_corruption_uses_original_derivations(derived, caplog):
    values = inputs()
    expected = build_context(RouteKey.GENERAL_CHAT, **values)
    keys = list(derived.client.scan_iter(match="contextpack:derived:" + values["continuity_context"]["user_id"] + ":*"))
    for key in keys:
        derived.client.set(key, '{"counts":{"bad":-1},"dedup":{}}')
    assert build_context(RouteKey.GENERAL_CHAT, **values) == expected
    assert "derived cache invalid" in caplog.text


def test_derived_hits_preserve_history_writes_and_lease_checks(derived):
    from app.agent.foundation.long_horizon.coordinator import active_call
    call = SimpleNamespace(revalidation={"ok": True}, lease=SimpleNamespace(check=Mock()),
        accounting=SimpleNamespace(identity="fixture"), coordinator=SimpleNamespace(writer=SimpleNamespace(append_public_event=Mock())))
    token = active_call.set(call)
    try:
        values = inputs()
        build_context(RouteKey.GENERAL_CHAT, **values)
        build_context(RouteKey.GENERAL_CHAT, **values)
        assert call.lease.check.call_count == 2
        assert call.coordinator.writer.append_public_event.call_count == 2
    finally:
        active_call.reset(token)


def test_derived_hits_do_not_bypass_cumulative_budget_failure(derived):
    from app.llm.runtime import active_ledger
    values = inputs()
    build_context(RouteKey.GENERAL_CHAT, **values)
    ledger = SimpleNamespace(check_task_budget=Mock(side_effect=ValueError("budget exceeded")))
    token = active_ledger.set(ledger)
    try:
        with pytest.raises(ValueError, match="budget exceeded"):
            build_context(RouteKey.GENERAL_CHAT, **values)
        ledger.check_task_budget.assert_called_once()
    finally:
        active_ledger.reset(token)


@pytest.fixture
def profile(redis_server):
    cold = SQLiteProfileStore(":memory:")
    cache = RedisProfileCache(RedisReuseConfig("redis://unused", 120), client=redis_server)
    wrapped = CachedProfileStore(cold, cache)
    user = uuid4().hex
    first = ProfileWriter(wrapped).update(user, [entry()], expected_version=0)
    try:
        yield SimpleNamespace(cold=cold, cache=cache, store=wrapped, user=user, first=first)
    finally:
        cold.close()


def test_profile_miss_refill_hit_and_update_invalidates_old_version(profile, monkeypatch):
    read = Mock(wraps=profile.cold.read)
    monkeypatch.setattr(profile.cold, "read", read)
    assert profile.store.read(profile.user) == profile.first
    assert read.call_count == 1
    read.reset_mock()
    assert profile.store.read(profile.user) == profile.first
    assert profile.store.read(profile.user, version=1) == profile.first
    read.assert_not_called()
    second = ProfileWriter(profile.store).update(profile.user, [entry(value="请详细回答")], expected_version=1)
    assert not profile.cache.client.exists(profile.cache.key(profile.user, 1))
    assert profile.store.read(profile.user) == second
    read.reset_mock()
    assert profile.store.read(profile.user) == second
    read.assert_not_called()
    assert profile.store.read(profile.user, version=1) == profile.first


@pytest.mark.parametrize("kind", ["scope", "extra", "oversize", "private", "version"])
def test_profile_cache_preserves_scope_whitelist_and_byte_gate(profile, caplog, kind):
    profile.store.read(profile.user)
    raw = profile.first.model_dump(mode="json")
    if kind == "scope":
        raw["user_id"] = "another-user"
    elif kind == "version":
        raw["version"] = 2
    elif kind == "extra":
        raw["prompt"] = "not-allowlisted"
    elif kind == "private":
        raw["entries"][0]["value"] = "Bearer private-fixture"
    else:
        raw["entries"] = [entry(str(index), "中" * 1000).model_dump(mode="json") for index in range(20)]
    profile.cache.client.set(profile.cache.key(profile.user, 1), json.dumps(raw))
    assert profile.store.read(profile.user) == profile.first
    assert "invalid document" in caplog.text and "private-fixture" not in caplog.text


def test_profile_fault_and_failed_invalidation_recover_from_pg(profile, monkeypatch, caplog):
    profile.store.read(profile.user)
    original_pipeline = profile.cache.client.pipeline
    monkeypatch.setattr(profile.cache.client, "pipeline", Mock(side_effect=ConnectionError("private-fixture")))
    second = ProfileWriter(profile.store).update(profile.user, [entry(value="更新")], expected_version=1)
    assert profile.store.read(profile.user) == second
    monkeypatch.setattr(profile.cache.client, "pipeline", original_pipeline)
    assert profile.store.read(profile.user) == second
    assert "profile cache unavailable" in caplog.text and "private-fixture" not in caplog.text


def test_profile_write_merge_reads_authoritative_store(profile, monkeypatch):
    profile.store.read(profile.user)
    second = ProfileWriter(profile.cold).update(profile.user, [entry(value="updated outside cache")], expected_version=1)
    assert profile.store.read(profile.user) == profile.first  # Simulated missed invalidation.
    read = Mock(wraps=profile.cold.read)
    monkeypatch.setattr(profile.cold, "read", read)
    third = ProfileWriter(profile.store).update(profile.user, [entry("artist", "喜欢爵士")], expected_version=2)
    assert third.entries[0] == second.entries[0]
    assert read.call_count > 0
    assert profile.store.read(profile.user) == third


def test_profile_db_errors_and_version_conflicts_are_not_swallowed(profile, monkeypatch):
    with pytest.raises(ValueError, match="version conflict"):
        ProfileWriter(profile.store).update(profile.user, [entry()], expected_version=0)
    read = Mock(side_effect=RuntimeError("cold failed"))
    monkeypatch.setattr(profile.cold, "read", read)
    with pytest.raises(RuntimeError, match="cold failed"):
        profile.store.read(uuid4().hex)
    read.assert_called_once()


def test_real_lock_concurrent_acquire_and_token_checked_release(redis_server):
    locks = [RedisDedupLocks(RedisReuseConfig("redis://unused"), client=redis_server) for _ in range(16)]
    resource = "derived-warm:" + uuid4().hex
    barrier = Barrier(16)
    def acquire(index):
        barrier.wait(timeout=10)
        return locks[index].acquire(resource, 2)
    with ThreadPoolExecutor(16) as pool:
        tokens = list(pool.map(acquire, range(16)))
    assert sum(token is not None for token in tokens) == 1
    winner = next(token for token in tokens if token is not None)
    assert not locks[0].release(resource, "redis:wrong")
    assert redis_server.get("lock:" + resource) == winner
    assert locks[tokens.index(winner)].release(resource, winner)
    assert not redis_server.exists("lock:" + resource)


def test_lock_ttl_expiry_and_stale_owner_cannot_release_successor(redis_server):
    first = RedisDedupLocks(RedisReuseConfig("redis://unused"), client=redis_server)
    second = RedisDedupLocks(RedisReuseConfig("redis://unused"), client=redis_server)
    resource = "warm:" + uuid4().hex
    old = first.acquire(resource, .08)
    assert 0 < redis_server.pttl("lock:" + resource) <= 80
    sleep(.15)
    current = second.acquire(resource, 2)
    assert current and current != old
    assert not first.release(resource, old)
    assert redis_server.get("lock:" + resource) == current
    assert second.release(resource, current)


def test_lock_fault_local_fallback_and_recovery_preserve_ownership(redis_server, monkeypatch, caplog):
    locks = RedisDedupLocks(RedisReuseConfig("redis://unused"), client=redis_server)
    resource = "warm:" + uuid4().hex
    original_set = redis_server.set
    monkeypatch.setattr(redis_server, "set", Mock(side_effect=ConnectionError("private-fixture")))
    token = locks.acquire(resource, 2)
    assert token.startswith("local:")
    assert locks.acquire(resource, 2) is None
    assert not locks.release(resource, "local:wrong")
    monkeypatch.setattr(redis_server, "set", original_set)
    assert locks.acquire(resource, 2) is None
    assert locks.release(resource, token)
    redis_token = locks.acquire(resource, 2)
    monkeypatch.setattr(redis_server, "eval", Mock(side_effect=ConnectionError("private-fixture")))
    assert not locks.release(resource, redis_token)
    assert redis_server.get("lock:" + resource) == redis_token
    assert "process-local" in caplog.text and "private-fixture" not in caplog.text


def test_local_lock_expiry_and_optional_backend(redis_server, monkeypatch):
    local = LocalDedupLocks()
    token = local.acquire("one", .01)
    assert not local.release("one", "wrong")
    sleep(.025)
    newer = local.acquire("one", 1)
    assert newer != token and not local.release("one", token)
    assert local.release("one", newer)
    monkeypatch.delenv("DEDUP_LOCK_BACKEND", raising=False)
    monkeypatch.setenv("REDIS_URL", "unused-invalid-url")
    assert isinstance(configured_dedup_locks(), LocalDedupLocks)


@pytest.mark.parametrize("ttl", [0, -1, float("nan"), float("inf"), 1e308, True, "one"])
def test_lock_invalid_ttl_rejected(ttl):
    with pytest.raises(ValueError):
        LocalDedupLocks().acquire("resource", ttl)


@pytest.mark.parametrize("prefix", ["CONTEXT_DERIVED_CACHE", "PROFILE_CACHE", "DEDUP_LOCK"])
def test_optional_connections_missing_url_and_invalid_config(prefix, monkeypatch, caplog):
    monkeypatch.delenv(prefix + "_BACKEND", raising=False)
    monkeypatch.setenv("REDIS_URL", "unused-invalid-url")
    assert RedisReuseConfig.configured(prefix) is None
    monkeypatch.setenv(prefix + "_BACKEND", "redis")
    monkeypatch.delenv("REDIS_URL")
    assert RedisReuseConfig.configured(prefix) is None
    assert "REDIS_URL missing" in caplog.text
    monkeypatch.setenv("REDIS_URL", "redis://unused")
    monkeypatch.setenv(prefix + "_TTL_SECONDS", "0")
    with pytest.raises(ValueError):
        RedisReuseConfig.configured(prefix)


def test_local_dedup_concurrency_and_expired_slot_cleanup():
    locks = LocalDedupLocks()
    with ThreadPoolExecutor(16) as pool:
        tokens = list(pool.map(lambda _: locks.acquire("one", 1), range(16)))
    assert sum(token is not None for token in tokens) == 1
    assert locks.release("one", next(token for token in tokens if token))
    locks.acquire("expired", .01)
    sleep(.025)
    locks._next_cleanup = 0
    assert locks.acquire("another", 1)
    assert "expired" not in locks._slots


@pytest.mark.parametrize("module,class_name,factory", [
    (derived_cache, "RedisDerivedCache", "_configured_cache"),
    ("app.agent.foundation.profile_cache", "RedisProfileCache", "_configured_cache"),
    ("app.agent.dedup_locks", "RedisDedupLocks", "_configured_locks"),
])
def test_optional_dependency_missing_uses_original_path(module, class_name, factory, monkeypatch, caplog):
    from importlib import import_module
    module = import_module(module) if isinstance(module, str) else module
    monkeypatch.setattr(module, class_name, Mock(side_effect=ImportError))
    configure = getattr(module, factory)
    configure.cache_clear()
    try:
        result = configure(RedisReuseConfig("redis://unused"))
        assert result is None or isinstance(result, LocalDedupLocks)
        assert "dependency unavailable" in caplog.text
    finally:
        configure.cache_clear()


@pytest.mark.parametrize("prefix,mode", [("CONTEXT_DERIVED_CACHE", "wrong"), ("PROFILE_CACHE", "wrong"), ("DEDUP_LOCK", "wrong")])
def test_unknown_backend_is_a_configuration_error(prefix, mode, monkeypatch):
    monkeypatch.setenv(prefix + "_BACKEND", mode)
    with pytest.raises(ValueError, match="backend"):
        RedisReuseConfig.configured(prefix)
