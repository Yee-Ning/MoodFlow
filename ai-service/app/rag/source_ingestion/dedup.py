"""Candidate identity deduplication without inspecting document bodies."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .models import CandidateSource
from .v2_models import DiscoveredCandidate, DuplicateDecision


_TRACKING_QUERY_KEYS = frozenset(
    {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "source"}
)


def normalize_url(value: str) -> str:
    parsed = urlsplit(value)
    host = (parsed.hostname or "").casefold()
    if host.startswith("www."):
        host = host[4:]
    port = parsed.port
    if port is not None and not (
        (parsed.scheme.casefold() == "https" and port == 443)
        or (parsed.scheme.casefold() == "http" and port == 80)
    ):
        host = f"{host}:{port}"
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if path != "/":
        path = path.rstrip("/")
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_")
        and key.casefold() not in _TRACKING_QUERY_KEYS
    ]
    return urlunsplit(
        (parsed.scheme.casefold(), host, path, urlencode(sorted(query)), "")
    )


def _normalize_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


class CandidateDeduplicator:
    def __init__(self, existing: Iterable[CandidateSource]) -> None:
        self._urls: dict[str, str] = {}
        self._titles: dict[tuple[str, str], str] = {}
        self._external_ids: dict[str, str] = {}
        for candidate in existing:
            self.register(candidate)

    def register(self, candidate: CandidateSource) -> None:
        source_id = candidate.source_id
        self._urls.setdefault(normalize_url(str(candidate.url)), source_id)
        canonical = getattr(candidate, "canonical_url", None)
        if canonical is not None:
            self._urls.setdefault(normalize_url(str(canonical)), source_id)
        self._titles.setdefault(
            (_normalize_text(candidate.publisher), _normalize_text(candidate.title)),
            source_id,
        )
        external_id = getattr(candidate, "external_id", None)
        if external_id:
            self._external_ids.setdefault(_normalize_text(str(external_id)), source_id)

    def register_url(self, source_id: str, url: str) -> None:
        self._urls.setdefault(normalize_url(url), source_id)

    def check(self, candidate: DiscoveredCandidate) -> DuplicateDecision:
        source_id = candidate.source_id
        normalized_candidate_url = normalize_url(str(candidate.url))
        duplicate_of = self._urls.get(normalized_candidate_url)
        if duplicate_of is not None:
            return DuplicateDecision(
                source_id=source_id,
                is_duplicate=True,
                reason="normalized-url",
                duplicate_of=duplicate_of,
            )
        if candidate.canonical_url is not None:
            duplicate_of = self._urls.get(normalize_url(str(candidate.canonical_url)))
            if duplicate_of is not None:
                return DuplicateDecision(
                    source_id=source_id,
                    is_duplicate=True,
                    reason="canonical-url",
                    duplicate_of=duplicate_of,
                )
        duplicate_of = self._titles.get(
            (_normalize_text(candidate.publisher), _normalize_text(candidate.title))
        )
        if duplicate_of is not None:
            return DuplicateDecision(
                source_id=source_id,
                is_duplicate=True,
                reason="publisher-title",
                duplicate_of=duplicate_of,
            )
        if candidate.external_id:
            duplicate_of = self._external_ids.get(
                _normalize_text(candidate.external_id)
            )
            if duplicate_of is not None:
                return DuplicateDecision(
                    source_id=source_id,
                    is_duplicate=True,
                    reason="external-id",
                    duplicate_of=duplicate_of,
                )
        return DuplicateDecision(source_id=source_id, is_duplicate=False)
