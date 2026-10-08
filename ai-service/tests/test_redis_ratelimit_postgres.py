"""Session admission shares P0's production PostgreSQL lifecycle and fence."""

from uuid import uuid4

import pytest

from app.agent.foundation.long_horizon.coordinator import ContinuityCoordinator
from app.agent.foundation.long_horizon.history_writer import digest
from app.agent.foundation.long_horizon.postgres_store import PostgresContinuityStore
from app.agent.foundation.long_horizon.service import LongHorizonContextService
from app.api.rate_limit import (
    RateLimitConfig, RateLimitExceeded, RedisRateLimiter, WindowLimit, enforce_chat_session_limit,
)
from tests.test_context_loop_postgres_recovery import BASE, database
from tests.test_redis_budget import redis_server

__all__ = ["database", "redis_server"]
pytestmark = pytest.mark.skipif(not BASE, reason="isolated PostgreSQL acceptance requires DATABASE_URL")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["foundation", "handoff"])
async def test_real_postgres_session_identity_and_fence_release(database, redis_server, monkeypatch, mode):
    store = PostgresContinuityStore(database)
    coordinator = ContinuityCoordinator(LongHorizonContextService(store))
    monkeypatch.setattr("app.agent.foundation.long_horizon.coordinator.get_continuity_coordinator", lambda: coordinator)
    limiter = RedisRateLimiter(RateLimitConfig("redis://unused", session=WindowLimit(2, 3600)), client=redis_server)
    thread = uuid4().hex
    conversation = "handoff:v1:" + thread if mode == "handoff" else thread
    key = None
    try:
        await enforce_chat_session_limit(thread, mode, "user-test", limiter=limiter)
        session = coordinator.service.open_conversation(conversation)
        key = limiter.key("session", session.session_id)
        await enforce_chat_session_limit(thread, mode, "user-test", limiter=limiter)
        with pytest.raises(RateLimitExceeded) as denied:
            await enforce_chat_session_limit(thread, mode, "user-test", limiter=limiter)
        assert denied.value.status_code == 429 and int(denied.value.headers["Retry-After"]) >= 1
        assert redis_server.hget(key, "current") == "2"
        assert coordinator.service.open_conversation(conversation).session_id == session.session_id
        # Both success and rejection release the short admission lease. The
        # unchanged graph runner can immediately acquire its normal fence.
        lease = coordinator.locks.acquire(conversation, digest([mode, "user-test"]))
        lease.check()
        lease.release()
    finally:
        if key:
            redis_server.delete(key)
        store.close()
