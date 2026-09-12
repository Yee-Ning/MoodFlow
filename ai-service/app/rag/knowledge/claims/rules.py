"""Conservative deterministic extraction for common music facts."""

from __future__ import annotations

import re

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
        self, content: str, entities: list[CanonicalEntity]
    ) -> list[ClaimCandidate]:
        claims: list[ClaimCandidate] = []
        for match in _SENTENCE_RE.finditer(content):
            evidence = match.group(0).strip()
            if not evidence or is_subjective(evidence):
                continue
            present = self._present_entities(evidence, entities)
            claims.extend(self._birth_claims(evidence, present))
            claims.extend(self._debut_claims(evidence, present))
            claims.extend(self._release_claims(evidence, present))
            claims.extend(self._first_album_claims(evidence, present))
            claims.extend(self._track_claims(evidence, present))
            claims.extend(self._genre_claims(evidence, present))
        return claims

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
            cue in evidence
            for cue in ("首张专辑", "首張專輯", "首张唱片", "首張唱片")
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
        if not any(cue in evidence for cue in ("融合", "融入", "结合", "結合")):
            return []
        genres = [item for item in entities if item.entity_type == EntityType.GENRE]
        subjects = [
            item
            for item in entities
            if item.entity_type in {EntityType.SONG, EntityType.ALBUM, EntityType.ARTIST}
        ]
        if not subjects:
            return []
        subject = subjects[0]
        return [
            ClaimCandidate(
                statement=f"{subject.canonical_name}融合{genre.canonical_name}元素",
                subject_entity_id=subject.entity_id,
                predicate=ClaimPredicate.CONTAINS_MUSIC_ELEMENT,
                object_entity_id=genre.entity_id,
                evidence_span=evidence,
                confidence=0.94,
            )
            for genre in genres
        ]
