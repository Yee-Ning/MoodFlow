"""Hint before INSERT; never return a Redis receipt without PG verification."""

import json
import logging
from uuid import uuid4

from app.agent.foundation.long_horizon.history_writer import HistoryWriter
from app.agent.foundation.long_horizon.models import HistoryEventType, same_event_content
from app.agent.foundation.long_horizon.postgres_store import PostgresContinuityStore
from .config import redis_errors

logger = logging.getLogger(__name__)
RELEASE = "if redis.call('GET',KEYS[1]) == ARGV[1] then return redis.call('DEL',KEYS[1]) end return 0"


class TransferStore:
    def __init__(self, store, client, ttl_ms):
        self.store, self.client, self.ttl_ms = store, client, ttl_ms

    def __getattr__(self, name):
        return getattr(self.store, name)

    def append_event_once(self, event):
        if event.event_type != HistoryEventType.AGENT_TRANSFER or self.client is None:
            return self.store.append_event_once(event)
        key = f'transfer:idempotency:{event.payload["transfer_id"]}:{event.event_id}'
        receipt = json.dumps({"event_id": event.event_id, "owner": uuid4().hex}, separators=(",", ":"))
        owned = False
        try:
            owned = bool(self.client.set(key, receipt, nx=True, px=self.ttl_ms))
            if not owned:
                existing = self.store.get_history_event(event.event_id)
                if existing is not None and same_event_content(existing, event):
                    return existing
                # A claim may precede PG commit, survive rollback, or have another
                # body. The original PG unique constraint decides every case.
        except redis_errors():
            logger.warning("Transfer Redis unavailable; using original PG arbitration")
        try:
            return self.store.append_event_once(event)
        except BaseException:
            if owned:
                try:
                    self.client.eval(RELEASE, 1, key, receipt)
                except redis_errors():
                    logger.warning("Transfer Redis cleanup unavailable; PG remains authoritative")
            raise


def attach_transfer_gate(journal, client, config):
    writer = journal.writer
    if isinstance(writer.store, TransferStore) or not isinstance(writer.store, PostgresContinuityStore):
        return
    # Reuse all HistoryWriter schema/reference/version checks and conflict ERROR
    # recording. Budget/accounting keep the coordinator's original PG writer.
    journal.writer = HistoryWriter(TransferStore(writer.store, client, config.transfer_ttl_ms),
                                   max_bytes=writer.max_bytes, now=writer.now)
