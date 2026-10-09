from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agent.foundation.user_profile import ProfileEntry, ProfileWriter, SQLiteProfileStore
from app.agent.memory.models import MemoryExtractionOutput, MemoryRetrievalRequest
from app.agent.memory.projection import synchronize_profile
from app.agent.memory.retriever import MemoryRetriever
from app.agent.memory.runtime import MemoryRuntime
from app.agent.memory.service import MemoryService
from app.agent.memory.store import InMemoryMemoryStore
from tests.test_memory_v1 import FakeEmbedder, episode, profile


def artist(subject="周杰伦"):
    return profile(category="artist", subject=subject, preference="like", value="liked",
                   content=f"用户喜欢{subject}", quote=f"我喜欢{subject}")


def setup_runtime(*candidates, profiles=None):
    store = InMemoryMemoryStore()
    service = MemoryService(store, embedder=FakeEmbedder())
    document_store = profiles or SQLiteProfileStore(":memory:")
    runtime = MemoryRuntime(service, MemoryRetriever(store, embedder=FakeEmbedder()),
        extractor=SimpleNamespace(extract=AsyncMock(return_value=MemoryExtractionOutput(memories=list(candidates)))),
        profile_store=document_store)
    return runtime, store, document_store


@pytest.mark.asyncio
async def test_projection_failure_is_repaired_by_next_read():
    document = SQLiteProfileStore(":memory:")
    class Unavailable:
        def read(self, *args):
            raise ConnectionError("offline")
    runtime, store, _ = setup_runtime(artist(), profiles=Unavailable())
    await runtime.extract_and_store(user_id="u", source_thread_id="t", user_message="我喜欢周杰伦")
    assert store.pending_profile_revision("u") is not None
    runtime.profile_store = document
    result = await runtime.retrieve(MemoryRetrievalRequest(user_id="u", query="周杰伦"))
    assert result.items and document.read("u").entries[0].value == "用户喜欢周杰伦"
    assert store.pending_profile_revision("u") is None
    version = document.read("u").version
    await runtime.sync_profile("u")
    assert document.read("u").version == version


def test_projection_cas_conflict_retries_without_losing_independent_entry():
    store = InMemoryMemoryStore()
    service = MemoryService(store)
    record = service.remember(user_id="u", source_thread_id="t", candidate=artist()).memory
    document = SQLiteProfileStore(":memory:")
    class ConcurrentStore:
        first = True
        def read(self, *args):
            return document.read(*args)
        def write(self, value, *, expected_version):
            if self.first:
                self.first = False
                ProfileWriter(document).update("u", [ProfileEntry(key="manual", value="简洁回复", source="manual",
                    updated_at=record.updated_at, confidence=1, intents=("general_chat",))], expected_version=0)
                raise ValueError("profile version conflict")
            return document.write(value, expected_version=expected_version)
    assert synchronize_profile(store, ConcurrentStore(), "u")
    assert {row.key for row in document.read("u").entries} == {"manual", record.memory_key}
    assert store.pending_profile_revision("u") is None


def test_bounded_projection_keeps_authoritative_facts_and_removes_superseded_values():
    store, document = InMemoryMemoryStore(), SQLiteProfileStore(":memory:")
    service = MemoryService(store)
    for index in range(70):
        service.remember(user_id="u", source_thread_id="t", candidate=artist(str(index)))
    assert synchronize_profile(store, document, "u")
    assert len(document.read("u").entries) == 64 and len(store.profile_records("u")) == 70
    assert store._profile_omitted["u"] == 6
    assert document.read("other") is None
    service.remember(user_id="u", source_thread_id="t", candidate=profile(
        category="artist", subject="0", preference="dislike", value="disliked", content="用户不喜欢0"))
    assert synchronize_profile(store, document, "u")
    selected = next(row for row in document.read("u").entries if row.key == "artist:0")
    assert selected.value == "用户不喜欢0"


@pytest.mark.asyncio
async def test_partial_candidate_failure_still_projects_committed_profile():
    runtime, store, document = setup_runtime(artist(), episode("健身跳过慢歌"))
    runtime.service.embedder = SimpleNamespace(embed_documents=lambda _: (_ for _ in ()).throw(ConnectionError("offline")))
    with pytest.raises(ConnectionError):
        await runtime.extract_and_store(user_id="u", source_thread_id="t", user_message="我喜欢周杰伦，健身跳过慢歌")
    assert len(store.all_records()) == 1 and document.read("u") is not None
    assert store.pending_profile_revision("u") is None
