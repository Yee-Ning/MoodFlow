"""Candidate manifest persistence and human-review state transitions."""

import json
import os
import tempfile
from pathlib import Path

from .models import CandidateSource, ReviewStatus


class CandidateManifestError(ValueError):
    """Raised when a candidate manifest is invalid or ambiguous."""


def load_candidates(path: Path) -> list[CandidateSource]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CandidateManifestError(f"cannot load candidate manifest: {path}") from exc
    if not isinstance(payload, list):
        raise CandidateManifestError("candidate manifest root must be a JSON array")

    candidates = [CandidateSource.model_validate(item) for item in payload]
    source_ids = [candidate.source_id for candidate in candidates]
    if len(source_ids) != len(set(source_ids)):
        raise CandidateManifestError("candidate source_id values must be unique")
    return candidates


def write_candidates(path: Path, candidates: list[CandidateSource]) -> None:
    source_ids = [candidate.source_id for candidate in candidates]
    if len(source_ids) != len(set(source_ids)):
        raise CandidateManifestError("candidate source_id values must be unique")

    rendered = json.dumps(
        [candidate.model_dump(mode="json") for candidate in candidates],
        ensure_ascii=False,
        indent=2,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(f"{rendered}\n")
            handle.flush()
            os.fsync(handle.fileno())
            temporary_path = Path(handle.name)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def update_review_status(
    path: Path,
    source_id: str,
    status: ReviewStatus,
) -> CandidateSource:
    candidates = load_candidates(path)
    matches = [candidate for candidate in candidates if candidate.source_id == source_id]
    if not matches:
        raise CandidateManifestError(f"unknown source_id: {source_id}")

    updated = matches[0].model_copy(update={"status": status})
    write_candidates(
        path,
        [updated if candidate.source_id == source_id else candidate for candidate in candidates],
    )
    return updated
