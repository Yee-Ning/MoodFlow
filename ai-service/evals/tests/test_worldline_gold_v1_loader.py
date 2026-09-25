import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from evals.worldline_gold_v1_loader import load_worldline_gold_dataset
from evals.worldline_gold_v1_models import GoldCase, content_sha256


def source_payload(content: str = "Nujabes is associated with Hip-Hop.") -> dict:
    return {
        "source_id": "source:nujabes-profile",
        "fixture_source_id": "fixture:nujabes-profile",
        "title": "Nujabes profile",
        "publisher": "Reviewed Music Source",
        "url": "https://example.com/nujabes",
        "provenance": None,
        "language": "en",
        "source_type": "editorial",
        "content": content,
        "content_hash": content_sha256(content),
    }


def case_payload(content: str = "Nujabes is associated with Hip-Hop.") -> dict:
    exact_span = "Nujabes is associated with Hip-Hop."
    return {
        "schema_version": "worldline-gold-v1",
        "dataset_version": "v1",
        "dataset_kind": "real_gold_fixture",
        "case_id": "wl-gold-v1-nujabes-hip-hop",
        "region": "japanese",
        "tags": ["accepted", "artist_to_genre"],
        "artist": {
            "canonical_name": "Nujabes",
            "knowledge_entity_id": "artist:nujabes",
            "aliases": [],
            "registry_scope": "fixture_only_registry",
            "catalog_reference": {
                "provider": "netease",
                "entity_id": "123",
                "entity_type": "artist",
                "name": "Nujabes",
            },
        },
        "semantic_relation": {
            "source_entity_id": "artist:nujabes",
            "target_genre": {
                "canonical_id": "genre:hip_hop",
                "canonical_name": "Hip-Hop",
                "registry_version": "v2",
            },
            "relation_type": "associated_with",
        },
        "evidence": [
            {
                "source_id": "source:nujabes-profile",
                "source_title": "Nujabes profile",
                "publisher": "Reviewed Music Source",
                "source_url": "https://example.com/nujabes",
                "source_type": "editorial",
                "language": "en",
                "exact_span": exact_span,
                "evidence_text": content,
                "span_start": content.index(exact_span),
                "span_end": content.index(exact_span) + len(exact_span),
                "content_sha256": content_sha256(content),
                "fixture_source_id": "fixture:nujabes-profile",
            }
        ],
        "expected": {
            "outcome": "accepted",
            "reason": "direct_source_support",
            "runtime_relation": "associated_with",
            "runtime_rejection_reason": None,
        },
        "identity": {
            "knowledge_status": "resolved",
            "catalog_status": "resolved",
            "ambiguity_status": "unambiguous",
            "expected_catalog_node_id": "netease:artist:123",
            "identity_evidence_source_ids": ["source:nujabes-profile"],
        },
        "review": {
            "status": "approved",
            "method": "source_and_identity_review",
            "relation_reviewer": "reviewer-a",
            "identity_reviewer": "reviewer-b",
            "adjudicator": None,
            "reviewed_at": "2026-09-25T00:00:00Z",
            "notes": "",
        },
    }


def write_dataset(
    tmp_path: Path,
    cases: list[dict],
    sources: list[dict],
) -> tuple[Path, Path]:
    dataset = tmp_path / "worldline_gold_v1.jsonl"
    ledger = tmp_path / "worldline_gold_v1_sources.jsonl"
    dataset.write_text(
        "".join(json.dumps(row) + "\n" for row in cases),
        encoding="utf-8",
    )
    ledger.write_text(
        "".join(json.dumps(row) + "\n" for row in sources),
        encoding="utf-8",
    )
    return dataset, ledger


def test_gold_schema_and_source_ledger_load() -> None:
    case = GoldCase.model_validate(case_payload())

    assert case.semantic_relation.relation_type == "associated_with"
    assert case.artist.catalog_reference is not None
    assert case.artist.catalog_reference.node_id == "netease:artist:123"


def test_accepted_case_rejects_missing_evidence_and_ambiguous_identity() -> None:
    missing_evidence = case_payload()
    missing_evidence["evidence"] = []
    with pytest.raises(ValidationError, match="accepted_evidence_required"):
        GoldCase.model_validate(missing_evidence)

    ambiguous = case_payload()
    ambiguous["identity"]["ambiguity_status"] = "name_collision"
    with pytest.raises(ValidationError, match="accepted_identity_ambiguous"):
        GoldCase.model_validate(ambiguous)


def test_gold_case_rejects_unregistered_genre_and_invalid_relation() -> None:
    unknown = case_payload()
    unknown["semantic_relation"]["target_genre"]["canonical_id"] = (
        "genre:not_registered"
    )
    with pytest.raises(ValidationError, match="genre_not_registered"):
        GoldCase.model_validate(unknown)

    invalid_relation = case_payload()
    invalid_relation["semantic_relation"]["relation_type"] = "influenced_by"
    with pytest.raises(ValidationError):
        GoldCase.model_validate(invalid_relation)


def test_loader_validates_exact_span_hash_and_ledger_consistency(
    tmp_path: Path,
) -> None:
    content = "Profile: Nujabes is associated with Hip-Hop. End."
    dataset, ledger = write_dataset(
        tmp_path,
        [case_payload(content)],
        [source_payload(content)],
    )

    loaded = load_worldline_gold_dataset(dataset, ledger)

    assert len(loaded.approved_cases) == 1
    assert len(loaded.dataset_sha256) == 64
    assert len(loaded.source_ledger_sha256) == 64

    bad_source = source_payload(content)
    bad_source["title"] = "Different title"
    dataset, ledger = write_dataset(tmp_path, [case_payload(content)], [bad_source])
    with pytest.raises(ValueError, match="evidence_ledger_mismatch"):
        load_worldline_gold_dataset(dataset, ledger)


def test_loader_rejects_duplicate_case_relation_source_and_missing_identity(
    tmp_path: Path,
) -> None:
    first = case_payload()
    duplicate = case_payload()
    duplicate["case_id"] = "wl-gold-v1-nujabes-hip-hop-copy"
    dataset, ledger = write_dataset(
        tmp_path,
        [first, duplicate],
        [source_payload()],
    )
    with pytest.raises(ValueError, match="duplicate_relation"):
        load_worldline_gold_dataset(dataset, ledger)

    missing_identity_source = case_payload()
    missing_identity_source["identity"]["identity_evidence_source_ids"] = [
        "source:missing"
    ]
    dataset, ledger = write_dataset(
        tmp_path,
        [missing_identity_source],
        [source_payload()],
    )
    with pytest.raises(ValueError, match="identity_source_missing"):
        load_worldline_gold_dataset(dataset, ledger)


def test_loader_rejects_duplicate_source_and_content_hash(tmp_path: Path) -> None:
    source = source_payload()
    dataset, ledger = write_dataset(
        tmp_path,
        [case_payload()],
        [source, source],
    )
    with pytest.raises(ValueError, match="duplicate_source"):
        load_worldline_gold_dataset(dataset, ledger)

    bad_hash = source_payload()
    bad_hash["content_hash"] = "0" * 64
    dataset, ledger = write_dataset(tmp_path, [case_payload()], [bad_hash])
    with pytest.raises(ValueError, match="source_ledger_invalid"):
        load_worldline_gold_dataset(dataset, ledger)
