"""Public comment totals are a popularity proxy, never playback counts or facts."""

import asyncio
import json
import logging
import math
import time
from dataclasses import replace
from functools import lru_cache
from weakref import WeakKeyDictionary

from app.agent.foundation.derived_cache import RedisDerivedCache, _hash
from app.agent.redis_reuse import RedisReuseConfig
from app.llm.runtime import active_ledger
from app.tools.music import MusicServiceClient, MusicServiceError

logger = logging.getLogger(__name__)
POPULARITY_VERSION = "netease-comment-total-v1-popularity-v1"
POPULARITY_TTL_SECONDS = 7 * 24 * 60 * 60
MAX_POPULARITY_BATCH = 60  # The existing Curator candidate window is at most 60.


class RedisPopularityCache(RedisDerivedCache):
    """Reuse the P2 connection/failure boundary with a separate value contract."""

    @staticmethod
    def key(song_id):
        return f"contextpack:derived:public-song-popularity:{_hash({'song_id': song_id})}:{POPULARITY_VERSION}"

    def load(self, song_id):
        try:
            raw = self.client.get(self.key(song_id))
            if raw is None:
                return None
            if len(raw.encode()) > 256:
                raise ValueError("oversize popularity entry")
            row = json.loads(raw)
            if (not isinstance(row, dict) or set(row) != {"song_id", "comment_count"}
                    or type(row["song_id"]) is not int or row["song_id"] != song_id
                    or type(row["comment_count"]) is not int or row["comment_count"] < 0):
                raise ValueError("invalid popularity entry")
            return row["comment_count"]
        except (self._redis_error, ValueError, TypeError):
            logger.warning("Popularity cache unavailable/invalid; reading original API")
            return None

    def save(self, song_id, comment_count):
        if type(comment_count) is not int or comment_count < 0:
            raise ValueError("only known nonnegative comment totals may be cached")
        row = json.dumps({"song_id": song_id, "comment_count": comment_count}, separators=(",", ":"))
        try:
            self.client.set(self.key(song_id), row, ex=POPULARITY_TTL_SECONDS)
        except self._redis_error:
            logger.warning("Popularity cache refill unavailable; retaining API result")


@lru_cache(maxsize=4)
def _cache(config):
    try:
        return RedisPopularityCache(config)
    except (ImportError, ValueError):
        logger.warning("Popularity Redis dependency unavailable; using API reads")
        return None


def configured_popularity_cache():
    config = RedisReuseConfig.configured("CONTEXT_DERIVED_CACHE")
    return _cache(replace(config, ttl_seconds=POPULARITY_TTL_SECONDS)) if config else None


class PopularityReadLimiter:
    """Serialize new reads per event loop, with no burst or automatic retry."""

    def __init__(self, min_interval_seconds=1.0):
        if not math.isfinite(min_interval_seconds) or min_interval_seconds < 0:
            raise ValueError("read interval must be finite and nonnegative")
        self.interval = min_interval_seconds
        self.lock = asyncio.Lock()
        self.next_start = 0.0

    async def read(self, client, song_id):
        async with self.lock:
            await asyncio.sleep(max(0.0, self.next_start - time.monotonic()))
            self.next_start = time.monotonic() + self.interval
            return await client.get_song_comment_count(song_id)


_limiters = WeakKeyDictionary()


def _limiter():
    loop = asyncio.get_running_loop()
    if loop not in _limiters:
        _limiters[loop] = PopularityReadLimiter()
    return _limiters[loop]


async def get_song_popularity(song_ids, *, client=None, cache=None, limiter=None) -> dict[int, int]:
    """Missing keys mean UNKNOWN. A real zero is retained; failures never become zero."""
    raw_ids = list(song_ids)
    if any(type(identity) is not int or identity <= 0 for identity in raw_ids):
        raise ValueError("song IDs must be positive integers")
    identities = list(dict.fromkeys(raw_ids))
    if len(identities) > MAX_POPULARITY_BATCH:
        raise ValueError("popularity read batch exceeds the candidate window")
    if not identities:
        return {}
    cache = cache if cache is not None else configured_popularity_cache()
    client = client if client is not None else MusicServiceClient()
    limiter = limiter if limiter is not None else _limiter()
    result, missing = {}, []
    for identity in identities:
        cached = cache.load(identity) if cache is not None else None
        if cached is None:
            missing.append(identity)
        else:
            result[identity] = cached
    for identity in missing:
        timeout_scope = None
        try:
            deadline = getattr(active_ledger.get(), "deadline", None)
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                async with asyncio.timeout(remaining) as timeout_scope:
                    count = await limiter.read(client, identity)
            else:
                count = await limiter.read(client, identity)
            if type(count) is not int or count < 0:
                raise ValueError("invalid comment total")
        except MusicServiceError as error:
            logger.warning("Comment total unknown for song %s (%s)", identity, error.code)
            if error.code == "http_429":
                break  # A quota rejection must not cause another upstream burst.
            continue
        except TimeoutError:
            logger.warning("Comment total unknown for song %s", identity)
            if timeout_scope is not None and timeout_scope.expired():
                break  # The entire request expired; never dispatch the next song.
            continue
        except (ValueError, OSError):
            logger.warning("Comment total unknown for song %s", identity)
            continue
        result[identity] = count
        if cache is not None:
            cache.save(identity, count)
    return result
