"""PostgreSQL session reads with optional Redis acceleration and post-commit eviction."""

from contextlib import contextmanager
from contextvars import ContextVar

from app.agent.session_cache import fingerprint
from app.rag.chunk.token_counter import UnicodeHeuristicTokenCounter

from .models import HandoffDraftV2, SessionRecord, read_handoff_projection
from .postgres_store import PostgresContinuityStore


class CachedPostgresContinuityStore(PostgresContinuityStore):
    def __init__(self, database_url, *, session_cache, max_pool_size=10):
        self.session_cache = session_cache
        self._cache_changes = ContextVar("session_cache_transaction_changes", default=None)
        super().__init__(database_url, max_pool_size=max_pool_size)

    @contextmanager
    def transaction(self):
        if self._cache_changes.get() is not None:
            with super().transaction():
                yield
            return
        changes = set()
        token = self._cache_changes.set(changes)
        try:
            with super().transaction():
                yield
            # The connection transaction has committed. Rollbacks never publish/evict.
            for key in changes:
                self.session_cache.invalidate(key)
        finally:
            self._cache_changes.reset(token)

    def _changed(self, session_id, *, state=False):
        changes = self._cache_changes.get()
        if state:
            changes.add(self.session_cache.session_key(session_id))
        changes.add(self.session_cache.view_key(session_id))

    def create_session(self, session):
        with self.transaction():
            super().create_session(session)
            self._changed(session.session_id, state=True)

    def close_session(self, session_id, closed_at):
        with self.transaction():
            result = super().close_session(session_id, closed_at)
            self._changed(session_id, state=True)
            return result

    def _append_event(self, event, *, idempotent):
        with self.transaction():
            result = super()._append_event(event, idempotent=idempotent)
            # Include proposals/rejections/decisions as they change the public view,
            # while accepted progress is still governed by the existing projector.
            self._changed(event.session_id)
            return result

    def put_handoff(self, handoff):
        with self.transaction():
            super().put_handoff(handoff)
            self._changed(handoff.session_id)

    def get_session(self, session_id):
        def loader():
            return super(CachedPostgresContinuityStore, self).get_session(session_id)
        if self._transaction_connection.get() is not None:
            return loader()

        def decode(raw):
            record = SessionRecord.model_validate_json(raw)
            if record.session_id != session_id:
                raise ValueError("cached session scope mismatch")
            return record

        return self.session_cache.read(self.session_cache.session_key(session_id), "record:v1", loader,
                                       encode=lambda row: row.model_dump_json(), decode=decode)

    def read_session_projection(self, session_id, handoff_id):
        def loader():
            return self.get_handoff(handoff_id)
        if self._transaction_connection.get() is not None:
            return loader()

        def decode(raw):
            import json
            row = read_handoff_projection(json.loads(raw))
            if row.handoff_id != handoff_id:
                raise ValueError("cached projection scope mismatch")
            return row

        return self.session_cache.read(self.session_cache.view_key(session_id), "archive:" + handoff_id,
                                       loader, encode=lambda row: row.model_dump_json(), decode=decode)

    def read_progress_projection(self, projector, session, cursor, budget, draft, loader):
        # Custom mutable token counters have no stable cache identity.
        if self._transaction_connection.get() is not None or type(projector.service.counter) is not UnicodeHeuristicTokenCounter:
            return loader()
        session_id = session if isinstance(session, str) else session.session_id
        field = "progress:" + fingerprint({"schema": 1, "draft": draft.model_dump(mode="json"),
            "cursor": cursor, "budget": budget, "policy": projector.service.policy.model_dump(mode="json"),
            "counter": projector.service.counter.name})
        return self.session_cache.read(self.session_cache.view_key(session_id), field, loader,
            encode=lambda row: row.model_dump_json(), decode=HandoffDraftV2.model_validate_json)
