import json
import logging
from collections.abc import AsyncIterator
from http.cookies import SimpleCookie
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.agent.graph import stream_current_song_assistant
from app.agent.foundation.streaming import stream_foundation_assistant
from app.config.settings import get_settings
from app.schemas.chat import (
    ChatRequest,
    ClientActionStreamEvent,
    FinalStreamEvent,
    MessageStreamEvent,
    UpdateStreamEvent,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ai", tags=["ai"])


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


@router.post("/chat", response_class=StreamingResponse)
async def chat(chat_request: ChatRequest, request: Request) -> StreamingResponse:
    session_cookie = _spring_session_cookie(request)

    async def generate_events() -> AsyncIterator[str]:
        try:
            thread_id = chat_request.thread_id or str(uuid4())
            if get_settings().agent_graph_mode == "foundation":
                event_stream = stream_foundation_assistant(
                    message=chat_request.message,
                    thread_id=thread_id,
                    routing_context=chat_request.resolved_routing_context(),
                    session_cookie=session_cookie,
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
