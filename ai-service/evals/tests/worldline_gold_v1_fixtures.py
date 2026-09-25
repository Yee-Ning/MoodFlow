"""Small reviewed-like records used only by Gold V1 infrastructure tests."""

from __future__ import annotations

import json
from pathlib import Path

from evals.worldline_gold_v1_models import content_sha256


def source_payload(
    content: str = "Nujabes is associated with Hip-Hop.",
    *,
    suffix: str = "nujabes",
) -> dict:
    return {
        "source_id": f"source:{suffix}-profile",
        "fixture_source_id": f"fixture:{suffix}-profile",
        "title": f"{suffix.title()} profile",
        "publisher": "Reviewed Music Source",
        "url": f"https://example.com/{suffix}",
        "provenance": None,
        "language": "en",
        "source_type": "editorial",
        "content": content,
        "content_hash": content_sha256(content),
    }


def case_payload(
    content: str = "Nujabes is associated with Hip-Hop.",
    *,
    suffix: str = "nujabes",
    artist_name: str = "Nujabes",
    artist_id: str = "artist:nujabes",
    catalog_id: str = "123",
) -> dict:
    exact_span = f"{artist_name} is associated with Hip-Hop."
    return {
        "schema_version": "worldline-gold-v1",
        "dataset_version": "v1",
        "dataset_kind": "real_gold_fixture",
        "case_id": f"wl-gold-v1-{suffix}-hip-hop",
        "region": "japanese",
        "tags": ["accepted", "artist_to_genre"],
        "artist": {
            "canonical_name": artist_name,
            "knowledge_entity_id": artist_id,
            "aliases": [],
            "registry_scope": "fixture_only_registry",
            "catalog_reference": {
                "provider": "netease",
                "entity_id": catalog_id,
                "entity_type": "artist",
                "name": artist_name,
            },
        },
        "semantic_relation": {
            "source_entity_id": artist_id,
            "target_genre": {
                "canonical_id": "genre:hip_hop",
                "canonical_name": "Hip-Hop",
                "registry_version": "v2",
            },
            "relation_type": "associated_with",
        },
        "evidence": [
            {
                "source_id": f"source:{suffix}-profile",
                "source_title": f"{suffix.title()} profile",
                "publisher": "Reviewed Music Source",
                "source_url": f"https://example.com/{suffix}",
                "source_type": "editorial",
                "language": "en",
                "exact_span": exact_span,
                "evidence_text": content,
                "span_start": content.index(exact_span),
                "span_end": content.index(exact_span) + len(exact_span),
                "content_sha256": content_sha256(content),
                "fixture_source_id": f"fixture:{suffix}-profile",
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
            "expected_catalog_node_id": f"netease:artist:{catalog_id}",
            "identity_evidence_source_ids": [f"source:{suffix}-profile"],
        },
        "review": {
            "status": "approved",
            "method": "source_and_identity_review",
            "relation_reviewer": "reviewer-a",
            "identity_reviewer": "reviewer-b",
            "adjudicator": None,
            "reviewed_at": "2026-09-25T00:00:00Z",
            "notes": "",
            "reviewer_notes": "fixture review",
            "promotion_time": "2026-09-25T00:00:00Z",
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
