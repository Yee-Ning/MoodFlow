"""Run the production knowledge pipeline with an explicit versioned registry.

The transparent trace records actual provider output; it never supplies claims,
modifies predicates, retries outside the production policy, or approves release.
"""

from __future__ import annotations

import argparse
import asyncio
from hashlib import sha256
import json
from pathlib import Path
import time

from app.config.settings import get_settings
from app.rag.knowledge.claims.extractor import ClaimExtractor
from app.rag.knowledge.claims.runtime import ExistingLLMClaimExtractionRuntime
from app.rag.knowledge.config import load_knowledge_config
from app.rag.knowledge.entity_linking.linker import EntityLinker
from app.rag.knowledge.entity_linking.registry import EntityRegistry
from app.rag.knowledge.pipeline import KnowledgePipeline
from app.rag.knowledge.service import KnowledgePipelineService
from app.rag.knowledge.verification.runtime import ExistingLLMClaimVerificationRuntime
from app.rag.knowledge.verification.verifier import ClaimVerifier


class ProviderTrace:
    def __init__(self, delegate, stage: str, trace: Path) -> None:
        self.delegate, self.stage, self.trace = delegate, stage, trace

    async def generate(self, payload: dict, **kwargs) -> str:
        started = time.perf_counter()
        event = {"stage": self.stage, "model": get_settings().model,
                 "schema_name": kwargs["schema_name"],
                 "payload_sha256": sha256(json.dumps(payload, ensure_ascii=False,
                                                      sort_keys=True).encode()).hexdigest()}
        content = payload.get("chunk_content")
        if isinstance(content, str):
            event["chunk_content_sha256"] = sha256(content.encode()).hexdigest()
        for key in ("chunk_id", "batch_start", "batch_end"):
            if key in payload:
                event[key] = payload[key]
        try:
            result = await self.delegate.generate(payload, **kwargs)
            event["provider_output"] = result
            event["request_status"] = "completed"
            return result
        except BaseException as exc:
            event["request_status"] = "failed"
            event["error_type"] = type(exc).__name__
            raise
        finally:
            event["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
            with self.trace.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")


async def run(root: Path, config_path: Path, registry_path: Path,
              enriched_path: Path, output_root: Path) -> dict:
    config = load_knowledge_config(config_path)
    if not config.llm_enabled:
        raise ValueError("native_preparation_requires_production_llm")
    registry = EntityRegistry.load(registry_path)
    output_root.mkdir(parents=True, exist_ok=True)
    trace = output_root / "provider_trace.jsonl"
    if trace.exists():
        raise ValueError("production_trace_already_exists_use_independent_attempt")
    trace.touch(exist_ok=False)
    pipeline = KnowledgePipeline(
        config,
        entity_linker=EntityLinker(config, registry=registry),
        claim_extractor=ClaimExtractor(config, runtime=ProviderTrace(
            ExistingLLMClaimExtractionRuntime(max_output_tokens=4096 if config.prompt_version == "v2" else None), "extraction", trace)),
        claim_verifier=ClaimVerifier(config, runtime=ProviderTrace(
            ExistingLLMClaimVerificationRuntime(), "verification", trace)),
    )
    started = time.perf_counter()
    outcome = await KnowledgePipelineService(
        root, config, enriched_path=enriched_path, output_root=output_root,
        pipeline=pipeline,
    ).run(sample_size=0)
    summary = {**outcome.summary, "model": get_settings().model,
               "elapsed_seconds": round(time.perf_counter() - started, 2),
               "config_path": config_path.relative_to(root).as_posix(),
               "registry_path": registry_path.relative_to(root).as_posix(),
               "claim_extraction_version": config.claim_extraction_version}
    (output_root / "native_preparation_run.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--registry", required=True, type=Path)
    parser.add_argument("--enriched-path", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    print(json.dumps(asyncio.run(run(root, *(root / p for p in (
        args.config, args.registry, args.enriched_path, args.output_root)))),
        ensure_ascii=False))


if __name__ == "__main__":
    main()
