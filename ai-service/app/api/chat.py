import asyncio
import json
import logging
from collections.abc import AsyncIterator
from http.cookies import SimpleCookie
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.agent.graph import stream_current_song_assistant
from app.agent.foundation.streaming import (
    invoke_foundation_assistant,
    stream_foundation_assistant,
)
from app.config.settings import get_settings
from app.agent.handoff.streaming import invoke_handoff_assistant, stream_handoff_assistant
from app.agent.handoff.rollout import select_chat_graph
from app.agent.handoff.observability import begin_observation, end_observation, finish_observation
from app.context.identity import UserIdentityProvider
from app.agent.foundation.long_horizon.execution import ContinuityConflict
from app.agent.foundation.long_horizon.action_ack import ActionAckConflict
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
from app.schemas.chat import (
    ChatRequest,
    ClientActionStreamEvent,
    FinalStreamEvent,
    FoundationResponse,
    MessageStreamEvent,
    UpdateStreamEvent,
)
from app.schemas.player import PlayerContext
from app.api.rate_limit import RateLimitExceeded, enforce_chat_session_limit, get_rate_limiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ai", tags=["ai"])


def _telemetry_sample_kind(request: Request) -> str:
    # This header changes observability labels only; it grants no privileges.
    value = request.headers.get("X-MoodFlow-Telemetry-Class")
    return value if value in {"acceptance", "synthetic"} else "production"


def _spring_session_cookie(request: Request) -> str | None:
    """Forward only Spring's session identity, never arbitrary browser cookies."""
    value = request.cookies.get("JSESSIONID")
    if not value:
        return None
    cookie = SimpleCookie()
    cookie["JSESSIONID"] = value
    return cookie["JSESSIONID"].OutputString()


def _encode_sse(event: str, data: dict[str, object]) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n"


def _with_action_owner(response: FoundationResponse, graph_mode: str, thread_id: str) -> FoundationResponse:
    # Stamp transport copies only; internal action DTOs and durable payload digests stay intact.
    results = []
    for result in response.results:
        metadata = dict(result.metadata)
        if "client_actions" in metadata:
            metadata["client_actions"] = [
                {**action, "graph_mode": graph_mode, "thread_id": thread_id}
                for action in metadata["client_actions"]
            ]
        results.append(result.model_copy(update={"metadata": metadata}))
    return response.model_copy(update={"results": results})


@router.post("/foundation", response_model=FoundationResponse)
async def foundation_chat(chat_request: ChatRequest, request: Request) -> FoundationResponse:
    try:
        session_cookie = _spring_session_cookie(request)
        user_id = await UserIdentityProvider().resolve(session_cookie)
        thread_id = chat_request.thread_id or str(uuid4())
        await enforce_chat_session_limit(thread_id, "foundation", user_id)
        response = await invoke_foundation_assistant(
            message=chat_request.message,
            thread_id=thread_id,
            routing_context=chat_request.resolved_routing_context(),
            session_cookie=session_cookie,
            user_id=user_id,
            telemetry_sample_kind=_telemetry_sample_kind(request),
            **chat_request.continuity_options(),
        )
        return _with_action_owner(response, "foundation", thread_id)
    except ContinuityConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RateLimitExceeded:
        raise
    except Exception as exc:
        logger.exception("Foundation JSON request failed")
        raise HTTPException(
            status_code=502,
            detail="MoodFlow AI 暂时离线，请稍后再试",
        ) from exc


@router.post("/handoff", response_model=FoundationResponse)
async def handoff_chat(chat_request: ChatRequest, request: Request) -> FoundationResponse:
    try:
        session_cookie = _spring_session_cookie(request)
        user_id = await UserIdentityProvider().resolve(session_cookie)
        thread_id = chat_request.thread_id or str(uuid4())
        await enforce_chat_session_limit(thread_id, "handoff", user_id)
        response = await invoke_handoff_assistant(
            message=chat_request.message,
            thread_id=thread_id,
            routing_context=chat_request.resolved_routing_context(),
            session_cookie=session_cookie, user_id=user_id,
            telemetry_sample_kind=_telemetry_sample_kind(request),
            **chat_request.continuity_options(),
        )
        return _with_action_owner(response, "handoff", thread_id)
    except ContinuityConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RateLimitExceeded:
        raise
    except Exception as exc:
        logger.warning("Handoff JSON request failed: %s", type(exc).__name__)
        raise HTTPException(status_code=502, detail="MoodFlow AI 暂时离线，请稍后再试") from exc


