import asyncio
import os
from collections.abc import AsyncIterator, Sequence
from typing import Any, Protocol

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver, CheckpointTuple
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


class CheckpointerFactory(Protocol):
    """Construction boundary for local or persistent LangGraph checkpointers."""

    def __call__(self) -> BaseCheckpointSaver: ...


_persistent_checkpointer_factory: CheckpointerFactory | None = None
_postgres_pool: ConnectionPool | None = None
_postgres_checkpointer: BaseCheckpointSaver | None = None


class SessionScopedSaverMixin:
    """Map an application session ID to the saver's checkpoint namespace."""

    @staticmethod
    def _storage_config(config: RunnableConfig) -> RunnableConfig:
        configurable = dict(config.get("configurable", {}))
        session_id = configurable.get("session_id")
        if session_id:
            configurable["checkpoint_ns"] = str(session_id)
        return {**config, "configurable": configurable}

    @staticmethod
    def _public_config(
        config: RunnableConfig | None,
        session_id: str | None = None,
    ) -> RunnableConfig | None:
        if config is None:
            return None
        configurable = dict(config.get("configurable", {}))
        resolved_session = session_id or configurable.get("checkpoint_ns")
        configurable["checkpoint_ns"] = ""
        if resolved_session:
            configurable["session_id"] = str(resolved_session)
        return {**config, "configurable": configurable}

    @classmethod
    def _public_tuple(
        cls,
        row: CheckpointTuple | None,
        session_id: str | None,
    ) -> CheckpointTuple | None:
        if row is None:
            return None
        return CheckpointTuple(
            config=cls._public_config(row.config, session_id),
            checkpoint=row.checkpoint,
            metadata=row.metadata,
            parent_config=cls._public_config(row.parent_config, session_id),
            pending_writes=row.pending_writes,
        )

    def get_tuple(self, config: RunnableConfig):
        session_id = config.get("configurable", {}).get("session_id")
        row = super().get_tuple(self._storage_config(config))
        return self._public_tuple(row, str(session_id) if session_id else None)

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ):
        session_id = (
            config.get("configurable", {}).get("session_id") if config else None
        )
        stored_config = self._storage_config(config) if config else None
        stored_before = self._storage_config(before) if before else None
        for row in super().list(
            stored_config, filter=filter, before=stored_before, limit=limit
        ):
            yield self._public_tuple(
                row, str(session_id) if session_id else None
            )

    def put(self, config, checkpoint, metadata, new_versions):
        session_id = config.get("configurable", {}).get("session_id")
        stored = super().put(
            self._storage_config(config), checkpoint, metadata, new_versions
        )
        return self._public_config(
            stored, str(session_id) if session_id else None
        )

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        return super().put_writes(
            self._storage_config(config), writes, task_id, task_path
        )


class SessionScopedInMemorySaver(SessionScopedSaverMixin, InMemorySaver):
    pass


class AsyncCompatiblePostgresSaver(SessionScopedSaverMixin, PostgresSaver):
    """Run the official sync saver off-loop for LangGraph async execution."""

    async def aget_tuple(self, config: RunnableConfig):
        return await asyncio.to_thread(self.get_tuple, config)

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator:
        rows = await asyncio.to_thread(
            lambda: list(
                self.list(config, filter=filter, before=before, limit=limit)
            )
        )
        for row in rows:
            yield row

    async def aput(self, config, checkpoint, metadata, new_versions):
        return await asyncio.to_thread(
            self.put, config, checkpoint, metadata, new_versions
        )

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        await asyncio.to_thread(
            self.put_writes, config, writes, task_id, task_path
        )

    async def adelete_thread(self, thread_id: str) -> None:
        await asyncio.to_thread(self.delete_thread, thread_id)

    async def acopy_thread(
        self, source_thread_id: str, target_thread_id: str
    ) -> None:
        await asyncio.to_thread(
            self.copy_thread, source_thread_id, target_thread_id
        )

    async def aprune(
        self, thread_ids: Sequence[str], *, strategy: str = "keep_latest"
    ) -> None:
        await asyncio.to_thread(self.prune, thread_ids, strategy=strategy)

    async def adelete_for_runs(self, run_ids: Sequence[str]) -> None:
        await asyncio.to_thread(self.delete_for_runs, run_ids)


