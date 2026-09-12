"""Deterministic event-time extraction from chunk content only."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from .models import TemporalMetadata


_YEAR = r"(?:1[5-9]\d{2}|20\d{2}|21\d{2})"
_CHINESE_DATE_RE = re.compile(
    rf"(?<!\d)(?P<year>{_YEAR})年\s*(?P<month>1[0-2]|0?[1-9])月\s*"
    r"(?P<day>3[01]|[12]\d|0?[1-9])日"
)
_ISO_DATE_RE = re.compile(
    rf"(?<!\d)(?P<year>{_YEAR})[-/.](?P<month>1[0-2]|0?[1-9])"
    r"[-/.](?P<day>3[01]|[12]\d|0?[1-9])(?!\d)"
)
_RANGE_RE = re.compile(
    rf"(?<!\d)(?P<start>{_YEAR})\s*年?\s*(?:-|–|—|~|～|至|到)\s*"
    rf"(?P<end>{_YEAR})\s*年?(?!\d)"
)
_YEAR_RE = re.compile(rf"(?<!\d)(?P<year>{_YEAR})\s*年?(?!\d)")
_NON_EVENT_YEAR_PREFIX_RE = re.compile(
    r"(?:©|copyright|版權所有|版权所有|著作權|著作权)\s*[:：]?\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class _Mention:
    start: int
    end: int
    raw: str
    years: tuple[int, ...]
    exact_date: date | None = None
    year_range: tuple[int, int] | None = None


class TemporalExtractor:
    def extract(self, content: str) -> TemporalMetadata:
        mentions: list[_Mention] = []
        occupied: list[tuple[int, int]] = []
        for pattern, kind in (
            (_CHINESE_DATE_RE, "date"),
            (_ISO_DATE_RE, "date"),
            (_RANGE_RE, "range"),
        ):
            for match in pattern.finditer(content):
                if self._overlaps(match.span(), occupied):
                    continue
                try:
                    if kind == "date":
                        exact = date(
                            int(match.group("year")),
                            int(match.group("month")),
                            int(match.group("day")),
                        )
                        mention = _Mention(
                            *match.span(),
                            match.group(0),
                            (exact.year,),
                            exact_date=exact,
                        )
                    else:
                        start_year = int(match.group("start"))
                        end_year = int(match.group("end"))
                        if start_year > end_year:
                            continue
                        mention = _Mention(
                            *match.span(),
                            match.group(0),
                            (start_year, end_year),
                            year_range=(start_year, end_year),
                        )
                except ValueError:
                    continue
                mentions.append(mention)
                occupied.append(match.span())
        for match in _YEAR_RE.finditer(content):
            if self._overlaps(match.span(), occupied):
                continue
            if self._has_non_event_prefix(content, match.start()):
                continue
            year = int(match.group("year"))
            mentions.append(_Mention(*match.span(), match.group(0), (year,)))
        mentions.sort(key=lambda item: item.start)

        unique_years = sorted({year for item in mentions for year in item.years})
        exact_dates = [item.exact_date for item in mentions if item.exact_date]
        ranges = [item.year_range for item in mentions if item.year_range]
        selected_range = ranges[0] if len(ranges) == 1 else None
        return TemporalMetadata(
            year=unique_years[0] if len(unique_years) == 1 else None,
            date=exact_dates[0] if len(exact_dates) == 1 else None,
            start_year=selected_range[0] if selected_range else None,
            end_year=selected_range[1] if selected_range else None,
            raw_mentions=[item.raw for item in mentions],
        )

    @staticmethod
    def _overlaps(span: tuple[int, int], occupied: list[tuple[int, int]]) -> bool:
        return any(span[0] < end and start < span[1] for start, end in occupied)

    @staticmethod
    def _has_non_event_prefix(content: str, start: int) -> bool:
        return bool(
            _NON_EVENT_YEAR_PREFIX_RE.search(content[max(0, start - 30) : start])
        )
