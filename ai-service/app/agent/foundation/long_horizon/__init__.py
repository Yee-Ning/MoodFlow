"""Durable cross-session continuity built on the Phase 1 context engine."""

from .models import (
    ArtifactRecord,
    ColdStartContext,
    HandoffDraft,
    HandoffProjection,
    HistoryEvent,
    HistoryEventType,
    HistoryPointer,
    RolloverReason,
    RolloverResult,
    SessionRecord,
    TaskRecord,
)
from .service import LongHorizonContextService, RolloverPolicy
from .store import ContinuityStore, SQLiteContinuityStore

__all__ = [
    "ArtifactRecord", "ColdStartContext", "HandoffDraft", "HandoffProjection",
    "HistoryEvent", "HistoryEventType", "HistoryPointer",
    "LongHorizonContextService", "RolloverPolicy", "RolloverReason",
    "RolloverResult", "ContinuityStore", "SQLiteContinuityStore", "SessionRecord",
    "TaskRecord",
]
