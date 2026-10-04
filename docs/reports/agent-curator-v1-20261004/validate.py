"""Local acceptance with process-only offline settings; no production env changes."""

import os
from pathlib import Path
import subprocess
import sys


root = Path(__file__).resolve().parents[3]
env = {
    **os.environ,
    "PYTHONUTF8": "1",
    "OPENBLAS_NUM_THREADS": "1",
    "OMP_NUM_THREADS": "1",
    "CHECKPOINT_BACKEND": "memory",
    "PERSISTENCE_BACKEND": "sqlite",
    "MEMORY_BACKEND": "disabled",
    "DATABASE_URL": "",
    "AI_AGENT_GRAPH_MODE": "foundation",
    "HANDOFF_CANARY_ENABLED": "false",
    "JEV_ENABLED": "false",
    "JEV_LIVE_TEST": "0",
    "RUN_LIVE_POSTGRES_SMOKE": "0",
    "RUN_WORLDLINE_POSTGRES_RESTART": "0",
    "QDRANT_INTEGRATION_URL": "",
    "AI_RAG_RETRIEVAL_MODE": "empty",
    "AI_RAG_RERANK_MODE": "off",
    "LLM_API_KEY": "offline-placeholder",
    "LLM_BASE_URL": "https://invalid.example/v1",
    "LLM_MODEL": "offline-test",
    "LLM_GENERATION_MODEL": "offline-test",
    "LLM_TIMEOUT_SECONDS": "5",
    "SPRING_BOOT_BASE_URL": "https://invalid.example",
    "SPRING_BOOT_TIMEOUT_SECONDS": "5",
}
raise SystemExit(
    subprocess.call([sys.executable, *sys.argv[1:]], cwd=root / "ai-service", env=env)
)
