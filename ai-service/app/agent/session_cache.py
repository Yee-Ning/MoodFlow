"""Optional cache-aside reads. PostgreSQL remains the only durable authority."""

import json
import logging
import math
import os
from contextvars import ContextVar
from dataclasses import dataclass
from functools import lru_cache, wraps
from hashlib import sha256
from inspect import iscoroutinefunction
from threading import RLock
from uuid import uuid4

logger = logging.getLogger(__name__)
_pg_evidence = ContextVar("session_cache_pg_evidence", default=False)


def authoritative_session_evidence(function):
    """Execution/recovery evidence must observe current PG even if eviction was lost."""
    if iscoroutinefunction(function):
        @wraps(function)
        async def read_async(*args, **kwargs):
            token = _pg_evidence.set(True)
            try:
                return await function(*args, **kwargs)
            finally:
                _pg_evidence.reset(token)
        return read_async

    @wraps(function)
    def read(*args, **kwargs):
        token = _pg_evidence.set(True)
        try:
            return function(*args, **kwargs)
        finally:
            _pg_evidence.reset(token)
    return read


@dataclass(frozen=True)
class SessionCacheConfig:
    url: str
    ttl_seconds: int = 3600
    view_ttl_seconds: int = 60
    timeout_seconds: float = 0.25

    def __post_init__(self):
        if self.ttl_seconds <= 0 or not 0 < self.view_ttl_seconds <= self.ttl_seconds or not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("Session cache TTLs and timeout must be positive; view TTL must not exceed session TTL")

    @classmethod
    def configured(cls):
        mode = os.getenv("SESSION_CACHE_BACKEND", "none").strip().lower()
        if mode == "none":
            return None
        if mode != "redis":
            raise ValueError("unsupported session cache backend")
        url = os.getenv("REDIS_URL", "").strip()
        if not url:
            logger.warning("Redis session cache unavailable: REDIS_URL missing; reading PostgreSQL")
            return None
        return cls(url, int(os.getenv("SESSION_CACHE_TTL_SECONDS", "3600")),
                   int(os.getenv("SESSION_VIEW_TTL_SECONDS", "60")),
                   float(os.getenv("SESSION_CACHE_TIMEOUT_SECONDS", "0.25")))


