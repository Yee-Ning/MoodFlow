"""Cache-aside immutable profile snapshots; PG still owns version conflicts."""

import logging
from functools import lru_cache
from threading import RLock

from app.agent.foundation.long_horizon.history_writer import validate_public_document
from app.agent.redis_reuse import RedisReuseConfig, redis_client

logger = logging.getLogger(__name__)


class RedisProfileCache:
    def __init__(self, config, *, client=None):
        from redis.exceptions import RedisError, WatchError
        self.config = config
        self.client = client if client is not None else redis_client(config)
        self._redis_error, self._watch_error = RedisError, WatchError
        self._dirty = set()
        self._lock = RLock()

    @staticmethod
    def key(user_id, version):
        return f"profile:{user_id}:{version}"

    def _failed(self, user_id, operation):
        with self._lock:
            self._dirty.add(user_id)
        logger.warning("Redis profile cache unavailable during %s; reading PostgreSQL", operation)

    def invalidate(self, user_id, *, previous_version=None, version=None):
        try:
            with self.client.pipeline() as pipe:
                # A tombstone also changes a missing pointer, fencing old in-flight refills.
                pipe.set(self.key(user_id, "latest"), "0", ex=self.config.ttl_seconds)
                for number in (previous_version, version):
                    if number:
                        pipe.delete(self.key(user_id, number))
                pipe.execute()
            with self._lock:
                self._dirty.discard(user_id)
            return True
        except self._redis_error:
            self._failed(user_id, "invalidation")
            return False

    @staticmethod
    def _validated(raw, user_id, version=None):
        from app.agent.foundation.user_profile import ProfileDocument
        if len(raw.encode()) > 16_384:
            raise ValueError("profile cache exceeds byte budget")
        document = ProfileDocument.model_validate_json(raw)
        validate_public_document(ProfileDocument, document)
        if document.user_id != user_id or (version is not None and document.version != version):
            raise ValueError("profile cache scope/version mismatch")
        return document

    def read(self, user_id, version, loader):
        with self._lock:
            dirty = user_id in self._dirty
        if dirty and not self.invalidate(user_id):
            return loader()
        pointer = self.key(user_id, "latest")
        try:
            pipe = self.client.pipeline()
        except self._redis_error:
            self._failed(user_id, "read")
            return loader()
        try:
            try:
                pipe.watch(pointer)
                number = version
                if number is None:
                    raw_number = pipe.get(pointer)
                    number = int(raw_number) if raw_number is not None else 0
                    if number < 0:
                        raise ValueError("invalid profile pointer")
                if number:
                    raw = pipe.get(self.key(user_id, number))
                    if raw is not None:
                        return self._validated(raw, user_id, number)
            except self._redis_error:
                self._failed(user_id, "read")
                return loader()
            except (ValueError, TypeError):
                logger.warning("Redis profile cache invalid document; reading PostgreSQL")
                # Do not publish under a corrupt pointer's old WATCH.
                return loader()
            document = loader()
            if document is None:
                return None
            from app.agent.foundation.user_profile import ProfileDocument
            validate_public_document(ProfileDocument, document)
            if document.user_id != user_id or (version is not None and document.version != version):
                raise ValueError("profile source scope/version mismatch")
            try:
                pipe.multi()
                pipe.set(self.key(user_id, document.version), document.model_dump_json(), ex=self.config.ttl_seconds)
                if version is None:
                    pipe.set(pointer, str(document.version), ex=self.config.ttl_seconds)
                pipe.execute()
            except self._watch_error:
                pass
            except self._redis_error:
                self._failed(user_id, "refill")
            return document
        finally:
            try:
                pipe.reset()
            except self._redis_error:
                self._failed(user_id, "cleanup")


class CachedProfileStore:
    def __init__(self, store, cache):
        self.store, self.cache = store, cache

    def read(self, user_id, *, version=None):
        return self.cache.read(user_id, version, lambda: self.store.read(user_id, version=version))

    def read_for_update(self, user_id):
        # A boundedly stale read cache must never participate in a profile merge.
        return self.store.read(user_id)

    def write(self, document, *, expected_version):
        written = self.store.write(document, expected_version=expected_version)
        self.cache.invalidate(written.user_id, previous_version=expected_version, version=written.version)
        return written

    def close(self):
        self.store.close()


@lru_cache(maxsize=4)
def _configured_cache(config):
    try:
        return RedisProfileCache(config)
    except (ImportError, ValueError):
        logger.warning("Redis profile cache connection/dependency unavailable; reading PostgreSQL")
        return None


def configured_profile_cache():
    config = RedisReuseConfig.configured("PROFILE_CACHE", ttl_seconds=3600)
    return _configured_cache(config) if config is not None else None
