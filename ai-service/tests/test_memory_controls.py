from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from app.api import memory
from app.agent.memory.models import MemoryExtractionOutput, MemoryRetrievalRequest
from app.agent.foundation.context_sources import SourceRequest, UserProfileSource
from tests.test_memory_hardening import artist, setup_runtime


@pytest.fixture
def client_setup(monkeypatch):
    runtime, store, documents = setup_runtime(artist())
    app = FastAPI()
    app.include_router(memory.router)
    async def owner(_self, cookie):
        return "u" if cookie else None
    monkeypatch.setattr(memory.UserIdentityProvider, "resolve", owner)
    monkeypatch.setattr(memory, "get_memory_runtime", lambda: runtime)
    return app, runtime, store, documents


@pytest.mark.asyncio
async def test_controls_authentication_validation_and_user_isolation(client_setup):
    app, runtime, store, _ = client_setup
    foreign = runtime.service.remember(user_id="other", source_thread_id="t", candidate=artist()).memory
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/api/ai/memory/records")).status_code == 401
        client.cookies.set("JSESSIONID", "fixture-only")
        assert (await client.get("/api/ai/memory/records?limit=0")).status_code == 422
        assert (await client.patch("/api/ai/memory/preferences", json={"enabled": "yes"})).status_code == 422
        assert (await client.patch("/api/ai/memory/preferences", json={"enabled": False, "user_id": "other"})).status_code == 422
        assert (await client.delete(f"/api/ai/memory/records/{foreign.memory_id}")).status_code == 404
        assert store.get_record("other", foreign.memory_id) is not None


@pytest.mark.asyncio
async def test_pause_blocks_read_write_and_profile_source_and_resume_restores_current_facts(client_setup):
    app, runtime, store, documents = client_setup
    await runtime.extract_and_store(user_id="u", source_thread_id="t", source_message_id="m", user_message="我喜欢周杰伦")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                 cookies={"JSESSIONID": "fixture-only"}) as client:
        paused = await client.patch("/api/ai/memory/preferences", json={"enabled": False})
        assert paused.status_code == 200 and not paused.json()["projection_pending"]
        assert (await runtime.retrieve(MemoryRetrievalRequest(user_id="u", query="推荐周杰伦"))).items == []
        assert await runtime.extract_and_store(user_id="u", source_thread_id="t", user_message="我喜欢周杰伦") == []
        assert UserProfileSource().collect(SourceRequest("推荐周杰伦", {"user_id": "u"}, profile_store=documents), limit=16) == []
        assert (await client.patch("/api/ai/memory/preferences", json={"enabled": True})).status_code == 200
        assert (await runtime.retrieve(MemoryRetrievalRequest(user_id="u", query="推荐周杰伦"))).items
        page = (await client.get("/api/ai/memory/records")).json()
        assert page["items"] and "embedding" not in page["items"][0] and "user_id" not in page["items"][0]
        assert len(store.all_records()) == 1


@pytest.mark.asyncio
async def test_forget_removes_fact_evidence_and_hides_immutable_profile_versions(client_setup):
    app, runtime, store, documents = client_setup
    await runtime.extract_and_store(user_id="u", source_thread_id="t", source_message_id="m", user_message="我喜欢周杰伦")
    record = store.all_records()[0]
    old_version = documents.read("u").version
    generation = store.preferences("u").generation
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                 cookies={"JSESSIONID": "fixture-only"}) as client:
        response = await client.delete(f"/api/ai/memory/records/{record.memory_id}")
        assert response.status_code == 200 and response.json()["forgotten"]
        assert store.all_records() == [] and store._evidence == {}
        assert documents.read("u", version=old_version).entries == ()
        assert (await client.get("/api/ai/memory/records")).json()["items"] == []
        assert runtime.service.remember(user_id="u", source_thread_id="t", candidate=artist(),
                                        expected_generation=generation).action.value == "disabled"
        assert await runtime.extract_and_store(user_id="u", source_thread_id="t", source_message_id="m",
                                               user_message="我喜欢周杰伦") == []
        assert store.all_records() == []


@pytest.mark.asyncio
async def test_correction_is_scoped_grounded_and_idempotent(client_setup):
    app, runtime, store, _ = client_setup
    await runtime.extract_and_store(user_id="u", source_thread_id="t", user_message="我喜欢周杰伦")
    old = store.all_records()[0]
    changed = artist().model_copy(update={"preference": "dislike", "value": "disliked",
        "content": "用户不喜欢周杰伦", "evidence_quote": "我不喜欢周杰伦"})
    runtime.extractor.extract = AsyncMock(return_value=MemoryExtractionOutput(memories=[changed]))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                 cookies={"JSESSIONID": "fixture-only"}) as client:
        body = {"text": "我不喜欢周杰伦", "request_id": "edit1"}
        response = await client.patch(f"/api/ai/memory/records/{old.memory_id}", json=body)
        assert response.status_code == 200 and response.json()["action"] == "superseded"
        response = await client.patch(f"/api/ai/memory/records/{old.memory_id}", json=body)
        assert response.json()["action"] == "replayed"
        assert len(store.all_records()) == 2
        assert (await client.patch(f"/api/ai/memory/records/{old.memory_id}",
                                  json={"text": "password=fixture-only", "request_id": "edit2"})).status_code == 422


@pytest.mark.asyncio
async def test_correction_cannot_recreate_fact_forgotten_during_model_call(client_setup):
    app, runtime, store, _ = client_setup
    record = runtime.service.remember(user_id="u", source_thread_id="t", candidate=artist()).memory
    async def extract(_):
        runtime.service.forget("u", record.memory_id)
        return MemoryExtractionOutput(memories=[artist()])
    runtime.extractor.extract = extract
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                 cookies={"JSESSIONID": "fixture-only"}) as client:
        response = await client.patch(f"/api/ai/memory/records/{record.memory_id}",
                                      json={"text": "我喜欢周杰伦", "request_id": "edit"})
        assert response.status_code == 409 and store.all_records() == []


def test_checkpoint_cached_memory_is_not_reused_after_forget_or_pause():
    from app.agent.memory.context import current_memory_items
    runtime, store, _ = setup_runtime()
    record = runtime.service.remember(user_id="u", source_thread_id="t", candidate=artist()).memory
    cached = [{"memory_id": record.memory_id, "content": record.content}]
    assert current_memory_items(cached, "u", store=store) == cached
    runtime.service.set_enabled("u", False)
    assert current_memory_items(cached, "u", store=store) == []
    runtime.service.set_enabled("u", True)
    runtime.service.forget("u", record.memory_id)
    assert current_memory_items(cached, "u", store=store) == []
