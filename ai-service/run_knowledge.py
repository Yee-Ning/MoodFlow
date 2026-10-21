"""Operator CLI for EnrichedChunk -> KnowledgeEnrichedChunk."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Sequence


AI_SERVICE_ROOT = Path(__file__).resolve().parent
if str(AI_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(AI_SERVICE_ROOT))

from app.rag.knowledge import (  # noqa: E402
    DEFAULT_KNOWLEDGE_CONFIG_PATH,
    EntityLinker,
    EntityRegistry,
    KnowledgePipeline,
    KnowledgePipelineService,
    latest_enriched_path,
    load_knowledge_config,
)
from app.rag.knowledge.entity_linking.registry import DEFAULT_ENTITY_REGISTRY_PATH  # noqa: E402


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Link entities, extract claims, and verify them against enriched chunks."
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_KNOWLEDGE_CONFIG_PATH)
    parser.add_argument("--enriched-path", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--sample-size", type=int, default=3)
    parser.add_argument("--rule-only", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument(
        "--allow-paid-llm",
        action="store_true",
        help="Explicitly allow billable LLM calls when the config enables them.",
    )
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    project_root: Path = AI_SERVICE_ROOT,
) -> int:
    args = parse_args(argv)
    root = project_root.resolve()
    try:
        if args.sample_size < 0 or args.sample_size > 5:
            raise ValueError("sample-size must be between 0 and 5")
        config = load_knowledge_config(_resolve(root, args.config))
        if args.rule_only:
            config = config.model_copy(update={"llm_enabled": False})
        if config.llm_enabled and not args.validate_only and not args.allow_paid_llm:
            raise ValueError(
                "paid LLM disabled by default; pass --allow-paid-llm or --rule-only"
            )
        enriched_path = _resolve(root, args.enriched_path) or latest_enriched_path(root)
        registry = EntityRegistry.load(
            DEFAULT_ENTITY_REGISTRY_PATH.with_name(f"{config.registry_version}.yaml")
        )
        pipeline = KnowledgePipeline(
            config, entity_linker=EntityLinker(config, registry=registry)
        )
        outcome = asyncio.run(
            KnowledgePipelineService(
                root,
                config,
                enriched_path=enriched_path,
                output_root=_resolve(root, args.output_root),
                pipeline=pipeline,
            ).run(
                limit=args.limit,
                validate_only=args.validate_only,
                sample_size=args.sample_size,
            )
        )
        payload = {
            **outcome.summary,
            "knowledge_chunks_path": _relative(root, outcome.chunks_path),
            "manifest": _relative(root, outcome.manifest_path),
            "quality_report": _relative(root, outcome.report_path),
            "registry_candidates": _relative(root, outcome.candidates_path),
        }
    except (OSError, RuntimeError, ValueError) as exc:
        payload = {"status": "failed", "error": str(exc).splitlines()[0][:500]}
        _print(payload)
        return 2
    _print(payload)
    return 0


def _resolve(root: Path, value: Path | None) -> Path | None:
    if value is None:
        return None
    return value.resolve() if value.is_absolute() else (root / value).resolve()


def _relative(root: Path, value: Path | None) -> str | None:
    if value is None:
        return None
    return (
        value.relative_to(root).as_posix() if value.is_relative_to(root) else str(value)
    )


def _print(payload: object) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
