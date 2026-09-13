"""Conservative deterministic extraction for common music facts."""

from __future__ import annotations

import re

from app.rag.enrichment.models import EnrichedChunk

from ..entity_linking.models import CanonicalEntity, EntityType
from .models import ClaimCandidate, ClaimPredicate, ClaimTime


_SENTENCE_RE = re.compile(r"[^。！？!?；;\n]+[。！？!?；;]?")
_YEAR_RE = re.compile(r"(?P<year>(?:18|19|20)\d{2})\s*年")
_BIRTH_RE = re.compile(
    r"(?P<year>(?:18|19|20)\d{2})\s*年\s*"
    r"(?P<month>\d{1,2})\s*月\s*(?P<day>\d{1,2})\s*日[^。！？!?；;]{0,30}(?:出生|生于|生於|生)"
    r"|(?:出生|生于|生於)[^。！？!?；;]{0,20}"
    r"(?P<year_after>(?:18|19|20)\d{2})\s*年\s*"
    r"(?P<month_after>\d{1,2})\s*月\s*(?P<day_after>\d{1,2})\s*日"
)
_SUBJECTIVE_MARKERS = (
    "我认为",
    "我認為",
    "我觉得",
    "我覺得",
    "最伟大",
    "最偉大",
    "最好",
    "无疑",
    "無疑",
    "应该",
    "應該",
    "或许",
    "或許",
    "令人",
    "感觉",
    "感覺",
)
_AWARD_NEGATIONS = (
    "未获奖",
    "未獲獎",
    "没有获奖",
    "沒有獲獎",
    "未能获奖",
    "未能獲獎",
    "拿不到",
    "无缘奖项",
    "無緣獎項",
)
_PERFORMANCE_NEGATIONS = (
    "不参加演唱会",
    "不參加演唱會",
    "未参加演唱会",
    "未參加演唱會",
    "没有参加演唱会",
    "沒有參加演唱會",
)
_PERFORMANCE_QUESTION_MARKERS = ("问：", "問：", "问:", "問:", "为什么", "為什麼")
_TABLE_NAV_MARKERS = ("查论编", "查論編", "音樂作品 創作作品", "音乐作品 创作作品")
_PERFORMANCE_CONTEXT_EXCLUSIONS = (
    "拍卖",
    "拍賣",
    "拍品",
    "服饰",
    "服飾",
    "服装",
    "服裝",
    "战袍",
    "戰袍",
    "策展",
    "捐赠",
    "捐贈",
)
_COLLABORATION_QUESTION_MARKERS = (
    "?",
    "？",
    "为什么",
    "為什麼",
    "为何",
    "為何",
    "怎样",
    "怎樣",
    "如何",
)


def is_subjective(text: str) -> bool:
    return any(marker in text for marker in _SUBJECTIVE_MARKERS)


