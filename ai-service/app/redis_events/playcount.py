"""INCR hot counts, retry-safe periodic PG snapshots, last-flush recovery."""

import logging
from pathlib import Path
import re
from uuid import uuid4

from .config import redis_errors

logger = logging.getLogger(__name__)
INCREMENT = (Path(__file__).parent / "lua" / "increment_playcount.lua").read_text(encoding="utf-8")
ALIGN = (Path(__file__).parent / "lua" / "align_playcount.lua").read_text(encoding="utf-8")


class PlaycountIndeterminate(RuntimeError):
    """An INCR was dispatched but its reply was lost; do not count it twice."""


def song_key(song_id):
    if not isinstance(song_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", song_id):
        raise ValueError("invalid song_id")
    return "playcount:" + song_id


class PlayCounts:
    def __init__(self, store, client):
        self.store, self.client = store, client
        self.unavailable = client is None

    def rebuild(self, song_id):
        """Restore missing keys only; never overwrite increments from a live peer."""
        from redis.exceptions import WatchError
        key = song_key(song_id)
        while True:
            with self.client.pipeline() as pipe:
                try:
                    pipe.watch(key, key + ":epoch")
                    if pipe.exists(key) and pipe.exists(key + ":epoch"):
                        return int(pipe.get(key))
                    base = self.store.playcount(song_id)
                    pipe.multi()
                    pipe.set(key, base)
                    pipe.delete(key + ":epoch")
                    pipe.hset(key + ":epoch", mapping={"epoch": uuid4().hex, "base": base})
                    pipe.execute()
                    return base
                except WatchError:
                    continue

    def increment(self, song_id):
        key = song_key(song_id)
        if not self.unavailable:
            dispatched = False
            try:
                for _ in range(3):
                    # Check connectivity before dispatch. Once INCR is sent,
                    # a lost reply cannot safely be retried against PostgreSQL.
                    self.client.ping()
                    dispatched = True
                    count = self.client.eval(INCREMENT, 2, key, key + ":epoch")
                    dispatched = False
                    if count is not None:
                        return int(count)
                    self.rebuild(song_id)
                raise OSError("playcount keys repeatedly lost")
            except redis_errors():
                # An INCR reply can be ambiguous. Fence this process onto PG;
                # replay the Redis epoch only at flush, never retry INCR itself.
                self.unavailable = True
                logger.warning("Playcount Redis unavailable; using PG counters")
                if dispatched:
                    raise PlaycountIndeterminate("Playcount INCR acknowledgement lost") from None
        return self.store.increment_playcount(song_id)

    def read(self, song_id):
        key = song_key(song_id)
        if not self.unavailable:
            try:
                value = self.client.get(key)
                return int(value) if value is not None and self.client.exists(key + ":epoch") else self.rebuild(song_id)
            except redis_errors():
                self.unavailable = True
                logger.warning("Playcount Redis unavailable; reading PG snapshot")
        return self.store.playcount(song_id)

    def flush(self, song_id):
        key = song_key(song_id)
        if self.client is None:
            return self.store.playcount(song_id)
        try:
            # Redis transaction gives one consistent snapshot of both keys.
            with self.client.pipeline(transaction=True) as pipe:
                pipe.get(key)
                pipe.hgetall(key + ":epoch")
                value, meta = pipe.execute()
            if value is None or not meta:
                return self.store.playcount(song_id)
            base = int(meta["base"])
            total, upto = self.store.flush_playcount(song_id, meta["epoch"], int(value) - base)
            new_base = total - upto
            if new_base > base:
                self.client.eval(ALIGN, 2, key, key + ":epoch", meta["epoch"], str(base), str(new_base - base), str(new_base))
            return total
        except redis_errors():
            logger.warning("Playcount Redis flush unavailable; retaining last PG snapshot")
            return self.store.playcount(song_id)

    def flush_all(self):
        if self.client is None:
            return
        try:
            for key in self.client.scan_iter(match="playcount:*"):
                if key.count(":") == 1:
                    self.flush(key.split(":", 1)[1])
        except redis_errors():
            logger.warning("Playcount Redis scan unavailable; retaining last PG snapshot")
