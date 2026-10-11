"""Optional Redis admission before HTTP streaming and any graph/router work."""

import asyncio
import logging
import math
import os
from dataclasses import dataclass
from functools import lru_cache
from hashlib import sha256
from pathlib import Path

from fastapi import HTTPException
from starlette.responses import JSONResponse

from app.llm.redis_budget import redis_client

logger = logging.getLogger(__name__)
SCRIPT = Path(__file__).with_name("lua").joinpath("sliding_window_counter.lua").read_text(encoding="utf-8")


@dataclass(frozen=True)
class WindowLimit:
    requests: int
    seconds: int

    def __post_init__(self):
        if (type(self.requests) is not int or type(self.seconds) is not int
                or self.requests <= 0 or self.seconds <= 0
                or self.requests * self.seconds * 1000 > (2**53 - 1) // 2):
            raise ValueError("rate limit/window must be positive integers within Lua precision")


@dataclass(frozen=True)
class RateLimitConfig:
    url: str
    gateway: WindowLimit = WindowLimit(600, 60)
    session: WindowLimit = WindowLimit(20, 60)
    timeout_seconds: float = 0.25

    @classmethod
    def configured(cls):
        backend = os.getenv("RATELIMIT_BACKEND", "none").strip().lower()
        if backend == "none":
            return None
        if backend != "redis":
            raise ValueError("RATELIMIT_BACKEND must be none or redis")
        url = os.getenv("REDIS_URL", "").strip()
        if not url:
            logger.warning("Redis rate limit unavailable: REDIS_URL missing; fail-open")
            return None
        timeout = float(os.getenv("RATELIMIT_REDIS_TIMEOUT_SECONDS", "0.25"))
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("rate limit Redis timeout must be finite and positive")
        return cls(url,
            WindowLimit(int(os.getenv("RATELIMIT_GATEWAY_LIMIT", "600")),
                        int(os.getenv("RATELIMIT_GATEWAY_WINDOW_SECONDS", "60"))),
            WindowLimit(int(os.getenv("RATELIMIT_SESSION_LIMIT", "20")),
                        int(os.getenv("RATELIMIT_SESSION_WINDOW_SECONDS", "60"))),
            timeout)


class RateLimitExceeded(HTTPException):
    def __init__(self, layer, retry_ms):
        super().__init__(status_code=429,
            detail={"code": "rate_limit_exceeded", "layer": layer},
            headers={"Retry-After": str(max(1, math.ceil(retry_ms / 1000)))})


class RedisRateLimiter:
    def __init__(self, config, *, client=None):
        self.config = config
        # Reuse P0's optional redis-py connection factory and URL/timeout policy.
        self.client = client if client is not None else redis_client(config)

    @staticmethod
    def key(layer, identity):
        if layer not in {"gateway", "session"} or not identity or "{" in identity or "}" in identity:
            raise ValueError("invalid rate limit identity/layer")
        return f"ratelimit:{layer}:{{{identity}}}"

    def require(self, layer, identity):
        key = self.key(layer, identity)
        rule = getattr(self.config, layer)
        try:
            accepted, retry_ms = self.client.eval(SCRIPT, 1, key, rule.requests, rule.seconds * 1000)
            if accepted not in (0, 1) or retry_ms < 0:
                raise ValueError("invalid rate limit script response")
        except Exception as exc:
            # Connection strings, exception details and identities stay out of logs.
            logger.warning("Redis rate limit unavailable (%s, %s); fail-open", layer, type(exc).__name__)
            return
        if not accepted:
            raise RateLimitExceeded(layer, retry_ms)


@lru_cache(maxsize=1)
def get_rate_limiter():
    config = RateLimitConfig.configured()
    if config is None:
        return None
    try:
        return RedisRateLimiter(config)
    except (ImportError, ValueError) as exc:
        logger.warning("Redis rate limit unavailable (%s); fail-open", type(exc).__name__)
        return None


class GatewayRateLimitMiddleware:
    """Coarse ingress admission; use peer IP, never client-supplied forwarding headers."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (scope["type"] == "http" and scope["path"].startswith("/api/ai/")
                and scope["method"] != "OPTIONS"):
            limiter = get_rate_limiter()
            if limiter is not None:
                peer = scope.get("client")
                try:
                    await asyncio.to_thread(limiter.require, "gateway", peer[0] if peer else "unknown")
                except RateLimitExceeded as exc:
                    await JSONResponse({"detail": exc.detail}, status_code=429,
                                       headers=exc.headers)(scope, receive, send)
                    return
        await self.app(scope, receive, send)


async def enforce_chat_session_limit(thread_id, graph_mode, user_id, *, limiter=None):
    """Resolve the server session under the existing fence before graph entry.

    Charge the entering session before new_session/automatic rollover, so those
    requests cannot reset their own admission bucket. No graph, route or LLM runs
    here. Disabled/unavailable configuration leaves the existing lifecycle alone.
    """
    limiter = limiter if limiter is not None else get_rate_limiter()
    if limiter is None:
        return
    if graph_mode == "legacy":
        # Legacy has no durable session; retain its existing thread-scoped contract.
        identity = "legacy:" + sha256(thread_id.encode("utf-8")).hexdigest()
        await asyncio.to_thread(limiter.require, "session", identity)
        return

    from app.agent.foundation.long_horizon.coordinator import get_continuity_coordinator
    from app.agent.foundation.long_horizon.history_writer import digest

    coordinator = get_continuity_coordinator()
    conversation = f"handoff:v1:{thread_id}" if graph_mode == "handoff" else thread_id

    def admit():
        lease = coordinator.locks.acquire(conversation, digest([graph_mode, user_id]))
        try:
            session = coordinator.service.open_conversation(conversation)
            limiter.require("session", session.session_id)
        finally:
            lease.release()

    await asyncio.to_thread(admit)