class RuleClaimExtractor:
    def extract(
        self, chunk: EnrichedChunk, entities: list[CanonicalEntity]
    ) -> list[ClaimCandidate]:
        claims: list[ClaimCandidate] = []
        evidence_units = [
            (match.group(0).strip(), match.group(0).strip())
            for match in _SENTENCE_RE.finditer(chunk.content)
        ]
        evidence_units.extend(self._table_evidence(chunk))
        for context, evidence in dict.fromkeys(evidence_units):
            if not evidence or is_subjective(evidence):
                continue
            table_context = context != evidence
            present = self._present_entities(context, entities)
            extracted = [
                *self._birth_claims(context, present),
                *self._debut_claims(context, present),
                *self._release_claims(context, present, table_context=table_context),
                *self._first_album_claims(context, present),
                *self._track_claims(context, present),
                *self._genre_claims(context, present),
                *self._award_claims(context, present, table_context=table_context),
                *self._chart_claims(context, present),
                *self._performance_claims(context, present, table_context=table_context),
                *self._collaboration_claims(context, present, table_context=table_context),
                *self._signed_claims(context, present),
                *self._creative_role_claims(context, present),
            ]
            claims.extend(
                item.model_copy(update={"evidence_span": evidence})
                for item in extracted
            )
        return claims

    @staticmethod
    def _table_evidence(chunk: EnrichedChunk) -> list[tuple[str, str]]:
        table = chunk.table_data
        if (
            table is None
            or not table.headers
            or len(table.headers) > 20
            or any(not header.strip() or len(header) > 100 for header in table.headers)
        ):
            return []
        headers = [item.strip() for item in table.headers]
        header_text = " ".join(headers)
        if any(marker in header_text for marker in _TABLE_NAV_MARKERS):
            return []
        results: list[tuple[str, str]] = []
        for row in table.rows:
            cells = [str(item).strip() for item in row]
            if not any(cells):
                continue
            evidence = f"| {' | '.join(cells)} |"
            if evidence not in chunk.content:
                continue
            context = " | ".join(
                f"{header}: {cell}"
                for header, cell in zip(headers, cells, strict=False)
                if cell
            )
            results.append((context, evidence))
        return results

    @staticmethod
    def _present_entities(
        evidence: str, entities: list[CanonicalEntity]
    ) -> list[CanonicalEntity]:
        return [
            entity
            for entity in entities
            if any(alias.casefold() in evidence.casefold() for alias in entity.aliases)
        ]

    @staticmethod
    def _alias_positions(evidence: str, entity: CanonicalEntity) -> list[tuple[int, int]]:
        positions: set[tuple[int, int]] = set()
        for alias in sorted(entity.aliases, key=len, reverse=True):
            if not alias:
                continue
            pattern = re.escape(alias)
            if alias.isascii() and alias[0].isalnum() and alias[-1].isalnum():
                pattern = rf"(?<![A-Za-z0-9]){pattern}(?![A-Za-z0-9])"
            positions.update(
                match.span() for match in re.finditer(pattern, evidence, re.IGNORECASE)
            )
        return sorted(positions)

    @classmethod
    def _nearest_entity(
        cls,
        evidence: str,
        entities: list[CanonicalEntity],
        anchor: int,
        allowed_types: set[EntityType],
        *,
        max_distance: int,
        before_only: bool = False,
    ) -> CanonicalEntity | None:
        ranked: list[tuple[int, int, int, str, CanonicalEntity]] = []
        for entity in entities:
            if entity.entity_type not in allowed_types:
                continue
            for start, end in cls._alias_positions(evidence, entity):
                if before_only and end > anchor:
                    continue
                distance = anchor - end if end <= anchor else start - anchor
                if distance < 0:
                    distance = 0
                if distance <= max_distance:
                    ranked.append(
                        (distance, -(end - start), -end, entity.entity_id, entity)
                    )
        if not ranked:
            return None
        return min(ranked, key=lambda item: item[:4])[4]

    @staticmethod
    def _nearest_year(
        evidence: str, anchor: int, *, max_distance: int
    ) -> re.Match[str] | None:
        matches = list(_YEAR_RE.finditer(evidence))
        eligible = [
            match
            for match in matches
            if min(abs(anchor - match.start()), abs(anchor - match.end())) <= max_distance
        ]
        return min(
            eligible,
            key=lambda match: (
                min(abs(anchor - match.start()), abs(anchor - match.end())),
                match.start(),
            ),
            default=None,
        )

    @classmethod
    def _nearest_occurrence(
        cls, evidence: str, entity: CanonicalEntity, anchor: int
    ) -> tuple[int, int] | None:
        return min(
            cls._alias_positions(evidence, entity),
            key=lambda span: min(abs(anchor - span[0]), abs(anchor - span[1])),
            default=None,
        )

    @staticmethod
    def _first(
        entities: list[CanonicalEntity], entity_type: EntityType
    ) -> CanonicalEntity | None:
        return next(
            (item for item in entities if item.entity_type == entity_type), None
        )

    def _birth_claims(
        self, evidence: str, entities: list[CanonicalEntity]
    ) -> list[ClaimCandidate]:
        results: list[ClaimCandidate] = []
        seen: set[tuple[str, str]] = set()
        for match in _BIRTH_RE.finditer(evidence):
            marker_matches = list(
                re.finditer(
                    r"(?:得奖者|得獎者|获奖者|獲獎者)(?P<brief>简介|簡介)?\s*[:：]",
                    evidence[: match.start()],
                )
            )
            subject = None
            marker_scoped = False
            if marker_matches:
                marker = marker_matches[-1]
                if match.start() - marker.end() <= 180:
                    marker_scoped = True
                    if marker.group("brief"):
                        # A “winner biography” marker introduces the date but
                        # repeats the winner before the marker (e.g. winner:
                        # Person ... winner profile: 1966...).  Bind to that
                        # person instead of borrowing a name from an adjacent
                        # table row.
                        previous_winner = next(
                            (
                                item
                                for item in reversed(marker_matches[:-1])
                                if not item.group("brief")
                            ),
                            None,
                        )
                        if previous_winner is not None:
                            scoped = evidence[previous_winner.end() : marker.start()]
                            scoped_people = [
                                entity
                                for entity in entities
                                if entity.entity_type
                                in {EntityType.ARTIST, EntityType.PERSON}
                                and self._alias_positions(scoped, entity)
                            ]
                            first_people = [
                                (
                                    self._alias_positions(scoped, entity)[0][0],
                                    entity,
                                )
                                for entity in scoped_people
                            ]
                            subject = (
                                min(first_people, key=lambda item: item[0])[1]
                                if first_people
                                else None
                            )
                    else:
                        scoped = evidence[marker.end() : match.start()]
                        scoped_people = [
                            entity
                            for entity in entities
                            if entity.entity_type
                            in {EntityType.ARTIST, EntityType.PERSON}
                            and self._alias_positions(scoped, entity)
                        ]
                        first_people = [
                            (
                                self._alias_positions(scoped, entity)[0][0],
                                entity,
                            )
                            for entity in scoped_people
                        ]
                        subject = (
                            min(first_people, key=lambda item: item[0])[1]
                            if first_people
                            else None
                        )
            if subject is None and not marker_scoped:
                subject = self._nearest_entity(
                    evidence,
                    entities,
                    match.start(),
                    {EntityType.ARTIST, EntityType.PERSON},
                    max_distance=120,
                    before_only=True,
                )
            if subject is None:
                continue
            year = int(match.group("year") or match.group("year_after"))
            month = int(match.group("month") or match.group("month_after"))
            day = int(match.group("day") or match.group("day_after"))
            try:
                raw_date = f"{year:04d}-{month:02d}-{day:02d}"
                time = ClaimTime(date=raw_date, raw=match.group(0))
            except ValueError:
                continue
            identity = (subject.entity_id, raw_date)
            if identity in seen:
                continue
            seen.add(identity)
            results.append(
                ClaimCandidate(
                    statement=f"{subject.canonical_name}出生于{raw_date}",
                    subject_entity_id=subject.entity_id,
                    predicate=ClaimPredicate.BORN_ON,
                    object_value=raw_date,
                    time=time,
                    evidence_span=evidence,
                    confidence=0.99,
                )
            )
        return results

    def _debut_claims(
        self, evidence: str, entities: list[CanonicalEntity]
    ) -> list[ClaimCandidate]:
        if any(cue in evidence for cue in ("未出道", "没有出道", "沒有出道")):
            return []
        results: list[ClaimCandidate] = []
        for cue_match in re.finditer(r"出道|进入乐坛|進入樂壇", evidence):
            year_match = None
            for candidate in _YEAR_RE.finditer(evidence):
                if candidate.end() <= cue_match.start():
                    gap = cue_match.start() - candidate.end()
                    if gap <= 18:
                        year_match = candidate
                elif candidate.start() >= cue_match.end():
                    gap = candidate.start() - cue_match.end()
                    between = evidence[cue_match.end() : candidate.start()]
                    if gap <= 12 and between.strip() in {"于", "於", "在"}:
                        year_match = candidate
                if year_match is not None:
                    break
            artist = self._nearest_entity(
                evidence,
                entities,
                cue_match.start(),
                {EntityType.ARTIST},
                max_distance=45,
            )
            if artist is None or year_match is None:
                continue
            year = int(year_match.group("year"))
            results.append(
                ClaimCandidate(
                    statement=f"{artist.canonical_name}于{year}年出道",
                    subject_entity_id=artist.entity_id,
                    predicate=ClaimPredicate.DEBUTED,
                    object_value="出道",
                    time=ClaimTime(year=year, raw=year_match.group(0)),
                    evidence_span=evidence,
                    confidence=0.98,
                )
            )
        return results

    def _release_claims(
        self,
        evidence: str,
        entities: list[CanonicalEntity],
        *,
        table_context: bool = False,
    ) -> list[ClaimCandidate]:
        if any(
            cue in evidence
            for cue in (
                "未发行",
                "未發行",
                "没有发行",
                "沒有發行",
                "并未推出",
                "並未推出",
            )
        ):
            return []
        works = [
            item
            for item in entities
            if item.entity_type in {EntityType.ALBUM, EntityType.SONG}
        ]
        if table_context and _YEAR_RE.search(evidence) is None:
            return []
        cue_matches = list(re.finditer(r"(?:发行|發行)(?!公司|商|方)", evidence))
        if not cue_matches:
            return []
        results: list[ClaimCandidate] = []
        for work in works:
            relation: tuple[int, re.Match[str]] | None = None
            for work_start, work_end in self._alias_positions(evidence, work):
                if any(
                    other.entity_id != work.entity_id
                    and any(
                        other_start <= work_start
                        and work_end <= other_end
                        and (other_end - other_start) > (work_end - work_start)
                        for other_start, other_end in self._alias_positions(
                            evidence, other
                        )
                    )
                    for other in works
                ):
                    continue
                for cue_match in cue_matches:
                    if cue_match.end() <= work_start:
                        between = evidence[cue_match.end() : work_start]
                        relation_text = between.rstrip().removesuffix("《").removesuffix("〈")
                        if len(between) > 28 or re.search(
                            r"[。！？!?；;《〈》〉]", relation_text
                        ):
                            continue
                        distance = len(between)
                    elif work_end <= cue_match.start():
                        between = evidence[work_end : cue_match.start()]
                        if len(between) > 12 or re.search(r"[。！？!?；;》〉]", between):
                            continue
                        distance = len(between)
                    else:
                        distance = 0
                    candidate = (distance, cue_match)
                    if relation is None or candidate[0] < relation[0]:
                        relation = candidate
            if relation is None:
                continue
            cue_match = relation[1]
            artist = self._nearest_entity(
                evidence,
                entities,
                cue_match.start(),
                {EntityType.ARTIST},
                max_distance=45,
                before_only=True,
            )
            subject = artist or work
            object_entity_id = work.entity_id if artist else None
            object_value = None if artist else "发行"
            year_match = self._nearest_year(
                evidence, cue_match.start(), max_distance=30
            )
            time = (
                ClaimTime(
                    year=int(year_match.group("year")), raw=year_match.group(0)
                )
                if year_match
                else None
            )
            timing = f"于{time.year}年" if time and time.year else ""
            results.append(
                ClaimCandidate(
                    statement=(
                        f"{subject.canonical_name}{timing}发行{work.canonical_name}"
                        if artist
                        else f"{work.canonical_name}{timing}发行"
                    ),
                    subject_entity_id=subject.entity_id,
                    predicate=ClaimPredicate.RELEASED,
                    object_entity_id=object_entity_id,
                    object_value=object_value,
                    time=time,
                    evidence_span=evidence,
                    confidence=0.96,
                )
            )
        return results

    def _first_album_claims(
        self, evidence: str, entities: list[CanonicalEntity]
    ) -> list[ClaimCandidate]:
        if not any(
            cue in evidence for cue in ("首张专辑", "首張專輯", "首张唱片", "首張唱片")
        ):
            return []
        artist = self._first(entities, EntityType.ARTIST)
        album = self._first(entities, EntityType.ALBUM)
        if artist is None or album is None:
            return []
        return [
            ClaimCandidate(
                statement=f"{album.canonical_name}是{artist.canonical_name}的首张专辑",
                subject_entity_id=album.entity_id,
                predicate=ClaimPredicate.FIRST_ALBUM_OF,
                object_entity_id=artist.entity_id,
                evidence_span=evidence,
                confidence=0.97,
            )
        ]

    def _track_claims(
        self, evidence: str, entities: list[CanonicalEntity]
    ) -> list[ClaimCandidate]:
        if any(
            cue in evidence
            for cue in (
                "未收录",
                "未收錄",
                "不包含",
                "不收录",
                "不收錄",
                "DVD",
                "音乐影片",
                "音樂影片",
                "电影（加长）版",
                "電影（加長）版",
            )
        ):
            return []
        cue_matches = list(re.finditer(r"收录|收錄|包含", evidence))
        if not cue_matches:
            return []
        albums = [item for item in entities if item.entity_type == EntityType.ALBUM]
        songs = [item for item in entities if item.entity_type == EntityType.SONG]
        results: list[ClaimCandidate] = []
        for cue_match in cue_matches:
            album = self._nearest_entity(
                evidence,
                albums,
                cue_match.start(),
                {EntityType.ALBUM},
                max_distance=35,
            )
            song = self._nearest_entity(
                evidence,
                songs,
                cue_match.start(),
                {EntityType.SONG},
                max_distance=35,
            )
            if album is None or song is None:
                continue
            album_span = self._nearest_occurrence(evidence, album, cue_match.start())
            song_span = self._nearest_occurrence(evidence, song, cue_match.start())
            if album_span is None or song_span is None:
                continue
            relation_start = min(album_span[0], song_span[0], cue_match.start())
            relation_end = max(album_span[1], song_span[1], cue_match.end())
            if relation_end - relation_start > 75:
                continue
            results.append(
                ClaimCandidate(
                    statement=f"{album.canonical_name}收录歌曲{song.canonical_name}",
                    subject_entity_id=album.entity_id,
                    predicate=ClaimPredicate.INCLUDES_TRACK,
                    object_entity_id=song.entity_id,
                    evidence_span=evidence,
                    confidence=0.97,
                )
            )
        return list(
            {
                (item.subject_entity_id, item.object_entity_id): item
                for item in results
            }.values()
        )

    def _genre_claims(
        self, evidence: str, entities: list[CanonicalEntity]
    ) -> list[ClaimCandidate]:
        if any(
            cue in evidence
            for cue in ("错误想象", "錯誤想像", "并非", "並非", "不属于", "不屬於")
        ):
            return []
        fusion = any(cue in evidence for cue in ("融合", "融入", "结合", "結合"))
        genre_statement = any(
            cue in evidence for cue in ("风格", "風格", "曲风", "曲風", "属于", "屬於")
        )
        if not fusion and not genre_statement:
            return []
        genres = [item for item in entities if item.entity_type == EntityType.GENRE]
        subjects = [
            item
            for item in entities
            if item.entity_type
            in {EntityType.SONG, EntityType.ALBUM, EntityType.ARTIST}
        ]
        cue_match = re.search(r"融合|融入|结合|結合|风格|風格|曲风|曲風|属于|屬於", evidence)
        if not subjects or cue_match is None:
            return []
        subject = self._nearest_entity(
            evidence,
            subjects,
            cue_match.start(),
            {EntityType.SONG, EntityType.ALBUM, EntityType.ARTIST},
            max_distance=55,
            before_only=True,
        )
        if subject is None:
            return []
        predicate = (
            ClaimPredicate.CONTAINS_MUSIC_ELEMENT
            if fusion
            else ClaimPredicate.HAS_GENRE
        )
        verb = "融合" if fusion else "具有"
        return [
            ClaimCandidate(
                statement=f"{subject.canonical_name}{verb}{genre.canonical_name}风格",
                subject_entity_id=subject.entity_id,
                predicate=predicate,
                object_entity_id=genre.entity_id,
                evidence_span=evidence,
                confidence=0.94,
            )
            for genre in genres
        ]

    def _award_claims(
        self,
        evidence: str,
        entities: list[CanonicalEntity],
        *,
        table_context: bool = False,
    ) -> list[ClaimCandidate]:
        if table_context and not self._table_has_award_result(evidence):
            return []
        if any(cue in evidence for cue in _AWARD_NEGATIONS) or re.search(
            r"没有.{0,4}(?:获奖|獲獎)", evidence
        ):
            return []
        if not any(
            cue in evidence for cue in ("获奖", "獲獎", "获得", "獲得", "荣获", "榮獲")
        ):
            return []
        win_matches = list(re.finditer(r"获奖|獲獎|获得|獲得|荣获|榮獲", evidence))
        awards = [item for item in entities if item.entity_type == EntityType.AWARD]
        if not awards or not win_matches:
            return []
        results: list[ClaimCandidate] = []
        for award in awards:
            award_positions = self._alias_positions(evidence, award)
            if not award_positions:
                continue
            award_span = min(
                award_positions,
                key=lambda span: min(
                    abs(span[0] - match.start()) for match in win_matches
                ),
            )
            award_anchor = award_span[0]
            win = min(
                win_matches,
                key=lambda item: min(
                    abs(item.start() - award_anchor), abs(item.end() - award_anchor)
                ),
            )
            if min(abs(win.start() - award_anchor), abs(win.end() - award_anchor)) > 45:
                continue
            if not table_context:
                if win.end() <= award_span[0]:
                    between = evidence[win.end() : award_span[0]]
                elif award_span[1] <= win.start():
                    between = evidence[award_span[1] : win.start()]
                else:
                    between = ""
                # A clause boundary or an explicit contrast means that the
                # nearest award belongs to another winner (for example, the
                # album award mentioned after Zhong Xingmin's arrangement
                # award in the same paragraph).
                if re.search(r"[。！？!?；;]", between) or re.search(
                    r"(?:而|但|然而|另外|另|同时|同時|这张专辑|這張專輯|该专辑|該專輯)",
                    between,
                ):
                    continue
            subject = self._nearest_entity(
                evidence,
                entities,
                win.start(),
                {EntityType.ARTIST, EntityType.PERSON},
                max_distance=65,
                before_only=True,
            )
            if subject is None:
                subject = self._nearest_entity(
                    evidence,
                    entities,
                    win.start(),
                    {EntityType.SONG, EntityType.ALBUM},
                    max_distance=45,
                )
            if subject is None:
                continue
            results.append(
                ClaimCandidate(
                    statement=f"{subject.canonical_name}获得{award.canonical_name}",
                    subject_entity_id=subject.entity_id,
                    predicate=ClaimPredicate.WON_AWARD,
                    object_entity_id=award.entity_id,
                    evidence_span=evidence,
                    confidence=0.96,
                )
            )
        return results

    @staticmethod
    def _table_has_award_result(evidence: str) -> bool:
        """Require an explicit result cell, not a generic column heading.

        Table contexts include header labels such as ``获奖专辑或歌曲``.
        Treating that label as a result produced claims from rows that only
        listed nominees/categories.  A result must therefore be a standalone
        cell value (or the value after a ``:`` separator).
        """

        result_cues = {"获奖", "獲獎", "得奖", "得獎"}
        for part in evidence.split("|"):
            value = part.rsplit(":", 1)[-1].strip()
            if value in result_cues:
                return True
        return False

    def _chart_claims(
        self, evidence: str, entities: list[CanonicalEntity]
    ) -> list[ClaimCandidate]:
        if any(
            cue in evidence
            for cue in ("福布斯", "名人榜", "富豪榜", "超话", "超話", "新浪", "微博")
        ):
            return []
        position = re.search(r"(?:第\s*\d+\s*名|冠军|冠軍|榜首|排名\s*\d+)", evidence)
        if position is None or not any(
            cue in evidence for cue in ("榜", "排名", "排行")
        ):
            return []
        subject = self._nearest_entity(
            evidence,
            entities,
            position.start(),
            {EntityType.SONG, EntityType.ALBUM, EntityType.ARTIST},
            max_distance=55,
        )
        if subject is None:
            return []
        return [
            ClaimCandidate(
                statement=f"{subject.canonical_name}榜单成绩为{position.group(0)}",
                subject_entity_id=subject.entity_id,
                predicate=ClaimPredicate.CHART_POSITION,
                object_value=position.group(0),
                evidence_span=evidence,
                confidence=0.94,
            )
        ]

    def _performance_claims(
        self,
        evidence: str,
        entities: list[CanonicalEntity],
        *,
        table_context: bool = False,
    ) -> list[ClaimCandidate]:
        if any(cue in evidence for cue in _PERFORMANCE_NEGATIONS):
            return []
        if any(cue in evidence for cue in _PERFORMANCE_QUESTION_MARKERS):
            return []
        if any(cue in evidence for cue in _PERFORMANCE_CONTEXT_EXCLUSIONS):
            return []
        cue = next(
            (
                item
                for item in ("演唱会", "演唱會", "巡回演出", "巡迴演出", "登台演出")
                if item in evidence
            ),
            None,
        )
        if cue is None:
            return []
        cue_spans = [
            match.span()
            for item in ("演唱会", "演唱會", "巡回演出", "巡迴演出", "登台演出")
            for match in re.finditer(re.escape(item), evidence)
        ]
        cue_position = evidence.find(cue)
        action_matches = list(
            re.finditer(
                r"举办|舉辦|开展|開展|开唱|開唱|演唱|巡演|登台|作为嘉宾|作為嘉賓|嘉宾|嘉賓",
                evidence,
            )
        )
        action_matches = [
            item
            for item in action_matches
            if not any(
                item.start() < cue_end and item.end() > cue_start
                for cue_start, cue_end in cue_spans
            )
        ]
        if not action_matches:
            return []
        action_matches.sort(key=lambda item: abs(item.start() - cue_position))
        artist = None
        action_match = None
        artist_span = None
        for candidate in action_matches:
            candidate_artist = self._nearest_entity(
                evidence,
                entities,
                candidate.start(),
                {EntityType.ARTIST},
                max_distance=35,
                before_only=True,
            )
            if candidate_artist is None:
                continue
            candidate_span = self._nearest_occurrence(
                evidence, candidate_artist, candidate.start()
            )
            if candidate_span is None:
                continue
            prefix = evidence[max(0, candidate_span[0] - 18) : candidate_span[0]]
            suffix = evidence[candidate_span[1] : min(len(evidence), candidate_span[1] + 12)]
            # Do not turn a comparison list ("继王菲之后...") into a claim
            # that the compared artist performed at the current concert.
            if re.search(r"(?:继|繼)[^。！？!?；;]{0,12}$", prefix) or re.match(
                r"(?:之后|之後)", suffix
            ):
                continue
            artist = candidate_artist
            action_match = candidate
            artist_span = candidate_span
            break
        if artist is None or action_match is None or artist_span is None:
            return []
        return [
            ClaimCandidate(
                statement=f"{artist.canonical_name}参加{cue}",
                subject_entity_id=artist.entity_id,
                predicate=ClaimPredicate.PERFORMED_AT,
                object_value=cue,
                evidence_span=evidence,
                confidence=0.92,
            )
        ]

    def _collaboration_claims(
        self,
        evidence: str,
        entities: list[CanonicalEntity],
        *,
        table_context: bool = False,
    ) -> list[ClaimCandidate]:
        if any(marker in evidence for marker in _COLLABORATION_QUESTION_MARKERS):
            return []
        if not any(cue in evidence for cue in ("合作", "合唱", "共同创作", "共同創作")):
            return []
        if table_context and not any(cue in evidence for cue in ("合作", "合唱")):
            return []
        people = [
            item
            for item in entities
            if item.entity_type in {EntityType.ARTIST, EntityType.PERSON}
        ]
        if len(people) < 2:
            return []
        cue_matches = list(re.finditer(r"合作|合唱|共同创作|共同創作", evidence))
        if not cue_matches:
            return []
        cue = min(cue_matches, key=lambda item: item.start())
        ranked = sorted(
            (
                min(abs(span[0] - cue.start()), abs(span[1] - cue.start())),
                entity.entity_id,
                entity,
            )
            for entity in people
            for span in self._alias_positions(evidence, entity)
        )
        nearby = [item[2] for item in ranked if item[0] <= 45]
        if len({item.entity_id for item in nearby}) < 2:
            return []
        subject, other = nearby[0], next(
            item for item in nearby[1:] if item.entity_id != nearby[0].entity_id
        )
        return [
            ClaimCandidate(
                statement=f"{subject.canonical_name}与{other.canonical_name}合作",
                subject_entity_id=subject.entity_id,
                predicate=ClaimPredicate.COLLABORATED_WITH,
                object_entity_id=other.entity_id,
                evidence_span=evidence,
                confidence=0.93,
            )
        ]

    def _signed_claims(
        self, evidence: str, entities: list[CanonicalEntity]
    ) -> list[ClaimCandidate]:
        if not any(cue in evidence for cue in ("签约", "簽約", "加盟")):
            return []
        artist = self._first(entities, EntityType.ARTIST)
        label = self._first(entities, EntityType.LABEL)
        if artist is None or label is None:
            return []
        return [
            ClaimCandidate(
                statement=f"{artist.canonical_name}签约{label.canonical_name}",
                subject_entity_id=artist.entity_id,
                predicate=ClaimPredicate.SIGNED_TO,
                object_entity_id=label.entity_id,
                evidence_span=evidence,
                confidence=0.96,
            )
        ]

    def _creative_role_claims(
        self, evidence: str, entities: list[CanonicalEntity]
    ) -> list[ClaimCandidate]:
        people = [
            item
            for item in entities
            if item.entity_type in {EntityType.ARTIST, EntityType.PERSON}
        ]
        role_specs = (
            (("作词", "作詞", "填词", "填詞"), ClaimPredicate.WROTE_LYRICS_FOR, "作词"),
            (("作曲",), ClaimPredicate.COMPOSED_FOR, "作曲"),
            (("编曲", "編曲"), ClaimPredicate.ARRANGED, "编曲"),
            (("制作人", "製作人"), ClaimPredicate.PRODUCED, "制作"),
        )
        results: list[ClaimCandidate] = []
        works = [
            item
            for item in entities
            if item.entity_type in {EntityType.SONG, EntityType.ALBUM}
        ]
        for cues, predicate, verb in role_specs:
            cue_pattern = "|".join(re.escape(item) for item in cues)
            for cue_match in re.finditer(cue_pattern, evidence):
                nearby = evidence[max(0, cue_match.start() - 4) : cue_match.end() + 4]
                if "奖" in nearby or "獎" in nearby:
                    continue
                suffix = evidence[cue_match.end() : cue_match.end() + 12]
                cue_is_assignment = bool(re.match(r"\s*[:：]", suffix))
                if cue_is_assignment:
                    person = self._nearest_entity(
                        evidence,
                        people,
                        cue_match.end(),
                        {EntityType.ARTIST, EntityType.PERSON},
                        max_distance=10,
                    )
                    if person is not None:
                        person_span = self._nearest_occurrence(
                            evidence, person, cue_match.end()
                        )
                        if person_span is None or person_span[0] < cue_match.end():
                            person = None
                else:
                    person = self._nearest_entity(
                        evidence,
                        people,
                        cue_match.start(),
                        {EntityType.ARTIST, EntityType.PERSON},
                        max_distance=12,
                        before_only=True,
                    )
                if person is None:
                    continue
                work = self._nearest_entity(
                    evidence,
                    works,
                    cue_match.start(),
                    {EntityType.SONG, EntityType.ALBUM},
                    max_distance=75,
                )
                if work is None:
                    continue
                results.append(
                    ClaimCandidate(
                        statement=f"{person.canonical_name}为{work.canonical_name}{verb}",
                        subject_entity_id=person.entity_id,
                        predicate=predicate,
                        object_entity_id=work.entity_id,
                        evidence_span=evidence,
                        confidence=0.95,
                    )
                )
        return list(
            {
                (item.subject_entity_id, item.predicate, item.object_entity_id): item
                for item in results
            }.values()
        )