def create_checkpoint_serializer() -> JsonPlusSerializer:
    return JsonPlusSerializer(
        allowed_msgpack_modules=[
            ("app.schemas.context", "SongContext"),
            ("app.agent.foundation.models", "GraphError"),
            ("app.agent.foundation.models", "TaskResult"),
            ("app.agent.foundation.models", "TaskStatus"),
            ("app.agent.foundation.routing", "IntentItem"),
            ("app.agent.foundation.routing", "IntentSource"),
            ("app.agent.foundation.routing", "IntentType"),
            ("app.schemas.player", "PlayerState"),
            ("app.schemas.player", "PlayMode"),
            ("app.agent.foundation.routing", "RouteDecision"),
            ("app.agent.foundation.routing", "RouteKey"),
            ("app.agent.foundation.routing", "RouteTask"),
            ("app.agent.foundation.routing", "RoutingContext"),
            (
                "app.agent.foundation.subgraphs.tool_calling.models",
                "ReferencedEntities",
            ),
            (
                "app.agent.foundation.subgraphs.tool_calling.models",
                "SongReference",
            ),
            (
                "app.agent.foundation.subgraphs.tool_calling.models",
                "PlaylistReference",
            ),
        ]
    )


def create_memory_checkpointer() -> InMemorySaver:
    """Create the unit-test/local-fallback checkpointer for agent graphs."""
    return SessionScopedInMemorySaver(serde=create_checkpoint_serializer())


def checkpoint_backend() -> str:
    configured = os.getenv("CHECKPOINT_BACKEND", "").strip().lower()
    if configured:
        return configured
    legacy = os.getenv("AI_LANGGRAPH_CHECKPOINTER_MODE", "memory").strip().lower()
    return "postgres" if legacy == "persistent" else legacy


def configure_persistent_checkpointer(
    factory: CheckpointerFactory | None,
) -> None:
    """Install the process-startup provider for a persistent checkpointer."""
    global _persistent_checkpointer_factory
    _persistent_checkpointer_factory = factory


def create_runtime_checkpointer() -> BaseCheckpointSaver:
    """Resolve the configured runtime backend and fail closed when it is absent."""
    mode = checkpoint_backend()
    if mode == "memory":
        return create_memory_checkpointer()
    if mode != "postgres":
        raise ValueError(f"unsupported LangGraph checkpointer mode:{mode}")
    if _persistent_checkpointer_factory is not None:
        return _persistent_checkpointer_factory()
    if _postgres_checkpointer is None:
        raise RuntimeError(
            "PostgreSQL checkpointer is not initialized; run the application "
            "lifespan or initialize_runtime_checkpointer() first"
        )
    return _postgres_checkpointer


async def initialize_runtime_checkpointer() -> None:
    global _postgres_checkpointer, _postgres_pool
    if checkpoint_backend() == "memory" or _persistent_checkpointer_factory is not None:
        return
    if checkpoint_backend() != "postgres":
        raise ValueError(
            f"unsupported LangGraph checkpointer mode:{checkpoint_backend()}"
        )
    if _postgres_checkpointer is not None:
        return
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL is required for PostgreSQL checkpointing")
    pool = ConnectionPool(
        database_url,
        min_size=1,
        max_size=int(os.getenv("CHECKPOINT_POOL_MAX_SIZE", "10")),
        open=False,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
        },
    )
    await asyncio.to_thread(pool.open, wait=True)

    def checkpoint_schema_exists() -> bool:
        with pool.connection() as connection:
            row = connection.execute(
                "SELECT to_regclass('public.checkpoint_migrations') AS table_name"
            ).fetchone()
            return row is not None and row["table_name"] is not None

    schema_exists = await asyncio.to_thread(checkpoint_schema_exists)
    if not schema_exists:
        await asyncio.to_thread(pool.close)
        raise RuntimeError(
            "LangGraph checkpoint schema is missing; run "
            "python -m scripts.migrate_agent_postgres first"
        )
    _postgres_pool = pool
    _postgres_checkpointer = AsyncCompatiblePostgresSaver(
        pool,
        serde=create_checkpoint_serializer(),
    )


async def close_runtime_checkpointer() -> None:
    global _postgres_checkpointer, _postgres_pool
    _postgres_checkpointer = None
    if _postgres_pool is not None:
        await asyncio.to_thread(_postgres_pool.close)
        _postgres_pool = None
