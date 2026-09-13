"""Immutable V1 entity registry and deterministic mention resolution."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from app.rag.enrichment.normalizer import normalize_text

from .models import CanonicalEntity, EntityRegistryDocument, EntityType, RegistryEntity


DEFAULT_ENTITY_REGISTRY_PATH = Path(__file__).with_name("registry") / "v3.yaml"


def normalize_alias(value: str) -> str:
    return normalize_text(value).casefold()


@dataclass(frozen=True, slots=True)
class RegistryResolution:
    entity: RegistryEntity | None
    candidate_entity_ids: tuple[str, ...]
    confidence: float
    reason: str


class EntityRegistry:
    def __init__(self, document: EntityRegistryDocument) -> None:
        self.version = document.registry_version
        self._entities = {item.entity_id: item for item in document.entities}
        if len(self._entities) != len(document.entities):
            raise ValueError("entity_registry_duplicate_id")

        aliases: dict[str, list[RegistryEntity]] = {}
        ambiguous: set[tuple[str, str]] = set()
        for entity in document.entities:
            for surface in [entity.canonical_name, *entity.aliases]:
                key = normalize_alias(surface)
                if not key:
                    raise ValueError("entity_registry_empty_alias")
                bucket = aliases.setdefault(key, [])
                if entity not in bucket:
                    bucket.append(entity)
            for surface in entity.ambiguous_aliases:
                key = normalize_alias(surface)
                if key not in aliases:
                    raise ValueError("entity_registry_unknown_ambiguous_alias")
                ambiguous.add((entity.entity_id, key))
        self._aliases = aliases
        self._ambiguous = ambiguous

    @classmethod
    def load(cls, path: Path | None = None) -> "EntityRegistry":
        selected = (path or DEFAULT_ENTITY_REGISTRY_PATH).resolve()
        try:
            payload = yaml.safe_load(selected.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("entity registry root must be an object")
            extends = payload.pop("extends", None)
            if extends is not None:
                if not isinstance(extends, str) or not extends:
                    raise ValueError("entity registry extends must be a file name")
                base_path = (selected.parent / extends).resolve()
                if base_path.parent != selected.parent or base_path == selected:
                    raise ValueError("entity registry extends path is unsafe")
                base = yaml.safe_load(base_path.read_text(encoding="utf-8"))
                if not isinstance(base, dict) or not isinstance(
                    base.get("entities"), list
                ):
                    raise ValueError("entity registry base is invalid")
                own_entities = payload.get("entities", [])
                if not isinstance(own_entities, list):
                    raise ValueError("entity registry entities must be a list")
                payload["entities"] = [*base["entities"], *own_entities]
            return cls(EntityRegistryDocument.model_validate(payload))
        except (OSError, ValueError, yaml.YAMLError) as exc:
            raise ValueError(f"entity_registry_invalid:{selected}") from exc

    def get(self, entity_id: str) -> RegistryEntity | None:
        return self._entities.get(entity_id)

    def candidates(self, mention: str) -> tuple[RegistryEntity, ...]:
        return tuple(self._aliases.get(normalize_alias(mention), ()))

    def resolve(
        self,
        mention: str,
        *,
        type_hint: EntityType | None = None,
        context_type: EntityType | None = None,
    ) -> RegistryResolution:
        key = normalize_alias(mention)
        candidates = list(self._aliases.get(key, ()))
        candidate_ids = tuple(sorted(item.entity_id for item in candidates))
        if not candidates:
            return RegistryResolution(None, (), 0, "registry_no_candidate")

        if type_hint is not None:
            candidates = [item for item in candidates if item.entity_type == type_hint]
            if not candidates:
                return RegistryResolution(
                    None, candidate_ids, 0, "registry_candidate_type_mismatch"
                )
            if len(candidates) == 1:
                return RegistryResolution(
                    candidates[0], candidate_ids, 0.99, "registry_typed_match"
                )

        if (
            len(candidates) == 1
            and (candidates[0].entity_id, key) not in self._ambiguous
        ):
            return RegistryResolution(
                candidates[0], candidate_ids, 0.98, "registry_exact_match"
            )

        if context_type is not None:
            candidates = [
                item for item in candidates if item.entity_type == context_type
            ]
            if not candidates:
                return RegistryResolution(
                    None, candidate_ids, 0, "registry_candidate_type_mismatch"
                )
            if len(candidates) == 1:
                return RegistryResolution(
                    candidates[0], candidate_ids, 0.92, "registry_context_match"
                )

        if len(candidates) > 1:
            return RegistryResolution(None, candidate_ids, 0, "registry_ambiguous")

        candidate = candidates[0]
        if (candidate.entity_id, key) in self._ambiguous:
            return RegistryResolution(
                None, candidate_ids, 0, "registry_alias_requires_context"
            )
        return RegistryResolution(
            candidate, candidate_ids, 0.98, "registry_exact_match"
        )

    @staticmethod
    def canonical(entity: RegistryEntity, confidence: float) -> CanonicalEntity:
        ambiguous = {normalize_alias(value) for value in entity.ambiguous_aliases}
        aliases = list(
            dict.fromkeys(
                [
                    entity.canonical_name,
                    *(
                        value
                        for value in entity.aliases
                        if normalize_alias(value) not in ambiguous
                    ),
                ]
            )
        )
        return CanonicalEntity(
            entity_id=entity.entity_id,
            entity_type=entity.entity_type,
            canonical_name=entity.canonical_name,
            aliases=aliases,
            confidence=confidence,
        )

    def detectable_aliases(self) -> tuple[str, ...]:
        values = {
            surface
            for entity in self._entities.values()
            for surface in [entity.canonical_name, *entity.aliases]
            if len(normalize_alias(surface)) >= 2
        }
        return tuple(sorted(values, key=lambda value: (-len(value), value.casefold())))
