"""Optional atomic hot budgets; immutable MODEL_USAGE remains the cold truth.

No Redis imports/connections occur unless explicitly selected. The Protocol's
split/model queries retain the existing receipt semantics, including JEV billing.
"""

import json
import logging
import os
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from app.llm.budget import HistoryBudgetLedger, PostgresBudgetLedger

logger = logging.getLogger(__name__)
OWNERS = ("general_chat", "curator", "explorer")
SCRIPT = Path(__file__).with_name("lua").joinpath("reserve_budget.lua").read_text(encoding="utf-8")


class RedisBudgetUnavailable(Exception):
    """A hot operation could not be confirmed; caller must use the old gate."""


@dataclass(frozen=True)
class RedisBudgetConfig:
    url: str
    ttl_seconds: int = 3600
    timeout_seconds: float = 0.25
    reconcile_seconds: float = 60

    @classmethod
    def configured(cls):
        if os.getenv("BUDGET_LEDGER_BACKEND", "history").strip().lower() != "redis":
            return None
        # Never include connection strings or exception details in diagnostics.
        url = os.getenv("REDIS_URL", "").strip()
        if not url:
            logger.warning("Redis budget unavailable: REDIS_URL missing; using existing budget logic")
            return None
        ttl = int(os.getenv("BUDGET_REDIS_TTL_SECONDS", "3600"))
        timeout = float(os.getenv("BUDGET_REDIS_TIMEOUT_SECONDS", "0.25"))
        interval = float(os.getenv("BUDGET_REDIS_RECONCILE_SECONDS", "60"))
        if ttl <= 0 or timeout <= 0 or interval <= 0:
            raise ValueError("Redis budget TTL, timeout and reconciliation interval must be positive")
        return cls(url, ttl, timeout, interval)


@lru_cache(maxsize=4)
def redis_client(config):
    from redis import Redis
    return Redis.from_url(config.url, decode_responses=True,
                          socket_timeout=config.timeout_seconds,
                          socket_connect_timeout=config.timeout_seconds,
                          retry_on_timeout=False)


def cold_ledger(accounting):
    from app.agent.foundation.long_horizon.postgres_store import PostgresContinuityStore
    adapter = PostgresBudgetLedger if isinstance(accounting.writer.store, PostgresContinuityStore) else HistoryBudgetLedger
    return adapter(accounting)


