"""Bounded source-context helpers shared by relation conflict aggregation.

These helpers inspect runtime documents only. They never read evaluation labels,
review notes, URLs as semantic hints, or replace the original evidence span.
"""

from __future__ import annotations

import re
import unicodedata

from app.agent.foundation.subgraphs.worldline.models import EvidenceSource, WorldLineGraph


def stable_projected_source(source: EvidenceSource, existing: EvidenceSource | None) -> EvidenceSource:
    """Keep evidence identity immutable when a relation later becomes disputed."""
    if existing is None:
        return source
    derived = {"relation_key", "temporal_conflict"}
    def identity(item):
        return item.model_copy(update={
            "metadata": {k: v for k, v in item.metadata.items() if k not in derived},
        })
    # A changed span, claim, chunk, URL or source context still causes the
    # existing merge's source_conflict error; only relation annotations differ.
    return existing if identity(source) == identity(existing) else source


def preserve_conflict_descriptions(graph: WorldLineGraph, increment: WorldLineGraph, keys: set) -> WorldLineGraph:
    descriptions = {
        (edge.source, edge.target, edge.relation): edge.description
        for edge in increment.edges if (edge.source, edge.target, edge.relation) in keys
    }
    return graph.model_copy(update={"edges": [
        edge.model_copy(update={"description": descriptions[key]}) if (
            key := (edge.source, edge.target, edge.relation)
        ) in descriptions else edge for edge in graph.edges
    ]}) if descriptions else graph


def normalized_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


def named_in_text(name: str, text: str) -> bool:
    name = normalized_text(name)
    # Catalog disambiguators are identity metadata, not part of source prose.
    name = re.sub(r"\s+\((?:album|song|artist|band)\)$", "", name)
    return re.search(rf"(?<!\w){re.escape(name)}(?!\w)", normalized_text(text)) is not None


def scene_aliases(name: str) -> set[str]:
    normalized = normalized_text(name)
    base = re.sub(r"\s+(?:music\s+)?(?:scene|movement|community)$", "", normalized)
    return {normalized, base}


def quoted_first_person_subject(name: str, title: str, span: str) -> bool:
    """A named interview heading can bind a first-person quotation.

    Merely mentioning the artist somewhere in a title is insufficient.
    """
    heading = normalized_text(title)
    return bool(
        re.match(rf"^{re.escape(normalized_text(name))}\s*:\s*[‘'\"“]", heading)
        and re.search(r"\b(?:we|i)\b", normalized_text(span))
    )


def balanced_evidence(items: list, *, limit: int, stance) -> list:
    """Retain both semantic sides before filling a bounded evidence list."""
    unique = {item.evidence.evidence_id: item for item in items}
    selected = {}
    sides = set()
    for evidence_id, item in unique.items():
        side = stance(item)
        if side not in sides:
            selected[evidence_id] = item
            sides.add(side)
            if len(selected) >= limit:
                break
    for evidence_id, item in unique.items():
        if len(selected) >= limit:
            break
        selected[evidence_id] = item
    return list(selected.values())


def aggregation_trace(decisions: list, relations: list) -> list[dict]:
    """Record validated inputs and actual aggregation outputs, including abstention."""
    return [{
        "stage": "validation_normalization_then_conflict_aggregation",
        "inputs": [{
            "claim_id": item.candidate.evidence.claim_id,
            "evidence_id": item.candidate.evidence.evidence_id,
            "validation_status": item.status.value,
            "rejection_reasons": item.rejection_reasons,
            "aggregation_role": (
                "support" if item.status.value == "accepted"
                else "contradiction" if item.rejection_reasons in (
                    ["negated_influence"], ["negated_scene_membership"],
                ) else "excluded"
            ),
        } for item in decisions],
        "outputs": [{
            "source_entity": relation.source_entity.node_id,
            "target_entity": (
                relation.target_entity or getattr(relation, "era", None)
                or getattr(relation, "scene", None)
            ).node_id,
            "predicate": relation.relation_type.value,
            "evidence_claim_ids": [item.claim_id for item in relation.evidence],
            "conflict_flags": [key for key in (
                "temporal_conflict", "attribution_conflict", "direction_conflict",
                "scope_conflict", "location_conflict",
            ) if relation.metadata.get(key)],
        } for relation in relations],
    }]
