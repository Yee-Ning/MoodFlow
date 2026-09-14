"""Conservative deterministic extraction of hard retrieval constraints."""

import re

from app.rag.enrichment.models import ContentType
from app.rag.query_understanding.models import (
    QueryConstraints,
    ResolvedQueryEntity,
)


_YEAR = r"(?:1[89]\d{2}|20\d{2}|21\d{2})"
_YEAR_RANGE = re.compile(
    rf"(?P<start>{_YEAR})\s*年?\s*(?:到|至|—|–|-|~|～)\s*(?P<end>{_YEAR})\s*年?"
)
_SINGLE_YEAR = re.compile(rf"(?<!\d)(?P<year>{_YEAR})\s*年?(?!\d)")
_NEGATIVE = re.compile(
    r"(?:不要|排除|不包括|不含)\s*(?P<term>[^，。；;！？\s]{1,40})"
)

_DOCUMENT_LANGUAGE_CUES = {
    "中文资料": "zh-CN",
    "中文資料": "zh-TW",
    "繁体资料": "zh-TW",
    "繁體資料": "zh-TW",
    "英文资料": "en",
    "英文資料": "en",
}
_EXPLICIT_CONTENT_TYPE_CUES = {
    "采访资料": ContentType.INTERVIEW,
    "訪談資料": ContentType.INTERVIEW,
    "新闻资料": ContentType.NEWS,
    "新聞資料": ContentType.NEWS,
    "乐评资料": ContentType.REVIEW,
    "樂評資料": ContentType.REVIEW,
}


def extract_query_constraints(
    query: str, entities: list[ResolvedQueryEntity]
) -> tuple[QueryConstraints, list[str]]:
    warnings: list[str] = []
    entity_ids = [
        item.knowledge_entity_id
        for item in entities
        if item.knowledge_entity_id is not None
        and item.status.value == "resolved"
        and item.confidence >= 0.9
    ]
    entity_types = [
        item.entity_type
        for item in entities
        if item.entity_type is not None
        and item.status.value == "resolved"
        and item.confidence >= 0.9
    ]
    range_match = _YEAR_RANGE.search(query)
    start_year: int | None = None
    end_year: int | None = None
    if range_match is not None:
        start_year = int(range_match.group("start"))
        end_year = int(range_match.group("end"))
    else:
        years = list(
            dict.fromkeys(
                int(item.group("year")) for item in _SINGLE_YEAR.finditer(query)
            )
        )
        if len(years) == 1:
            start_year = end_year = years[0]
        elif len(years) > 1:
            warnings.append("multiple_discrete_years_kept_as_query_terms")

    languages = [
        language for cue, language in _DOCUMENT_LANGUAGE_CUES.items() if cue in query
    ]
    content_types = [
        content_type
        for cue, content_type in _EXPLICIT_CONTENT_TYPE_CUES.items()
        if cue in query
    ]
    negative_terms = [
        match.group("term").strip() for match in _NEGATIVE.finditer(query)
    ]
    return (
        QueryConstraints(
            entity_ids=list(dict.fromkeys(entity_ids)),
            entity_types=list(dict.fromkeys(entity_types)),
            start_year=start_year,
            end_year=end_year,
            languages=list(dict.fromkeys(languages)),
            content_types=list(dict.fromkeys(content_types)),
            negative_terms=list(dict.fromkeys(negative_terms)),
        ),
        warnings,
    )