def fingerprint(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


class RedisSessionCache:
    """WATCH prevents a read started before invalidation from refilling stale data.

    Invalidation leaves a generation tombstone even for a previously absent key.
    Cached reads never swallow a loader/database error or retry its side effects.
    """

    def __init__(self, config, *, client=None):
        from redis import Redis
        from redis.exceptions import RedisError, WatchError

        self.config = config
        self.client = client if client is not None else Redis.from_url(
            config.url, decode_responses=True, socket_timeout=config.timeout_seconds,
            socket_connect_timeout=config.timeout_seconds, retry_on_timeout=False,
        )
        self._redis_error, self._watch_error = RedisError, WatchError
        self._dirty = set()
        self._lock = RLock()
        self._disabled = False

    @staticmethod
    def session_key(session_id):
        return "session:" + session_id

    @staticmethod
    def view_key(session_id):
        return "sessionview:" + session_id

    def _ttl(self, key):
        return self.config.view_ttl_seconds if key.startswith("sessionview:") else self.config.ttl_seconds

    @staticmethod
    def _now(pipe):
        seconds, micros = pipe.time()
        return seconds + micros / 1_000_000

    def _failed(self, key, operation):
        with self._lock:
            self._dirty.add(key)
        # Never log a connection string, exception details or session contents.
        logger.warning("Redis session cache unavailable during %s; reading PostgreSQL", operation)

    def invalidate(self, key):
        if self._disabled:
            return False
        try:
            with self.client.pipeline() as pipe:
                pipe.delete(key)
                pipe.hset(key, "__generation", uuid4().hex)
                pipe.expire(key, self._ttl(key))
                pipe.execute()
            with self._lock:
                self._dirty.discard(key)
            return True
        except self._redis_error:
            self._failed(key, "invalidation")
            return False

    def read(self, key, field, loader, *, encode, decode, tags=None):
        if self._disabled or _pg_evidence.get():
            return loader()
        with self._lock:
            dirty = key in self._dirty
        if dirty and not self.invalidate(key):
            return loader()
        try:
            pipe = self.client.pipeline()
        except self._redis_error:
            self._failed(key, "read")
            return loader()
        try:
            try:
                pipe.watch(key)
                raw, expires_raw = pipe.hmget(key, [field, "__expires:" + field])
            except self._redis_error:
                self._failed(key, "read")
                return loader()
            if raw is not None:
                try:
                    expires = float(expires_raw)
                    if not math.isfinite(expires):
                        raise ValueError("invalid cache expiry")
                    if expires > self._now(pipe):
                        return decode(raw)
                except self._redis_error:
                    self._failed(key, "freshness read")
                    return loader()
                except (ValueError, TypeError, KeyError, AttributeError):
                    logger.warning("Redis session cache invalid document; reading PostgreSQL")
                    try:
                        pipe.unwatch()
                    except self._redis_error:
                        self._failed(key, "invalid document cleanup")
                    self.invalidate(key)
                    # Do not publish using the watch that preceded invalidation.
                    return loader()
            value = loader()
            if value is None:
                return None
            try:
                document = encode(value)
            except (ValueError, TypeError):
                logger.warning("Redis session cache unsupported document; reading PostgreSQL")
                return value
            try:
                # Drafts/namespace variants must not grow one hot hash indefinitely.
                fields = [item for item in pipe.hkeys(key) if not item.startswith("__")]
                expires = self._now(pipe) + self._ttl(key)
                pipe.multi()
                if field not in fields and len(fields) >= 32:
                    removed = fields[:len(fields) - 31]
                    pipe.hdel(key, *[item for old in removed for item in (old, "__expires:" + old)])
                pipe.hset(key, mapping={field: document, "__expires:" + field: str(expires), **(tags or {})})
                pipe.expire(key, self._ttl(key))
                pipe.execute()
            except self._watch_error:
                # Return the DB read, but a concurrent commit owns the newer view.
                pass
            except self._redis_error:
                self._failed(key, "refill")
            return value
        finally:
            try:
                pipe.reset()
            except self._redis_error:
                self._failed(key, "connection cleanup")

    def invalidate_sessions(self, thread_ids=None):
        """Evict working states for administrative checkpoint mutations."""
        try:
            for key in self.client.scan_iter(match="session:*"):
                if thread_ids is None or self.client.hget(key, "__thread_id") in thread_ids:
                    self.invalidate(key)
        except self._redis_error:
            # Administrative mutation must also stop this process trusting old keys.
            logger.warning("Redis session cache unavailable during administrative invalidation; reading PostgreSQL")
            self._disabled = True


@lru_cache(maxsize=4)
def _configured_cache(config):
    try:
        return RedisSessionCache(config)
    except ImportError:
        logger.warning("Redis session cache dependency missing; reading PostgreSQL")
        return None


def configured_session_cache():
    config = SessionCacheConfig.configured()
    return _configured_cache(config) if config is not None else None


def read_session_projection(store, session_id, handoff_id):
    """Replace only the archive read source; legacy stores keep their exact path."""
    reader = getattr(store, "read_session_projection", None)
    return reader(session_id, handoff_id) if reader else store.get_handoff(handoff_id)


def cached_progress_projection(function):
    @wraps(function)
    def read(self, session, event_cursor=0, projection_budget=None, *, draft):
        reader = getattr(self.store, "read_progress_projection", None)
        if reader is None:
            return function(self, session, event_cursor, projection_budget, draft=draft)
        return reader(self, session, event_cursor, projection_budget, draft,
                      lambda: function(self, session, event_cursor, projection_budget, draft=draft))
    return read
