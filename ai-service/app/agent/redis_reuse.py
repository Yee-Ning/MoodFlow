"""Optional Redis connections for P2 caches and deduplication slot locks."""

import logging
import math
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RedisReuseConfig:
    url: str
    ttl_seconds: int = 300
    timeout_seconds: float = 0.25

    def __post_init__(self):
        if self.ttl_seconds <= 0 or not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("Redis reuse TTL and timeout must be positive")

    @classmethod
    def configured(cls, prefix, *, ttl_seconds=300):
        mode = os.getenv(prefix + "_BACKEND", "none").strip().lower()
        if mode == "none":
            return None
        if mode != "redis":
            raise ValueError("unsupported Redis reuse backend")
        url = os.getenv("REDIS_URL", "").strip()
        if not url:
            logger.warning("Redis %s unavailable: REDIS_URL missing; using original path", prefix)
            return None
        return cls(url, int(os.getenv(prefix + "_TTL_SECONDS", str(ttl_seconds))),
                   float(os.getenv(prefix + "_TIMEOUT_SECONDS", "0.25")))


def redis_client(config):
    from redis import Redis
    return Redis.from_url(config.url, decode_responses=True,
                          socket_timeout=config.timeout_seconds,
                          socket_connect_timeout=config.timeout_seconds, retry_on_timeout=False)
