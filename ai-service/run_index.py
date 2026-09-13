"""Operator CLI for the final offline KnowledgeEnrichedChunk -> Qdrant stage."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence


AI_SERVICE_ROOT = Path(__file__).resolve().parent
if str(AI_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(AI_SERVICE_ROOT))

from app.rag.indexing import (  # noqa: E402
    DEFAULT_INDEXING_CONFIG_PATH,
    OfflineIndexingService,
    latest_knowledge_path,
    load_indexing_config,
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the offline Qwen3 dense and Qdrant BM25 child-chunk index."
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_INDEXING_CONFIG_PATH)
    parser.add_argument("--knowledge-path", type=Path)
    parser.add_argument("--release-manifest", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--local-files-only", action="store_true")
    parser.add_argument("--prune-stale", action="store_true")
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    project_root: Path = AI_SERVICE_ROOT,
) -> int:
    args = parse_args(argv)
    root = project_root.resolve()
    try:
        config = load_indexing_config(_resolve(root, args.config))
        if args.local_files_only:
            config = config.model_copy(
                update={
                    "dense": config.dense.model_copy(update={"local_files_only": True})
                }
            )
        knowledge_path = _resolve(root, args.knowledge_path) or latest_knowledge_path(
            root
        )
        outcome = OfflineIndexingService(
            root,
            config,
            knowledge_path=knowledge_path,
            release_manifest_path=_resolve(root, args.release_manifest),
            output_root=_resolve(root, args.output_root),
        ).run(
            limit=args.limit,
            validate_only=args.validate_only,
            prune_stale=args.prune_stale or None,
        )
        payload = {
            **outcome.summary,
            "projection": _relative(root, outcome.projection_path),
            "manifest": _relative(root, outcome.manifest_path),
            "failures": _relative(root, outcome.failures_path),
        }
    except (OSError, RuntimeError, ValueError) as exc:
        payload = {"status": "failed", "error": str(exc).splitlines()[0][:500]}
        _print(payload)
        return 2
    _print(payload)
    return 1 if outcome.failures else 0


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
