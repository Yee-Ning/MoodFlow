"""Strict JSONL I/O for RAG evaluation datasets and traces."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from app.rag.evaluation.models import RAGEvaluationCase, RAGEvaluationTrace

ModelT = TypeVar("ModelT", bound=BaseModel)


def load_evaluation_cases(path: Path) -> list[RAGEvaluationCase]:
    return _load_jsonl(path, RAGEvaluationCase, "rag_evaluation_case")


def load_evaluation_traces(path: Path) -> list[RAGEvaluationTrace]:
    return _load_jsonl(path, RAGEvaluationTrace, "rag_evaluation_trace")


def write_jsonl(path: Path, records: Iterable[BaseModel]) -> None:
    """Write validated models atomically so interrupted runs do not corrupt artifacts."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    payload = "\n".join(
        json.dumps(item.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))
        for item in records
    )
    try:
        temporary.write_text(payload + ("\n" if payload else ""), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


def _load_jsonl(path: Path, model_type: type[ModelT], label: str) -> list[ModelT]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"{label}_dataset_unreadable:{path}") from exc
    records: list[ModelT] = []
    for line_number, raw_line in enumerate(lines, start=1):
        if not raw_line.strip():
            continue
        try:
            records.append(model_type.model_validate_json(raw_line))
        except (ValidationError, ValueError) as exc:
            raise ValueError(f"{label}_invalid:{path}:{line_number}") from exc
    if not records:
        raise ValueError(f"{label}_dataset_empty:{path}")
    case_ids = [str(getattr(item, "case_id")) for item in records]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError(f"{label}_case_id_duplicate:{path}")
    return records
