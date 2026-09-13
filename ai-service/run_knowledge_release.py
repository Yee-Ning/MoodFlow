"""Prepare QA packets and freeze the validated pre-embedding Knowledge V2 corpus."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence


AI_SERVICE_ROOT = Path(__file__).resolve().parent
if str(AI_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(AI_SERVICE_ROOT))

from app.rag.knowledge import (  # noqa: E402
    DEFAULT_KNOWLEDGE_CONFIG_PATH,
    latest_enriched_path,
)
from app.rag.knowledge.entity_linking.registry import (  # noqa: E402
    DEFAULT_ENTITY_REGISTRY_PATH,
)
from app.rag.knowledge.release import (  # noqa: E402
    KnowledgeReleaseBuilder,
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Review and freeze Knowledge V2 without running embedding or indexing."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare-review", action="store_true")
    mode.add_argument("--finalize", action="store_true")
    parser.add_argument("--source-root", type=Path, default=Path("data/knowledge/v1"))
    parser.add_argument("--output-root", type=Path, default=Path("data/knowledge/v2"))
    parser.add_argument("--review-root", type=Path, default=Path("data/knowledge/review-v2"))
    parser.add_argument("--enriched-path", type=Path)
    parser.add_argument("--config", type=Path, default=DEFAULT_KNOWLEDGE_CONFIG_PATH)
    parser.add_argument("--registry", type=Path, default=DEFAULT_ENTITY_REGISTRY_PATH)
    parser.add_argument("--claim-sample-size", type=int, default=100)
    parser.add_argument("--entity-sample-size", type=int, default=100)
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    project_root: Path = AI_SERVICE_ROOT,
) -> int:
    args = parse_args(argv)
    root = project_root.resolve()
    try:
        builder = KnowledgeReleaseBuilder(
            root,
            source_root=_resolve(root, args.source_root),
            output_root=_resolve(root, args.output_root),
            review_root=_resolve(root, args.review_root),
            enriched_path=(
                _resolve(root, args.enriched_path)
                if args.enriched_path is not None
                else latest_enriched_path(root)
            ),
            config_path=_resolve(root, args.config),
            registry_path=_resolve(root, args.registry),
            claim_sample_size=args.claim_sample_size,
            entity_sample_size=args.entity_sample_size,
        )
        outcome = builder.prepare_review() if args.prepare_review else builder.finalize()
        payload = {
            "status": outcome.status,
            "release_id": outcome.release_id,
            "output_root": (
                _relative(root, outcome.output_root) if outcome.output_root else None
            ),
            "review_root": _relative(root, outcome.review_root),
            **outcome.summary,
        }
    except (OSError, RuntimeError, ValueError) as exc:
        payload = {"status": "failed", "error": str(exc).splitlines()[0][:500]}
        _print(payload)
        return 2
    _print(payload)
    return 0


def _resolve(root: Path, value: Path) -> Path:
    return value.resolve() if value.is_absolute() else (root / value).resolve()


def _relative(root: Path, value: Path) -> str:
    return value.relative_to(root).as_posix() if value.is_relative_to(root) else str(value)


def _print(payload: object) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
