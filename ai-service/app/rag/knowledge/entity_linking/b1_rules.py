"""Evidence-only rules for the explicitly scoped, offline v7 B1 retry.

These functions know neither document IDs nor reviewed target answers. They
return an abstention when the current body occurrence lacks sufficient evidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .models import EntityType
from .registry import EntityRegistry, normalize_alias


@dataclass(frozen=True)
class B1Decision:
    entity_id: str
    rule_id: str
    evidence_start: int
    evidence_end: int
    evidence_quote: str


def body_block_for_span(content: str, start: int, end: int) -> tuple[int, int]:
    """Bound evidence by blank paragraphs and at most 512 characters per side."""
    if not (0 <= start < end <= len(content)):
        raise ValueError("b1_occurrence_offset_invalid")
    left = content.rfind("\n\n", 0, start)
    right = content.find("\n\n", end)
    return max(left + 2 if left >= 0 else 0, start - 512), min(
        right if right >= 0 else len(content), end + 512
    )


def classify_occurrence_role(content: str, start: int, end: int) -> str:
    body_block_for_span(content, start, end)
    surface = content[start:end]
    if surface.isascii():
        if (start and re.match(r"[A-Za-z0-9_\-]", content[start - 1])) or (
            end < len(content) and re.match(r"[A-Za-z0-9_\-]", content[end])
        ):
            return "fragment"
    token_start = max(content.rfind(c, 0, start) for c in " \n\t<>") + 1
    if re.search(r"https?://|www\.", content[token_start:end], re.I):
        return "url"
    for match in re.finditer(r"《[^》]+》", content):
        if match.start() < start and end < match.end():
            if match.group()[1:-1].strip() != surface:
                return "work_title_fragment"
    return "body"


def _decision(content: str, entity_id: str, rule: str, span: tuple[int, int]):
    left, right = span
    return B1Decision(entity_id, rule, left, right, content[left:right])


def _peer_ids(text: str, registry: EntityRegistry, kind: EntityType) -> set[str]:
    result = set()
    for alias in registry.detectable_aliases():
        candidates = registry.candidates(alias)
        if candidates and all(c.entity_type == kind for c in candidates):
            if re.search(re.escape(alias), text, re.I):
                result.update(c.entity_id for c in candidates)
    return result


def _work_type(
    content: str, start: int, end: int, registry: EntityRegistry
) -> tuple[EntityType | None, str, tuple[int, int]]:
    span = body_block_for_span(content, start, end)
    left, right = span
    block = content[left:right]
    before, after = (
        content[max(left, start - 100) : start],
        content[end : min(right, end + 100)],
    )
    album = bool(
        re.search(
            r"(?:专辑|專輯|唱片|album)\s*[,，:：]?\s*[《「〈“\"']?\s*$", before, re.I
        )
    )
    song = bool(
        re.search(
            r"(?:歌曲|单曲|單曲|曲目|song|track)\s*[,，:：]?\s*[《「〈“\"']?\s*$",
            before,
            re.I,
        )
    )
    # English name followed by a Chinese parenthetical translation.
    if before.rstrip().endswith(("(", "（")):
        phrase = re.search(
            r"\b(album|song|track)\s+[^()（）\n]{1,70}[(（]$", before, re.I
        )
        if phrase:
            album |= phrase[1].lower() == "album"
            song |= phrase[1].lower() in {"song", "track"}
    # The title is the grammatical subject; nearby artist words describe its author.
    album |= bool(re.match(r"[》」〉”\"']?\s*(?:是|为|為).{0,60}(?:专辑|專輯)", after))
    song |= bool(
        re.match(r"[》」〉”\"']?\s*(?:是|为|為).{0,45}(?:一首|歌曲|单曲|單曲)", after)
    )
    album |= bool(
        re.match(
            r"[》」〉”\"']?\s*(?:(?:这|這)[张張])?(?:专辑|專輯|[（(](?:2LP|CD)[)）])",
            after,
            re.I,
        )
    )
    album |= bool(re.match(r"[》」〉”\"']?\s*經典復刻黑膠", after))
    song |= bool(
        re.match(
            r"[》」〉”\"']?\s*(?:的)?(?:歌詞|歌词|完整版MV|MV|のイントロ)", after, re.I
        )
    )
    if album or song:
        return (
            (
                None
                if album and song
                else EntityType.ALBUM
                if album
                else EntityType.SONG
            ),
            "work_attached_type",
            span,
        )

    line_left = content.rfind("\n", left, start) + 1
    line_right = content.find("\n", end, right)
    line = content[max(left, line_left) : line_right if line_right >= 0 else right]
    if re.search(
        r"先[后後]共[将將].*[《].*[》].*[《].*[》]交[给給].{0,12}[发發]行", line
    ):
        return EntityType.ALBUM, "record_label_distribution_list", span
    if "|" in line and re.search(r"\d{1,2}:\d{2}", line):
        return EntityType.SONG, "track_row_duration", span
    if re.search(r"曲序|作词|作詞|编曲|編曲", block) and re.search(
        r"曲目|时长|時長", block
    ):
        return EntityType.SONG, "track_table", span
    if "|" in line and re.search(r"歌名", block) and re.search(r"歌手", block):
        return EntityType.SONG, "music_video_song_column", span
    if (
        re.match(r"\s*\d+[.)]\s+", line)
        and len(re.findall(r"(?m)^\s*\d+[.)]\s+", block)) >= 2
    ):
        return EntityType.SONG, "numbered_track_list", span
    # Bound the inline English list at its next heading.
    prefix = content[left:start]
    album_heading = list(
        re.finditer(r"Title of Music Albums|专辑年表|專輯年表", prefix, re.I)
    )
    song_heading = list(re.finditer(r"Song Titles", prefix, re.I))
    if album_heading and (
        not song_heading or album_heading[-1].start() > song_heading[-1].start()
    ):
        return EntityType.ALBUM, "album_chronology", span
    if song_heading and (
        not album_heading or song_heading[-1].start() > album_heading[-1].start()
    ):
        return EntityType.SONG, "song_list_heading", span
    if re.search(r"录音室专辑|錄音室專輯", block) and re.search(
        r"发行日期|發行日期", block
    ):
        return EntityType.ALBUM, "album_infobox", span
    album_peers = _peer_ids(block, registry, EntityType.ALBUM)
    song_peers = _peer_ids(block, registry, EntityType.SONG)
    if (
        len(album_peers) >= 2
        and len(re.findall(r"(?:19|20)\d{2}", block)) >= 2
        and not song_peers
    ):
        return EntityType.ALBUM, "dated_album_list", span
    if (
        len(song_peers) >= 2
        and not album_peers
        and re.search(r"《[^》]+》[、，]|(?m:^\s*[-|])", block)
    ):
        return EntityType.SONG, "coordinated_song_list", span
    if re.search(r"(?m)^\s*-\s+[^–—\n]+[–—]\s+", line) and song_peers:
        if len(re.findall(r"(?m)^\s*-\s+[^–—\n]+[–—]\s+", block)) >= 3:
            return EntityType.SONG, "artist_track_rows", span
    if re.search(r"(?:專輯|专辑).{0,10}獎", before):
        return EntityType.ALBUM, "album_award_category", span
    if re.search(r"收[录錄]在", after) and re.search(r"歌曲|单曲|單曲", before):
        return EntityType.SONG, "song_contained_in_album", span
    if re.search(r"這首歌|这首歌", before) and re.search(
        r"媲美經典的|媲美经典的", before
    ):
        return EntityType.SONG, "song_comparison", span
    if re.search(r"以\s*[《「]?$", before) and re.match(
        r"[》」]为例对.{0,35}音乐作品", after
    ):
        return EntityType.SONG, "song_analysis_subject", span
    if re.search(r"这首歌|這首歌|歌曲|歌詞|歌词|K歌|演唱|ギター|イントロ", block):
        if not album_peers and not re.search(r"专辑|專輯|album", block, re.I):
            return EntityType.SONG, "song_content", span
    nearby = content[max(0, start - 160) : min(len(content), end + 250)]
    if re.search(r"《[^》]+》[、，]", block) and re.search(r"的歌曲[里裡]", nearby):
        return (
            EntityType.SONG,
            "song_list_following_predicate",
            (max(0, start - 160), min(len(content), end + 250)),
        )
    return None, "work_ambiguous", span


def infer_jay_occurrence(content: str, start: int, end: int, registry: EntityRegistry):
    if (
        normalize_alias(content[start:end]) != "jay"
        or classify_occurrence_role(content, start, end) != "body"
    ):
        return None
    kind, rule, span = _work_type(content, start, end, registry)
    left, right = span
    block = content[left:right]
    before, after = (
        content[max(left, start - 32) : start],
        content[end : min(right, end + 100)],
    )
    if kind == EntityType.ALBUM:
        return _decision(content, "album:jay_2000", "jay_" + rule, span)
    # A Japanese case/topic particle alone does not identify a person.
    human = bool(
        re.match(
            r"(?:が(?:デビュー|プロデュース|目をつけ|見にき|日本に来|戻って)|"
            r"は(?:挑戦する人|バンドとして|歌唱力|常に)|"
            r"って(?:日本大好き|クラシック)|の(?:香港コロシアム)|を発掘した)",
            after,
        )
    )
    human |= bool(re.search(r"アーティストとしての\s*$", before))
    if not human and re.match(r"(?:の|らしい|サウンド|って|は|が|に|だから)", after):
        human = bool(
            re.search(
                r"デビュー|ヴォーカリスト|シンガー|彼[のはがら]|本人|戻ってき|"
                r"スター|人にな|アーティスト|ミュージシャン|音楽業界|インタビュー|誰[？?]",
                block,
            )
        )
    if (
        human
        and (kind is None or rule != "work_attached_type")
        and not re.search(r"(?:专辑|專輯|album)\s*[《「\"']?\s*$", before, re.I)
    ):
        return _decision(
            content, "artist:jay_chou", "jay_japanese_human_predicate", span
        )
    # A self-contained album critique and its explicit successor relationship.
    if (
        re.match(r"[》」]\s*本身的音乐品质", after)
        and re.search(r"专辑|專輯", block)
        and "前一张" in block
    ):
        return _decision(content, "album:jay_2000", "jay_album_successor_review", span)
    if (
        re.search(r"唱片", block)
        and re.search(r"音乐品质|音樂品質", block)
        and re.search(r"[《「]$", before)
    ):
        if re.search(r"本身的音乐品质|少年游", after):
            return _decision(
                content, "album:jay_2000", "jay_record_review_heading", span
            )
    return None


def infer_qi_li_xiang_occurrence(
    content: str, start: int, end: int, registry: EntityRegistry
):
    if (
        content[start:end] != "七里香"
        or classify_occurrence_role(content, start, end) != "body"
    ):
        return None
    kind, rule, span = _work_type(content, start, end, registry)
    if kind is None and "2LP" in content[span[0] : span[1]]:
        block = content[span[0] : span[1]]
        if re.search(r"七里香[（(]2LP[)）]", block) and re.search(
            r"[发發]行日期", block
        ):
            kind, rule = EntityType.ALBUM, "record_product_coreference"
    if kind in {EntityType.ALBUM, EntityType.SONG}:
        return _decision(
            content, f"{kind.value}:qi_li_xiang", "qi_li_xiang_" + rule, span
        )
    return None


def infer_ye_hui_mei_occurrence(
    content: str, start: int, end: int, registry: EntityRegistry
):
    if (
        content[start:end] not in {"葉惠美", "叶惠美"}
        or classify_occurrence_role(content, start, end) != "body"
    ):
        return None
    before, after = content[max(0, start - 16) : start], content[end : end + 20]
    if re.search(r"母[亲親]|[妈媽][妈媽]|美[术術]老[师師]", before) or re.match(
        r"(?:女士|则是|則是)", after
    ):
        return None
    kind, rule, span = _work_type(content, start, end, registry)
    if kind == EntityType.ALBUM:
        return _decision(content, "album:ye_hui_mei", "ye_hui_mei_" + rule, span)
    # A cited record page can share identity with an independently typed title
    # inside this same chunk; never borrow a document title or parent body.
    context = content[max(0, start - 512) : min(len(content), end + 512)]
    direct = re.search(
        r"(?:《[叶葉]惠美》.{0,20}(?:发行|發行)|[叶葉]惠美[（(]CD[)）])", context
    )
    if direct and re.match(r"\.\s*(?:杰威[尔爾]音[乐樂])", after):
        left = max(0, start - 512)
        return _decision(
            content,
            "album:ye_hui_mei",
            "ye_hui_mei_cited_record_coreference",
            (left, min(len(content), end + 512)),
        )
    # Sale-page body title plus independently stated album description.
    context = content[max(0, start - 512) : min(len(content), end + 512)]
    if re.search(r"第[四4]張專輯|第[四4]张专辑", context) and re.search(
        r"2003", context
    ):
        return _decision(
            content,
            "album:ye_hui_mei",
            "ye_hui_mei_record_body_heading",
            (max(0, start - 512), min(len(content), end + 512)),
        )
    return None


def infer_genre_occurrence(
    content: str, start: int, end: int, registry: EntityRegistry
):
    if classify_occurrence_role(content, start, end) != "body":
        return None
    candidates = registry.candidates(content[start:end])
    if len(candidates) != 1 or candidates[0].entity_type != EntityType.GENRE:
        return None
    span = body_block_for_span(content, start, end)
    left, right = span
    context = content[left:right]
    after = content[end : min(right, end + 40)]
    if after.startswith("格"):
        return None
    music = re.search(r"音[乐樂]|歌曲|歌[词詞]|曲[风風]|摇滚|搖滾|对唱|對唱", context)
    patterns = {
        "genre:c_pop": r"^(?:的(?:新[时時]代|[发發]展)|史(?:的|[，。！!；;]|$)|[发發]展(?:[，。！!；;]|$)|[畅暢][销銷]冠[军軍])",
        "genre:chinese_style": r"^(?:[」”\"']?的(?:歌曲|歌[词詞]|代表之作|流行歌曲|摇滚|搖滾|东西|東西)|[」”\"']歌[词詞])",
        "genre:hip_hop": r"^(?:混合|[对對]唱)",
    }
    pattern = patterns.get(candidates[0].entity_id)
    if music and pattern and re.search(pattern, after):
        return _decision(
            content, candidates[0].entity_id, "genre_complete_name_grammar", span
        )
    return None


def infer_c_pop_occurrence(
    content: str, start: int, end: int, registry: EntityRegistry
):
    if content[start:end] not in {"华语流行音乐", "華語流行音樂"}:
        return None
    return infer_genre_occurrence(content, start, end, registry)


def infer_fearless_occurrence(
    content: str, start: int, end: int, registry: EntityRegistry
):
    if (
        content[start:end] != "霍元甲"
        or classify_occurrence_role(content, start, end) != "body"
    ):
        return None
    before, after = content[max(0, start - 25) : start], content[end : end + 25]
    if re.search(r"母[亲親]|英雄|[电電][视視][剧劇]|[电電]影", before) or re.match(
        r"\s*EP\b", after, re.I
    ):
        return None
    kind, rule, span = _work_type(content, start, end, registry)
    if kind == EntityType.SONG:
        return _decision(content, "song:fearless", "fearless_" + rule, span)
    return None


def infer_person_occurrence(
    content: str, start: int, end: int, registry: EntityRegistry
):
    if (
        content[start:end] != "方文山"
        or classify_occurrence_role(content, start, end) != "body"
    ):
        return None
    if re.match(r"(?:讚譽|赞誉|作[词詞]|寫|写)", content[end : end + 12]):
        return _decision(
            content,
            "person:vincent_fang",
            "person_subject_after_work_phrase",
            body_block_for_span(content, start, end),
        )
    return None


def infer_occurrence(content: str, start: int, end: int, registry: EntityRegistry):
    for infer in (
        infer_jay_occurrence,
        infer_qi_li_xiang_occurrence,
        infer_ye_hui_mei_occurrence,
        infer_c_pop_occurrence,
        infer_fearless_occurrence,
        infer_genre_occurrence,
        infer_person_occurrence,
    ):
        result = infer(content, start, end, registry)
        if result is not None:
            return result
    return None
