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
    r"(?P<month>\d{1,2})\s*月\s*(?P<day>\d{1,2})\s*日[^。！？!?；;]{0,30}(?:出生|生于|生於)"
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
            present = self._present_entities(context, entities)
            extracted = [
                *self._birth_claims(context, present),
                *self._debut_claims(context, present),
                *self._release_claims(context, present),
                *self._first_album_claims(context, present),
                *self._track_claims(context, present),
                *self._genre_claims(context, present),
                *self._award_claims(context, present),
                *self._chart_claims(context, present),
                *self._performance_claims(context, present),
                *self._collaboration_claims(context, present),
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
    def _first(
        entities: list[CanonicalEntity], entity_type: EntityType
    ) -> CanonicalEntity | None:
        return next(
            (item for item in entities if item.entity_type == entity_type), None
        )

    def _birth_claims(
        self, evidence: str, entities: list[CanonicalEntity]
    ) -> list[ClaimCandidate]:
        artist = self._first(entities, EntityType.ARTIST)
        match = _BIRTH_RE.search(evidence)
        if artist is None or match is None:
            return []
        year = int(match.group("year") or match.group("year_after"))
        month = int(match.group("month") or match.group("month_after"))
        day = int(match.group("day") or match.group("day_after"))
        try:
            raw_date = f"{year:04d}-{month:02d}-{day:02d}"
            time = ClaimTime(date=raw_date, raw=match.group(0))
        except ValueError:
            return []
        return [
            ClaimCandidate(
                statement=f"{artist.canonical_name}出生于{raw_date}",
                subject_entity_id=artist.entity_id,
                predicate=ClaimPredicate.BORN_ON,
                object_value=raw_date,
                time=time,
                evidence_span=evidence,
                confidence=0.99,
            )
        ]

    def _debut_claims(
        self, evidence: str, entities: list[CanonicalEntity]
    ) -> list[ClaimCandidate]:
        artist = self._first(entities, EntityType.ARTIST)
        year_match = _YEAR_RE.search(evidence)
        if (
            artist is None
            or year_match is None
            or not any(cue in evidence for cue in ("出道", "进入乐坛", "進入樂壇"))
        ):
            return []
        year = int(year_match.group("year"))
        return [
            ClaimCandidate(
                statement=f"{artist.canonical_name}于{year}年出道",
                subject_entity_id=artist.entity_id,
                predicate=ClaimPredicate.DEBUTED,
                object_value="出道",
                time=ClaimTime(year=year, raw=year_match.group(0)),
                evidence_span=evidence,
                confidence=0.98,
            )
        ]

    def _release_claims(
        self, evidence: str, entities: list[CanonicalEntity]
    ) -> list[ClaimCandidate]:
        if not any(cue in evidence for cue in ("发行", "發行", "发布", "發布", "推出")):
            return []
        year_match = _YEAR_RE.search(evidence)
        time = (
            ClaimTime(year=int(year_match.group("year")), raw=year_match.group(0))
            if year_match
            else None
        )
        artist = self._first(entities, EntityType.ARTIST)
        works = [
            item
            for item in entities
            if item.entity_type in {EntityType.ALBUM, EntityType.SONG}
        ]
        results: list[ClaimCandidate] = []
        for work in works:
            subject = artist or work
            object_entity_id = work.entity_id if artist else None
            object_value = None if artist else "发行"
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
        if not any(cue in evidence for cue in ("收录", "收錄", "包含")):
            return []
        album = self._first(entities, EntityType.ALBUM)
        songs = [item for item in entities if item.entity_type == EntityType.SONG]
        if album is None:
            return []
        return [
            ClaimCandidate(
                statement=f"{album.canonical_name}收录歌曲{song.canonical_name}",
                subject_entity_id=album.entity_id,
                predicate=ClaimPredicate.INCLUDES_TRACK,
                object_entity_id=song.entity_id,
                evidence_span=evidence,
                confidence=0.97,
            )
            for song in songs
        ]

    def _genre_claims(
        self, evidence: str, entities: list[CanonicalEntity]
    ) -> list[ClaimCandidate]:
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
        if not subjects:
            return []
        subject = subjects[0]
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
        self, evidence: str, entities: list[CanonicalEntity]
    ) -> list[ClaimCandidate]:
        if not any(
            cue in evidence for cue in ("获奖", "獲獎", "获得", "獲得", "荣获", "榮獲")
        ):
            return []
        award = self._first(entities, EntityType.AWARD)
        subject = next(
            (
                item
                for item in entities
                if item.entity_type
                in {EntityType.ALBUM, EntityType.SONG, EntityType.ARTIST}
            ),
            None,
        )
        if award is None or subject is None:
            return []
        return [
            ClaimCandidate(
                statement=f"{subject.canonical_name}获得{award.canonical_name}",
                subject_entity_id=subject.entity_id,
                predicate=ClaimPredicate.WON_AWARD,
                object_entity_id=award.entity_id,
                evidence_span=evidence,
                confidence=0.96,
            )
        ]

    def _chart_claims(
        self, evidence: str, entities: list[CanonicalEntity]
    ) -> list[ClaimCandidate]:
        position = re.search(r"(?:第\s*\d+\s*名|冠军|冠軍|榜首|排名\s*\d+)", evidence)
        if position is None or not any(
            cue in evidence for cue in ("榜", "排名", "排行")
        ):
            return []
        subject = next(
            (
                item
                for item in entities
                if item.entity_type
                in {EntityType.SONG, EntityType.ALBUM, EntityType.ARTIST}
            ),
            None,
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
        self, evidence: str, entities: list[CanonicalEntity]
    ) -> list[ClaimCandidate]:
        cue = next(
            (
                item
                for item in ("演唱会", "演唱會", "巡回演出", "巡迴演出", "登台演出")
                if item in evidence
            ),
            None,
        )
        artist = self._first(entities, EntityType.ARTIST)
        if cue is None or artist is None:
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
        self, evidence: str, entities: list[CanonicalEntity]
    ) -> list[ClaimCandidate]:
        if not any(cue in evidence for cue in ("合作", "合唱", "共同创作", "共同創作")):
            return []
        people = [
            item
            for item in entities
            if item.entity_type in {EntityType.ARTIST, EntityType.PERSON}
        ]
        if len(people) < 2:
            return []
        subject, other = people[0], people[1]
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
        work = next(
            (
                item
                for item in entities
                if item.entity_type in {EntityType.SONG, EntityType.ALBUM}
            ),
            None,
        )
        if work is None:
            return []
        people = [
            item
            for item in entities
            if item.entity_type in {EntityType.ARTIST, EntityType.PERSON}
        ]
        role_specs = (
            (("作词", "作詞", "填词", "填詞"), ClaimPredicate.WROTE_LYRICS_FOR, "作词"),
            (("作曲",), ClaimPredicate.COMPOSED_FOR, "作曲"),
            (("编曲", "編曲"), ClaimPredicate.ARRANGED, "编曲"),
            (("制作", "製作", "制作人", "製作人"), ClaimPredicate.PRODUCED, "制作"),
        )
        results: list[ClaimCandidate] = []
        for person in people:
            aliases = [re.escape(alias) for alias in person.aliases]
            if not aliases:
                continue
            name = "(?:" + "|".join(aliases) + ")"
            for cues, predicate, verb in role_specs:
                cue = "(?:" + "|".join(re.escape(item) for item in cues) + ")"
                if not re.search(
                    rf"(?:{name}.{{0,12}}{cue}|{cue}\s*[:：]?\s*.{{0,6}}{name})",
                    evidence,
                    re.IGNORECASE,
                ):
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
        return results
