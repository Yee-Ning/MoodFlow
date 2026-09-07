from fastapi import APIRouter, HTTPException

from app.agent.foundation.subgraphs.worldline.models import (
    WorldLineRequest,
    WorldLineResult,
)
from app.agent.foundation.subgraphs.worldline.service import (
    WorldLineExecutionError,
    generate_worldline,
)

router = APIRouter(prefix="/api/ai", tags=["ai"])


@router.post("/worldline", response_model=WorldLineResult)
async def worldline(request: WorldLineRequest) -> WorldLineResult:
    try:
        return await generate_worldline(request.query)
    except WorldLineExecutionError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.public_message},
        ) from exc
