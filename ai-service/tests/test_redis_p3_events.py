"""Opt-in wiring, validation and old HTTP compatibility without external IO."""

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from fastapi.testclient import TestClient
import pytest

from app.main import app
from app.redis_events import runtime
from app.redis_events.config import EventsConfig, enabled, redis_client
from app.redis_events.streams import identifier, public_progress, sequence


def test_all_switches_default_off_without_redis_or_database(monkeypatch):
    for name in ("TRANSFER_IDEMPOTENCY_BACKEND", "PLAYCOUNT_BACKEND", "PROGRESS_EVENTS_BACKEND", "REDIS_URL", "DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)
    runtime.playcounts.cache_clear()
    runtime.progress_events.cache_clear()
    connect = Mock(side_effect=AssertionError("default must not connect"))
    monkeypatch.setattr(runtime, "redis_client", connect)
    assert runtime.playcounts() is None and runtime.progress_events() is None
    runtime.attach_transfers(SimpleNamespace())
    assert not connect.called
    runtime.playcounts.cache_clear()
    runtime.progress_events.cache_clear()


@pytest.mark.parametrize("name", ["TRANSFER_IDEMPOTENCY_BACKEND", "PLAYCOUNT_BACKEND", "PROGRESS_EVENTS_BACKEND"])
def test_invalid_switch_is_rejected(monkeypatch, name):
    monkeypatch.setenv(name, "typo")
    with pytest.raises(ValueError):
        enabled(name)


@pytest.mark.parametrize("name", ["TRANSFER_IDEMPOTENCY_TTL_MS", "PROGRESS_EVENTS_TTL_SECONDS", "PROGRESS_EVENTS_MAXLEN", "PLAYCOUNT_FLUSH_INTERVAL_SECONDS", "REDIS_EVENTS_TIMEOUT_SECONDS"])
@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf"])
def test_invalid_numeric_config_is_rejected(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError):
        EventsConfig.from_env()


def test_missing_url_warns_and_does_not_connect(monkeypatch, caplog):
    monkeypatch.delenv("REDIS_URL", raising=False)
    assert redis_client(EventsConfig()) is None
    assert "absent" in caplog.text and "PostgreSQL" in caplog.text


@pytest.mark.parametrize("cursor", ["", "1", "1-1", "01-0", "-1-0", "9223372036854775808-0", "1-0\nevent: fake"])
def test_resume_cursor_is_bounded_and_canonical(cursor):
    with pytest.raises(ValueError):
        sequence(cursor)


def test_no_generated_text_or_arbitrary_fields_in_progress():
    assert public_progress("intent_router", "completed") == {"event": "update", "node": "intent_router", "status": "completed"}
    with pytest.raises(ValueError):
        public_progress("private model output", "completed")
    with pytest.raises(ValueError):
        public_progress("intent_router", "arbitrary generated text")
    with pytest.raises(ValueError):
        identifier("run\nsecret")


def test_new_endpoints_disabled_and_validation(monkeypatch):
    from app.api import redis_events as api
    monkeypatch.setattr(api, "playcounts", lambda: None)
    monkeypatch.setattr(api, "progress_events", lambda: None)
    client = TestClient(app)
    assert client.post("/api/ai/playcounts/123").status_code == 503
    assert client.get("/api/ai/playcounts/123").status_code == 503
    assert client.get("/api/ai/events/resume?run_id=run:test").status_code == 503
    assert client.get("/api/ai/events/resume?run_id=run:test&limit=0").status_code == 422
    assert client.get("/api/ai/events/resume?run_id=run:test&graph_mode=other").status_code == 422
    assert client.get("/api/ai/events/resume").status_code == 422


@pytest.mark.asyncio
async def test_periodic_flush_shutdown_and_error_recovery(monkeypatch, caplog):
    flushed = asyncio.Event()
    loop = asyncio.get_running_loop()
    attempts = 0
    def flush():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("controlled")
        loop.call_soon_threadsafe(flushed.set)
    counter = SimpleNamespace(flush_all=Mock(side_effect=flush), client=None)
    monkeypatch.setattr(runtime, "playcounts", Mock(return_value=counter))
    monkeypatch.setattr(runtime, "progress_events", Mock(return_value=None))
    monkeypatch.setattr(EventsConfig, "from_env", classmethod(lambda cls: EventsConfig(flush_interval=0.01)))
    task = await runtime.start_flush()
    try:
        await asyncio.wait_for(flushed.wait(), timeout=5)
        assert counter.flush_all.call_count >= 2
        assert "PG flush failed" in caplog.text
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_progress_failure_does_not_change_existing_execution(monkeypatch, caplog):
    service = SimpleNamespace(publish=Mock(side_effect=RuntimeError("controlled")))
    monkeypatch.setattr(runtime, "progress_events", lambda: service)
    call = SimpleNamespace(accounting=SimpleNamespace(identity=SimpleNamespace(run_id="run:test")),
                           identity=SimpleNamespace(conversation_id="thread", graph_mode="foundation"),
                           lease=SimpleNamespace(scope="scope"))
    await runtime.publish_progress(call, "intent_router")
    assert "execution continues" in caplog.text


def test_http_storage_errors_are_generic(monkeypatch):
    from app.api import redis_events as api
    counter = SimpleNamespace(increment=Mock(side_effect=RuntimeError("controlled private details")),
                              read=Mock(side_effect=RuntimeError("controlled private details")))
    monkeypatch.setattr(api, "playcounts", lambda: counter)
    client = TestClient(app)
    for response in (client.post("/api/ai/playcounts/song"), client.get("/api/ai/playcounts/song")):
        assert response.status_code == 503
        assert "private" not in response.text


def test_http_initialization_failure_returns_generic_503(monkeypatch):
    from app.api import redis_events as api
    fail = Mock(side_effect=RuntimeError("controlled private database details"))
    monkeypatch.setattr(api, "playcounts", fail)
    monkeypatch.setattr(api, "progress_events", fail)
    client = TestClient(app)
    for response in (client.post("/api/ai/playcounts/song"), client.get("/api/ai/events/resume?run_id=run:test")):
        assert response.status_code == 503
        assert "private" not in response.text


def test_old_chat_sse_encoding_retains_event_data_only():
    from app.api.chat import _encode_sse
    assert _encode_sse("update", {"node": "intent_router", "status": "completed"}) == 'event: update\ndata: {"node":"intent_router","status":"completed"}\n\n'


@pytest.mark.asyncio
async def test_shutdown_flushes_then_closes_owned_resources(monkeypatch):
    counter = SimpleNamespace(flush_all=Mock(), client=Mock())
    events = SimpleNamespace(client=Mock())
    durable = Mock()
    for factory in (runtime.playcounts, runtime.progress_events, runtime.transfer_client, runtime.store):
        factory.cache_clear()
    monkeypatch.setattr(runtime, "enabled", lambda name: True)
    monkeypatch.setattr(runtime, "redis_client", lambda config: Mock())
    monkeypatch.setattr(runtime, "store", Mock(return_value=durable, cache_info=Mock(return_value=SimpleNamespace(currsize=0)), cache_clear=Mock()))
    # Exercise actual cached factories, then replace only service methods.
    live_counter = runtime.playcounts()
    live_events = runtime.progress_events()
    monkeypatch.setattr(live_counter, "flush_all", counter.flush_all)
    monkeypatch.setattr(live_counter.client, "close", counter.client.close)
    monkeypatch.setattr(live_events.client, "close", events.client.close)
    await runtime.close_runtime(None)
    counter.flush_all.assert_called_once()
    counter.client.close.assert_called_once()
    events.client.close.assert_called_once()


@pytest.mark.asyncio
async def test_periodic_cancellation_joins_inflight_flush(monkeypatch):
    started = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    def flush():
        loop.call_soon_threadsafe(started.set)
        assert release.wait(5)
    monkeypatch.setattr(runtime, "playcounts", lambda: SimpleNamespace(flush_all=flush))
    monkeypatch.setattr(runtime, "progress_events", lambda: None)
    monkeypatch.setattr(EventsConfig, "from_env", classmethod(lambda cls: EventsConfig(flush_interval=0.01)))
    task = await runtime.start_flush()
    try:
        await asyncio.wait_for(started.wait(), 5)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_run_without_id_does_not_publish(monkeypatch):
    service = SimpleNamespace(publish=AsyncMock())
    monkeypatch.setattr(runtime, "progress_events", lambda: service)
    await runtime.publish_progress(SimpleNamespace(accounting=SimpleNamespace(identity=SimpleNamespace(run_id=None)), graph_input=None), "intent_router")
    service.publish.assert_not_called()
