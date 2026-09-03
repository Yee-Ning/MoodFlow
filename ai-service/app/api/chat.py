import logging

from fastapi import APIRouter, HTTPException, status
from openai import OpenAIError

from app.agent.runner import run_agent
from app.context.builder import build_context
from app.schemas.chat import ChatRequest, ChatResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ai", tags=["ai"])


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    try:
        resolved_context = await build_context(request.context)
        structured = await run_agent(request.message, resolved_context)
    except (OpenAIError, RuntimeError) as exc:
        logger.exception("AI agent request failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="LLM service request failed",
        ) from exc

    return structured
