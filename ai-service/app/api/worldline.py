from fastapi import APIRouter, HTTPException

from app.agent.foundation.subgraphs.worldline.models import (
    WorldLineRequest,
    WorldLineResult,
    WorldLineExploreRequest,
    WorldLineSessionResponse,
)
from app.agent.foundation.subgraphs.worldline.service import (
    WorldLineExecutionError,
    generate_worldline,
)
from app.agent.foundation.subgraphs.worldline.session import get_worldline_session_service

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


@router.post("/worldline/sessions", response_model=WorldLineSessionResponse)
async def create_worldline_session(request: WorldLineRequest) -> WorldLineSessionResponse:
    try:
        return await get_worldline_session_service().create(request.query)
    except WorldLineExecutionError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.public_message},
        ) from exc


@router.post("/worldline/explore", response_model=WorldLineSessionResponse)
async def explore_worldline(request: WorldLineExploreRequest) -> WorldLineSessionResponse:
    try:
        return await get_worldline_session_service().explore(request.session_id, request.branch_id)
    except WorldLineExecutionError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.public_message},
        ) from exc
