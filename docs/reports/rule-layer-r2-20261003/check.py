"""Reproduce offline checks without editing .env or retaining newline changes."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
FILES = [
    "ai-service/evals/config/router_capability_contract.json",
    "ai-service/evals/datasets/router_e2e_blind_v4.jsonl",
    "ai-service/evals/datasets/router_eval_gold_v2.jsonl",
    "ai-service/evals/datasets/jev_router_baseline_v1.jsonl",
    "ai-service/evals/reports/jev_router_baseline_v1_results.jsonl",
]
saved = {}
try:
    for name in FILES:
        path = ROOT / name
        original = path.read_bytes()
        blob = subprocess.check_output(["git", "show", f"HEAD:{name}"], cwd=ROOT)
        if original.replace(b"\r\n", b"\n") != blob:
            raise RuntimeError(f"Refusing to overwrite modified input: {name}")
        saved[path] = original
        path.write_bytes(blob)
    env = {
        **os.environ, "PYTHONUTF8": "1", "OPENBLAS_NUM_THREADS": "1",
        "OMP_NUM_THREADS": "1", "CHECKPOINT_BACKEND": "memory",
        "PERSISTENCE_BACKEND": "sqlite", "MEMORY_BACKEND": "disabled",
        "DATABASE_URL": "", "AI_AGENT_GRAPH_MODE": "foundation",
        "HANDOFF_CANARY_ENABLED": "false", "HANDOFF_CANARY_PERCENTAGE": "10",
        "JEV_ENABLED": "false", "LLM_API_KEY": "offline-validation-placeholder",
        "LLM_BASE_URL": "https://invalid.example/v1", "LLM_MODEL": "offline-test",
        "LLM_GENERATION_MODEL": "offline-test", "LLM_TIMEOUT_SECONDS": "30",
        "SPRING_BOOT_BASE_URL": "http://127.0.0.1:8080",
        "SPRING_BOOT_TIMEOUT_SECONDS": "10", "TYPESAFE_API_KEY": "offline-placeholder",
        "AI_RAG_RETRIEVAL_MODE": "empty", "AI_RAG_RERANK_MODE": "off",
        "JEV_LIVE_TEST": "0", "RUN_LIVE_POSTGRES_SMOKE": "0",
        "RUN_WORLDLINE_POSTGRES_RESTART": "0", "QDRANT_INTEGRATION_URL": "",
    }
    result = subprocess.call(
        [sys.executable, *sys.argv[1:]], cwd=ROOT / "ai-service", env=env,
    )
finally:
    for path, original in saved.items():
        path.write_bytes(original)
raise SystemExit(result)
