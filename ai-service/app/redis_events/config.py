"""P3 switches are opt-in and reuse P0/P1's optional REDIS_URL dependency."""

from dataclasses import dataclass
import logging
import math
import os

logger = logging.getLogger(__name__)


def positive(name, default, *, integer=False):
    value = float(os.getenv(name, str(default)))
    if not math.isfinite(value) or value <= 0 or (integer and value != int(value)):
        raise ValueError(f"{name} must be a finite positive {'integer' if integer else 'number'}")
    return int(value) if integer else value


def enabled(name):
    value = os.getenv(name, "none").strip().lower()
    if value not in {"none", "redis"}:
        raise ValueError(f"{name} must be none or redis")
    return value == "redis"


@dataclass(frozen=True)
class EventsConfig:
    transfer_ttl_ms: int = 3_600_000
    stream_ttl: int = 86_400
    stream_maxlen: int = 1_000
    flush_interval: float = 60
    timeout: float = 0.25

    @classmethod
    def from_env(cls):
        return cls(
            positive("TRANSFER_IDEMPOTENCY_TTL_MS", 3_600_000, integer=True),
            positive("PROGRESS_EVENTS_TTL_SECONDS", 86_400, integer=True),
            positive("PROGRESS_EVENTS_MAXLEN", 1_000, integer=True),
            positive("PLAYCOUNT_FLUSH_INTERVAL_SECONDS", 60),
            positive("REDIS_EVENTS_TIMEOUT_SECONDS", 0.25),
        )


def redis_client(config):
    url = os.getenv("REDIS_URL", "").strip()
    if not url:
        logger.warning("P3 Redis URL absent; using PostgreSQL")
        return None
    try:
        from redis import Redis
    except ImportError:
        logger.warning("P3 Redis dependency absent; using PostgreSQL")
        return None
    return Redis.from_url(url, decode_responses=True, socket_timeout=config.timeout,
                          socket_connect_timeout=config.timeout)


def redis_errors():
    # Importing P3 with all switches off does not require redis to be installed.
    try:
        from redis.exceptions import RedisError
    except ImportError:
        return (OSError,)
    return (RedisError, OSError)
