"""Admission acceptance against real Redis and the public JSON/SSE endpoints."""

import logging
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError

import app.api.chat as chat_api
import app.api.rate_limit as rate_limit
from app.agent.foundation.entry_context import EntryContextEngine
from app.agent.foundation.long_horizon.coordinator import ContinuityCoordinator
from app.agent.foundation.long_horizon.service import LongHorizonContextService
from app.agent.foundation.long_horizon.store import SQLiteContinuityStore
from app.agent.foundation.main_graph import build_main_graph
from app.agent.foundation.response import foundation_response
from app.api.rate_limit import (
    GatewayRateLimitMiddleware, RateLimitConfig, RateLimitExceeded,
    RedisRateLimiter, SCRIPT, WindowLimit, enforce_chat_session_limit, get_rate_limiter,
)
from app.main import app
from app.schemas.chat import ChatOutput, FinalStreamEvent
from tests.test_redis_budget import redis_server

__all__ = ["redis_server"]


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch):
    monkeypatch.setenv("RATELIMIT_BACKEND", "none")
    monkeypatch.delenv("REDIS_URL", raising=False)
    get_rate_limiter.cache_clear()
    yield
    get_rate_limiter.cache_clear()


@pytest.fixture
def keys(redis_server):
    allocated = []
    def allocate(layer="session"):
        key = RedisRateLimiter.key(layer, uuid4().hex)
        allocated.append(key)
        return key
    yield allocate
    if allocated:
        redis_server.delete(*allocated)


def at(client, key, timestamp, *, limit=10, window=1000):
    return client.eval(SCRIPT, 1, key, limit, window, timestamp)


@pytest.mark.parametrize("layer", ["gateway", "session"])
def test_real_redis_concurrent_admissions_never_exceed_limit(redis_server, keys, layer):
    key = keys(layer)
    identity = key.split("{")[1][:-1]
    rule = WindowLimit(37, 3600)
    limiters = [RedisRateLimiter(RateLimitConfig("redis://unused", rule, rule), client=redis_server)
                for _ in range(3)]
    barrier = Barrier(24)
    def admit(index):
        if index < 24:
            barrier.wait(timeout=10)
        try:
            limiters[index % 3].require(layer, identity)
            return True
        except RateLimitExceeded as exc:
            assert exc.status_code == 429
            assert int(exc.headers["Retry-After"]) >= 1
            return False
    with ThreadPoolExecutor(max_workers=24) as pool:
        results = list(pool.map(admit, range(240)))
    assert sum(results) == 37
    assert redis_server.hget(key, "current") == "37"
    assert redis_server.hget(key, "previous") == "0"
    assert 0 < redis_server.pttl(key) <= 7_200_000


def test_boundary_burst_fixed_window_admits_twice_sliding_admits_once(redis_server, keys):
    sliding, fixed = keys(), keys()
    # The fixed reference also executes atomically in real Redis.
    fixed_script = """
    local bucket = math.floor(tonumber(ARGV[1]) / 1000)
    local old = tonumber(redis.call('HGET', KEYS[1], 'bucket'))
    local count = tonumber(redis.call('HGET', KEYS[1], 'current')) or 0
    if old ~= bucket then count = 0 end
    if count >= 10 then return 0 end
    redis.call('HSET', KEYS[1], 'bucket', bucket, 'current', count + 1)
    return 1
    """
    fixed_admitted = sliding_admitted = 0
    for timestamp in (999, 1000):
        for _ in range(10):
            fixed_admitted += redis_server.eval(fixed_script, 1, fixed, timestamp)
            sliding_admitted += at(redis_server, sliding, timestamp)[0]
    assert fixed_admitted == 20
    assert sliding_admitted == 10
    assert redis_server.hgetall(sliding)["previous"] == "10"
    assert redis_server.hgetall(sliding)["current"] == "0"


def test_concurrent_weighted_window_admits_only_decayed_capacity(redis_server, keys):
    key = keys()
    for _ in range(10):
        assert at(redis_server, key, 999)[0] == 1
    with ThreadPoolExecutor(max_workers=24) as pool:
        results = list(pool.map(lambda _: at(redis_server, key, 1500), range(240)))
    assert sum(result[0] for result in results) == 5
    counters = redis_server.hgetall(key)
    assert counters["previous"] == "10" and counters["current"] == "5"
    assert int(counters["current"]) * 1000 + int(counters["previous"]) * 500 == 10_000


