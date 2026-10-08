"""Durable bounded progress only; no model text, tool payload or browser ACK."""

import json
import logging
import re

from .config import redis_errors

logger = logging.getLogger(__name__)
NODES = {"prepare_context", "intent_router", "final_response", "clarify", "aggregate_results",
         "development_route", "tool_calling_subgraph", "general_chat_subgraph", "worldline_subgraph", "current_song_subgraph"}


def sequence(event_id):
    if not isinstance(event_id, str) or not re.fullmatch(r"(?:0|[1-9][0-9]{0,18})-0", event_id):
        raise ValueError("since must be a canonical sequence-0 stream ID")
    value = int(event_id.split("-", 1)[0])
    if value > 9_223_372_036_854_775_807:
        raise ValueError("stream ID exceeds PG bigint")
    return value


def identifier(value, max_length=160):
    if not isinstance(value, str) or not value or len(value) > max_length or any(ord(c) < 32 for c in value):
        raise ValueError("invalid progress identifier")
    return value


def public_progress(node, status):
    if node not in NODES or status != "completed":
        raise ValueError("unallowlisted progress event")
    return {"event": "update", "node": node, "status": status}


class ProgressEvents:
    def __init__(self, store, client, config):
        self.store, self.client, self.config = store, client, config

    @staticmethod
    def key(run_id):
        return "events:" + identifier(run_id)

    @staticmethod
    def decode(entries):
        decoded = []
        for event_id, fields in entries:
            sequence(event_id)
            payload = json.loads(fields["payload"])
            if payload != public_progress(payload["node"], payload["status"]):
                raise ValueError("invalid cached progress")
            decoded.append((event_id, payload))
        return decoded

    def rebuild(self, run_id):
        from redis.exceptions import WatchError
        key = self.key(run_id)
        # WATCH serializes outbox copies from independent producers. Explicit
        # seq-0 IDs survive Redis loss and keep resume cursors stable on PG.
        for _ in range(10):
            with self.client.pipeline() as pipe:
                try:
                    pipe.watch(key)
                    latest = pipe.xrevrange(key, count=1)
                    cursor = sequence(latest[0][0]) if latest else 0
                    head = self.store.latest_seq(run_id)
                    start = max(cursor, head - self.config.stream_maxlen)
                    rows = self.store.progress(run_id, start, self.config.stream_maxlen)
                    pipe.multi()
                    for event_id, payload in rows:
                        pipe.xadd(key, {"payload": json.dumps(payload, separators=(",", ":"))}, id=event_id)
                    pipe.xtrim(key, maxlen=self.config.stream_maxlen, approximate=False)
                    pipe.expire(key, self.config.stream_ttl)
                    pipe.execute()
                    return
                except WatchError:
                    continue
        raise OSError("progress stream rebuild contention")

    def publish(self, run_id, scope, conversation, mode, node, status="completed"):
        identifier(run_id)
        entry = self.store.append_progress(run_id, scope, conversation, mode, public_progress(node, status))
        if self.client is not None:
            try:
                self.rebuild(run_id)
            except redis_errors():
                logger.warning("Progress Redis unavailable; event retained in PG outbox")
        return entry

    def resume(self, run_id, since, scope, limit=100):
        cursor = sequence(since)
        self.key(run_id)
        if not 1 <= limit <= 1000:
            raise ValueError("invalid replay limit")
        self.store.require_scope(run_id, scope)
        if self.client is not None:
            try:
                rows = self.decode(self.client.xrange(self.key(run_id), min="(" + since, max="+", count=limit))
                head = self.store.latest_seq(run_id)
                # Trimming, TTL and unmirrored outbox commits must not hide a
                # gap. A concurrent append may appear on the next resume page.
                expected = min(limit, max(0, head - cursor))
                rows = [r for r in rows if sequence(r[0]) <= head]
                if len(rows) == expected and all(sequence(r[0]) == cursor + i + 1 for i, r in enumerate(rows)):
                    return rows
                self.rebuild(run_id)
            except (*redis_errors(), ValueError, KeyError, TypeError):
                logger.warning("Progress Redis unavailable or invalid; replaying PG outbox")
        return self.store.progress(run_id, cursor, limit)

    def consume(self, run_id, group, consumer, *, limit=100, claim_idle=None):
        key = self.key(run_id)
        identifier(group, 128)
        identifier(consumer, 128)
        if not 1 <= limit <= 1000 or claim_idle is not None and claim_idle < 0:
            raise ValueError("invalid consumer options")
        # PG claims establish ordered pending/ACK state across Redis loss and
        # XTRIM. Stream payloads serve those claims; XREADGROUP tracks transport
        # delivery, while a Redis PEL is not treated as a durable browser ACK.
        expected = self.store.consume(run_id, group, consumer, limit, claim_idle)
        if self.client is not None:
            from redis.exceptions import ResponseError
            try:
                self.rebuild(run_id)
                try:
                    self.client.xgroup_create(key, group, id="0-0", mkstream=True)
                except ResponseError as exc:
                    if "BUSYGROUP" not in str(exc):
                        raise
                batches = self.client.xreadgroup(group, consumer, {key: "0"}, count=limit)
                entries = [(i, f) for _, batch in batches for i, f in batch if f]
                if not entries and claim_idle is not None:
                    entries = self.client.xautoclaim(key, group, consumer, int(claim_idle * 1000), "0-0", count=limit)[1]
                if not entries:
                    batches = self.client.xreadgroup(group, consumer, {key: ">"}, count=limit)
                    entries = [(i, f) for _, batch in batches for i, f in batch if f]
                decoded = self.decode(entries)
                # Redis can retain a PEL entry after a durable ACK / lost reply.
                for event_id, _ in decoded:
                    if self.store.acknowledged(run_id, group, event_id):
                        self.client.xack(key, group, event_id)
                by_id = dict(decoded)
                return [(event_id, by_id.get(event_id, payload)) for event_id, payload in expected]
            except (*redis_errors(), ValueError, KeyError, TypeError):
                logger.warning("Progress Redis consumer unavailable; using PG delivery receipts")
        # Also serves messages trimmed from Redis but still pending in PG.
        return expected

    def ack(self, run_id, group, consumer, event_id):
        sequence(event_id)
        acknowledged = self.store.ack(run_id, group, consumer, event_id)
        if self.client is not None and (acknowledged or self.store.acknowledged(run_id, group, event_id)):
            try:
                self.client.xack(self.key(run_id), group, event_id)
            except redis_errors():
                logger.warning("Progress Redis ACK unavailable; durable PG ACK retained")
        return acknowledged
