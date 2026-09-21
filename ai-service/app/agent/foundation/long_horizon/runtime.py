from functools import lru_cache
from pathlib import Path

from .service import LongHorizonContextService
from .store import SQLiteContinuityStore


@lru_cache
def get_long_horizon_service() -> LongHorizonContextService:
    project_root = Path(__file__).resolve().parents[4]
    return LongHorizonContextService(
        SQLiteContinuityStore(project_root / "tmp" / "context_continuity.sqlite")
    )
