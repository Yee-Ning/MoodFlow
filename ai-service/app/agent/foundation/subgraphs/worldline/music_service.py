from __future__ import annotations

from typing import Protocol

from app.agent.foundation.subgraphs.worldline.models import (
    EntityType,
    EvidenceSource,
    EvidenceType,
    MusicEntityFacts,
    MusicFact,
    ResolvedEntity,
    WorldLineRelation,
)
from app.schemas.music import MusicEntity, MusicEntityReference
from app.tools.music import MusicServiceClient


def catalog_entity_id(entity_type: EntityType | str, entity_id: int) -> str:
    value = entity_type.value if isinstance(entity_type, EntityType) else entity_type
    return f"netease:{value}:{entity_id}"


def _resolved_entity(entity: MusicEntity | MusicEntityReference) -> ResolvedEntity:
    entity_type = EntityType(entity.entity_type)
    metadata = entity.metadata if isinstance(entity, MusicEntity) else {}
    if isinstance(entity, MusicEntity):
        metadata = {
            **metadata,
            "cover_url": entity.cover_url,
        }
    return ResolvedEntity(
        entity_id=catalog_entity_id(entity_type, entity.entity_id),
        entity_type=entity_type,
        name=entity.name,
        artist=entity.artist or None,
        album=entity.album or None,
        source="netease",
        metadata=metadata,
    )


class WorldLineMusicService(Protocol):
    async def search_entities(
        self,
        query: str,
        expected_entity_type: EntityType | None,
        *,
        limit: int = 5,
    ) -> list[ResolvedEntity]: ...

    async def load_entity_facts(
        self,
        entity: ResolvedEntity,
    ) -> MusicEntityFacts: ...


class JavaMusicEntityService:
    """WorldLine adapter over the existing trusted Spring Boot client."""

    def __init__(self, client: MusicServiceClient | None = None) -> None:
        self._client = client or MusicServiceClient()

    async def search_entities(
        self,
        query: str,
        expected_entity_type: EntityType | None,
        *,
        limit: int = 5,
    ) -> list[ResolvedEntity]:
        entities = await self._client.search_entities(
            query,
            entity_type=(
                expected_entity_type.value
                if expected_entity_type is not None
                else None
            ),
            limit=limit,
        )
        return [_resolved_entity(entity) for entity in entities]

    async def load_entity_facts(
        self,
        entity: ResolvedEntity,
    ) -> MusicEntityFacts:
        raw_id = _raw_entity_id(entity)
        detail = await self._client.get_entity_detail(
            entity.entity_type.value,
            raw_id,
        )
        canonical = _resolved_entity(detail)
        if canonical.entity_id != entity.entity_id:
            raise ValueError("music service returned a mismatched entity")

        related = [_resolved_entity(item) for item in detail.related_entities]
        source_id = f"music-api:{canonical.entity_type.value}:{raw_id}"
        facts = _relation_facts(canonical, related, source_id)
        facts.extend(_metadata_facts(canonical, source_id))
        return MusicEntityFacts(
            entity=canonical,
            related_entities=related,
            facts=facts,
            sources=[
                EvidenceSource(
                    id=source_id,
                    source_type=EvidenceType.MUSIC_API,
                    title=(
                        f"网易云音乐 {canonical.entity_type.value} "
                        f"entity {raw_id}"
                    ),
                    source="MoodFlow Java Music Service",
                    metadata={
                        "provider": "netease",
                        "entity_type": canonical.entity_type.value,
                        "entity_id": raw_id,
                    },
                )
            ],
        )


def _raw_entity_id(entity: ResolvedEntity) -> int:
    try:
        provider, entity_type, raw_id = entity.entity_id.split(":", 2)
        if provider != "netease" or entity_type != entity.entity_type.value:
            raise ValueError
        parsed = int(raw_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("unsupported canonical entity id") from exc
    if parsed <= 0:
        raise ValueError("music entity id must be positive")
    return parsed


def _relation_facts(
    root: ResolvedEntity,
    related: list[ResolvedEntity],
    source_id: str,
) -> list[MusicFact]:
    facts: list[MusicFact] = []
    for index, item in enumerate(related):
        relation: WorldLineRelation
        subject = root
        target = item
        if root.entity_type is EntityType.SONG and item.entity_type is EntityType.ARTIST:
            relation = WorldLineRelation.PERFORMED_BY
        elif root.entity_type is EntityType.SONG and item.entity_type is EntityType.ALBUM:
            relation = WorldLineRelation.BELONGS_TO_ALBUM
        elif root.entity_type is EntityType.ARTIST and item.entity_type is EntityType.SONG:
            relation = WorldLineRelation.PERFORMED_BY
            subject, target = item, root
        elif root.entity_type is EntityType.ALBUM and item.entity_type is EntityType.SONG:
            relation = WorldLineRelation.BELONGS_TO_ALBUM
            subject, target = item, root
        else:
            relation = WorldLineRelation.RELATED_TO
        facts.append(
            MusicFact(
                id=f"fact:relation:{index}",
                subject_id=subject.entity_id,
                predicate=relation.value,
                value=f"{subject.name} {relation.value} {target.name}",
                object_entity_id=target.entity_id,
                evidence_ids=[source_id],
            )
        )
    return facts


def _metadata_facts(
    entity: ResolvedEntity,
    source_id: str,
) -> list[MusicFact]:
    visible_keys = {
        "duration": "duration_ms",
        "publish_time": "publish_time",
        "company": "record_company",
        "description": "description",
        "album_size": "album_count",
        "music_size": "music_count",
    }
    facts: list[MusicFact] = []
    for key, predicate in visible_keys.items():
        value = entity.metadata.get(key)
        if value is None or value == "" or value == []:
            continue
        facts.append(
            MusicFact(
                id=f"fact:metadata:{key}",
                subject_id=entity.entity_id,
                predicate=predicate,
                value=str(value),
                evidence_ids=[source_id],
            )
        )
    return facts
