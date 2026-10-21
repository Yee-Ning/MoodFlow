"""Metadata-first, registry-backed entity linker with safe ambiguity handling."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

from pydantic import ValidationError

from app.rag.enrichment.models import (
    EnrichedChunk,
    EntityTypeHint,
    MentionSource,
)

from ..config import KnowledgeConfig
from ..structured import strict_model_schema
from .models import (
    CanonicalEntity,
    EntityDisambiguationOutput,
    EntityLinkingResult,
    EntityLinkingStatus,
    EntityType,
    UnresolvedMention,
)
from .prompts import ENTITY_DISAMBIGUATION_PROMPT_V1
from .registry import EntityRegistry, RegistryResolution, normalize_alias
from .runtime import ExistingLLMEntityLinkingRuntime, EntityLinkingRuntime


_CONTEXT_CUES: dict[EntityType, tuple[str, ...]] = {
    EntityType.ARTIST: ("歌手", "艺人", "藝人", "音乐人", "音樂人", "artist"),
    EntityType.ALBUM: ("专辑", "專輯", "唱片", "album"),
    EntityType.SONG: ("歌曲", "单曲", "單曲", "song", "track"),
    EntityType.GENRE: ("曲风", "曲風", "风格", "風格", "genre"),
    EntityType.AWARD: ("奖项", "獎項", "获奖", "獲獎", "award"),
    EntityType.LABEL: ("唱片公司", "厂牌", "廠牌", "label"),
}

# Only the shared Jay alias uses these cues; other entities retain their rules.
_JAY_CONTEXT_WINDOW = 32
_JAY_ALBUM_CUES = (
    "专辑", "專輯", "唱片", "发行", "發行", "曲目", "单曲", "單曲",
    "album", "albums", "record", "records", "release", "released", "releases",
    "track", "tracks", "single", "singles",
)
_JAY_ARTIST_CUES = (
    "歌手", "艺人", "藝人", "音乐人", "音樂人", "出生", "演唱",
    "artist", "artists", "singer", "singers", "born", "sang", "singing", "he", "his",
)
_JAY_SHORT_NAME = re.compile(r"(?<![A-Za-z0-9])jay(?![A-Za-z0-9])", re.I)
_JAY_FULL_NAME = re.compile(r"(?<![A-Za-z0-9])jay\s+chou(?![A-Za-z0-9])", re.I)

_METADATA_ENTITY_FIELDS: dict[str, EntityType] = {
    "artist": EntityType.ARTIST,
    "artists": EntityType.ARTIST,
    "song": EntityType.SONG,
    "track": EntityType.SONG,
    "album": EntityType.ALBUM,
    "genre": EntityType.GENRE,
    "genres": EntityType.GENRE,
    "label": EntityType.LABEL,
    "record_label": EntityType.LABEL,
}

_LINKABLE_TYPES = {
    EntityType.ARTIST,
    EntityType.SONG,
    EntityType.ALBUM,
    EntityType.GENRE,
    EntityType.AWARD,
    EntityType.CHART,
    EntityType.LABEL,
    EntityType.PERSON,
}


@dataclass(frozen=True, slots=True)
class _Mention:
    text: str
    type_hint: EntityType | None
    confidence: float
    priority: int
    source: str


@dataclass(frozen=True, slots=True)
class _Pending:
    mention: _Mention
    resolution: RegistryResolution


class EntityLinker:
    def __init__(
        self,
        config: KnowledgeConfig,
        *,
        registry: EntityRegistry | None = None,
        runtime: EntityLinkingRuntime | None = None,
    ) -> None:
        self.config = config
        self.registry = registry or EntityRegistry.load()
        if self.registry.version != config.registry_version:
            raise ValueError("entity_registry_version_mismatch")
        self.runtime = runtime or ExistingLLMEntityLinkingRuntime()

    async def link(self, chunk: EnrichedChunk) -> EntityLinkingResult:
        mentions = self._collect_mentions(chunk)
        linked: dict[str, CanonicalEntity] = {}
        pending: list[_Pending] = []
        for mention in mentions:
            is_shared_jay = normalize_alias(mention.text) == "jay" and {
                item.entity_id for item in self.registry.candidates(mention.text)
            } == {"artist:jay_chou", "album:jay_2000"}
            context_type = (
                self._infer_jay_context_type(chunk.content)
                if is_shared_jay
                else self._infer_context_type(chunk.content, mention.text)
            )
            if context_type is None and not is_shared_jay:
                title = str(chunk.metadata.get("title") or "")
                section_context = " > ".join([title, *chunk.section_path, mention.text])
                context_type = self._infer_context_type(section_context, mention.text)
            resolution = self.registry.resolve(
                mention.text,
                type_hint=mention.type_hint,
                context_type=context_type,
            )
            if resolution.entity is not None:
                confidence = min(mention.confidence, resolution.confidence)
                current = linked.get(resolution.entity.entity_id)
                if current is None or confidence > current.confidence:
                    linked[resolution.entity.entity_id] = self.registry.canonical(
                        resolution.entity, confidence
                    )
            else:
                pending.append(_Pending(mention, resolution))

        warnings: list[str] = []
        llm_error: str | None = None
        if self.config.llm_enabled and any(
            item.resolution.candidate_entity_ids for item in pending
        ):
            try:
                choices = await self._disambiguate(chunk, pending)
                pending = self._apply_choices(pending, choices, linked)
            except Exception as exc:
                llm_error = self._error_code(exc)
                warnings.append(llm_error)

        unresolved = [
            UnresolvedMention(
                text=item.mention.text,
                normalized_text=normalize_alias(item.mention.text),
                type_hint=(
                    item.mention.type_hint.value
                    if item.mention.type_hint is not None
                    else EntityTypeHint.UNKNOWN.value
                ),
                confidence=item.mention.confidence,
                candidate_entity_ids=list(item.resolution.candidate_entity_ids),
                reason=item.resolution.reason,
            )
            for item in pending
        ]
        return EntityLinkingResult(
            linked_entities=sorted(linked.values(), key=lambda item: item.entity_id),
            unresolved_mentions=sorted(
                unresolved, key=lambda item: (item.normalized_text, item.type_hint)
            ),
            status=(
                EntityLinkingStatus.PARTIAL
                if llm_error is not None
                else EntityLinkingStatus.SUCCESS
            ),
            warnings=warnings,
        )

    def _collect_mentions(self, chunk: EnrichedChunk) -> list[_Mention]:
        values: list[_Mention] = []
        primary = chunk.enrichment.entities.primary_subject
        if primary is not None:
            values.append(
                _Mention(
                    primary.text,
                    self._type_hint(primary.type_hint),
                    primary.confidence,
                    0,
                    "primary_subject",
                )
            )
        for mention in chunk.enrichment.entities.mentions:
            priority = {
                MentionSource.METADATA: 0,
                MentionSource.RULE: 1,
                MentionSource.LLM: 2,
            }[mention.source]
            values.append(
                _Mention(
                    mention.text,
                    self._type_hint(mention.type_hint),
                    mention.confidence,
                    priority,
                    mention.source.value,
                )
            )
        values.extend(self._metadata_mentions(chunk.metadata))
        values.extend(self._detected_mentions(chunk))

        source_names = self._source_provenance_names(chunk)
        deduplicated: dict[str, _Mention] = {}
        for mention in sorted(
            values,
            key=lambda item: (
                item.priority,
                item.type_hint is None,
                -item.confidence,
            ),
        ):
            key = normalize_alias(mention.text)
            if not self._is_linkable_mention(mention, key, source_names):
                continue
            deduplicated.setdefault(key, mention)
        return list(deduplicated.values())

    def _is_linkable_mention(
        self,
        mention: _Mention,
        normalized: str,
        source_names: set[str],
    ) -> bool:
        if not normalized or normalized in source_names:
            return False
        if mention.type_hint is not None and mention.type_hint not in _LINKABLE_TYPES:
            return False
        if self.registry.candidates(mention.text):
            return True
        return len(normalized) >= 2

    @staticmethod
    def _source_provenance_names(chunk: EnrichedChunk) -> set[str]:
        values: list[str] = []
        if chunk.enrichment.source.source_name:
            values.append(chunk.enrichment.source.source_name)

        def visit(value: object) -> None:
            if not isinstance(value, dict):
                return
            for key, child in value.items():
                normalized_key = str(key).casefold()
                if normalized_key in {"publisher", "source", "source_name"}:
                    if isinstance(child, str) and child.strip():
                        values.append(child)
                elif normalized_key in {"document_metadata", "source_metadata"}:
                    visit(child)

        visit(chunk.metadata)
        return {normalize_alias(value) for value in values if normalize_alias(value)}

    def _metadata_mentions(self, metadata: dict[str, object]) -> list[_Mention]:
        results: list[_Mention] = []

        def visit(value: object, parent_key: str | None = None) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    normalized_key = str(key).casefold()
                    entity_type = _METADATA_ENTITY_FIELDS.get(normalized_key)
                    if entity_type is not None:
                        add_value(child, entity_type)
                    elif normalized_key in {"document_metadata", "source_metadata"}:
                        visit(child, normalized_key)
                return
            if parent_key in {"document_metadata", "source_metadata"}:
                return

        def add_value(value: object, entity_type: EntityType) -> None:
            if isinstance(value, str) and value.strip():
                results.append(_Mention(value, entity_type, 1.0, 0, "metadata"))
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, str) and item.strip():
                        results.append(_Mention(item, entity_type, 1.0, 0, "metadata"))

        visit(metadata)
        return results

    def _detected_mentions(self, chunk: EnrichedChunk) -> list[_Mention]:
        text = "\n".join(
            [
                str(chunk.metadata.get("title") or ""),
                " > ".join(chunk.section_path),
                chunk.content,
            ]
        )
        occupied: list[tuple[int, int]] = []
        found: list[_Mention] = []
        for alias in self.registry.detectable_aliases():
            flags = re.IGNORECASE if re.search(r"[A-Za-z]", alias) else 0
            pattern = re.escape(alias)
            if alias.isascii() and alias[0].isalnum() and alias[-1].isalnum():
                pattern = rf"(?<![A-Za-z0-9]){pattern}(?![A-Za-z0-9])"
            for match in re.finditer(pattern, text, flags):
                span = match.span()
                if any(span[0] < end and start < span[1] for start, end in occupied):
                    continue
                occupied.append(span)
                found.append(_Mention(match.group(0), None, 0.85, 3, "content"))
        return found

    @staticmethod
    def _type_hint(value: EntityTypeHint) -> EntityType | None:
        if value == EntityTypeHint.UNKNOWN:
            return None
        try:
            return EntityType(value.value)
        except ValueError:
            return None

    @staticmethod
    def _infer_jay_context_type(content: str) -> EntityType | None:
        decisions: set[EntityType] = set()
        full_names = list(_JAY_FULL_NAME.finditer(content))
        for match in _JAY_SHORT_NAME.finditer(content):
            # Jay within the full name identifies the artist even in album prose.
            if any(name.start() == match.start() for name in full_names):
                decisions.add(EntityType.ARTIST)
                continue
            start = max(0, match.start() - _JAY_CONTEXT_WINDOW)
            end = match.end() + _JAY_CONTEXT_WINDOW
            context = content[start:end].casefold()
            album = EntityLinker._has_jay_cue(context, _JAY_ALBUM_CUES)
            artist = (
                EntityLinker._has_jay_cue(context, _JAY_ARTIST_CUES)
                # Exclude common non-pronoun uses such as 其他 / 吉他 / 他人.
                or re.search(r"(?<![其吉])他(?!人)", context) is not None
                or any(start <= name.start() and name.end() <= end for name in full_names)
            )
            if album == artist:
                return None  # No evidence, or conflicting evidence: keep unresolved.
            decisions.add(EntityType.ALBUM if album else EntityType.ARTIST)
        # Mentions are deduplicated per chunk; conflicting occurrences stay pending.
        return next(iter(decisions)) if len(decisions) == 1 else None

    @staticmethod
    def _has_jay_cue(context: str, cues: tuple[str, ...]) -> bool:
        return any(
            re.search(rf"(?<![a-z0-9]){re.escape(cue)}(?![a-z0-9])", context)
            if cue.isascii()
            else cue in context
            for cue in cues
        )

    @staticmethod
    def _infer_context_type(content: str, mention: str) -> EntityType | None:
        positions = [
            match.start() for match in re.finditer(re.escape(mention), content, re.I)
        ]
        scores: dict[EntityType, int] = {}
        for position in positions:
            before = content[max(0, position - 16) : position].casefold()
            after = content[
                position + len(mention) : position + len(mention) + 12
            ].casefold()
            for entity_type, cues in _CONTEXT_CUES.items():
                score = 0
                for cue in cues:
                    normalized_cue = cue.casefold()
                    before_position = before.rfind(normalized_cue)
                    if before_position >= 0:
                        distance = len(before) - before_position - len(normalized_cue)
                        score = max(score, 100 - distance)
                    after_position = after.find(normalized_cue)
                    if after_position >= 0:
                        score = max(score, 50 - after_position)
                scores[entity_type] = max(scores.get(entity_type, 0), score)
        positive = [
            (score, entity_type) for entity_type, score in scores.items() if score
        ]
        if not positive:
            return None
        positive.sort(key=lambda item: (-item[0], item[1].value))
        if len(positive) > 1 and positive[0][0] == positive[1][0]:
            return None
        return positive[0][1]

    async def _disambiguate(
        self, chunk: EnrichedChunk, pending: list[_Pending]
    ) -> EntityDisambiguationOutput:
        eligible = [item for item in pending if item.resolution.candidate_entity_ids]
        payload: dict[str, object] = {
            "chunk": {
                "title": chunk.metadata.get("title"),
                "section_path": chunk.section_path,
                "content": chunk.content,
            },
            "mentions": [
                {
                    "mention_text": item.mention.text,
                    "type_hint": (
                        item.mention.type_hint.value
                        if item.mention.type_hint is not None
                        else "unknown"
                    ),
                    "candidate_entity_ids": list(item.resolution.candidate_entity_ids),
                    "candidates": [
                        self.registry.get(entity_id).model_dump(mode="json")
                        for entity_id in item.resolution.candidate_entity_ids
                        if self.registry.get(entity_id) is not None
                    ],
                }
                for item in eligible
            ],
        }
        last_error: Exception | None = None
        for attempt in range(self.config.retry_count + 1):
            instructions = ENTITY_DISAMBIGUATION_PROMPT_V1
            if attempt:
                instructions += "\nCorrect the previous invalid or ungrounded response."
            try:
                raw = await asyncio.wait_for(
                    self.runtime.generate(
                        payload,
                        instructions=instructions,
                        schema=strict_model_schema(EntityDisambiguationOutput),
                        schema_name=f"music_entity_linking_{self.config.prompt_version}",
                    ),
                    timeout=self.config.timeout_seconds,
                )
                output = EntityDisambiguationOutput.model_validate_json(raw)
                self._validate_choices(output, eligible)
                return output
            except Exception as exc:
                last_error = exc
        raise RuntimeError(self._error_code(last_error)) from last_error

    @staticmethod
    def _validate_choices(
        output: EntityDisambiguationOutput, eligible: list[_Pending]
    ) -> None:
        allowed = {
            normalize_alias(item.mention.text): set(
                item.resolution.candidate_entity_ids
            )
            for item in eligible
        }
        if len(output.resolutions) != len(eligible):
            raise ValueError("entity_linking_incomplete_resolution")
        seen: set[str] = set()
        for choice in output.resolutions:
            key = normalize_alias(choice.mention_text)
            if key not in allowed or key in seen:
                raise ValueError("entity_linking_unknown_resolution_mention")
            seen.add(key)
            if (
                choice.selected_entity_id is not None
                and choice.selected_entity_id not in allowed[key]
            ):
                raise ValueError("entity_linking_non_candidate_selection")

    def _apply_choices(
        self,
        pending: list[_Pending],
        output: EntityDisambiguationOutput,
        linked: dict[str, CanonicalEntity],
    ) -> list[_Pending]:
        choices = {
            normalize_alias(item.mention_text): item for item in output.resolutions
        }
        remaining: list[_Pending] = []
        for item in pending:
            choice = choices.get(normalize_alias(item.mention.text))
            if choice is None or choice.selected_entity_id is None:
                remaining.append(item)
                continue
            entity = self.registry.get(choice.selected_entity_id)
            if entity is None:
                remaining.append(item)
                continue
            confidence = min(item.mention.confidence, choice.confidence)
            current = linked.get(entity.entity_id)
            if current is None or confidence > current.confidence:
                linked[entity.entity_id] = self.registry.canonical(entity, confidence)
        return remaining

    @staticmethod
    def _error_code(error: Exception | None) -> str:
        if isinstance(error, asyncio.TimeoutError):
            return "entity_linking_timeout"
        if isinstance(error, ValidationError):
            return "entity_linking_invalid_output"
        if isinstance(error, (ValueError, RuntimeError)):
            return str(error)[:500]
        return "entity_linking_request_failed"