class RedisBudgetLedger:
    """BudgetLedger plus reserve/settle, replay and reconciliation capabilities.

    L2 is execution-owner usage (JEV belongs to its caller); L3 counts each call
    once. Receipts bridge the short gap between hot mutation and cold append.
    Maintenance uses WATCH, so it cannot overwrite concurrent Lua reservations.
    """

    def __init__(self, accounting, *, config=None, client=None):
        self.accounting = accounting
        self.cold = cold_ledger(accounting)
        self.config = config or RedisBudgetConfig.configured()
        if self.config is None:
            raise RedisBudgetUnavailable("Redis budget not configured")
        try:
            self.client = client if client is not None else redis_client(self.config)
        except ImportError:
            raise RedisBudgetUnavailable("optional Redis dependency missing") from None
        self.ttl = self.config.ttl_seconds
        self._minimum = {}

    def _scope(self, task_id):
        if task_id != self.accounting.identity.run_id:
            raise ValueError("budget task scope mismatch")
        if not task_id or len(task_id) > 128 or "{" in task_id or "}" in task_id:
            raise ValueError("invalid budget run hash tag")

    def keys(self, task_id):
        self._scope(task_id)
        tag = "{" + task_id + "}"
        return {**{owner: f"budget:l2:{tag}:{owner}" for owner in OWNERS},
                "task": f"budget:l3:{tag}", "receipts": f"budget:receipts:{tag}",
                "floor": f"budget:floor:{tag}"}

    @staticmethod
    def _tokens(value):
        number = Decimal(str(value))
        if not number.is_finite() or number < 0 or number != int(number) or number > 2**53 - 1:
            raise RedisBudgetUnavailable("invalid hot token counter")
        return int(number)

    @staticmethod
    def _amount(amount):
        if type(amount) is not int or amount < 0 or amount > 2**53 - 1:
            raise ValueError("budget tokens must be nonnegative safe integers")

    def _unavailable(self, operation):
        logger.warning("Redis budget unavailable during %s; using existing budget logic", operation)
        return RedisBudgetUnavailable(operation)

    def add(self, task_id, agent, input_tokens, output_tokens, model, *, estimated=False):
        # Same validation and immutable receipts as the original adapter.
        self.cold.add(task_id, agent, input_tokens, output_tokens, model, estimated=estimated)
        try:
            self.reconcile(task_id)
        except RedisBudgetUnavailable:
            pass  # Cold append succeeded; the next bind/reconcile replays it.

    def get_task_total(self, task_id):
        return self.cold.get_task_total(task_id)

    def get_agent_total(self, task_id, agent):
        return self.cold.get_agent_total(task_id, agent)

    def get_model_breakdown(self, task_id):
        return self.cold.get_model_breakdown(task_id)

    def get_cost(self, task_id):
        return self.cold.get_cost(task_id)

    def _target(self, task_id, receipts, floors):
        events = self.accounting.events(task_id)
        reserved = {event.call_id: event for event in events if event.phase == "reserved"}
        settled = {event.call_id: event for event in events if event.phase == "settled" and event.redis_snapshot is None}
        baseline = {}
        snapshot_floor = {owner: 0 for owner in OWNERS}
        for event in events:
            if event.phase == "baseline":
                baseline.setdefault(event.agent, event.tokens)
            if event.redis_snapshot is not None:
                for owner in OWNERS:
                    snapshot_floor[owner] = max(snapshot_floor[owner], event.redis_snapshot[owner])
        totals = {owner: baseline.get(owner, 0) for owner in OWNERS}
        for call_id, event in reserved.items():
            billed = settled.get(call_id, event)
            totals[billed.agent] += billed.tokens
        for call_id, encoded in receipts.items():
            receipt = json.loads(encoded)
            owner, tokens = receipt["agent"], self._tokens(receipt["tokens"])
            if owner not in OWNERS:
                raise RedisBudgetUnavailable("invalid reservation owner")
            if call_id in settled:
                continue  # Cold actual usage wins over an old hot receipt.
            old = reserved.get(call_id)
            totals[owner] += tokens - (old.tokens if old is not None else 0)
        # Saved conservative snapshots must never regain already-spent capacity.
        for owner in OWNERS:
            totals[owner] = max(totals[owner], self._tokens(floors.get(owner, 0)), snapshot_floor[owner])
        return totals

    def reconcile(self, task_id, *, minimum=None, refresh_ttl=True):
        """Replay cold events plus in-flight receipts and atomically correct drift."""
        from redis.exceptions import RedisError, WatchError
        keys = self.keys(task_id)
        saved_minimum = self._minimum.setdefault(task_id, {})
        if minimum is not None:
            for owner, amount in minimum.items():
                if owner not in OWNERS:
                    raise ValueError("unknown budget owner")
                saved_minimum[owner] = max(saved_minimum.get(owner, 0), self._tokens(amount))
        watched = list(keys.values())
        try:
            for _ in range(32):
                with self.client.pipeline() as pipe:
                    try:
                        pipe.watch(*watched)
                        receipts = pipe.hgetall(keys["receipts"])
                        floors = pipe.hgetall(keys["floor"])
                        for owner, amount in saved_minimum.items():
                            floors[owner] = max(self._tokens(floors.get(owner, 0)), self._tokens(amount))
                        target = self._target(task_id, receipts, floors)
                        desired = [target[owner] for owner in OWNERS] + [sum(target.values())]
                        counters = [keys[owner] for owner in OWNERS] + [keys["task"]]
                        before = pipe.mget(counters)
                        remaining_ttl = pipe.ttl(keys["task"])
                        ttl = self.ttl if refresh_ttl else max(1, min(self.ttl, remaining_ttl))
                        def differs(value, amount):
                            if value is None:
                                return False
                            try:
                                return self._tokens(value) != amount
                            except (RedisBudgetUnavailable, ValueError):
                                return True
                        drift = any(differs(value, amount) for value, amount in zip(before, desired, strict=True))
                        pipe.multi()
                        for key, amount in zip(counters, desired, strict=True):
                            pipe.set(key, amount, ex=ttl)
                        for owner, amount in floors.items():
                            pipe.hset(keys["floor"], owner, amount)
                        for key in (keys["receipts"], keys["floor"]):
                            pipe.expire(key, ttl)
                        pipe.execute()
                        if drift:
                            logger.warning("Redis budget drift corrected for run %s", task_id)
                        return {**target, "task": sum(target.values()), "drift": drift}
                    except WatchError:
                        continue
            raise self._unavailable("reconcile contention")
        except (RedisError, OSError, ValueError, TypeError, KeyError):
            raise self._unavailable("reconcile") from None

    def rebuild(self, task_id):
        """Safe on complete/partial eviction; cold reserves remain charged."""
        return self.reconcile(task_id)

    def counters(self, task_id, agent):
        from redis.exceptions import RedisError
        keys = self.keys(task_id)
        if agent not in OWNERS:
            raise ValueError("unknown budget owner")
        try:
            values = self.client.mget(keys[agent], keys["task"])
            if any(value is None for value in values):
                self.rebuild(task_id)
                values = self.client.mget(keys[agent], keys["task"])
            return tuple(self._tokens(value) for value in values)
        except (RedisError, OSError, ValueError, TypeError):
            raise self._unavailable("read") from None

    def reserve(self, task_id, agent, amount, l2_limit, l3_limit, *, call_id=None):
        from redis.exceptions import RedisError
        self._amount(amount)
        keys = self.keys(task_id)
        if agent not in OWNERS:
            raise ValueError("unknown budget owner")
        call_id = call_id or uuid4().hex
        receipt = json.dumps({"agent": agent, "tokens": amount, "state": "reserved"})
        try:
            for _ in range(2):
                result = self.client.eval(SCRIPT, 3, keys[agent], keys["task"], keys["receipts"],
                                          amount, l2_limit, l3_limit, call_id, receipt, self.ttl)
                if result != "rebuild":
                    return result, call_id
                self.rebuild(task_id)
            raise self._unavailable("reserve rebuild")
        except (RedisError, OSError):
            raise self._unavailable("reserve") from None

    def settle(self, task_id, agent, call_id, actual):
        """INCRBYFLOAT(actual - previous) in MULTI; receipt makes retry idempotent.

        No settlement Lua. WATCH + MULTI prevents duplicate refunds and keeps
        the two individually atomic commands together, including timeout replay.
        """
        from redis.exceptions import RedisError, WatchError
        self._amount(actual)
        keys = self.keys(task_id)
        if agent not in OWNERS:
            raise ValueError("unknown budget owner")
        try:
            self.counters(task_id, agent)  # Replay missing counters before delta.
            for _ in range(32):
                with self.client.pipeline() as pipe:
                    try:
                        pipe.watch(keys[agent], keys["task"], keys["receipts"])
                        encoded = pipe.hget(keys["receipts"], call_id)
                        if encoded is None:
                            events = self.accounting.events(task_id)
                            old = next((event for event in reversed(events)
                                        if event.call_id == call_id and event.phase in {"reserved", "settled"}), None)
                            if old is None:
                                raise self._unavailable("settle receipt missing")
                            receipt = {"agent": old.agent, "tokens": old.tokens, "state": old.phase}
                        else:
                            receipt = json.loads(encoded)
                        if receipt["agent"] != agent:
                            raise ValueError("settlement owner mismatch")
                        # Redis settlement never rewrites an already-final receipt.
                        if receipt["state"] == "settled":
                            if self._tokens(receipt["tokens"]) != actual:
                                raise ValueError("conflicting settlement")
                            return
                        if any(value is None for value in pipe.mget(keys[agent], keys["task"])):
                            pipe.unwatch()
                            self.rebuild(task_id)
                            continue
                        delta = actual - self._tokens(receipt["tokens"])
                        receipt.update(tokens=actual, state="settled")
                        pipe.multi()
                        pipe.incrbyfloat(keys[agent], delta)
                        pipe.incrbyfloat(keys["task"], delta)
                        pipe.hset(keys["receipts"], call_id, json.dumps(receipt))
                        for key in (keys[agent], keys["task"], keys["receipts"]):
                            pipe.expire(key, self.ttl)
                        pipe.execute()
                        return
                    except WatchError:
                        continue
            raise self._unavailable("settle contention")
        except (RedisError, OSError):
            raise self._unavailable("settle") from None

    def flush(self, task_id):
        """One idempotent MODEL_USAGE settlement snapshot, never a second bill."""
        from app.agent.foundation.long_horizon.public_events import PublicUsage
        snapshot = self.reconcile(task_id)
        owners = {owner: snapshot[owner] for owner in OWNERS}
        digest = sha256(json.dumps(owners, sort_keys=True).encode()).hexdigest()[:32]
        body = PublicUsage(run_id=task_id, agent="general_chat", call_id="redis-flush-" + digest,
                           phase="settled", tokens=snapshot["task"], redis_snapshot=owners,
                           model="redis-budget-snapshot")
        # No matching reserved event: existing queries/recovery do not bill it twice.
        self.accounting.write_usage(body)
        return body


def select_budget_ledger(accounting):
    config = RedisBudgetConfig.configured()
    if config is None:
        return cold_ledger(accounting)
    try:
        return RedisBudgetLedger(accounting, config=config)
    except RedisBudgetUnavailable:
        logger.warning("Redis budget unavailable at selection; using existing budget logic")
        return cold_ledger(accounting)
