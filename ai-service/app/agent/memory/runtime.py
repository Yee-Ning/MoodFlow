from __future__ import annotations

import asyncio
import logging
import os
from time import perf_counter
from functools import lru_cache
from typing import Protocol

from app.agent.structured_output import StructuredOutputError
from app.config.settings import memory_backend
from app.agent.foundation.long_horizon.history_writer import digest

from .embeddings import QwenMemoryEmbedder
from .extraction import MemoryExtractor
from .models import MemoryRetrievalRequest, MemoryRetrievalResult, MemoryWriteResult
from .retriever import MemoryRetriever
from .service import MemoryService
from .store import InMemoryMemoryStore, MemoryStore
from .policy import MemoryAdmissionError, safe_user_message, validate_candidate

logger = logging.getLogger(__name__)


class MemoryStoreFactory(Protocol):
    def __call__(self) -> MemoryStore: ...


class MemoryRuntime:
    def __init__(
        self,
        service: MemoryService,
        retriever: MemoryRetriever,
        *,
        extractor: MemoryExtractor | None = None,
        profile_store=None,
    ) -> None:
        self.service = service
        self.retriever = retriever
        self.extractor = extractor or MemoryExtractor()
        self.profile_store = profile_store
        self.job_worker = None

    def worker(self):
        if self.job_worker is None:
            from .jobs import MemoryJobWorker
            self.job_worker = MemoryJobWorker(self)
        return self.job_worker

    async def enqueue(self, **kwargs):
        return await self.worker().enqueue(**kwargs)

    async def stop_worker(self):
        if self.job_worker is not None:
            await self.job_worker.stop()

    async def retrieve(
        self, request: MemoryRetrievalRequest
    ) -> MemoryRetrievalResult:
        started = perf_counter()
        preferences = await asyncio.to_thread(self.service.store.preferences, request.user_id)
        if not preferences.enabled:
            return MemoryRetrievalResult(user_id=request.user_id)
        await self.sync_profile(request.user_id)
        result = await asyncio.to_thread(self.retriever.retrieve, request)
        from .telemetry import record_memory_event
        record_memory_event("retrieve", outcome="completed", count=len(result.items),
                            latency_ms=(perf_counter() - started) * 1000)
        return result

    async def sync_profile(self, user_id):
        from .projection import synchronize_profile
        from app.agent.foundation.user_profile import get_profile_store
        try:
            if await asyncio.to_thread(self.service.store.pending_profile_revision, user_id) is None:
                return True
            return await asyncio.to_thread(synchronize_profile, self.service.store,
                                          self.profile_store or get_profile_store(), user_id)
        except Exception as exc:
            logger.warning("profile projection pending error_type=%s", type(exc).__name__)
            return False

    async def extract_and_store(
        self, *, user_id: str, source_thread_id: str, user_message: str, source_message_id=None,
    ) -> list[MemoryWriteResult]:
        preferences = await asyncio.to_thread(self.service.store.preferences, user_id)
        if not preferences.enabled:
            return []
        if not safe_user_message(user_message):
            return []
        if source_message_id:
            completed = await asyncio.to_thread(self.service.store.prepare_message, user_id,
                source_message_id, digest([source_thread_id, user_message]))
            if completed:
                await self.sync_profile(user_id)
                return []
        try:
            extraction = await self.extractor.extract(user_message)
            for candidate in extraction.memories:
                validate_candidate(candidate, user_message=user_message)
        except (StructuredOutputError, MemoryAdmissionError):
            logger.warning(
                "memory extraction rejected; no memory was persisted",
            )
            return []
        results: list[MemoryWriteResult] = []
        try:
            for candidate in extraction.memories:
                results.append(await asyncio.to_thread(self.service.remember, user_id=user_id,
                    source_thread_id=source_thread_id, candidate=candidate, source_message_id=source_message_id,
                    user_message=user_message, expected_generation=preferences.generation))
            if source_message_id:
                await asyncio.to_thread(self.service.store.complete_message, user_id, source_message_id)
        finally:
            # Earlier candidates may have committed even if a later embedding
            # failed. Their projection repair must still be attempted.
            await self.sync_profile(user_id)
        return results

    def close(self) -> None:
        close = getattr(self.service.store, "close", None)
        if close is not None:
            close()


_persistent_store_factory: MemoryStoreFactory | None = None


def configure_persistent_memory_store(factory: MemoryStoreFactory | None) -> None:
    global _persistent_store_factory
    _persistent_store_factory = factory
    close_memory_runtime()


@lru_cache
def get_memory_runtime() -> MemoryRuntime:
    mode = memory_backend()
    if mode == "disabled":
        raise RuntimeError("long-term memory is disabled")
    if mode == "memory":
        store: MemoryStore = InMemoryMemoryStore()
    elif mode == "postgres":
        if _persistent_store_factory is not None:
            store = _persistent_store_factory()
        else:
            database_url = os.getenv("DATABASE_URL", "").strip()
            if not database_url:
                raise RuntimeError("DATABASE_URL is required for PostgreSQL memory")
            from .postgres_store import PostgresMemoryStore

            store = PostgresMemoryStore(
                database_url,
                max_pool_size=int(os.getenv("MEMORY_POOL_MAX_SIZE", "10")),
            )
    else:
        raise ValueError(f"unsupported memory backend:{mode}")
    embedder = QwenMemoryEmbedder()
    return MemoryRuntime(
        MemoryService(store, embedder=embedder),
        MemoryRetriever(store, embedder=embedder),
    )


def close_memory_runtime() -> None:
    if get_memory_runtime.cache_info().currsize:
        get_memory_runtime().close()
    get_memory_runtime.cache_clear()
