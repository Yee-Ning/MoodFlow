"""Load and validate WorldLine Gold V1 cases and their source ledger."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path

from evals.worldline_gold_v1_models import (
    GoldCase,
    GoldReviewStatus,
    GoldSourceLedgerEntry,
)


@dataclass(frozen=True)
class WorldLineGoldDataset:
    cases: tuple[GoldCase, ...]
    sources: tuple[GoldSourceLedgerEntry, ...]
    dataset_sha256: str
    source_ledger_sha256: str

    @property
    def approved_cases(self) -> tuple[GoldCase, ...]:
        return tuple(
            case
            for case in self.cases
            if case.review.status is GoldReviewStatus.APPROVED
        )


def _file_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _load_jsonl(path: Path, model_type, *, kind: str):
    if not path.is_file():
        raise ValueError(f"worldline_gold_{kind}_missing:{path}")
    rows = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        try:
            rows.append(model_type.model_validate(json.loads(line)))
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError(
                f"worldline_gold_{kind}_invalid:{path}:{line_number}"
            ) from exc
    if not rows:
        raise ValueError(f"worldline_gold_{kind}_empty:{path}")
    return rows


def _validate_unique_sources(sources: list[GoldSourceLedgerEntry]) -> None:
    source_ids: set[str] = set()
    fixture_ids: set[str] = set()
    for source in sources:
        if source.source_id in source_ids:
            raise ValueError(f"worldline_gold_duplicate_source:{source.source_id}")
        if source.fixture_source_id in fixture_ids:
            raise ValueError(
                f"worldline_gold_duplicate_fixture_source:{source.fixture_source_id}"
            )
        source_ids.add(source.source_id)
        fixture_ids.add(source.fixture_source_id)


def _validate_cases(
    cases: list[GoldCase],
    sources: list[GoldSourceLedgerEntry],
) -> None:
    source_by_fixture_id = {source.fixture_source_id: source for source in sources}
    case_ids: set[str] = set()
    relations: set[tuple[str, str, str]] = set()
    for case in cases:
        if case.case_id in case_ids:
            raise ValueError(f"worldline_gold_duplicate_case:{case.case_id}")
        case_ids.add(case.case_id)
        relation_key = (
            case.artist.knowledge_entity_id,
            case.semantic_relation.target_genre.canonical_id,
            case.semantic_relation.relation_type,
        )
        if relation_key in relations:
            raise ValueError(
                "worldline_gold_duplicate_relation:" + "|".join(relation_key)
            )
        relations.add(relation_key)

        for evidence in case.evidence:
            source = source_by_fixture_id.get(evidence.fixture_source_id)
            if source is None:
                raise ValueError(
                    "worldline_gold_evidence_source_missing:"
                    f"{case.case_id}:{evidence.fixture_source_id}"
                )
            comparisons = {
                "source_id": (evidence.source_id, source.source_id),
                "title": (evidence.source_title, source.title),
                "publisher": (evidence.publisher, source.publisher),
                "url": (
                    str(evidence.source_url) if evidence.source_url else None,
                    str(source.url) if source.url else None,
                ),
                "language": (evidence.language, source.language),
                "source_type": (evidence.source_type, source.source_type),
                "content": (evidence.evidence_text, source.content),
                "content_hash": (evidence.content_sha256, source.content_hash),
            }
            mismatch = next(
                (
                    field
                    for field, (case_value, ledger_value) in comparisons.items()
                    if case_value != ledger_value
                ),
                None,
            )
            if mismatch:
                raise ValueError(
                    "worldline_gold_evidence_ledger_mismatch:"
                    f"{case.case_id}:{evidence.fixture_source_id}:{mismatch}"
                )

        identity_sources = set(case.identity.identity_evidence_source_ids)
        known_source_ids = {source.source_id for source in sources}
        missing_identity_sources = identity_sources - known_source_ids
        if missing_identity_sources:
            missing = sorted(missing_identity_sources)[0]
            raise ValueError(
                f"worldline_gold_identity_source_missing:{case.case_id}:{missing}"
            )


def load_worldline_gold_dataset(
    dataset_path: Path,
    source_ledger_path: Path,
) -> WorldLineGoldDataset:
    """Load a fully validated dataset or raise before evaluation can start."""

    cases = _load_jsonl(dataset_path, GoldCase, kind="dataset")
    sources = _load_jsonl(
        source_ledger_path,
        GoldSourceLedgerEntry,
        kind="source_ledger",
    )
    _validate_unique_sources(sources)
    _validate_cases(cases, sources)
    return WorldLineGoldDataset(
        cases=tuple(cases),
        sources=tuple(sources),
        dataset_sha256=_file_sha256(dataset_path),
        source_ledger_sha256=_file_sha256(source_ledger_path),
    )
