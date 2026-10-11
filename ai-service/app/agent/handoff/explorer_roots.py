"""Guide non-catalog roots without extending or guessing knowledge identities."""

import re

from app.agent.foundation.subgraphs.worldline.entity_resolver import (
    _LEADING_REQUEST,
    parse_entity_query,
)
from app.agent.foundation.subgraphs.worldline.genre_registry import (
    GenreVocabularyRegistry,
)


def root_guidance(text):
    try:
        parsed = parse_entity_query(text)
    except ValueError:
        # A pure era phrase can be fully consumed as a catalog query suffix.
        # Classify the original topic instead of sending an empty catalog root.
        parsed = None
    # Explicit song/artist/album titles keep the existing ambiguity resolver,
    # even when the title happens to contain a style or a decade.
    if parsed is not None and parsed.expected_entity_type is not None:
        return None
    root = _LEADING_REQUEST.sub("", text, count=1).strip()
    genre_query = parsed.query if parsed is not None else root
    genre = GenreVocabularyRegistry.load().resolve(genre_query)
    is_topic = genre is not None or bool(
        re.search(
            r"(?:\d{2,4}|九十|八十|七十|六十|零零|两千)\s*年代|时代|时期|音乐场景|地域场景|音乐社群|音乐圈|港乐|港樂|流派|风格|摇滚|爵士|电子乐|場景|场景",
            root,
        )
    )
    if not is_topic:
        return None
    topic = genre.canonical_name if genre else root
    return f"可以探索“{topic}”。当前需要从具体歌曲、歌手或专辑开始，我可以从某位与这个主题有关的歌手展开，再沿有来源的风格/时代/场景节点探索。你想从谁或哪首歌、哪张专辑开始？我不会凭主题编造实体或关联。"
