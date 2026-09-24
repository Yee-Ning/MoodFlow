"""Versioned, controlled genre vocabulary for WorldLine semantic nodes."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator
import yaml

from app.rag.knowledge.entity_linking.registry import normalize_alias


DEFAULT_GENRE_REGISTRY_PATH = (
    Path(__file__).with_name("registry") / "genres_v1.yaml"
)


class GenreVocabularyEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    canonical_id: str = Field(pattern=r"^genre:[a-z0-9][a-z0-9_]*$")
    canonical_name: str = Field(min_length=1, max_length=200)
    aliases: list[str] = Field(default_factory=list, max_length=100)
    executable: bool = False
    expandable: bool = False

    @model_validator(mode="after")
    def validate_aliases(self) -> "GenreVocabularyEntry":
        normalized = [normalize_alias(item) for item in self.aliases]
        if any(not item for item in normalized):
            raise ValueError("genre aliases cannot be blank")
        if len(normalized) != len(set(normalized)):
            raise ValueError("genre aliases must be unique")
        if normalize_alias(self.canonical_name) in normalized:
            raise ValueError("genre aliases must not repeat canonical name")
        if self.expandable and not self.executable:
            raise ValueError("expandable genre must be executable")
        return self


class GenreVocabularyDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    registry_version: str = Field(pattern=r"^v\d+$")
    genres: list[GenreVocabularyEntry] = Field(min_length=1)


class GenreVocabularyRegistry:
    def __init__(self, document: GenreVocabularyDocument) -> None:
        self.version = document.registry_version
        self._genres = {item.canonical_id: item for item in document.genres}
        if len(self._genres) != len(document.genres):
            raise ValueError("genre_registry_duplicate_id")
        aliases: dict[str, GenreVocabularyEntry] = {}
        for genre in document.genres:
            for surface in [genre.canonical_name, *genre.aliases]:
                key = normalize_alias(surface)
                previous = aliases.get(key)
                if previous is not None and previous != genre:
                    raise ValueError("genre_registry_alias_conflict")
                aliases[key] = genre
        self._aliases = aliases

    @classmethod
    def load(cls, path: Path | None = None) -> "GenreVocabularyRegistry":
        selected = (path or DEFAULT_GENRE_REGISTRY_PATH).resolve()
        try:
            payload = yaml.safe_load(selected.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("genre registry root must be an object")
            return cls(GenreVocabularyDocument.model_validate(payload))
        except (OSError, ValueError, yaml.YAMLError) as exc:
            raise ValueError(f"genre_registry_invalid:{selected}") from exc

    def get(self, canonical_id: str) -> GenreVocabularyEntry | None:
        return self._genres.get(canonical_id)

    def resolve(self, value: str) -> GenreVocabularyEntry | None:
        return self._aliases.get(normalize_alias(value))

    def entries(self) -> tuple[GenreVocabularyEntry, ...]:
        return tuple(self._genres.values())
