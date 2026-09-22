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
from app.api.chat import router as chat_router
from app.api.worldline import router as worldline_router


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await initialize_runtime_checkpointer()
    if persistence_backend() == "postgres":
        get_long_horizon_service()
    try:
        yield
    finally:
        close_long_horizon_service()
        await close_runtime_checkpointer()


app = FastAPI(title="MoodFlow AI Service", version="0.1.0", lifespan=lifespan)
app.include_router(chat_router)
app.include_router(worldline_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "ok"
    }
