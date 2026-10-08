"""P3 backend endpoints. Existing chat POST and frontend framing stay intact."""

import asyncio
import json
import logging
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.context.identity import UserIdentityProvider
from app.agent.foundation.long_horizon.history_writer import digest
from app.redis_events.runtime import playcounts, progress_events
from .chat import _spring_session_cookie

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ai", tags=["redis-events"])


def backend(factory, label):
    try:
        service = factory()
    except Exception as exc:
        logger.warning("%s initialization failed (%s)", label, type(exc).__name__)
        raise HTTPException(503, f"{label} unavailable") from exc
    if service is None:
        raise HTTPException(503, f"{label} backend is disabled")
    return service


@router.post("/playcounts/{song_id}")
async def record_play(song_id: str):
    counter = backend(playcounts, "Playcount")
    try:
        return {"song_id": song_id, "count": await asyncio.to_thread(counter.increment, song_id)}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        logger.warning("Playcount recording failed (%s)", type(exc).__name__)
        raise HTTPException(503, "Playcount unavailable") from exc


@router.get("/playcounts/{song_id}")
async def read_playcount(song_id: str):
    counter = backend(playcounts, "Playcount")
    try:
        return {"song_id": song_id, "count": await asyncio.to_thread(counter.read, song_id)}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        logger.warning("Playcount read failed (%s)", type(exc).__name__)
        raise HTTPException(503, "Playcount unavailable") from exc


@router.get("/events/resume")
async def resume_events(request: Request, run_id: str = Query(min_length=1, max_length=160),
                        since: str = "0-0", graph_mode: Literal["foundation", "handoff"] = "foundation",
                        limit: int = Query(default=100, ge=1, le=1000)):
    events = backend(progress_events, "Progress")
    try:
        user_id = await UserIdentityProvider().resolve(_spring_session_cookie(request))
        rows = await asyncio.to_thread(events.resume, run_id, since, digest([graph_mode, user_id]), limit)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, "Progress run unavailable") from exc
    except Exception as exc:
        logger.warning("Progress replay failed (%s)", type(exc).__name__)
        raise HTTPException(503, "Progress replay unavailable") from exc
    # Finite replay page, not a live subscription and not an implicit ACK.
    frames = [f"id: {event_id}\nevent: update\ndata: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"
              for event_id, payload in rows]
    return StreamingResponse(iter(frames), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
