"""Seed the RAG V0 review set from existing project query fixtures."""

from __future__ import annotations

import json
from pathlib import Path

from app.rag.evaluation.models import ExpectedOutcome, RAGEvaluationCase

JAY_CHOU_CORPUS_SCOPE = "jay_chou_corpus_v1"
JAY_CHOU_CORE_ENTITY_IDS = frozenset(
    {
        "artist:jay_chou",
        "album:eight_dimensions",
        "album:fantasy",
        "album:greatest_works_of_art",
        "album:novembers_chopin",
        "album:qi_li_xiang",
        "album:still_fantasy",
        "album:the_era",
        "album:ye_hui_mei",
        "song:blue_and_white_porcelain",
        "song:dragon_fist",
        "song:east_wind_breaks",
        "song:far_away",
        "song:fearless",
        "song:love_confession",
        "song:nocturne",
        "song:nunchucks",
        "song:qi_li_xiang",
        "song:qing_tian",
        "song:rice_field",
    }
)
JAY_CHOU_CONTEXT_CASES = frozenset(
    {
        "七里香的创作背景",
        "霍元甲的创作背景",
        "这首歌的创作背景",
        "这张专辑有什么故事",
        "他的音乐风格有什么变化",
    }
)


def build_pending_dataset(source: Path, *, limit: int = 50) -> list[RAGEvaluationCase]:
    """Create a Jay Chou corpus-scoped set without inventing answer labels."""
    if limit < 1:
        raise ValueError("evaluation dataset limit must be positive")
    try:
        lines = source.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"evaluation_seed_unreadable:{source}") from exc
    rows: list[dict[str, object]] = []
    for line_number, raw_line in enumerate(lines, start=1):
        if not raw_line.strip():
            continue
        try:
            payload = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"evaluation_seed_invalid:{source}:{line_number}") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("query"), str):
            raise ValueError(f"evaluation_seed_invalid:{source}:{line_number}")
        if payload.get("status") not in {"ready", "needs_input"}:
            raise ValueError(f"evaluation_seed_status_invalid:{source}:{line_number}")
        if _is_jay_chou_corpus_case(payload):
            rows.append(payload)
    if len(rows) < limit:
        raise ValueError(f"evaluation_scoped_seed_too_small:{source}:{len(rows)}<{limit}")

    needs_input = [item for item in rows if item["status"] == "needs_input"]
    reserved_needs_input = needs_input[: min(5, limit)]
    ready_limit = limit - len(reserved_needs_input)
    selected = [item for item in rows if item["status"] == "ready"][:ready_limit]
    selected.extend(reserved_needs_input)
    if len(selected) != limit:
        raise ValueError(f"evaluation_seed_distribution_invalid:{source}")
    return [
        _build_case(index, payload)
        for index, payload in enumerate(selected, start=1)
    ]


def _build_case(index: int, payload: dict[str, object]) -> RAGEvaluationCase:
    needs_input = payload["status"] == "needs_input"
    entity_ids = payload.get("entity_ids", [])
    query_types = payload.get("query_types", [])
    if not isinstance(entity_ids, list) or not all(
        isinstance(item, str) for item in entity_ids
    ):
        raise ValueError(f"evaluation_seed_entity_ids_invalid:{index}")
    if not isinstance(query_types, list) or not all(
        isinstance(item, str) for item in query_types
    ):
        raise ValueError(f"evaluation_seed_query_types_invalid:{index}")
    return RAGEvaluationCase(
        case_id=f"rag-v0-{index:03d}",
        query=str(payload["query"]),
        expected_outcome=(
            ExpectedOutcome.NEEDS_INPUT
            if needs_input
            else ExpectedOutcome.ANSWERABLE
        ),
        expected_entity_ids=entity_ids,
        expected_query_types=query_types,
        tags=[JAY_CHOU_CORPUS_SCOPE, "query_understanding_v1", payload["status"]],
    )


def _is_jay_chou_corpus_case(payload: dict[str, object]) -> bool:
    query = str(payload["query"])
    if payload["status"] == "needs_input":
        return query in JAY_CHOU_CONTEXT_CASES
    entity_ids = payload.get("entity_ids", [])
    return isinstance(entity_ids, list) and bool(
        JAY_CHOU_CORE_ENTITY_IDS.intersection(
            item for item in entity_ids if isinstance(item, str)
        )
    )
