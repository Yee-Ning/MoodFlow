"""Registry-first query entity resolution with safe ambiguity handling."""

from __future__ import annotations

import re

from app.rag.knowledge.entity_linking.models import EntityType
from app.rag.knowledge.entity_linking.linker import EntityLinker, _JAY_SHORT_NAME
from app.rag.knowledge.entity_linking.registry import EntityRegistry, normalize_alias
from app.rag.query_understanding.models import (
    CatalogReference,
    QueryEntityStatus,
    ResolvedQueryEntity,
)


_TYPE_CUES: dict[EntityType, tuple[str, ...]] = {
    EntityType.ARTIST: ("歌手", "艺人", "音樂人", "音乐人"),
    EntityType.SONG: ("歌曲", "这首歌", "那首歌", "单曲"),
    EntityType.ALBUM: ("专辑", "唱片", "这张专辑", "那张专辑"),
    EntityType.PERSON: ("作词人", "作曲人", "制作人", "人物"),
    EntityType.GENRE: ("流派", "音乐流派", "音樂流派"),
}


def infer_entity_type(query: str, surface: str) -> EntityType | None:
    """Infer only a label immediately attached to one mention, never globally."""

    position = query.casefold().find(surface.casefold())
    if position < 0:
        return None
    prefix = query[max(0, position - 12) : position]
    matches = [
        entity_type
        for entity_type, cues in _TYPE_CUES.items()
        if any(
            re.search(rf"{re.escape(cue)}[《\"“'\s：:]*$", prefix)
            for cue in cues
        )
    ]
    return matches[0] if len(matches) == 1 else None


def _surface_span(query: str, surface: str) -> tuple[int, int] | None:
    if surface.isascii() and surface.replace(" ", "").isalnum():
        match = re.search(
            rf"(?<![A-Za-z0-9]){re.escape(surface)}(?![A-Za-z0-9])",
            query,
            re.IGNORECASE,
        )
        return match.span() if match is not None else None
    start = query.casefold().find(surface.casefold())
    return (start, start + len(surface)) if start >= 0 else None


class QueryEntityResolver:
    def __init__(self, registry: EntityRegistry | None = None) -> None:
        self.registry = registry or EntityRegistry.load()

    def resolve(
        self,
        query: str,
        *,
        context_entities: list[ResolvedQueryEntity] | None = None,
        catalog_references: list[CatalogReference] | None = None,
    ) -> list[ResolvedQueryEntity]:
        result: list[ResolvedQueryEntity] = list(context_entities or [])
        seen_knowledge_ids = {
            item.knowledge_entity_id
            for item in result
            if item.knowledge_entity_id is not None
        }
        seen_catalog = {
            (item.catalog_reference.provider, item.catalog_reference.entity_id)
            for item in result
            if item.catalog_reference is not None
        }
        for reference in catalog_references or []:
            key = (reference.provider, reference.entity_id)
            if key in seen_catalog:
                continue
            item = self._from_catalog(reference)
            if item.knowledge_entity_id in seen_knowledge_ids:
                result = [
                    existing.model_copy(update={"catalog_reference": reference})
                    if existing.knowledge_entity_id == item.knowledge_entity_id
                    and existing.catalog_reference is None
                    else existing
                    for existing in result
                ]
            else:
                result.append(item)
                if item.knowledge_entity_id is not None:
                    seen_knowledge_ids.add(item.knowledge_entity_id)
            seen_catalog.add(key)

        seen_surfaces: set[str] = set()
        occupied_spans: list[tuple[int, int]] = []
        occupied_ids = set(seen_knowledge_ids)
        for surface in self.registry.detectable_aliases():
            normalized_surface = normalize_alias(surface)
            is_shared_jay = normalized_surface == "jay" and {
                item.entity_id for item in self.registry.candidates(surface)
            } == {"artist:jay_chou", "album:jay_2000"}
            span = _surface_span(query, surface)
            if is_shared_jay:
                # A longer full-name alias may occupy the first Jay occurrence.
                span = next((match.span() for match in _JAY_SHORT_NAME.finditer(query)
                             if not any(match.start() < end and start < match.end()
                                        for start, end in occupied_spans)), None)
            if normalized_surface in seen_surfaces or span is None:
                continue
            if any(span[0] < end and start < span[1] for start, end in occupied_spans):
                continue
            seen_surfaces.add(normalized_surface)
            occupied_spans.append(span)
            type_hint = infer_entity_type(query, surface)
            if is_shared_jay:
                # Reuse the offline 32-character window and exact cue vocabulary.
                # A nearby label must not override missing/conflicting evidence.
                context_type = EntityLinker._infer_jay_context_type(query)
                resolution = self.registry.resolve(
                    surface,
                    type_hint=type_hint if context_type is not None else None,
                    context_type=context_type,
                )
            else:
                resolution = self.registry.resolve(surface, type_hint=type_hint)
            if resolution.candidate_entity_ids and set(
                resolution.candidate_entity_ids
            ) <= occupied_ids:
                continue
            if resolution.entity is not None:
                if resolution.entity.entity_id in occupied_ids:
                    continue
                canonical = self.registry.canonical(
                    resolution.entity, resolution.confidence
                )
                result.append(
                    ResolvedQueryEntity(
                        surface=surface,
                        canonical_name=canonical.canonical_name,
                        entity_type=canonical.entity_type,
                        knowledge_entity_id=canonical.entity_id,
                        aliases=canonical.aliases,
                        status=QueryEntityStatus.RESOLVED,
                        confidence=canonical.confidence,
                    )
                )
                occupied_ids.add(canonical.entity_id)
            elif resolution.candidate_entity_ids:
                result.append(
                    ResolvedQueryEntity(
                        surface=surface,
                        status=QueryEntityStatus.AMBIGUOUS,
                        confidence=0,
                        candidate_knowledge_ids=list(
                            resolution.candidate_entity_ids
                        ),
                    )
                )
        return result

    def _from_catalog(self, reference: CatalogReference) -> ResolvedQueryEntity:
        resolution = self.registry.resolve(
            reference.name, type_hint=reference.entity_type
        )
        if resolution.entity is None:
            return ResolvedQueryEntity(
                surface=reference.name,
                canonical_name=reference.name,
                entity_type=reference.entity_type,
                catalog_reference=reference,
                status=QueryEntityStatus.RESOLVED,
                confidence=0.95,
            )
        canonical = self.registry.canonical(resolution.entity, 0.99)
        return ResolvedQueryEntity(
            surface=reference.name,
            canonical_name=canonical.canonical_name,
            entity_type=canonical.entity_type,
            knowledge_entity_id=canonical.entity_id,
            catalog_reference=reference,
            aliases=canonical.aliases,
            status=QueryEntityStatus.RESOLVED,
            confidence=0.99,
        )
