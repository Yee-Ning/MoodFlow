"""Resolve query references from bounded, trusted application context."""

from __future__ import annotations

import re
from typing import Protocol

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from app.context.music import MusicContextProvider
from app.rag.knowledge.entity_linking.models import EntityType
from app.rag.knowledge.entity_linking.registry import EntityRegistry
from app.rag.query_understanding.models import (
    CatalogReference,
    ContextDependency,
    ContextResolution,
    ContextResolutionStatus,
    QueryEntityStatus,
    ResolvedQueryEntity,
)
from app.schemas.context import SongContext
from app.schemas.player import PlayerContext


CURRENT_SONG_CLARIFICATION = "请先选择并播放一首歌曲，或直接告诉我歌曲名称。"
CONVERSATION_CLARIFICATION = "请明确你指的是哪位歌手、哪首歌或哪张专辑。"

_CURRENT_SONG_REFERENCE = re.compile(
    r"(?:这|那|当前|正在播放的)(?:一)?首歌|(?:这|那)首|当前歌曲"
)
_CONVERSATION_REFERENCE = re.compile(
    r"(?:这个歌手|这位歌手|这张专辑|那张专辑)|"
    r"(?:^|[，。！？\s])(?:他|她|它)(?=的|有|是|与|和|在|曾|为|如)"
)
_REFERENCE_REPLACEMENTS = re.compile(
    r"(?:他|她|它|这个歌手|这位歌手|这张专辑|那张专辑)"
)
_MAX_HISTORY_MESSAGES = 12
_MAX_HISTORY_CHARACTERS = 12_000


class CurrentSongProvider(Protocol):
    async def get_song(self, song_id: int) -> SongContext: ...


def _message_text(message: BaseMessage) -> str:
    text = getattr(message, "text", None)
    return str(text if text is not None else message.content)


def _select_recent_history(
    messages: list[BaseMessage], current_input: str
) -> list[BaseMessage]:
    history = [
        message
        for message in messages
        if isinstance(message, (HumanMessage, AIMessage))
    ]
    if history and isinstance(history[-1], HumanMessage):
        history.pop()
    selected: list[BaseMessage] = []
    used_characters = len(current_input)
    for message in reversed(history):
        length = len(_message_text(message))
        if selected and used_characters + length > _MAX_HISTORY_CHARACTERS:
            break
        if length > _MAX_HISTORY_CHARACTERS:
            continue
        selected.append(message)
        used_characters += length
        if len(selected) >= _MAX_HISTORY_MESSAGES:
            break
    selected.reverse()
    return selected


