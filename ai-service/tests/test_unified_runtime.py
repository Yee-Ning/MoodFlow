"""Regression coverage for independently developed background runtimes."""

from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize("memory_enabled", [True, False])
async def test_memory_worker_and_redis_flush_share_application_lifespan(monkeypatch, memory_enabled):
    import app.main as main
    from app.agent.foundation.long_horizon import runner
    from app.redis_events import runtime as events

    calls = []

    async def initialize():
        calls.append("initialize")

    async def drain():
        calls.append("drain")

    async def stop_memory():
        calls.append("stop_memory")

    async def start_flush():
        calls.append("start_flush")
        return "flush-handle"

    async def close_flush(handle):
        assert handle == "flush-handle"
        calls.append("close_flush")

    async def close_checkpointer():
        calls.append("close_checkpointer")

    memory = SimpleNamespace(
        worker=lambda: SimpleNamespace(start=lambda: calls.append("start_memory")),
        stop_worker=stop_memory,
    )
    monkeypatch.setattr(main, "load_runtime_environment", lambda: None)
    monkeypatch.setattr(main, "initialize_runtime_checkpointer", initialize)
    monkeypatch.setattr(main, "persistence_backend", lambda: "sqlite")
    monkeypatch.setattr(main, "memory_backend", lambda: "memory" if memory_enabled else "disabled")
    monkeypatch.setattr(main, "get_memory_runtime", lambda: memory)
    monkeypatch.setattr(main, "close_memory_runtime", lambda: calls.append("close_memory"))
    monkeypatch.setattr(main, "close_long_horizon_service", lambda: None)
    monkeypatch.setattr(main, "close_profile_store", lambda: None)
    monkeypatch.setattr(main, "close_runtime_checkpointer", close_checkpointer)
    monkeypatch.setattr(runner, "drain_workers", drain)
    monkeypatch.setattr(events, "start_flush", start_flush)
    monkeypatch.setattr(events, "close_runtime", close_flush)

    async with main.lifespan(main.app):
        calls.append("serve")

    expected = ["initialize"] + (["start_memory"] if memory_enabled else [])
    expected += ["start_flush", "serve", "drain", "close_flush"]
    expected += (["stop_memory"] if memory_enabled else [])
    assert calls == expected + ["close_memory", "close_checkpointer"]
