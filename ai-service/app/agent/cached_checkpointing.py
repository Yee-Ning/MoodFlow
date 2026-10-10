"""Cache the PG saver's latest working state; pinned recovery evidence stays in PG."""

import base64
import json

from langgraph.checkpoint.base import CheckpointTuple

from .checkpointing import AsyncCompatiblePostgresSaver
from .session_cache import fingerprint


class CachedPostgresSaver(AsyncCompatiblePostgresSaver):
    def __init__(self, conn, *, session_cache, **kwargs):
        super().__init__(conn, **kwargs)
        self.session_cache = session_cache

    def get_tuple(self, config):
        scope = config.get("configurable", {})
        session_id = scope.get("session_id")
        def loader():
            return super(CachedPostgresSaver, self).get_tuple(config)
        if not session_id or scope.get("checkpoint_id"):
            return loader()
        field = "checkpoint:" + fingerprint([scope.get("thread_id"), scope.get("checkpoint_ns", "")])

        def encode(row):
            kind, data = self.serde.dumps_typed(dict(row._asdict()))
            return json.dumps([kind, base64.b64encode(data).decode("ascii")])

        def decode(raw):
            kind, data = json.loads(raw)
            value = self.serde.loads_typed((kind, base64.b64decode(data, validate=True)))
            row = CheckpointTuple(**value)
            actual = row.config["configurable"]
            if (actual.get("thread_id"), actual.get("session_id"), actual.get("checkpoint_ns", "")) != (
                scope.get("thread_id"), session_id, scope.get("checkpoint_ns", "")
            ) or not row.checkpoint.get("id"):
                raise ValueError("cached checkpoint scope mismatch")
            return row

        return self.session_cache.read(self.session_cache.session_key(session_id), field, loader,
                                       encode=encode, decode=decode, tags={"__thread_id": scope.get("thread_id", "")})

    def _invalidate(self, config):
        session_id = config.get("configurable", {}).get("session_id")
        if session_id:
            self.session_cache.invalidate(self.session_cache.session_key(session_id))

    def put(self, config, checkpoint, metadata, new_versions):
        result = super().put(config, checkpoint, metadata, new_versions)
        self._invalidate(config)
        return result

    def put_writes(self, config, writes, task_id, task_path=""):
        result = super().put_writes(config, writes, task_id, task_path)
        self._invalidate(config)
        return result

    def delete_thread(self, thread_id):
        result = super().delete_thread(thread_id)
        self.session_cache.invalidate_sessions({thread_id})
        return result

    def copy_thread(self, source_thread_id, target_thread_id):
        result = super().copy_thread(source_thread_id, target_thread_id)
        self.session_cache.invalidate_sessions({target_thread_id})
        return result

    def prune(self, thread_ids, *, strategy="keep_latest"):
        result = super().prune(thread_ids, strategy=strategy)
        self.session_cache.invalidate_sessions(set(thread_ids))
        return result

    def delete_for_runs(self, run_ids):
        result = super().delete_for_runs(run_ids)
        self.session_cache.invalidate_sessions()
        return result