def test_fractional_estimate_not_rounded_down_and_retry_after_is_sufficient(redis_server, keys):
    key = keys()
    for _ in range(10):
        assert at(redis_server, key, 999)[0] == 1
    assert at(redis_server, key, 1001) == [0, 99]
    assert at(redis_server, key, 1099) == [0, 1]
    assert at(redis_server, key, 1100) == [1, 0]
    assert redis_server.hgetall(key)["current"] == "1"


def test_full_current_retry_includes_next_window_decay_and_idle_reset(redis_server, keys):
    key = keys()
    for _ in range(3):
        assert at(redis_server, key, 0, limit=3) == [1, 0]
    assert at(redis_server, key, 0, limit=3) == [0, 1334]
    assert at(redis_server, key, 1333, limit=3) == [0, 1]
    assert at(redis_server, key, 1334, limit=3) == [1, 0]
    assert at(redis_server, key, 5000, limit=3) == [1, 0]
    assert redis_server.hgetall(key) == {"bucket": "5", "current": "1", "previous": "0", "window_ms": "1000"}


def test_backwards_clock_does_not_reset_consumed_window(redis_server, keys):
    key = keys()
    assert at(redis_server, key, 1000, limit=1) == [1, 0]
    assert at(redis_server, key, 900, limit=1)[0] == 0
    assert redis_server.hgetall(key)["current"] == "1"


def test_window_configuration_change_does_not_permanently_freeze_old_bucket(redis_server, keys):
    key = keys()
    assert at(redis_server, key, 50_000, limit=1, window=1000) == [1, 0]
    assert at(redis_server, key, 50_000, limit=1, window=1000)[0] == 0
    # The new window's bucket (25) is below the old bucket (50). It is a
    # policy change, rather than a backwards clock that must retain bucket 50.
    assert at(redis_server, key, 50_000, limit=1, window=2000) == [1, 0]
    assert at(redis_server, key, 50_000, limit=1, window=2000) == [0, 4000]
    assert redis_server.hgetall(key)["bucket"] == "25"
    assert redis_server.hgetall(key)["window_ms"] == "2000"


def test_independent_scopes_and_expiry_bound_memory(redis_server, keys):
    one, two, gateway = keys(), keys(), keys("gateway")
    assert at(redis_server, one, 0, limit=1, window=50)[0] == 1
    assert at(redis_server, one, 1, limit=1, window=50)[0] == 0
    assert at(redis_server, two, 1, limit=1, window=50)[0] == 1
    assert at(redis_server, gateway, 1, limit=1, window=50)[0] == 1
    deadline = time.monotonic() + 2
    while redis_server.exists(one) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not redis_server.exists(one, two, gateway)


def test_disabled_backend_never_builds_connection(monkeypatch, caplog):
    monkeypatch.setattr(rate_limit, "redis_client", lambda _: pytest.fail("disabled Redis was constructed"))
    assert get_rate_limiter() is None
    assert not caplog.records


def test_missing_url_and_dependency_fail_open_with_warning(monkeypatch, caplog):
    monkeypatch.setenv("RATELIMIT_BACKEND", "redis")
    assert get_rate_limiter() is None
    assert "REDIS_URL missing; fail-open" in caplog.text
    get_rate_limiter.cache_clear()
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1")
    def missing(_):
        raise ImportError("private dependency diagnostic")
    monkeypatch.setattr(rate_limit, "redis_client", missing)
    assert get_rate_limiter() is None
    assert "ImportError" in caplog.text and "private dependency" not in caplog.text
    assert all(record.levelno == logging.WARNING for record in caplog.records)


