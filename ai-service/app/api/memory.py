"""Authenticated self-service controls; user identity never comes from JSON."""

import asyncio

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from app.api.chat import _spring_session_cookie
from app.context.identity import UserIdentityProvider
from app.agent.memory.runtime import get_memory_runtime
from app.agent.memory.policy import MemoryAdmissionError, safe_user_message, validate_candidate
from app.agent.structured_output import StructuredOutputError
from app.agent.memory.telemetry import record_memory_event

router = APIRouter(prefix="/api/ai/memory", tags=["memory"])


class PreferenceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool


class MemoryCorrection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=4000)
    request_id: str = Field(min_length=1, max_length=128, pattern=r"\S")


async def _owner(request):
    user_id = await UserIdentityProvider().resolve(_spring_session_cookie(request))
    if user_id is None:
        raise HTTPException(401, "登录后才能管理记忆")
    try:
        return user_id, get_memory_runtime()
    except RuntimeError:
        raise HTTPException(503, "记忆服务暂不可用") from None


@router.get("/preferences")
async def preferences(request: Request):
    user, runtime = await _owner(request)
    return await asyncio.to_thread(runtime.service.store.preferences, user)


@router.patch("/preferences")
async def update_preferences(payload: PreferenceUpdate, request: Request):
    user, runtime = await _owner(request)
    result = await asyncio.to_thread(runtime.service.set_enabled, user, payload.enabled)
    synced = await runtime.sync_profile(user)
    record_memory_event("preferences", outcome="enabled" if payload.enabled else "paused")
    return {**result.model_dump(), "projection_pending": not synced}


@router.get("/records")
async def records(request: Request, after_id: str = Query("", max_length=160),
                  limit: int = Query(20, ge=1, le=50)):
    user, runtime = await _owner(request)
    rows = await asyncio.to_thread(runtime.service.store.list_records, user, after_id=after_id, limit=limit + 1)
    visible = rows[:limit]
    return {"items": [row.model_dump(mode="json", exclude={"embedding", "user_id"}) for row in visible],
            "next_cursor": visible[-1].memory_id if len(rows) > limit else None,
            "preferences": (await asyncio.to_thread(runtime.service.store.preferences, user)).model_dump()}


@router.delete("/records/{memory_id}")
async def forget(memory_id: str, request: Request):
    user, runtime = await _owner(request)
    try:
        await asyncio.to_thread(runtime.service.forget, user, memory_id)
    except KeyError:
        raise HTTPException(404, "记忆不存在") from None
    synced = await runtime.sync_profile(user)
    record_memory_event("forget", outcome="completed")
    return {"forgotten": True, "projection_pending": not synced,
            "history_policy": "原对话和历史画像按既有保留规则保留；撤销条目不再参与记忆召回。"}


@router.patch("/records/{memory_id}")
async def correct(memory_id: str, payload: MemoryCorrection, request: Request):
    user, runtime = await _owner(request)
    existing = await asyncio.to_thread(runtime.service.store.get_record, user, memory_id)
    if existing is None:
        raise HTTPException(404, "记忆不存在")
    preferences = await asyncio.to_thread(runtime.service.store.preferences, user)
    if not preferences.enabled:
        raise HTTPException(409, "请先恢复记忆，再更正偏好")
    if not safe_user_message(payload.text):
        raise HTTPException(422, "更正内容不能包含敏感信息")
    try:
        output = await runtime.extractor.extract(payload.text)
        if len(output.memories) != 1:
            raise MemoryAdmissionError("single correction required")
        candidate = output.memories[0]
        validate_candidate(candidate, user_message=payload.text)
        if (candidate.memory_type != existing.memory_type
                or candidate.category != existing.structured_content.get("category")
                or candidate.subject != existing.structured_content.get("subject")
                or candidate.scene != existing.structured_content.get("scene")):
            raise MemoryAdmissionError("correction topic mismatch")
        result = await asyncio.to_thread(runtime.service.remember, user_id=user,
            source_thread_id=existing.source_thread_id, source_message_id="correction:" + payload.request_id,
            candidate=candidate, user_message=payload.text, expected_generation=preferences.generation)
        if result.action.value == "disabled":
            raise HTTPException(409, "记忆已被暂停或撤销，请刷新后重试")
    except (StructuredOutputError, MemoryAdmissionError):
        raise HTTPException(422, "请用第一人称明确描述同一主题的偏好") from None
    except ValueError:
        raise HTTPException(409, "更正请求发生冲突，请刷新后重试") from None
    synced = await runtime.sync_profile(user)
    record_memory_event("correct", outcome=result.action.value)
    return {"action": result.action, "memory": result.memory.model_dump(mode="json", exclude={"embedding", "user_id"})
            if result.memory else None, "projection_pending": not synced}
