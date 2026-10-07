"""Real Redis cache-aside, invalidation races and optional configuration."""

from concurrent.futures import ThreadPoolExecutor
import json
from threading import Event
from unittest.mock import Mock
from uuid import uuid4

import pytest

from app.agent.session_cache import RedisSessionCache, SessionCacheConfig, configured_session_cache, _configured_cache
from tests.test_redis_budget import redis_server

__all__ = ["redis_server"]


@pytest.fixture
def cache(redis_server):
    result = RedisSessionCache(SessionCacheConfig("redis://unused", 120, 20), client=redis_server)
    yield result


def test_real_hash_miss_then_hit_never_calls_cold_loader(cache):
    key = cache.session_key(uuid4().hex)
    load = Mock(return_value={"stage": "working"})
    try:
        first = cache.read(key, "state", load, encode=json.dumps, decode=json.loads)
        assert load.call_count == 1 and cache.client.type(key) == "hash"
        load.reset_mock()
        assert cache.read(key, "state", load, encode=json.dumps, decode=json.loads) == first
        load.assert_not_called()
        assert 0 < cache.client.ttl(key) <= 120
    finally:
        cache.client.delete(key)


def test_empty_key_invalidation_blocks_old_inflight_refill(cache):
    key = cache.view_key(uuid4().hex)
    fetched, resume = Event(), Event()

    def stale_load():
        fetched.set()
        assert resume.wait(10)
        return {"revision": 1}

    try:
        with ThreadPoolExecutor(1) as pool:
            future = pool.submit(cache.read, key, "view", stale_load, encode=json.dumps, decode=json.loads)
            assert fetched.wait(10)
            assert cache.invalidate(key)
            resume.set()
            assert future.result(timeout=10) == {"revision": 1}
        assert cache.client.hget(key, "view") is None
        assert cache.read(key, "view", lambda: {"revision": 2}, encode=json.dumps, decode=json.loads) == {"revision": 2}
        assert 0 < cache.client.ttl(key) <= 20
    finally:
        cache.client.delete(key)


def test_loader_failure_is_not_retried_or_cached(cache):
    key = cache.session_key(uuid4().hex)
    load = Mock(side_effect=RuntimeError("database failed"))
    with pytest.raises(RuntimeError, match="database failed"):
        cache.read(key, "state", load, encode=json.dumps, decode=json.loads)
    load.assert_called_once()
    assert not cache.client.exists(key)


def test_bad_document_falls_back_without_exposing_content(cache, caplog):
    key = cache.session_key(uuid4().hex)
    try:
        cache.client.hset(key, "state", "private-invalid-fixture")
        assert cache.read(key, "state", lambda: {"ok": True}, encode=json.dumps, decode=json.loads) == {"ok": True}
        assert "invalid document" in caplog.text and "private-invalid-fixture" not in caplog.text
        assert cache.client.hget(key, "state") is None
    finally:
        cache.client.delete(key)


def test_default_does_not_inspect_redis_connection(monkeypatch):
    monkeypatch.delenv("SESSION_CACHE_BACKEND", raising=False)
    monkeypatch.setenv("REDIS_URL", "invalid-unused-url")
    assert configured_session_cache() is None


def test_optional_config_missing_url_and_dependency_fall_back(monkeypatch, caplog):
    monkeypatch.setenv("SESSION_CACHE_BACKEND", "redis")
    monkeypatch.delenv("REDIS_URL", raising=False)
    assert configured_session_cache() is None
    assert "REDIS_URL missing" in caplog.text
    monkeypatch.setenv("REDIS_URL", "redis://synthetic-private-fixture")
    monkeypatch.setattr("app.agent.session_cache.RedisSessionCache", Mock(side_effect=ImportError))
    _configured_cache.cache_clear()
    try:
        assert configured_session_cache() is None
        assert "dependency missing" in caplog.text and "synthetic-private-fixture" not in caplog.text
    finally:
        _configured_cache.cache_clear()


@pytest.mark.parametrize("option,value", [("SESSION_CACHE_TTL_SECONDS", "0"), ("SESSION_VIEW_TTL_SECONDS", "0"),
                                          ("SESSION_VIEW_TTL_SECONDS", "9999"), ("SESSION_CACHE_TIMEOUT_SECONDS", "-1"),
                                          ("SESSION_CACHE_TIMEOUT_SECONDS", "nan")])
def test_invalid_config_rejected(monkeypatch, option, value):
    monkeypatch.setenv("SESSION_CACHE_BACKEND", "redis")
    monkeypatch.setenv("REDIS_URL", "redis://unused")
    monkeypatch.setenv(option, value)
    with pytest.raises(ValueError):
        SessionCacheConfig.configured()


def test_administrative_fault_disables_cached_reads(cache, monkeypatch, caplog):
    from redis.exceptions import ConnectionError
    monkeypatch.setattr(cache.client, "scan_iter", Mock(side_effect=ConnectionError("private-fixture")))
    cache.invalidate_sessions()
    assert cache.read("session:fixture", "state", lambda: "pg", encode=json.dumps, decode=json.loads) == "pg"
    assert "administrative invalidation" in caplog.text and "private-fixture" not in caplog.text


def test_variant_count_is_bounded(cache):
    key = cache.view_key(uuid4().hex)
    try:
        for index in range(40):
            cache.read(key, str(index), lambda: index, encode=json.dumps, decode=json.loads)
        assert len([field for field in cache.client.hkeys(key) if not field.startswith("__")]) <= 32
        assert cache.client.hlen(key) <= 64
    finally:
        cache.client.delete(key)


def test_refilling_another_field_cannot_extend_old_field_freshness(cache, monkeypatch):
    key = cache.view_key(uuid4().hex)
    clock = [100.0]
    monkeypatch.setattr(cache, "_now", lambda pipe: clock[0])
    load = Mock(return_value="first")
    try:
        assert cache.read(key, "old", load, encode=json.dumps, decode=json.loads) == "first"
        clock[0] = 121.0
        cache.read(key, "new", lambda: "newer", encode=json.dumps, decode=json.loads)
        assert cache.client.ttl(key) > 0  # Hash is live because another variant was filled.
        load.reset_mock()
        load.return_value = "updated"
        assert cache.read(key, "old", load, encode=json.dumps, decode=json.loads) == "updated"
        load.assert_called_once()
    finally:
        cache.client.delete(key)
