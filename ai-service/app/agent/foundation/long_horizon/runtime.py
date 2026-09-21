import os
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from .service import LongHorizonContextService
from .store import ContinuityStore, SQLiteContinuityStore


class ContinuityStoreFactory(Protocol):
    def __call__(self) -> ContinuityStore: ...


_persistent_store_factory: ContinuityStoreFactory | None = None


def configure_persistent_continuity_store(
    factory: ContinuityStoreFactory | None,
) -> None:
    """Install the process-startup provider for a shared continuity store."""
    global _persistent_store_factory
    _persistent_store_factory = factory
    get_long_horizon_service.cache_clear()


@lru_cache
def get_long_horizon_service() -> LongHorizonContextService:
    mode = os.getenv("AI_CONTINUITY_STORE_MODE", "sqlite").strip().lower()
    if mode == "persistent":
        if _persistent_store_factory is None:
            raise RuntimeError(
                "AI_CONTINUITY_STORE_MODE=persistent requires a registered "
                "continuity store factory"
            )
        return LongHorizonContextService(_persistent_store_factory())
    if mode != "sqlite":
        raise ValueError(f"unsupported continuity store mode:{mode}")
    project_root = Path(__file__).resolve().parents[4]
    return LongHorizonContextService(
        SQLiteContinuityStore(project_root / "tmp" / "context_continuity.sqlite")
    )
