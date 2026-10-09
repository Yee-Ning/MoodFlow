"""Recoverable, bounded profile views of authoritative Memory records."""

import hashlib
from datetime import UTC, datetime

from app.agent.foundation.context_sources import load_context_policy
from app.agent.foundation.long_horizon.history_writer import validate_public_document
from app.agent.foundation.user_profile import ProfileDocument, ProfileEntry, ProfileWriter

from .models import MemoryStatus
from .scopes import is_current


def profile_key(memory_key):
    return memory_key if len(memory_key) <= 80 else hashlib.sha256(memory_key.encode()).hexdigest()


def synchronize_profile(memory_store, profile_store, user_id, *, attempts=3):
    with memory_store.transaction(user_id, "profile_projection"):
        revision = memory_store.pending_profile_revision(user_id)
        if revision is None:
            return True
        records = memory_store.profile_records(user_id)
        owned = {profile_key(record.memory_key) for record in records}
        active = sorted((row for row in records if row.status is MemoryStatus.ACTIVE and is_current(row.valid_until)),
                        key=lambda row: (row.structured_content.get("preference") in {"avoid", "dislike"},
                                         row.confidence, row.importance, row.updated_at, row.memory_id), reverse=True)
        categories = load_context_policy()["profile_categories"]
        for attempt in range(attempts):
            previous = profile_store.read(user_id)
            retained = tuple(entry for entry in previous.entries if entry.key not in owned) if previous else ()
            entries = []
            for memory in active:
                entry = ProfileEntry(
                    key=profile_key(memory.memory_key), value=memory.content[:1000],
                    source=memory.source_thread_id, updated_at=memory.updated_at, confidence=memory.confidence,
                    intents=tuple(categories.get(memory.structured_content.get("category"), categories["default"])),
                    scene=memory.structured_content.get("scene"), valid_until=memory.valid_until,
                )
                try:
                    validate_public_document(ProfileDocument, ProfileDocument(
                        user_id=user_id, version=1, updated_at=datetime.now(UTC),
                        entries=(*retained, *entries, entry)))
                except ValueError:
                    continue  # A bounded view may omit facts; the main store keeps them.
                entries.append(entry)
            try:
                ProfileWriter(profile_store).project(user_id, entries, managed_keys=owned,
                    expected_version=previous.version if previous else 0)
            except ValueError as exc:
                if str(exc) == "profile version conflict" and attempt < attempts - 1:
                    continue
                raise
            return memory_store.complete_profile_revision(user_id, revision, len(active) - len(entries))
    return False
