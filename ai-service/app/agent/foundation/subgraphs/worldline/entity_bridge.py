"""Bridge stable knowledge identities to executable catalog identities."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agent.foundation.subgraphs.worldline.genre_registry import (
    GenreVocabularyRegistry,
)
from app.agent.foundation.subgraphs.worldline.models import (
    EntityType,
    ResolvedEntity,
    WorldLineNodeType,
)
from app.rag.knowledge.entity_linking.models import (
    EntityType as KnowledgeEntityType,
    RegistryEntity,
)
from app.rag.knowledge.entity_linking.registry import EntityRegistry, normalize_alias
from app.rag.query_understanding.models import CatalogReference, SearchPlan


class CatalogResolutionStatus(StrEnum):
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    AMBIGUOUS = "ambiguous"


class WorldLineEntityReference(BaseModel):
    """Dual identity used at the semantic-to-executable graph boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: str = Field(min_length=1, max_length=128)
    node_type: WorldLineNodeType
    canonical_name: str = Field(min_length=1, max_length=500)
    knowledge_entity_id: str = Field(
        pattern=r"^[a-z][a-z0-9_]*:[a-z0-9][a-z0-9_]*$"
    )
    catalog_reference: CatalogReference | None = None
    aliases: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_identity(self) -> "WorldLineEntityReference":
        if self.node_type is WorldLineNodeType.ARTIST:
            reference = self.catalog_reference
            if not self.knowledge_entity_id.startswith("artist:"):
                raise ValueError("artist reference requires artist knowledge id")
            if reference is None:
                raise ValueError("artist reference requires catalog identity")
            if reference.entity_type is not KnowledgeEntityType.ARTIST:
                raise ValueError("artist reference requires artist catalog type")
            expected_node_id = (
                f"{reference.provider}:artist:{reference.entity_id}"
            )
            if self.node_id != expected_node_id:
                raise ValueError("artist node id must match catalog identity")
        elif self.node_type is WorldLineNodeType.GENRE:
            if not self.knowledge_entity_id.startswith("genre:"):
                raise ValueError("genre reference requires genre knowledge id")
            if self.catalog_reference is not None:
                raise ValueError("genre reference cannot invent catalog identity")
            if self.node_id != self.knowledge_entity_id:
                raise ValueError("genre node id must equal knowledge identity")
        else:
            raise ValueError("entity bridge supports only artist and genre")
        normalized_aliases = [normalize_alias(item) for item in self.aliases]
        if any(not item for item in normalized_aliases):
            raise ValueError("entity reference aliases cannot be blank")
        if len(normalized_aliases) != len(set(normalized_aliases)):
            raise ValueError("entity reference aliases must be unique")
        return self


class CatalogResolutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: CatalogResolutionStatus
    reference: WorldLineEntityReference | None = None
    candidate_knowledge_ids: list[str] = Field(default_factory=list, max_length=20)
    reason: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_outcome(self) -> "CatalogResolutionResult":
        if self.status is CatalogResolutionStatus.RESOLVED:
            if self.reference is None:
                raise ValueError("resolved bridge result requires a reference")
        elif self.reference is not None:
            raise ValueError("unresolved bridge result cannot contain a reference")
        if (
            self.status is CatalogResolutionStatus.AMBIGUOUS
            and not self.candidate_knowledge_ids
        ):
            raise ValueError("ambiguous bridge result requires candidates")
        return self


