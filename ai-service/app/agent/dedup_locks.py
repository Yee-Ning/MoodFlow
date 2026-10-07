"""TTL slot locks for NEW dedup work, never correctness-critical mutual exclusion.

WorldLine's asyncio + PG advisory locks remain unchanged. No Redlock: a single
Redis instance is sufficient for these expiring duplicate-suppression slots.
Outages preserve process-local suppression, not cross-process exclusion.
"""

import logging
import math
from functools import lru_cache
from pathlib import Path
from threading import RLock
from time import monotonic
from uuid import uuid4

from app.agent.redis_reuse import RedisReuseConfig, redis_client

logger = logging.getLogger(__name__)


def _validate(resource, ttl):
    if not isinstance(resource, str) or not resource or len(resource) > 512:
        raise ValueError("dedup resource must be a nonempty bounded string")
    if isinstance(ttl, bool) or not isinstance(ttl, (int, float)) or not math.isfinite(ttl) or ttl <= 0:
        raise ValueError("dedup TTL seconds must be finite and positive")
    if ttl > (2**63 - 1) / 1000:
        raise ValueError("dedup TTL exceeds Redis bounds")
    milliseconds = math.ceil(ttl * 1000)
    return milliseconds


class LocalDedupLocks:
    def __init__(self):
        self._slots = {}
        self._mutex = RLock()
        self._next_cleanup = 0

    def _prune(self):
        now = monotonic()
        if now >= self._next_cleanup:
            self._slots = {key: row for key, row in self._slots.items() if row[1] > now}
            self._next_cleanup = now + 1

    def acquire(self, resource, ttl):
        milliseconds = _validate(resource, ttl)
        with self._mutex:
            self._prune()
            old = self._slots.get(resource)
            if old is not None and old[1] > monotonic():
                return None
            token = "local:" + uuid4().hex
            self._slots[resource] = (token, monotonic() + milliseconds / 1000)
            return token

    def release(self, resource, token):
        _validate(resource, 1)
        with self._mutex:
            old = self._slots.get(resource)
            if old is None or old[0] != token or old[1] <= monotonic():
                return False
            del self._slots[resource]
            return True


class RedisDedupLocks:
    def __init__(self, config, *, client=None, fallback=None):
        from redis.exceptions import RedisError
        self.config = config
        self.client = client if client is not None else redis_client(config)
        self.fallback = fallback if fallback is not None else LocalDedupLocks()
        self._redis_error = RedisError
        self._release_script = Path(__file__).with_name("lua").joinpath("release_dedup_lock.lua").read_text(encoding="utf-8")

    def acquire(self, resource, ttl):
        milliseconds = _validate(resource, ttl)
        # Local shadow slots prevent a same-process duplicate across an outage/recovery.
        with self.fallback._mutex:
            self.fallback._prune()
            old = self.fallback._slots.get(resource)
            if old is not None and old[1] > monotonic():
                return None
            token = "redis:" + uuid4().hex
            try:
                if not self.client.set("lock:" + resource, token, nx=True, px=milliseconds):
                    return None
                self.fallback._slots[resource] = (token, monotonic() + milliseconds / 1000)
                return token
            except self._redis_error:
                logger.warning("Redis dedup lock unavailable; using process-local deduplication")
                return self.fallback.acquire(resource, ttl)

    def release(self, resource, token):
        _validate(resource, 1)
        if not isinstance(token, str):
            return False
        if token.startswith("local:"):
            return self.fallback.release(resource, token)
        try:
            released = bool(self.client.eval(self._release_script, 1, "lock:" + resource, token))
            if released:
                self.fallback.release(resource, token)
            return released
        except self._redis_error:
            logger.warning("Redis dedup lock release unavailable; slot will expire by TTL")
            return False


_local = LocalDedupLocks()


@lru_cache(maxsize=4)
def _configured_locks(config):
    try:
        return RedisDedupLocks(config, fallback=_local)
    except (ImportError, ValueError):
        logger.warning("Redis dedup lock connection/dependency unavailable; using process-local deduplication")
        return _local


def configured_dedup_locks():
    config = RedisReuseConfig.configured("DEDUP_LOCK")
    return _configured_locks(config) if config is not None else _local
