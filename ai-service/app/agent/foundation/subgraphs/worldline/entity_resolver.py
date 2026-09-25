import re
import unicodedata
from dataclasses import dataclass

from app.agent.foundation.subgraphs.worldline.models import (
    EntityResolution,
    EntityResolutionStatus,
    EntityType,
)
from app.agent.foundation.subgraphs.worldline.music_service import (
    WorldLineMusicService,
)


_QUOTED_ENTITY = re.compile(r"[《\"“'](?P<name>[^》\"”']{1,300})[》\"”']")
_LEADING_REQUEST = re.compile(
    r"^(?:请|麻烦)?(?:帮我)?(?:生成|创建|构建|探索|查看|看看|讲讲|分析)?(?:一下)?"
)
_TRAILING_WORLDLINE = re.compile(
    r"(?:的)?(?:音乐)?(?:世界线|world\s*line)(?:图谱)?[。.!！?？]*$",
    re.IGNORECASE,
)
_TRAILING_ERA_REQUEST = re.compile(
    r"(?:的|\s+)(?:\s*(?:18|19|20)\d{2}\s*年(?:代)?|\s*"
    r"(?:时代|時代|时期|時期|年代|时间线|時間線|历程|歷程|"
    r"阶段|階段|早期|初期|突破期|巅峰期|巔峰期|晚期)).*$"
)
_TRAILING_SCENE_REQUEST = re.compile(
    r"(?:的|\s+)(?:(?:所属|參與|参与|关联|關聯|所在|相关|相關)的?)?"
    r"(?:音乐|音樂|地域)?(?:场景|場景|scene|社群|音乐圈|音樂圈|"
    r"音乐群体|音樂群體|地域音乐文化|地域音樂文化).*$",
    re.IGNORECASE,
)
_TRAILING_INFLUENCE_REQUEST = re.compile(
    r"(?:的|\s+)(?:音乐|音樂|风格|風格)?"
    r"(?:影响(?:关系|来源)?|影響(?:關係|來源)?|启发|啟發|"
    r"风格来源|風格來源|"
    r"influences?|influenced\s+by|inspired\s+by|musical\s+influence).*$",
    re.IGNORECASE,
)
_LEADING_INFLUENCE_REQUEST = re.compile(
    r"^(?:(?:谁|誰|哪些歌手|哪些艺人|哪些藝人)(?:影响|影響|启发|啟發)(?:了)?|"
    r"who\s+(?:influenced|inspired))\s*",
    re.IGNORECASE,
)
_TRAILING_WHO_INFLUENCED = re.compile(
    r"(?:的)?(?:受|受到)(?:谁|誰)(?:的)?(?:影响|影響|启发|啟發).*$"
)


@dataclass(frozen=True)
class ParsedEntityQuery:
    query: str
    expected_entity_type: EntityType | None
    alternate_query: str | None = None


def parse_entity_query(user_query: str) -> ParsedEntityQuery:
    text = user_query.strip()
    if not text:
        raise ValueError("WorldLine query must not be blank")

    expected = _expected_type(text)
    quoted = _QUOTED_ENTITY.search(text)
    alternate_query: str | None = None
    if quoted is not None:
        query = quoted.group("name").strip()
    else:
        query = _LEADING_REQUEST.sub("", text, count=1).strip()
        query = _LEADING_INFLUENCE_REQUEST.sub("", query, count=1).strip()
        query = _TRAILING_WORLDLINE.sub("", query, count=1).strip()
        prefixes = {
            EntityType.SONG: ("歌曲", "单曲"),
            EntityType.ARTIST: ("歌手", "音乐人", "艺人", "乐队", "乐团"),
            EntityType.ALBUM: ("专辑", "唱片"),
        }
        if expected is not None:
            for prefix in prefixes[expected]:
                if query.startswith(prefix):
                    query = query[len(prefix):].strip(" ：:")
                    break
        before_era_suffix = query
        query = _TRAILING_ERA_REQUEST.sub("", query).strip()
        query = _TRAILING_SCENE_REQUEST.sub("", query).strip()
        query = _TRAILING_INFLUENCE_REQUEST.sub("", query).strip()
        query = _TRAILING_WHO_INFLUENCED.sub("", query).strip()
        if query != before_era_suffix:
            alternate_query = before_era_suffix
        query = query.removesuffix("的").strip()

    if not query or len(query) > 300:
        raise ValueError("WorldLine entity query could not be extracted")
    return ParsedEntityQuery(
        query=query,
        expected_entity_type=expected,
        alternate_query=alternate_query,
    )


def _expected_type(text: str) -> EntityType | None:
    labels = {
        EntityType.SONG: ("歌曲", "单曲", "这首歌"),
        EntityType.ARTIST: ("歌手", "音乐人", "艺人", "乐队", "乐团"),
        EntityType.ALBUM: ("专辑", "唱片", "这张专辑"),
    }
    matches = [
        entity_type
        for entity_type, keywords in labels.items()
        if any(keyword in text for keyword in keywords)
    ]
    return matches[0] if len(matches) == 1 else None


def _normalized_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if character.isalnum())


async def resolve_music_entity(
    service: WorldLineMusicService,
    user_query: str,
) -> EntityResolution:
    parsed = parse_entity_query(user_query)
    if parsed.alternate_query is not None:
        # A title may itself contain "年代" or "时代". Prefer an exact catalog
        # title before treating the suffix as a question about the entity.
        try:
            alternate_candidates = await service.search_entities(
                parsed.alternate_query,
                parsed.expected_entity_type,
            )
        except Exception:
            alternate_candidates = []
        if parsed.expected_entity_type is not None:
            alternate_candidates = [
                item for item in alternate_candidates
                if item.entity_type is parsed.expected_entity_type
            ]
        alternate_name = _normalized_name(parsed.alternate_query)
        alternate_exact = [
            item for item in alternate_candidates
            if _normalized_name(item.name) == alternate_name
        ]
        if len(alternate_exact) == 1:
            return EntityResolution(
                status=EntityResolutionStatus.RESOLVED,
                query=parsed.alternate_query,
                expected_entity_type=parsed.expected_entity_type,
                entity=alternate_exact[0],
                candidates=alternate_exact,
            )
        if len(alternate_exact) > 1:
            return EntityResolution(
                status=EntityResolutionStatus.AMBIGUOUS,
                query=parsed.alternate_query,
                expected_entity_type=parsed.expected_entity_type,
                candidates=alternate_exact[:10],
            )
    candidates = await service.search_entities(
        parsed.query,
        parsed.expected_entity_type,
    )
    if parsed.expected_entity_type is not None:
        candidates = [
            item
            for item in candidates
            if item.entity_type is parsed.expected_entity_type
        ]

    expected_name = _normalized_name(parsed.query)
    exact = [
        candidate
        for candidate in candidates
        if _normalized_name(candidate.name) == expected_name
    ]
    if len(exact) == 1:
        return EntityResolution(
            status=EntityResolutionStatus.RESOLVED,
            query=parsed.query,
            expected_entity_type=parsed.expected_entity_type,
            entity=exact[0],
            candidates=exact,
        )
    if len(exact) > 1:
        return EntityResolution(
            status=EntityResolutionStatus.AMBIGUOUS,
            query=parsed.query,
            expected_entity_type=parsed.expected_entity_type,
            candidates=exact[:10],
        )
    return EntityResolution(
        status=EntityResolutionStatus.UNRESOLVED,
        query=parsed.query,
        expected_entity_type=parsed.expected_entity_type,
        candidates=candidates[:10],
    )
