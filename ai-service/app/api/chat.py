import logging
from uuid import uuid4

from fastapi import APIRouter, HTTPException, status
from openai import OpenAIError

from app.agent.graph import run_current_song_assistant
from app.schemas.chat import ChatRequest, ChatResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ai", tags=["ai"])


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    try:
        structured = await run_current_song_assistant(
            message=request.message,
            thread_id=request.thread_id or str(uuid4()),
            current_song_id=request.resolved_current_song_id(),
        )
    except (OpenAIError, RuntimeError) as exc:
        logger.exception("AI agent request failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="LLM service request failed",
        ) from exc

    return structured
