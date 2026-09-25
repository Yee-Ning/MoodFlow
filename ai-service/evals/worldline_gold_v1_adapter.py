"""Adapt approved Gold V1 records to the existing WorldLine eval executor."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from app.agent.foundation.subgraphs.worldline.genre_registry import (
    GenreVocabularyRegistry,
)
from evals.worldline_eval_models import (
    SCHEMA_VERSION as EVAL_SCHEMA_VERSION,
    LabelProvenance,
    WorldLineEvalCase,
    WorldLineEvalTask,
    WorldLineEvalTier,
)
from evals.worldline_gold_v1_loader import WorldLineGoldDataset
from evals.worldline_gold_v1_models import GoldCase, GoldOutcome


@dataclass(frozen=True)
class GoldAdapterResult:
    cases: tuple[WorldLineEvalCase, ...]
    semantic_case_count: int
    identity_case_count: int
    genre_case_count: int
    approved_gold_case_count: int


def _slug(case_id: str) -> str:
    return case_id.removeprefix("wl-gold-v1-")


def _claim_id(case: GoldCase) -> str:
    key = (
        f"{case.artist.knowledge_entity_id}|"
        f"{case.semantic_relation.target_genre.canonical_id}|"
        f"{case.semantic_relation.relation_type}"
    )
    return f"claim:{sha256(key.encode('utf-8')).hexdigest()[:24]}"


def _provenance(case: GoldCase) -> LabelProvenance:
    return LabelProvenance(
        source="real_gold_fixture",
        review_status="approved",
        reviewer=case.review.relation_reviewer,
        notes=(
            f"real_gold_fixture:{case.case_id};"
            f"identity_reviewer={case.review.identity_reviewer}"
        ),
    )


def _semantic_case(case: GoldCase) -> WorldLineEvalCase:
    catalog = case.artist.catalog_reference
    if catalog is None and case.expected.outcome is GoldOutcome.ACCEPTED:
        raise ValueError(f"worldline_gold_adapter_identity_missing:{case.case_id}")
    catalog_node_id = (
        catalog.node_id if catalog else f"eval:artist:{_slug(case.case_id)}"
    )
    evidence = case.evidence[0] if case.evidence else None
    accepted = case.expected.outcome is GoldOutcome.ACCEPTED
    relation = {
        "source_id": catalog_node_id,
        "target_id": case.semantic_relation.target_genre.canonical_id,
        "relation": case.semantic_relation.relation_type,
        "claim_id": _claim_id(case),
        "exact_span": evidence.exact_span if evidence else "",
    }
    return WorldLineEvalCase(
        schema_version=EVAL_SCHEMA_VERSION,
        dataset_version="v0",
        case_id=f"wl-real-gold-semantic-{_slug(case.case_id)}",
        tier=WorldLineEvalTier.COMPONENT,
        task=WorldLineEvalTask.SEMANTIC_EXPANSION,
        tags=[
            "real_gold",
            "positive" if accepted else "negative",
            "artist_to_genre",
        ],
        input={
            "artist_catalog_id": catalog_node_id,
            "catalog_provider": catalog.provider if catalog else "eval",
            "artist_name": case.artist.canonical_name,
            "artist_alias": case.artist.aliases[0] if case.artist.aliases else None,
            "artist_knowledge_id": case.artist.knowledge_entity_id,
        },
        fixtures={
            "scenario": "semantic_gold" if accepted else "semantic_no_evidence",
            "documents": (
                [
                    {
                        "content": evidence.evidence_text,
                        "source_id": evidence.source_id,
                        "fixture_source_id": evidence.fixture_source_id,
                    }
                ]
                if evidence
                else []
            ),
        },
        expected=(
            {"outcome": "accepted", "relations": [relation]}
            if accepted
            else {
                "outcome": "rejected",
                "relations": [],
                "candidate_count": 0,
                "rejection_reasons": [],
                "gold_reason": case.expected.reason,
            }
        ),
        label_provenance=_provenance(case),
    )


def _identity_case(case: GoldCase) -> WorldLineEvalCase:
    catalog = case.artist.catalog_reference
    if catalog is None:
        raise ValueError(f"worldline_gold_adapter_identity_missing:{case.case_id}")
    return WorldLineEvalCase(
        schema_version=EVAL_SCHEMA_VERSION,
        dataset_version="v0",
        case_id=f"wl-real-gold-identity-{_slug(case.case_id)}",
        tier=WorldLineEvalTier.COMPONENT,
        task=WorldLineEvalTask.ENTITY_RESOLUTION,
        tags=["real_gold", "positive", "identity"],
        input={
            "knowledge_entity_id": case.artist.knowledge_entity_id,
            "canonical_name": case.artist.canonical_name,
            "aliases": case.artist.aliases,
            "catalog_candidates": [
                {
                    "id": catalog.node_id,
                    "name": catalog.name,
                    "provider": catalog.provider,
                }
            ],
        },
        fixtures={"scenario": "identity_exact"},
        expected={
            "outcome": "resolved",
            "status": "resolved",
            "reason": "knowledge_catalog_identity_resolved",
            "catalog_node_id": catalog.node_id,
        },
        label_provenance=_provenance(case),
    )


def _genre_case(root: GoldCase, target: GoldCase) -> WorldLineEvalCase:
    root_catalog = root.artist.catalog_reference
    target_catalog = target.artist.catalog_reference
    if root_catalog is None or target_catalog is None or not target.evidence:
        raise ValueError(f"worldline_gold_adapter_genre_identity_missing:{target.case_id}")
    evidence = target.evidence[0]
    return WorldLineEvalCase(
        schema_version=EVAL_SCHEMA_VERSION,
        dataset_version="v0",
        case_id=f"wl-real-gold-genre-{_slug(target.case_id)}",
        tier=WorldLineEvalTier.COMPONENT,
        task=WorldLineEvalTask.GENRE_EXPLORATION,
        tags=["real_gold", "positive", "genre_to_artist"],
        input={
            "target_genre_id": target.semantic_relation.target_genre.canonical_id,
            "root_artist_id": root_catalog.node_id,
            "root_artist_name": root.artist.canonical_name,
            "root_artist_knowledge_id": root.artist.knowledge_entity_id,
            "root_catalog_provider": root_catalog.provider,
        },
        fixtures={
            "scenario": "genre_gold",
            "documents": [
                {
                    "content": evidence.evidence_text,
                    "source_id": evidence.source_id,
                    "fixture_source_id": evidence.fixture_source_id,
                }
            ],
        },
        expected={
            "outcome": "accepted",
            "artists": [
                {
                    "knowledge_entity_id": target.artist.knowledge_entity_id,
                    "catalog_node_id": target_catalog.node_id,
                    "catalog_provider": target_catalog.provider,
                    "artist_name": target.artist.canonical_name,
                    "claim_id": _claim_id(target),
                    "exact_span": evidence.exact_span,
                }
            ],
        },
        label_provenance=_provenance(target),
    )


def adapt_worldline_gold_dataset(
    dataset: WorldLineGoldDataset,
) -> GoldAdapterResult:
    """Create isolated replay cases without mutating any production registry."""

    approved = list(dataset.approved_cases)
    semantic = [_semantic_case(case) for case in approved]
    accepted = [
        case for case in approved if case.expected.outcome is GoldOutcome.ACCEPTED
    ]
    identity = [_identity_case(case) for case in accepted]

    registry = GenreVocabularyRegistry.load()
    by_genre: dict[str, list[GoldCase]] = {}
    for case in accepted:
        genre_id = case.semantic_relation.target_genre.canonical_id
        genre = registry.get(genre_id)
        if genre is not None and genre.executable:
            by_genre.setdefault(genre_id, []).append(case)
    genre_cases: list[WorldLineEvalCase] = []
    for grouped in by_genre.values():
        ordered = sorted(grouped, key=lambda item: item.case_id)
        if len(ordered) < 2:
            continue
        root = ordered[0]
        genre_cases.extend(_genre_case(root, target) for target in ordered[1:])

    cases = tuple([*semantic, *identity, *genre_cases])
    if len({case.case_id for case in cases}) != len(cases):
        raise ValueError("worldline_gold_adapter_duplicate_eval_case")
    return GoldAdapterResult(
        cases=cases,
        semantic_case_count=len(semantic),
        identity_case_count=len(identity),
        genre_case_count=len(genre_cases),
        approved_gold_case_count=len(approved),
    )
