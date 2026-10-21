"""Inspect QueryAnalysis and SearchPlan without running online retrieval."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Sequence

from langchain_core.messages import HumanMessage

from app.rag.query_understanding import (
    QueryUnderstandingService,
    QueryUnderstandingStatus,
    create_query_understanding_graph,
    create_query_understanding_state,
    load_index_profile,
    load_query_understanding_config,
)
from app.schemas.player import PlayerContext


PROJECT_ROOT = Path(__file__).resolve().parent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a retrieval SearchPlan for one music query."
    )
    parser.add_argument("query", help="Music knowledge query to analyze")
    parser.add_argument("--current-song-id", type=int)
    parser.add_argument("--index-manifest", type=Path)
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Use deterministic rules only for query-type analysis",
    )
    return parser


async def _run(args: argparse.Namespace, project_root: Path) -> tuple[int, str]:
    config = load_query_understanding_config()
    if args.no_llm:
        config = config.model_copy(update={"llm_fallback_enabled": False})
    profile = (
        load_index_profile(project_root, args.index_manifest)
        if args.index_manifest is not None
        else None
    )
    service = QueryUnderstandingService(config=config, index_profile=profile, project_root=project_root)
    graph = create_query_understanding_graph(service=service)
    current_song_id = args.current_song_id
    routing_context = PlayerContext(
        has_current_song=current_song_id is not None,
        current_song_id=current_song_id,
    )
    output = await graph.ainvoke(
        create_query_understanding_state(
            args.query,
            messages=[HumanMessage(content=args.query)],
            routing_context=routing_context,
        )
    )
    result = output["result"]
    status = 0 if result.status is QueryUnderstandingStatus.READY else 2
    return status, result.model_dump_json(indent=2)


def main(
    argv: Sequence[str] | None = None,
    *,
    project_root: Path = PROJECT_ROOT,
) -> int:
    args = build_parser().parse_args(argv)
    try:
        status, payload = asyncio.run(_run(args, project_root.resolve()))
    except ValueError as exc:
        print(
            json.dumps(
                {"status": "failed", "error": str(exc)},
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        return 1
    print(payload)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
