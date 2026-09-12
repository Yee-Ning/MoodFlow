"""Document -> parent -> child metadata inheritance for enrichment."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import (
    BaseChunk,
    ContentType,
    EnrichedParentChunk,
    EntityMention,
    EntityTypeHint,
    MentionSource,
    PrimarySubject,
    SourceMetadata,
)
from .normalizer import deduplicate_strings, normalize_optional_text, normalize_text


@dataclass(frozen=True, slots=True)
class EnrichmentContext:
    title: str | None
    language: str | None
    content_type: ContentType
    topics: tuple[str, ...]
    keywords: tuple[str, ...]
    source: SourceMetadata
    primary_subject: PrimarySubject | None
    entity_mentions: tuple[EntityMention, ...]
    inherited_metadata: dict[str, object]
    parent_primary_subject: PrimarySubject | None
    evidence_texts: tuple[str, ...]


class MetadataInheritanceResolver:
    def resolve(
        self,
        chunk: BaseChunk,
        *,
        parent: EnrichedParentChunk | None = None,
    ) -> EnrichmentContext:
        metadata = dict(chunk.metadata)
        document_metadata = self._mapping(metadata.get("document_metadata"))
        source_metadata = self._mapping(document_metadata.get("source_metadata"))

        title = self._first_text(
            metadata.get("title"),
            document_metadata.get("title"),
            source_metadata.get("title"),
        )
        language = self._first_text(
            metadata.get("language"),
            document_metadata.get("language"),
            source_metadata.get("language"),
        )
        source = SourceMetadata(
            source_id=self._first_text(
                metadata.get("source_id"), source_metadata.get("source_id")
            ),
            source_type=self._first_text(
                metadata.get("source_type"), source_metadata.get("source_type")
            ),
            source_name=self._first_text(
                metadata.get("source_name"),
                metadata.get("source"),
                source_metadata.get("publisher"),
                source_metadata.get("source_name"),
                source_metadata.get("source"),
            ),
            source_url=self._first_text(
                metadata.get("source_url"),
                source_metadata.get("url"),
                source_metadata.get("original_url"),
            ),
            source_published_at=self._first_text(
                metadata.get("source_published_at"),
                metadata.get("published_at"),
                source_metadata.get("published_at"),
            ),
        )
        content_type = self._content_type(metadata.get("content_type"))
        topics = tuple(
            deduplicate_strings(
                [
                    *self._string_list(metadata.get("topics")),
                    *self._string_list(document_metadata.get("topics")),
                    *self._string_list(source_metadata.get("topics")),
                ],
                limit=8,
            )
        )
        keywords = tuple(
            deduplicate_strings(
                [
                    *self._string_list(metadata.get("keywords")),
                    *self._string_list(document_metadata.get("keywords")),
                    *self._string_list(source_metadata.get("keywords")),
                ],
                limit=12,
            )
        )

        mentions: list[EntityMention] = []
        document_subject = self._document_subject(
            metadata, document_metadata, source_metadata
        )
        if document_subject is not None:
            mentions.append(
                EntityMention(
                    text=document_subject.text,
                    normalized_text=normalize_text(document_subject.text),
                    type_hint=document_subject.type_hint,
                    confidence=document_subject.confidence,
                    source=MentionSource.METADATA,
                )
            )
        mentions.extend(self._metadata_mentions(metadata.get("entity_mentions")))

        parent_subject = (
            parent.enrichment.entities.primary_subject if parent is not None else None
        )
        primary_subject = document_subject or parent_subject
        if primary_subject is None:
            primary_subject = self._title_subject(
                title,
                self._first_text(
                    metadata.get("document_type"),
                    document_metadata.get("document_type"),
                ),
            )

        evidence_values = [
            chunk.content,
            title or "",
            *chunk.section_path,
            *(
                value
                for value in source.model_dump(mode="python").values()
                if isinstance(value, str)
            ),
            *topics,
            *keywords,
            *(item.text for item in mentions),
        ]
        if primary_subject is not None:
            evidence_values.append(primary_subject.text)
        return EnrichmentContext(
            title=title,
            language=language,
            content_type=content_type,
            topics=topics,
            keywords=keywords,
            source=source,
            primary_subject=primary_subject,
            entity_mentions=tuple(mentions),
            inherited_metadata=metadata,
            parent_primary_subject=parent_subject,
            evidence_texts=tuple(value for value in evidence_values if value),
        )

    @staticmethod
    def _mapping(value: object) -> dict[str, Any]:
        return dict(value) if isinstance(value, dict) else {}

    @staticmethod
    def _first_text(*values: object) -> str | None:
        for value in values:
            normalized = normalize_optional_text(value)
            if normalized is not None:
                return normalized
        return None

    @staticmethod
    def _string_list(value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, str)]

    @staticmethod
    def _content_type(value: object) -> ContentType:
        try:
            return ContentType(value)
        except (TypeError, ValueError):
            return ContentType.UNKNOWN

    def _document_subject(
        self,
        metadata: dict[str, Any],
        document_metadata: dict[str, Any],
        source_metadata: dict[str, Any],
    ) -> PrimarySubject | None:
        explicit = metadata.get(
            "primary_subject", document_metadata.get("primary_subject")
        )
        parsed = self._primary_from_value(explicit)
        if parsed is not None:
            return parsed
        for field, type_hint in (
            ("artist", EntityTypeHint.ARTIST),
            ("album", EntityTypeHint.ALBUM),
            ("song", EntityTypeHint.SONG),
        ):
            text = self._first_text(
                metadata.get(field),
                document_metadata.get(field),
                source_metadata.get(field),
            )
            if text is not None:
                return PrimarySubject(text=text, type_hint=type_hint, confidence=0.98)
        return None

    @staticmethod
    def _primary_from_value(value: object) -> PrimarySubject | None:
        if isinstance(value, str) and (text := normalize_optional_text(value)):
            return PrimarySubject(
                text=text, type_hint=EntityTypeHint.UNKNOWN, confidence=1.0
            )
        if not isinstance(value, dict):
            return None
        try:
            return PrimarySubject.model_validate(value)
        except ValueError:
            return None

    @staticmethod
    def _title_subject(
        title: str | None, document_type: str | None
    ) -> PrimarySubject | None:
        if not title or not document_type or len(title) > 80:
            return None
        mappings = {
            "artist": EntityTypeHint.ARTIST,
            "album": EntityTypeHint.ALBUM,
            "song": EntityTypeHint.SONG,
        }
        type_hint = mappings.get(document_type.casefold())
        if type_hint is None:
            return None
        suffixes = (
            "艺人介绍与作品年表",
            "藝人介紹與作品年表",
            "艺人介绍",
            "藝人介紹",
            "专辑介绍",
            "專輯介紹",
            "歌曲介绍",
            "歌曲介紹",
        )
        subject = title
        for suffix in suffixes:
            if subject.endswith(suffix):
                subject = subject[: -len(suffix)].strip(" -—:：")
                break
        if not subject:
            return None
        return PrimarySubject(text=subject, type_hint=type_hint, confidence=0.75)

    @staticmethod
    def _metadata_mentions(value: object) -> list[EntityMention]:
        if not isinstance(value, list):
            return []
        result: list[EntityMention] = []
        for item in value:
            try:
                if isinstance(item, str):
                    result.append(
                        EntityMention(
                            text=item,
                            normalized_text=normalize_text(item),
                            type_hint=EntityTypeHint.UNKNOWN,
                            confidence=1.0,
                            source=MentionSource.METADATA,
                        )
                    )
                elif isinstance(item, dict):
                    payload = dict(item)
                    payload.setdefault(
                        "normalized_text", normalize_text(str(payload.get("text", "")))
                    )
                    payload["source"] = MentionSource.METADATA
                    result.append(EntityMention.model_validate(payload))
            except ValueError:
                continue
        return result