class EntityBridge:
    """Resolve controlled knowledge identities into WorldLine node identities."""

    def __init__(
        self,
        knowledge_registry: EntityRegistry,
        genre_registry: GenreVocabularyRegistry,
    ) -> None:
        self.knowledge_registry = knowledge_registry
        self.genre_registry = genre_registry

    def reference_for_catalog_artist(
        self,
        artist: ResolvedEntity,
        search_plan: SearchPlan,
    ) -> CatalogResolutionResult:
        if artist.entity_type is not EntityType.ARTIST:
            return self._unresolved("unsupported_catalog_entity_type")
        parts = artist.entity_id.split(":")
        if (
            len(parts) != 3
            or parts[0] != artist.source
            or parts[1] != EntityType.ARTIST.value
            or not parts[2]
        ):
            return self._unresolved("invalid_catalog_artist_id")
        matches = [
            entity
            for entity in search_plan.entities
            if entity.knowledge_entity_id is not None
            and entity.entity_type is KnowledgeEntityType.ARTIST
            and entity.catalog_reference is not None
            and entity.catalog_reference.provider == artist.source
            and entity.catalog_reference.entity_id == parts[2]
            and entity.catalog_reference.entity_type
            is KnowledgeEntityType.ARTIST
        ]
        unique_ids = list(
            dict.fromkeys(
                entity.knowledge_entity_id
                for entity in matches
                if entity.knowledge_entity_id is not None
            )
        )
        if not unique_ids:
            return self._unresolved("catalog_artist_not_mapped")
        if len(unique_ids) > 1:
            return self._ambiguous(unique_ids, "catalog_artist_mapping_ambiguous")
        registry_entity = self.knowledge_registry.get(unique_ids[0])
        if (
            registry_entity is None
            or registry_entity.entity_type is not KnowledgeEntityType.ARTIST
        ):
            return self._unresolved(
                "knowledge_artist_not_registered",
                unique_ids,
            )
        catalog_reference = matches[0].catalog_reference
        if catalog_reference is None:
            return self._unresolved("catalog_artist_not_mapped", unique_ids)
        return self._artist_reference(registry_entity, catalog_reference)

    def resolve_artist(
        self,
        value: str,
        search_plan: SearchPlan,
    ) -> CatalogResolutionResult:
        registry_entity: RegistryEntity | None
        candidate_ids: list[str]
        if value.startswith("artist:"):
            registry_entity = self.knowledge_registry.get(value)
            candidate_ids = [value] if registry_entity is not None else []
        else:
            resolution = self.knowledge_registry.resolve(
                value,
                type_hint=KnowledgeEntityType.ARTIST,
            )
            registry_entity = resolution.entity
            candidate_ids = list(resolution.candidate_entity_ids)
            if registry_entity is None and len(candidate_ids) > 1:
                return self._ambiguous(
                    candidate_ids,
                    "knowledge_artist_alias_ambiguous",
                )
        if (
            registry_entity is None
            or registry_entity.entity_type is not KnowledgeEntityType.ARTIST
        ):
            return self._unresolved(
                "knowledge_artist_not_registered",
                candidate_ids,
            )
        matches = [
            entity.catalog_reference
            for entity in search_plan.entities
            if entity.knowledge_entity_id == registry_entity.entity_id
            and entity.entity_type is KnowledgeEntityType.ARTIST
            and entity.catalog_reference is not None
            and entity.catalog_reference.entity_type
            is KnowledgeEntityType.ARTIST
        ]
        unique_catalogs = {
            (item.provider, item.entity_id): item for item in matches
        }
        if not unique_catalogs:
            return self._unresolved(
                "knowledge_artist_has_no_catalog_mapping",
                [registry_entity.entity_id],
            )
        if len(unique_catalogs) > 1:
            return self._ambiguous(
                [registry_entity.entity_id, *registry_entity.aliases],
                "knowledge_artist_catalog_mapping_ambiguous",
            )
        return self._artist_reference(
            registry_entity,
            next(iter(unique_catalogs.values())),
        )

    def resolve_genre(self, value: str) -> CatalogResolutionResult:
        genre = (
            self.genre_registry.get(value)
            if value.startswith("genre:")
            else self.genre_registry.resolve(value)
        )
        if genre is None:
            return self._unresolved("genre_not_registered")
        reference = WorldLineEntityReference(
            node_id=genre.canonical_id,
            node_type=WorldLineNodeType.GENRE,
            canonical_name=genre.canonical_name,
            knowledge_entity_id=genre.canonical_id,
            catalog_reference=None,
            aliases=genre.aliases,
        )
        return CatalogResolutionResult(
            status=CatalogResolutionStatus.RESOLVED,
            reference=reference,
            candidate_knowledge_ids=[genre.canonical_id],
            reason="genre_registry_match",
        )

    @staticmethod
    def _artist_reference(
        registry_entity: RegistryEntity,
        catalog_reference: CatalogReference,
    ) -> CatalogResolutionResult:
        aliases_by_key: dict[str, str] = {}
        for alias in registry_entity.aliases:
            aliases_by_key.setdefault(normalize_alias(alias), alias)
        reference = WorldLineEntityReference(
            node_id=(
                f"{catalog_reference.provider}:artist:"
                f"{catalog_reference.entity_id}"
            ),
            node_type=WorldLineNodeType.ARTIST,
            canonical_name=registry_entity.canonical_name,
            knowledge_entity_id=registry_entity.entity_id,
            catalog_reference=catalog_reference,
            aliases=list(aliases_by_key.values()),
        )
        return CatalogResolutionResult(
            status=CatalogResolutionStatus.RESOLVED,
            reference=reference,
            candidate_knowledge_ids=[registry_entity.entity_id],
            reason="knowledge_catalog_identity_resolved",
        )

    @staticmethod
    def _unresolved(
        reason: str,
        candidate_ids: list[str] | None = None,
    ) -> CatalogResolutionResult:
        return CatalogResolutionResult(
            status=CatalogResolutionStatus.UNRESOLVED,
            candidate_knowledge_ids=candidate_ids or [],
            reason=reason,
        )

    @staticmethod
    def _ambiguous(
        candidate_ids: list[str],
        reason: str,
    ) -> CatalogResolutionResult:
        unique = list(dict.fromkeys(candidate_ids))
        return CatalogResolutionResult(
            status=CatalogResolutionStatus.AMBIGUOUS,
            candidate_knowledge_ids=unique,
            reason=reason,
        )
