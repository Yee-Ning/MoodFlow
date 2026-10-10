import os
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from .service import LongHorizonContextService
from .store import ContinuityStore, SQLiteContinuityStore


class ContinuityStoreFactory(Protocol):
    def __call__(self) -> ContinuityStore: ...


_persistent_store_factory: ContinuityStoreFactory | None = None


def persistence_backend() -> str:
    configured = os.getenv("PERSISTENCE_BACKEND", "").strip().lower()
    if configured:
        return configured
    legacy = os.getenv("AI_CONTINUITY_STORE_MODE", "sqlite").strip().lower()
    return "postgres" if legacy == "persistent" else legacy


def configure_persistent_continuity_store(
    factory: ContinuityStoreFactory | None,
) -> None:
    """Install the process-startup provider for a shared continuity store."""
    global _persistent_store_factory
    _persistent_store_factory = factory
    get_long_horizon_service.cache_clear()


@lru_cache
def get_long_horizon_service() -> LongHorizonContextService:
    mode = persistence_backend()
    if mode == "postgres":
        if _persistent_store_factory is not None:
            return LongHorizonContextService(_persistent_store_factory())
        database_url = os.getenv("DATABASE_URL", "").strip()
        if not database_url:
            raise RuntimeError(
                "DATABASE_URL is required for PostgreSQL continuity persistence"
            )
        from .postgres_store import PostgresContinuityStore
        from app.agent.session_cache import configured_session_cache

        cache = configured_session_cache()
        store_type, options = PostgresContinuityStore, {}
        if cache is not None:
            from .cached_postgres_store import CachedPostgresContinuityStore
            store_type, options = CachedPostgresContinuityStore, {"session_cache": cache}

        return LongHorizonContextService(
            store_type(
                database_url,
                max_pool_size=int(os.getenv("PERSISTENCE_POOL_MAX_SIZE", "10")),
                **options,
            )
        )
    if mode != "sqlite":
        raise ValueError(f"unsupported continuity store mode:{mode}")
    project_root = Path(__file__).resolve().parents[4]
    return LongHorizonContextService(
        SQLiteContinuityStore(project_root / "tmp" / "context_continuity.sqlite")
    )


def close_long_horizon_service() -> None:
    if get_long_horizon_service.cache_info().currsize:
        service = get_long_horizon_service()
        close = getattr(service.store, "close", None)
        if close is not None:
            close()
    get_long_horizon_service.cache_clear()
