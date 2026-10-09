"""Revocation checks for checkpoint-cached memory before prompt assembly."""

from .models import MemoryStatus
from .scopes import is_current


def current_memory_items(items, user_id, *, store=None):
    if not user_id:
        return items
    def memory_id(row):
        return row.get("memory_id", "") if isinstance(row, dict) else getattr(row, "memory_id", "")
    canonical = [row for row in items if str(memory_id(row)).startswith("memory:")]
    if not canonical:
        return items
    try:
        if store is None:
            from .runtime import get_memory_runtime
            store = get_memory_runtime().service.store
        if not store.preferences(user_id).enabled:
            return [row for row in items if row not in canonical]
        valid = set()
        for row in canonical:
            record = store.get_record(user_id, memory_id(row))
            if record is not None and record.status is MemoryStatus.ACTIVE and is_current(record.valid_until):
                valid.add(record.memory_id)
        return [row for row in items if row not in canonical or memory_id(row) in valid]
    except Exception:
        return [row for row in items if row not in canonical]