def test_configuration_uses_p0_connection_factory_and_all_env_overrides(monkeypatch, redis_server):
    port = redis_server.connection_pool.connection_kwargs["port"]
    monkeypatch.setenv("RATELIMIT_BACKEND", "redis")
    monkeypatch.setenv("REDIS_URL", f"redis://127.0.0.1:{port}")
    for name, value in {"GATEWAY_LIMIT": "900", "GATEWAY_WINDOW_SECONDS": "120",
                        "SESSION_LIMIT": "8", "SESSION_WINDOW_SECONDS": "30",
                        "REDIS_TIMEOUT_SECONDS": "0.1"}.items():
        monkeypatch.setenv("RATELIMIT_" + name, value)
    limiter = get_rate_limiter()
    assert limiter.config.gateway == WindowLimit(900, 120)
    assert limiter.config.session == WindowLimit(8, 30)
    options = limiter.client.connection_pool.connection_kwargs
    assert options["port"] == port
    assert options["socket_timeout"] == options["socket_connect_timeout"] == 0.1
    assert limiter.client.ping()


@pytest.mark.parametrize("name,value", [
    ("BACKEND", "invalid"), ("GATEWAY_LIMIT", "0"), ("SESSION_LIMIT", "-1"),
    ("SESSION_LIMIT", "not-an-int"), ("GATEWAY_WINDOW_SECONDS", "0"),
    ("SESSION_WINDOW_SECONDS", "-1"), ("REDIS_TIMEOUT_SECONDS", "0"),
    ("REDIS_TIMEOUT_SECONDS", "nan"), ("REDIS_TIMEOUT_SECONDS", "inf"),
    ("SESSION_LIMIT", str(2**53)),
])
def test_invalid_policy_is_configuration_error(monkeypatch, name, value):
    monkeypatch.setenv("RATELIMIT_BACKEND", "redis")
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1")
    monkeypatch.setenv("RATELIMIT_" + name, value)
    with pytest.raises(ValueError):
        get_rate_limiter()


def test_wrong_type_in_real_redis_fails_open_with_sanitized_warning(redis_server, keys, caplog):
    key = keys()
    identity = key.split("{")[1][:-1]
    redis_server.set(key, "corrupt-counter")
    limiter = RedisRateLimiter(RateLimitConfig("redis://private-url"), client=redis_server)
    limiter.require("session", identity)
    assert "ResponseError" in caplog.text and "fail-open" in caplog.text
    assert identity not in caplog.text and "private-url" not in caplog.text


@pytest.fixture
def http_runtime(tmp_path, redis_server, monkeypatch):
    store = SQLiteContinuityStore(tmp_path / "admission.sqlite")
    coordinator = ContinuityCoordinator(LongHorizonContextService(store))
    monkeypatch.setattr("app.agent.foundation.long_horizon.coordinator.get_continuity_coordinator", lambda: coordinator)
    limiter = RedisRateLimiter(RateLimitConfig("redis://unused", WindowLimit(1000, 60), WindowLimit(2, 60)),
                               client=redis_server)
    monkeypatch.setattr(rate_limit, "get_rate_limiter", lambda: limiter)
    monkeypatch.setattr(chat_api, "get_rate_limiter", lambda: limiter)
    monkeypatch.setattr(chat_api, "get_settings", lambda: SimpleNamespace(agent_graph_mode="foundation"))
    monkeypatch.setattr(chat_api.UserIdentityProvider, "resolve", AsyncMock(return_value=None))
    calls = []
    async def invoke(**kwargs):
        calls.append(kwargs)
        return foundation_response("已处理", [])
    async def stream(**kwargs):
        calls.append(kwargs)
        yield FinalStreamEvent(response=foundation_response("已处理", []))
    for name in ("invoke_foundation_assistant", "invoke_handoff_assistant"):
        monkeypatch.setattr(chat_api, name, invoke)
    for name in ("stream_foundation_assistant", "stream_handoff_assistant"):
        monkeypatch.setattr(chat_api, name, stream)
    async def legacy(**kwargs):
        calls.append(kwargs)
        yield FinalStreamEvent(response=ChatOutput(type="chat", message="已处理", search_query=None))
    monkeypatch.setattr(chat_api, "stream_current_song_assistant", legacy)
    peer = "test-peer-" + uuid4().hex
    client = TestClient(app, client=(peer, 12345))
    state = SimpleNamespace(client=client, coordinator=coordinator, limiter=limiter, calls=calls, peer=peer,
                            threads=[])
    yield state
    allocated = [limiter.key("gateway", peer)]
    for thread, mode in state.threads:
        if mode == "legacy":
            from hashlib import sha256
            allocated.append(limiter.key("session", "legacy:" + sha256(thread.encode()).hexdigest()))
        else:
            conversation = "handoff:v1:" + thread if mode == "handoff" else thread
            task = store.active_task(conversation)
            if task:
                session = store.active_session(task.task_id)
                allocated.append(limiter.key("session", session.session_id))
    redis_server.delete(*allocated)
    client.close()