class QueryContextResolver:
    def __init__(
        self,
        *,
        song_provider: CurrentSongProvider | None = None,
        registry: EntityRegistry | None = None,
    ) -> None:
        self.song_provider = song_provider or MusicContextProvider()
        self.registry = registry or EntityRegistry.load()

    async def resolve(
        self,
        query: str,
        *,
        messages: list[BaseMessage] | None = None,
        routing_context: PlayerContext | None = None,
    ) -> ContextResolution:
        normalized = " ".join(query.split())
        if not normalized:
            raise ValueError("query must not be blank")
        if _CURRENT_SONG_REFERENCE.search(normalized):
            return await self._resolve_current_song(
                normalized, routing_context or PlayerContext()
            )
        if _CONVERSATION_REFERENCE.search(normalized):
            history_entity = self._latest_history_entity(
                messages or [], normalized
            )
            if history_entity is None:
                return ContextResolution(
                    status=ContextResolutionStatus.NEEDS_INPUT,
                    dependency=ContextDependency.AMBIGUOUS,
                    original_query=normalized,
                    clarification_question=CONVERSATION_CLARIFICATION,
                )
            replacement = self._typed_label(history_entity)
            standalone = _REFERENCE_REPLACEMENTS.sub(replacement, normalized)
            return ContextResolution(
                status=ContextResolutionStatus.RESOLVED,
                dependency=ContextDependency.CONVERSATION,
                original_query=normalized,
                standalone_query=standalone,
                evidence=[f"conversation_entity:{history_entity.knowledge_entity_id}"],
                entities=[history_entity],
            )
        return ContextResolution(
            status=ContextResolutionStatus.RESOLVED,
            dependency=ContextDependency.STANDALONE,
            original_query=normalized,
            standalone_query=normalized,
        )

    async def _resolve_current_song(
        self, query: str, routing_context: PlayerContext
    ) -> ContextResolution:
        if not routing_context.has_current_song or routing_context.current_song_id is None:
            return ContextResolution(
                status=ContextResolutionStatus.NEEDS_INPUT,
                dependency=ContextDependency.CURRENT_SONG,
                original_query=query,
                clarification_question=CURRENT_SONG_CLARIFICATION,
            )
        try:
            song = await self.song_provider.get_song(routing_context.current_song_id)
        except Exception:
            return ContextResolution(
                status=ContextResolutionStatus.NEEDS_INPUT,
                dependency=ContextDependency.CURRENT_SONG,
                original_query=query,
                clarification_question="当前歌曲资料暂时无法读取，请稍后再试或直接告诉我歌曲名称。",
            )
        entity = self._song_entity(song)
        replacement = f"歌曲《{song.name}》（歌手：{song.artist}）"
        standalone = _CURRENT_SONG_REFERENCE.sub(replacement, query)
        return ContextResolution(
            status=ContextResolutionStatus.RESOLVED,
            dependency=ContextDependency.CURRENT_SONG,
            original_query=query,
            standalone_query=standalone,
            evidence=[f"player_context.current_song_id:{song.song_id}"],
            entities=[entity],
        )

    def _song_entity(self, song: SongContext) -> ResolvedQueryEntity:
        registry_resolution = self.registry.resolve(
            song.name, type_hint=EntityType.SONG
        )
        registry_entity = registry_resolution.entity
        return ResolvedQueryEntity(
            surface=song.name,
            canonical_name=(
                registry_entity.canonical_name if registry_entity is not None else song.name
            ),
            entity_type=EntityType.SONG,
            knowledge_entity_id=(
                registry_entity.entity_id if registry_entity is not None else None
            ),
            catalog_reference=CatalogReference(
                provider="netease",
                entity_id=str(song.song_id),
                entity_type=EntityType.SONG,
                name=song.name,
            ),
            aliases=(
                list(self.registry.canonical(registry_entity, 0.99).aliases)
                if registry_entity is not None
                else []
            ),
            status=QueryEntityStatus.RESOLVED,
            confidence=0.99,
        )

    def _latest_history_entity(
        self, messages: list[BaseMessage], current_input: str
    ) -> ResolvedQueryEntity | None:
        history = _select_recent_history(messages, current_input)
        surfaces = self.registry.detectable_aliases()
        for message in reversed(history):
            content = _message_text(message).casefold()
            for surface in surfaces:
                if surface.casefold() not in content:
                    continue
                resolution = self.registry.resolve(surface)
                if resolution.entity is None:
                    continue
                canonical = self.registry.canonical(resolution.entity, 0.92)
                return ResolvedQueryEntity(
                    surface=surface,
                    canonical_name=canonical.canonical_name,
                    entity_type=canonical.entity_type,
                    knowledge_entity_id=canonical.entity_id,
                    aliases=canonical.aliases,
                    status=QueryEntityStatus.RESOLVED,
                    confidence=0.92,
                )
        return None

    @staticmethod
    def _typed_label(entity: ResolvedQueryEntity) -> str:
        labels = {
            EntityType.ARTIST: "歌手",
            EntityType.SONG: "歌曲",
            EntityType.ALBUM: "专辑",
            EntityType.PERSON: "人物",
        }
        label = labels.get(entity.entity_type, "音乐实体")
        return f"{label}《{entity.canonical_name}》"
