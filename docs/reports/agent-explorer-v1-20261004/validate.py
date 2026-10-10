"""Run isolated offline checks, preserving frozen input bytes and user files."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[3]
FROZEN = (
    "ai-service/evals/config/router_capability_contract.json",
    "ai-service/evals/datasets/router_e2e_blind_v4.jsonl",
    "ai-service/evals/datasets/router_eval_gold_v2.jsonl",
    "ai-service/evals/datasets/router_eval_v2.jsonl",
    "ai-service/evals/datasets/multi_dev_v3.jsonl",
    "ai-service/evals/datasets/multi_blind_test_v3.jsonl",
    "ai-service/evals/datasets/single_blind_v3.jsonl",
    "ai-service/evals/datasets/jev_router_baseline_v1.jsonl",
    "ai-service/evals/reports/jev_router_baseline_v1_results.jsonl",
    "ai-service/evals/datasets/rag_eval_gold_v3.ai_assisted_source_resolution.jsonl",
    "ai-service/evals/datasets/rag_eval_gold_v4.candidate.jsonl",
    "ai-service/evals/datasets/rag_eval_gold_v4.expanded_50.candidate.jsonl",
    "ai-service/evals/datasets/rag_eval_gold_v4.frozen.jsonl",
)
GENERATED_REPORT = "ai-service/evals/reports/router_gold_v2_review.md"


def sha256(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def within(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("fixture path outside root")
    return path


LOCAL_FIXTURES = (
    "ai-service/data/knowledge/v2/knowledge_release_manifest.json",
    "ai-service/data/knowledge/v2/knowledge_enriched_chunks.jsonl",
    "ai-service/evals/artifacts/gold_v2_trace_v3_fallback_20260916.jsonl",
    "ai-service/evals/artifacts/gold_v2_trace_v3_nav_context6_20260916.jsonl",
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture-root", type=Path)
    options, command = parser.parse_known_args()
    if not command:
        parser.error("supply a Python command such as -m pytest tests -q")
    fixture_root = options.fixture_root.resolve() if options.fixture_root else None
    saved, provenance = {}, []
    try:
        report = ROOT / GENERATED_REPORT
        saved[report] = report.read_bytes()
        for relative in FROZEN:
            path = ROOT / relative
            original = path.read_bytes()
            blob = subprocess.check_output(
                ["git", "show", f"HEAD:{relative}"], cwd=ROOT
            )
            if original.replace(b"\r\n", b"\n") != blob.replace(b"\r\n", b"\n"):
                raise RuntimeError(
                    f"refusing to replace edited frozen input: {relative}"
                )
            data = blob
            if fixture_root and "/rag_eval_gold_" in relative:
                data = (fixture_root / relative).read_bytes()
                if data.replace(b"\r\n", b"\n") != blob.replace(b"\r\n", b"\n"):
                    raise RuntimeError(
                        f"fixture differs from frozen HEAD input: {relative}"
                    )
            saved[path] = original
            path.write_bytes(data)
            provenance.append(
                {
                    "path": relative,
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "kind": "frozen_test_input; restored_after_execution",
                }
            )
        for relative in LOCAL_FIXTURES:
            target = ROOT / relative
            if not target.exists() and fixture_root:
                data = (fixture_root / relative).read_bytes()
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            if target.exists():
                provenance.append(
                    {
                        "path": relative,
                        "sha256": sha256(target),
                        "kind": "existing_local_test_fixture; no new knowledge_source",
                    }
                )
        lock = json.loads(
            (ROOT / "deploy/frozen-artifacts.json").read_text(encoding="utf-8")
        )
        for relative, expected in lock["files"].items():
            relative = "ai-service/" + relative
            target = within(ROOT, relative)
            if not target.exists() and fixture_root:
                source = within(fixture_root, relative)
                if "sha256:" + sha256(source) != expected:
                    raise RuntimeError(
                        f"existing fixture differs from artifact lock: {relative}"
                    )
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
            if target.exists():
                digest = sha256(target)
                if "sha256:" + digest != expected:
                    raise RuntimeError(
                        f"refusing to replace edited artifact: {relative}"
                    )
                provenance.append(
                    {
                        "path": relative,
                        "sha256": digest,
                        "kind": "existing_locked_test_artifact; no index rebuild",
                    }
                )
        output = ROOT / ".dev/agent-explorer-v1/fixture-provenance.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
        env = {
            **os.environ,
            "PYTHONUTF8": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "OMP_NUM_THREADS": "1",
            "PYTHONPATH": os.pathsep.join((str(ROOT / "ai-service"), str(ROOT))),
            "CHECKPOINT_BACKEND": "memory",
            "PERSISTENCE_BACKEND": "sqlite",
            "MEMORY_BACKEND": "disabled",
            "DATABASE_URL": "",
            "AI_AGENT_GRAPH_MODE": "foundation",
            "HANDOFF_CANARY_ENABLED": "false",
            "JEV_HANDOFF_ROUTER_ENABLED": "false",
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
        return subprocess.call(
            [sys.executable, *command], cwd=ROOT / "ai-service", env=env
        )
    finally:
        for path, data in saved.items():
            path.write_bytes(data)


if __name__ == "__main__":
    raise SystemExit(main())