@pytest.mark.parametrize("path,mode", [("foundation", "foundation"), ("handoff", "handoff"),
                                       ("chat", "foundation"), ("chat", "handoff"), ("chat", "legacy")])
def test_http_session_limit_returns_429_before_json_or_sse_execution(http_runtime, monkeypatch, path, mode):
    h = http_runtime
    monkeypatch.setattr(chat_api, "get_settings", lambda: SimpleNamespace(agent_graph_mode=mode))
    thread = uuid4().hex
    h.threads.append((thread, mode))
    payload = {"thread_id": thread, "message": "暂停播放"}
    for _ in range(2):
        assert h.client.post("/api/ai/" + path, json=payload).status_code == 200
    denied = h.client.post("/api/ai/" + path, json=payload)
    assert denied.status_code == 429
    assert denied.json() == {"detail": {"code": "rate_limit_exceeded", "layer": "session"}}
    assert int(denied.headers["Retry-After"]) >= 1
    assert "event:" not in denied.text
    assert len(h.calls) == 2
    if mode != "legacy":
        conversation = "handoff:v1:" + thread if mode == "handoff" else thread
        session = h.coordinator.service.open_conversation(conversation)
        assert h.limiter.client.hget(h.limiter.key("session", session.session_id), "current") == "2"
        assert not h.limiter.client.exists(h.limiter.key("session", thread))


def test_json_sse_and_lifecycle_requests_share_entering_session_bucket(http_runtime):
    h = http_runtime
    thread = uuid4().hex
    h.threads.append((thread, "foundation"))
    payload = {"thread_id": thread, "message": "暂停播放"}
    assert h.client.post("/api/ai/foundation", json=payload).status_code == 200
    assert h.client.post("/api/ai/chat", json=payload).status_code == 200
    session = h.coordinator.service.open_conversation(thread)
    for body in ({**payload, "new_session": True}, {"thread_id": thread, "message": "", "resume": True}):
        denied = h.client.post("/api/ai/chat", json=body)
        assert denied.status_code == 429 and int(denied.headers["Retry-After"]) >= 1
    assert h.coordinator.service.open_conversation(thread).session_id == session.session_id
    assert len(h.calls) == 2
    other = uuid4().hex
    h.threads.append((other, "foundation"))
    assert h.client.post("/api/ai/foundation", json={**payload, "thread_id": other}).status_code == 200


