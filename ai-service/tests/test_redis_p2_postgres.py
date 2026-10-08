"""P2 cache-aside against actual immutable PostgreSQL profile versions."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
import socket
from unittest.mock import Mock
from uuid import uuid4

import pytest

from app.agent.foundation import derived_cache
from app.agent.foundation.context_engineering import build_context
from app.agent.foundation.context_sources import SourceRegistry, SourceRequest
from app.agent.foundation.profile_cache import CachedProfileStore, RedisProfileCache
from app.agent.foundation.routing import RouteKey
from app.agent.foundation.user_profile import PostgresProfileStore, ProfileWriter
from app.agent.dedup_locks import RedisDedupLocks
from app.agent.redis_reuse import RedisReuseConfig
from tests.test_context_entry_phase1 import entry
from tests.test_context_loop_postgres_recovery import database
from tests.test_redis_budget import redis_server

__all__ = ["database", "redis_server"]


@pytest.fixture
def profile(database, redis_server):
    cold = PostgresProfileStore(database)
    cache = RedisProfileCache(RedisReuseConfig("redis://unused", 120), client=redis_server)
    store = CachedProfileStore(cold, cache)
    try:
        yield store
    finally:
        store.close()


def test_real_pg_profile_miss_hit_zero_queries_update_and_source_integration(profile, monkeypatch):
    user = uuid4().hex
    first = ProfileWriter(profile).update(user, [entry()], expected_version=0)
    query = Mock(wraps=profile.store.pool.connection)
    monkeypatch.setattr(profile.store.pool, "connection", query)
    assert profile.read(user) == first and query.call_count == 1
    query.reset_mock()
    assert profile.read(user) == first
    candidates, status = SourceRegistry().collect(SourceRequest("音乐", {"user_id": user}, profile_store=profile))
    assert candidates and status["user_profile"] == "available"
    query.assert_not_called()
    second = ProfileWriter(profile).update(user, [entry(value="喜欢爵士")], expected_version=1)
    assert profile.read(user) == second
    query.reset_mock()
    candidates, _ = SourceRegistry().collect(SourceRequest("音乐", {"user_id": user}, profile_store=profile))
    assert candidates[0].metadata["version"] == 2
    query.assert_not_called()
    assert profile.read(user, version=1) == first


def test_pg_old_inflight_read_cannot_refill_after_update(profile):
    user = uuid4().hex
    first = ProfileWriter(profile).update(user, [entry()], expected_version=0)
    fetched, resume = Event(), Event()
    def old_load():
        value = profile.store.read(user)
        fetched.set()
        assert resume.wait(15)
        return value
    with ThreadPoolExecutor(1) as pool:
        result = pool.submit(profile.cache.read, user, None, old_load)
        assert fetched.wait(15)
        second = ProfileWriter(profile).update(user, [entry(value="更新后的画像")], expected_version=1)
        resume.set()
        assert result.result(timeout=15) == first
    assert profile.cache.client.get(profile.cache.key(user, "latest")) == "0"
    assert not profile.cache.client.exists(profile.cache.key(user, 1))
    assert profile.read(user) == second


def test_profile_update_changes_materialized_derived_cache_inputs(profile, redis_server, monkeypatch):
    cache = derived_cache.RedisDerivedCache(RedisReuseConfig("redis://unused"), client=redis_server)
    monkeypatch.setattr(derived_cache, "configured_derived_cache", lambda: cache)
    user = uuid4().hex
    ProfileWriter(profile).update(user, [entry()], expected_version=0)
    def pack():
        candidates, _ = SourceRegistry().collect(SourceRequest("音乐", {"user_id": user}, profile_store=profile))
        return build_context(RouteKey.GENERAL_CHAT, query="音乐", system="rules", registered_items=candidates,
            continuity_context={"user_id": user}, intent={"routes": ["general_chat"]})
    first = pack()
    prefix = "contextpack:derived:" + user + ":*"
    keys = set(redis_server.scan_iter(match=prefix))
    ProfileWriter(profile).update(user, [entry(value="更新后的画像")], expected_version=1)
    second = pack()
    assert second != first and second.items[-1].content == first.items[-1].content
    assert set(redis_server.scan_iter(match=prefix)) - keys
    assert next(item for item in second.items if item.type == "user_profile").metadata["version"] == 2


def test_configured_pg_cache_is_optional_and_closes_original_pool(database, redis_server, monkeypatch):
    from app.agent.foundation.user_profile import close_profile_store, get_profile_store
    cache = RedisProfileCache(RedisReuseConfig("redis://unused"), client=redis_server)
    monkeypatch.setenv("PERSISTENCE_BACKEND", "postgres")
    monkeypatch.setenv("DATABASE_URL", database)
    monkeypatch.setattr("app.agent.foundation.profile_cache.configured_profile_cache", lambda: cache)
    close_profile_store()
    try:
        store = get_profile_store()
        assert isinstance(store, CachedProfileStore)
        pool = store.store.pool
        ProfileWriter(store).update(uuid4().hex, [entry()], expected_version=0)
    finally:
        close_profile_store()
    assert pool.closed


def test_real_connection_refusal_falls_back_for_all_three_components(profile, monkeypatch, caplog):
    user = uuid4().hex
    first = ProfileWriter(profile).update(user, [entry()], expected_version=0)
    arguments = {"query": "音乐", "system": "rules", "continuity_context": {"user_id": user}}
    expected = build_context(RouteKey.GENERAL_CHAT, **arguments)
    with socket.socket() as unavailable:
        unavailable.bind(("127.0.0.1", 0))  # Reserved, deliberately not listening.
        config = RedisReuseConfig(f"redis://127.0.0.1:{unavailable.getsockname()[1]}", timeout_seconds=.05)
        hot_profile = RedisProfileCache(config)
        hot_derived = derived_cache.RedisDerivedCache(config)
        locks = RedisDedupLocks(config)
        try:
            query = Mock(wraps=profile.store.pool.connection)
            monkeypatch.setattr(profile.store.pool, "connection", query)
            assert CachedProfileStore(profile.store, hot_profile).read(user) == first
            assert query.call_count == 1
            monkeypatch.setattr(derived_cache, "configured_derived_cache", lambda: hot_derived)
            assert build_context(RouteKey.GENERAL_CHAT, **arguments) == expected
            token = locks.acquire("new-dedup:" + user, 1)
            assert token.startswith("local:")
            assert locks.acquire("new-dedup:" + user, 1) is None
            assert locks.release("new-dedup:" + user, token)
            assert "profile cache unavailable" in caplog.text
            assert "derived cache unavailable" in caplog.text
            assert "Redis dedup lock unavailable" in caplog.text
        finally:
            for component in (hot_profile, hot_derived, locks):
                component.client.close()
