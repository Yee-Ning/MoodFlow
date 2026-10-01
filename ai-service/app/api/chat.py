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
from app.context.identity import UserIdentityProvider
from app.schemas.chat import (
    ChatRequest,
    ClientActionStreamEvent,
    FinalStreamEvent,
    FoundationResponse,
    MessageStreamEvent,
    UpdateStreamEvent,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ai", tags=["ai"])


def _telemetry_sample_kind(request: Request) -> str:
    # This header changes observability labels only; it grants no privileges.
    return "acceptance" if request.headers.get("X-MoodFlow-Telemetry-Class") == "acceptance" else "production"


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


@router.post("/foundation", response_model=FoundationResponse)
async def foundation_chat(chat_request: ChatRequest, request: Request) -> FoundationResponse:
    try:
        session_cookie = _spring_session_cookie(request)
        user_id = await UserIdentityProvider().resolve(session_cookie)
        return await invoke_foundation_assistant(
            message=chat_request.message,
            thread_id=chat_request.thread_id or str(uuid4()),
            routing_context=chat_request.resolved_routing_context(),
            session_cookie=session_cookie,
            user_id=user_id,
            telemetry_sample_kind=_telemetry_sample_kind(request),
        )
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
        return await invoke_handoff_assistant(
            message=chat_request.message,
            thread_id=chat_request.thread_id or str(uuid4()),
            routing_context=chat_request.resolved_routing_context(),
            session_cookie=session_cookie, user_id=user_id,
            telemetry_sample_kind=_telemetry_sample_kind(request),
        )
    except Exception as exc:
        logger.warning("Handoff JSON request failed: %s", type(exc).__name__)
        raise HTTPException(status_code=502, detail="MoodFlow AI 暂时离线，请稍后再试") from exc


@router.post("/chat", response_class=StreamingResponse)
async def chat(chat_request: ChatRequest, request: Request) -> StreamingResponse:
    session_cookie = _spring_session_cookie(request)

    async def generate_events() -> AsyncIterator[str]:
        try:
            thread_id = chat_request.thread_id or str(uuid4())
            mode = get_settings().agent_graph_mode
            if mode in {"foundation", "handoff"}:
                user_id = await UserIdentityProvider().resolve(session_cookie)
                stream = stream_handoff_assistant if mode == "handoff" else stream_foundation_assistant
                event_stream = stream(
                    message=chat_request.message,
                    thread_id=thread_id,
                    routing_context=chat_request.resolved_routing_context(),
                    session_cookie=session_cookie,
                    user_id=user_id,
                    telemetry_sample_kind=_telemetry_sample_kind(request),
                )
            else:
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
                    yield _encode_sse(
                        "action",
                        event.action.model_dump(mode="json"),
                    )
                elif isinstance(event, FinalStreamEvent):
                    yield _encode_sse(
                        "final",
                        event.response.model_dump(mode="json"),
                    )
        except Exception:
            logger.exception("AI agent streaming request failed")
            yield _encode_sse(
                "error",
                {"message": "MoodFlow AI 暂时离线，请稍后再试"},
            )

        yield _encode_sse("done", {})

    return StreamingResponse(
        generate_events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