def test_gateway_uses_peer_ip_not_spoofed_forwarded_headers(http_runtime):
    h = http_runtime
    h.limiter.config = RateLimitConfig("redis://unused", WindowLimit(1, 60), WindowLimit(20, 60))
    thread = uuid4().hex
    h.threads.append((thread, "foundation"))
    payload = {"thread_id": thread, "message": "暂停播放"}
    assert h.client.post("/api/ai/foundation", json=payload, headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 200
    denied = h.client.post("/api/ai/foundation", json=payload, headers={"X-Forwarded-For": "2.2.2.2"})
    assert denied.status_code == 429 and denied.json()["detail"]["layer"] == "gateway"
    assert int(denied.headers["Retry-After"]) >= 1
    assert len(h.calls) == 1
    assert h.client.get("/health").status_code == 200
    assert h.client.options("/api/ai/chat").status_code != 429
    assert any(middleware.cls == GatewayRateLimitMiddleware for middleware in app.user_middleware)


@pytest.mark.parametrize("path", ["foundation", "handoff", "chat"])
def test_real_disconnected_redis_fail_open_at_both_layers(http_runtime, caplog, path):
    h = http_runtime
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    h.limiter.client = rate_limit.redis_client(RateLimitConfig(f"redis://127.0.0.1:{port}", timeout_seconds=0.05))
    thread = uuid4().hex
    h.threads.append((thread, "handoff" if path == "handoff" else "foundation"))
    response = h.client.post("/api/ai/" + path, json={"thread_id": thread, "message": "暂停播放"})
    assert response.status_code == 200
    assert len(h.calls) == 1
    for layer in ("gateway", "session"):
        assert any(f"({layer}, {error})" in caplog.text for error in ("ConnectionError", "TimeoutError"))
    assert "fail-open" in caplog.text
    assert thread not in caplog.text
    assert all(record.levelno == logging.WARNING for record in caplog.records)


def test_script_reply_loss_fail_open_does_not_retry_or_fallback(redis_server, keys, caplog):
    key = keys()
    identity = key.split("{")[1][:-1]
    calls = []
    class LostReply:
        def eval(self, *args):
            calls.append(args)
            redis_server.eval(*args)
            raise ConnectionError("private-url-and-token")
    limiter = RedisRateLimiter(RateLimitConfig("redis://unused"), client=LostReply())
    limiter.require("session", identity)
    assert len(calls) == 1 and redis_server.hget(key, "current") == "1"
    assert "fail-open" in caplog.text and "private-url-and-token" not in caplog.text


def test_http_gate_runs_before_real_main_graph_rule_preflight(http_runtime, monkeypatch):
    h = http_runtime
    thread = uuid4().hex
    h.threads.append((thread, "foundation"))
    routed = []
    class Rules(EntryContextEngine):
        def preflight(self, state):
            routed.append(state["current_input"])
            return super().preflight(state)
    class NoFallback:
        async def route(self, text, context):
            pytest.fail("complete local rule unexpectedly invoked model/LLM fallback")
    graph = build_main_graph(entry_context=Rules(), intent_router=NoFallback()).compile()
    # Exercise the actual HTTP -> worker -> MainGraph -> RuleRouter path.
    from app.agent.foundation.streaming import invoke_foundation_assistant, stream_foundation_assistant
    monkeypatch.setattr(chat_api, "invoke_foundation_assistant", invoke_foundation_assistant)
    monkeypatch.setattr(chat_api, "stream_foundation_assistant", stream_foundation_assistant)
    monkeypatch.setattr("app.agent.foundation.streaming.get_foundation_graph", lambda: graph)
    payload = {"thread_id": thread, "message": "暂停播放"}
    assert h.client.post("/api/ai/foundation", json=payload).status_code == 200
    assert h.client.post("/api/ai/chat", json=payload).status_code == 200
    denied = h.client.post("/api/ai/chat", json=payload)
    assert denied.status_code == 429 and int(denied.headers["Retry-After"]) >= 1
    assert routed == ["暂停播放", "暂停播放"]


@pytest.mark.asyncio
async def test_disabled_gate_does_not_touch_continuity(monkeypatch):
    monkeypatch.setattr("app.agent.foundation.long_horizon.coordinator.get_continuity_coordinator",
                        lambda: pytest.fail("disabled gate touched continuity"))
    await enforce_chat_session_limit("thread", "foundation", None)


def test_validation_still_rejects_invalid_body_without_graph_work(http_runtime):
    h = http_runtime
    assert h.client.post("/api/ai/foundation", json={"message": " "}).status_code == 422
    assert not h.calls


@pytest.mark.parametrize("missing", ["url", "dependency"])
def test_missing_redis_configuration_allows_http_and_warns(http_runtime, monkeypatch, caplog, missing):
    h = http_runtime
    monkeypatch.setattr(rate_limit, "get_rate_limiter", get_rate_limiter)
    monkeypatch.setattr(chat_api, "get_rate_limiter", get_rate_limiter)
    monkeypatch.setenv("RATELIMIT_BACKEND", "redis")
    if missing == "dependency":
        monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1")
        def missing_dependency(_):
            raise ImportError("controlled missing optional dependency")
        monkeypatch.setattr(rate_limit, "redis_client", missing_dependency)
    response = h.client.post("/api/ai/chat", json={"message": "暂停播放"})
    assert response.status_code == 200 and len(h.calls) == 1
    assert "fail-open" in caplog.text
    assert all(record.levelno == logging.WARNING for record in caplog.records)
