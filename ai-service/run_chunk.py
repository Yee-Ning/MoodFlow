"""Operator CLI for the offline Clean -> Chunk V1 pipeline."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence


AI_SERVICE_ROOT = Path(__file__).resolve().parent
if str(AI_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(AI_SERVICE_ROOT))

from app.rag.chunk import (  # noqa: E402
    DEFAULT_CHUNK_CONFIG_PATH,
    ChunkPipelineService,
    TokenCounterBackend,
    latest_cleaned_root,
    load_chunk_config,
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Chunk the cleaned Music RAG corpus.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CHUNK_CONFIG_PATH)
    parser.add_argument("--cleaned-root", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--tokenizer-name-or-path")
    parser.add_argument("--tokenizer-local-files-only", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--include-manual-review", action="store_true")
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    project_root: Path = AI_SERVICE_ROOT,
) -> int:
    args = parse_args(argv)
    root = project_root.resolve()
    try:
        config = load_chunk_config(_resolve(root, args.config))
        if args.tokenizer_name_or_path:
            config = config.model_copy(
                update={
                    "token_counter": config.token_counter.model_copy(
                        update={
                            "backend": TokenCounterBackend.HUGGINGFACE,
                            "tokenizer_name_or_path": args.tokenizer_name_or_path,
                            "local_files_only": args.tokenizer_local_files_only,
                        }
                    )
                }
            )
        cleaned_root = _resolve(root, args.cleaned_root) or latest_cleaned_root(root)
        outcome = ChunkPipelineService(
            root,
            config,
            cleaned_root=cleaned_root,
            cleaned_manifest_path=_resolve(root, args.manifest),
            output_root=_resolve(root, args.output_root),
        ).run(validate_only=args.validate_only, include_manual_review=args.include_manual_review)
        payload = {
            **outcome.summary,
            "manifest": (
                outcome.manifest_path.relative_to(root).as_posix()
                if outcome.manifest_path is not None
                and outcome.manifest_path.is_relative_to(root)
                else None
            ),
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


def _print(payload: object) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
