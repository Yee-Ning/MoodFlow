"""Cache numeric/hash derivations, never assembled context or generated text.

The collector and selector always execute, including budget checks and History
writes. A snapshot addresses only their pure token-count and dedup computations.
"""

import json
import logging
from contextvars import ContextVar
from copy import copy
from datetime import date, datetime
from enum import Enum
from functools import lru_cache, wraps
from hashlib import sha256
from inspect import iscoroutinefunction, signature

from pydantic import BaseModel

from app.agent.redis_reuse import RedisReuseConfig, redis_client
from app.rag.chunk.token_counter import UnicodeHeuristicTokenCounter

logger = logging.getLogger(__name__)
_user = ContextVar("derived_cache_user", default=None)
_batch = ContextVar("derived_cache_batch", default=None)
VERSION = "unicode-v1-derived-v1"
MAX_BYTES = 262_144


def _json_value(value):
    if isinstance(value, BaseModel):
        return _json_value(value.model_dump(mode="json"))
    if isinstance(value, Enum):
        return _json_value(value.value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("non-string input key")
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_json_value(item) for item in value), key=_serialize)
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise TypeError("unsupported cache input")


def _serialize(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value):
    return sha256(_serialize(_json_value(value)).encode()).hexdigest()


class DerivationBatch:
    def __init__(self, counter, values=None):
        self.counter = counter
        self.values = values if values is not None else {"counts": {}, "dedup": {}}
        self.changed = False

    @property
    def name(self):
        return self.counter.name

    def count(self, text):
        key = sha256(text.encode()).hexdigest()
        if key not in self.values["counts"]:
            self.values["counts"][key] = self.counter.count(text)
            self.changed = True
        return self.values["counts"][key]

    def dedup(self, item, compute):
        key = _hash(item)
        if key not in self.values["dedup"]:
            self.values["dedup"][key] = sorted(compute(item))
            self.changed = True
        return set(self.values["dedup"][key])


class RedisDerivedCache:
    def __init__(self, config, *, client=None):
        from redis.exceptions import RedisError
        self.config = config
        self.client = client if client is not None else redis_client(config)
        self._redis_error = RedisError

    @staticmethod
    def key(user_id, inputs):
        return f"contextpack:derived:{user_id}:{_hash(inputs)}:{VERSION}"

    def load(self, key):
        try:
            raw = self.client.get(key)
            if raw is None:
                return None
            if len(raw.encode()) > MAX_BYTES:
                raise ValueError("oversize derivation")
            values = json.loads(raw)
            if set(values) != {"counts", "dedup"} or any(not isinstance(values[name], dict) for name in values):
                raise ValueError("invalid derivation")
            for digest, count in values["counts"].items():
                if len(digest) != 64 or type(count) is not int or count < 0:
                    raise ValueError("invalid token count")
            for digest, keys in values["dedup"].items():
                if len(digest) != 64 or not isinstance(keys, list) or not all(isinstance(item, str) and item.startswith(("content:", "evidence:")) for item in keys):
                    raise ValueError("invalid dedup keys")
            return values
        except self._redis_error:
            logger.warning("Redis derived cache unavailable; computing original derivations")
        except (ValueError, TypeError):
            logger.warning("Redis derived cache invalid; computing original derivations")
        return None

    def save(self, key, values):
        document = _serialize(values)
        if len(document.encode()) <= MAX_BYTES:
            try:
                self.client.set(key, document, ex=self.config.ttl_seconds)
            except self._redis_error:
                logger.warning("Redis derived cache refill unavailable; using original derivations")


@lru_cache(maxsize=4)
def _configured_cache(config):
    try:
        return RedisDerivedCache(config)
    except (ImportError, ValueError):
        logger.warning("Redis derived cache connection/dependency unavailable; computing original derivations")
        return None


def configured_derived_cache():
    config = RedisReuseConfig.configured("CONTEXT_DERIVED_CACHE")
    return _configured_cache(config) if config is not None else None


def personalized_derivations(function):
    """Only an authenticated user dimension enables reuse, with ContextVar isolation."""
    def user(kwargs):
        return (kwargs.get("continuity_context") or {}).get("user_id")

    if iscoroutinefunction(function):
        @wraps(function)
        async def wrapped(*args, **kwargs):
            token = _user.set(user(kwargs))
            try:
                return await function(*args, **kwargs)
            finally:
                _user.reset(token)
    else:
        @wraps(function)
        def wrapped(*args, **kwargs):
            token = _user.set(user(kwargs))
            try:
                return function(*args, **kwargs)
            finally:
                _user.reset(token)
    return wrapped


def cached_derivation_stage(function):
    bound_signature = signature(function)

    @wraps(function)
    def wrapped(self, *args, **kwargs):
        user_id = _user.get() or (kwargs.get("continuity_context") or {}).get("user_id")
        # Custom tokenizers may hide mutable state; their original behavior stays intact.
        if not user_id or type(self.counter) is not UnicodeHeuristicTokenCounter:
            return function(self, *args, **kwargs)
        cache = configured_derived_cache()
        if cache is None:
            return function(self, *args, **kwargs)
        arguments = bound_signature.bind(self, *args, **kwargs)
        arguments.apply_defaults()
        from app.agent.foundation.context_sources import load_context_policy
        from app.agent.foundation.long_horizon.coordinator import active_call
        call = active_call.get()
        try:
            key = cache.key(user_id, {"stage": function.__qualname__, "counter": self.counter.name,
                "inputs": {name: value for name, value in arguments.arguments.items() if name != "self"},
                "source_policy": load_context_policy(), "revalidation": call.revalidation if call else None})
        except (TypeError, ValueError):
            return function(self, *args, **kwargs)
        batch = DerivationBatch(self.counter, cache.load(key))
        isolated = copy(self)
        isolated.counter = batch
        token = _batch.set(batch)
        try:
            result = function(isolated, *args, **kwargs)
            if batch.changed:
                cache.save(key, batch.values)
            return result
        finally:
            _batch.reset(token)
    return wrapped


def derived_dedup_keys(item, compute):
    batch = _batch.get()
    return batch.dedup(item, compute) if batch is not None else compute(item)
