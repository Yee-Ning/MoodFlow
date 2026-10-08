from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.agent.checkpointing import (
    close_runtime_checkpointer,
    initialize_runtime_checkpointer,
)
from app.agent.foundation.long_horizon.runtime import (
    close_long_horizon_service,
    get_long_horizon_service,
    persistence_backend,
)
from app.agent.memory.runtime import close_memory_runtime, get_memory_runtime, memory_backend
from app.agent.foundation.user_profile import close_profile_store
from app.api.chat import router as chat_router
from app.api.worldline import router as worldline_router
from app.api.rate_limit import GatewayRateLimitMiddleware
from app.api.redis_events import router as redis_events_router
from app.agent.foundation.subgraphs.worldline.session import get_worldline_session_service
from app.config.settings import load_runtime_environment


@asynccontextmanager
async def lifespan(_app: FastAPI):
    load_runtime_environment()
    await initialize_runtime_checkpointer()
    if persistence_backend() == "postgres":
        get_long_horizon_service()
    if memory_backend() != "disabled":
        get_memory_runtime()
    from app.redis_events.runtime import start_flush, close_runtime
    flush_task = await start_flush()
    try:
        yield
    finally:
        from app.agent.foundation.long_horizon.runner import drain_workers
        from app.agent.foundation.long_horizon.coordinator import get_continuity_coordinator
        from app.agent.foundation.streaming import get_foundation_graph, get_handoff_graph
        await drain_workers()
        await close_runtime(flush_task)
        get_continuity_coordinator.cache_clear()
        get_foundation_graph.cache_clear()
        get_handoff_graph.cache_clear()
        get_worldline_session_service.cache_clear()
        close_long_horizon_service()
        close_memory_runtime()
        close_profile_store()
        await close_runtime_checkpointer()


app = FastAPI(title="MoodFlow AI Service", version="0.1.0", lifespan=lifespan)
app.add_middleware(GatewayRateLimitMiddleware)
app.include_router(chat_router)
app.include_router(worldline_router)
app.include_router(redis_events_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "ok"
    }
