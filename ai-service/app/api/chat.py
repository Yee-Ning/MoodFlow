import logging
import json
from collections.abc import AsyncIterator
from uuid import uuid4

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.agent.graph import stream_current_song_assistant
from app.schemas.chat import (
    ChatRequest,
    FinalStreamEvent,
    MessageStreamEvent,
    UpdateStreamEvent,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ai", tags=["ai"])


def _encode_sse(event: str, data: dict[str, object]) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n"


@router.post("/chat", response_class=StreamingResponse)
async def chat(request: ChatRequest) -> StreamingResponse:
    async def generate_events() -> AsyncIterator[str]:
        try:
            async for event in stream_current_song_assistant(
                message=request.message,
                thread_id=request.thread_id or str(uuid4()),
                current_song_id=request.resolved_current_song_id(),
            ):
                if isinstance(event, MessageStreamEvent):
                    yield _encode_sse("message", {"delta": event.delta})
                elif isinstance(event, UpdateStreamEvent):
                    yield _encode_sse(
                        "update",
                        {"node": event.node, "status": event.status},
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