@router.post("/chat", response_class=StreamingResponse)
async def chat(chat_request: ChatRequest, request: Request) -> StreamingResponse:
    session_cookie = _spring_session_cookie(request)
    limiter = get_rate_limiter()
    admitted_thread = admitted_mode = admitted_user = None
    if limiter is not None:
        # Reject before StreamingResponse sends HTTP 200 or starts a worker.
        admitted_thread = chat_request.thread_id or str(uuid4())
        admitted_mode = select_chat_graph(get_settings(), chat_request.thread_id)
        try:
            if admitted_mode != "legacy":
                admitted_user = await UserIdentityProvider().resolve(session_cookie)
            await enforce_chat_session_limit(admitted_thread, admitted_mode, admitted_user, limiter=limiter)
        except ContinuityConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except RateLimitExceeded:
            raise
        except Exception as exc:
            logger.warning("Chat admission failed: %s", type(exc).__name__)
            raise HTTPException(status_code=502, detail="MoodFlow AI 暂时离线，请稍后再试") from exc

    async def generate_events() -> AsyncIterator[str]:
        observation = observation_token = None
        try:
            thread_id = admitted_thread or chat_request.thread_id or str(uuid4())
            mode = admitted_mode or select_chat_graph(get_settings(), chat_request.thread_id)
            observation, observation_token = begin_observation(mode, thread_id,
                _telemetry_sample_kind(request), entrypoint="chat")
            if mode in {"foundation", "handoff"}:
                user_id = admitted_user if limiter is not None else await UserIdentityProvider().resolve(session_cookie)
                stream = stream_handoff_assistant if mode == "handoff" else stream_foundation_assistant
                event_stream = stream(
                    message=chat_request.message,
                    thread_id=thread_id,
                    routing_context=chat_request.resolved_routing_context(),
                    session_cookie=session_cookie,
                    user_id=user_id,
                    telemetry_sample_kind=_telemetry_sample_kind(request),
                    **chat_request.continuity_options(),
                )
            else:
                if chat_request.resume or chat_request.new_session:
                    raise ContinuityConflict("legacy graph does not support continuity recovery")
                event_stream = stream_current_song_assistant(
                    message=chat_request.message,
                    thread_id=thread_id,
                    current_song_id=chat_request.resolved_current_song_id(),
                    session_cookie=session_cookie,
                )

            async for event in event_stream:
                if isinstance(event, MessageStreamEvent):
                    yield _encode_sse("message", {"delta": event.delta})
                elif isinstance(event, UpdateStreamEvent):
                    yield _encode_sse(
                        "update",
                        {"node": event.node, "status": event.status},
                    )
                elif isinstance(event, ClientActionStreamEvent):
                    action = event.action.model_dump(mode="json")
                    if mode in {"foundation", "handoff"}:
                        action.update(graph_mode=mode, thread_id=thread_id)
                    yield _encode_sse(
                        "action",
                        action,
                    )
                elif isinstance(event, FinalStreamEvent):
                    response = event.response
                    if not observation.worker_started:
                        finish_observation(observation, getattr(response, "status", "unknown"))
                    if mode in {"foundation", "handoff"} and isinstance(response, FoundationResponse):
                        response = _with_action_owner(response, mode, thread_id)
                    yield _encode_sse(
                        "final",
                        response.model_dump(mode="json"),
                    )
        except Exception:
            if observation is not None and not observation.worker_started:
                finish_observation(observation, "failed")
            logger.exception("AI agent streaming request failed")
            yield _encode_sse(
                "error",
                {"message": "MoodFlow AI 暂时离线，请稍后再试"},
            )
        finally:
            if observation_token is not None:
                end_observation(observation_token)

        yield _encode_sse("done", {})

    return StreamingResponse(
        generate_events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


class ActionAckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    thread_id: str = Field(min_length=1, max_length=128)
    graph_mode: Literal["foundation", "handoff"] | None = None
    action_id: str = Field(min_length=1, max_length=128)
    status: Literal["applied", "failed"]


@router.post("/actions/ack")
async def action_ack(body: ActionAckRequest, request: Request):
    from app.agent.foundation.long_horizon.coordinator import get_continuity_coordinator
    user_id = await UserIdentityProvider().resolve(_spring_session_cookie(request))
    try:
        return await get_continuity_coordinator().acknowledge(graph_mode=body.graph_mode,
            thread_id=body.thread_id, user_id=user_id, action_id=body.action_id, status=body.status)
    except ActionAckConflict as exc:
        raise HTTPException(status_code=409, detail={"code": exc.code, "message": str(exc)}) from exc
    except (ContinuityConflict, ValueError) as exc:
        raise HTTPException(status_code=409, detail={"code": "ack_state_conflict",
            "message": "动作回执与原动作或已保存回执不一致"}) from exc


class NamespaceMigrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    thread_id: str = Field(min_length=1, max_length=128)
    migration_id: str = Field(min_length=1, max_length=128)
    player_context: PlayerContext | None = None


@router.post("/continuity/migrate")
async def migrate_namespace(body: NamespaceMigrationRequest, request: Request):
    from app.agent.foundation.long_horizon.coordinator import get_continuity_coordinator
    from app.agent.foundation.long_horizon.migration import NamespaceMigrator
    from app.agent.foundation.streaming import get_handoff_graph
    from app.agent.foundation.routing import RoutingContext
    coordinator = get_continuity_coordinator()
    user_id = await UserIdentityProvider().resolve(_spring_session_cookie(request))
    call = None
    try:
        current_player = RoutingContext.model_validate(body.player_context.model_dump() if body.player_context else {})
        call = await coordinator.begin_call(graph=get_handoff_graph(), graph_mode="handoff", thread_id=body.thread_id,
            message="", routing_context=current_player, user_id=user_id, resume=True)
        config = await NamespaceMigrator(coordinator).migrate(call, migration_id=body.migration_id)
        return {"session_id": config["configurable"]["session_id"], "status": "migrated"}
    except (ContinuityConflict, ValueError) as exc:
        raise HTTPException(status_code=409, detail="运行状态不满足跨窗迁移条件") from exc
    finally:
        if call is not None:
            await call.close()


class RecoveryStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    thread_id: str = Field(min_length=1, max_length=128)
    graph_mode: Literal["foundation", "handoff"]


class WriteOperationStatusRequest(RecoveryStatusRequest):
    operation_id: str = Field(pattern=r"^op:[0-9a-f]{64}$")


@router.post("/continuity/operations/status")
async def write_operation_status(body: WriteOperationStatusRequest, request: Request):
    """Return persisted failed/unknown/confirmed only; no reconciliation or business write."""
    from app.agent.foundation.long_horizon.coordinator import get_continuity_coordinator
    user_id = await UserIdentityProvider().resolve(_spring_session_cookie(request))
    if user_id is None:
        raise HTTPException(status_code=401, detail={"code": "write_status_auth_required"})
    try:
        return await asyncio.to_thread(get_continuity_coordinator().write_operation_status,
            **body.model_dump(), user_id=user_id)
    except ContinuityConflict as exc:
        raise HTTPException(status_code=409, detail={"code": "write_status_scope_or_operation_mismatch",
            "message": "账号、图类型、会话或 operation ID 与写操作归属不一致"}) from exc


@router.post("/continuity/recovery/status")
async def recovery_status(body: RecoveryStatusRequest, request: Request):
    """Read diagnostics only; querying status cannot resume tools or reset limits."""
    from app.agent.foundation.long_horizon.coordinator import get_continuity_coordinator
    user_id = await UserIdentityProvider().resolve(_spring_session_cookie(request))
    try:
        return await asyncio.to_thread(get_continuity_coordinator().recovery_status,
            graph_mode=body.graph_mode, thread_id=body.thread_id, user_id=user_id)
    except ContinuityConflict as exc:
        raise HTTPException(status_code=409, detail={"code": "recovery_scope_mismatch",
            "message": "当前账号或图类型与原运行作用域不一致，无法读取恢复记录"}) from exc
