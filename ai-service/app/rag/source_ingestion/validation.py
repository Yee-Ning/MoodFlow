"""Pre-persistence validation for raw source responses."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from bs4 import BeautifulSoup

from .fetchers import FETCHERS
from .http_client import RawHttpResponse
from .models import CandidateSource, FileType
from .v2_models import SnapshotValidationResult


class InvalidSnapshotError(ValueError):
    """Response bytes failed validation and must not enter the raw corpus."""


@dataclass(frozen=True, slots=True)
class SnapshotValidationConfig:
    minimum_text_characters: int = 12
    maximum_error_scan_characters: int = 8_000
    text_encodings: tuple[str, ...] = ("utf-8", "utf-16", "gb18030", "big5")

    def __post_init__(self) -> None:
        if self.minimum_text_characters <= 0:
            raise ValueError("minimum_text_characters must be positive")
        if self.maximum_error_scan_characters <= 0:
            raise ValueError("maximum_error_scan_characters must be positive")


class SnapshotValidator:
    def __init__(self, config: SnapshotValidationConfig | None = None) -> None:
        self.config = config or SnapshotValidationConfig()

    def validate(
        self, candidate: CandidateSource, response: RawHttpResponse
    ) -> SnapshotValidationResult:
        reasons: list[str] = []
        media_type = response.content_type.partition(";")[0].strip().casefold()
        if not response.content:
            reasons.append("empty-content")
        if not 200 <= response.http_status < 300:
            reasons.append("non-success-http-status")
        if not FETCHERS[candidate.file_type].accepts_content_type(
            response.content_type
        ):
            reasons.append("content-type-mismatch")

        detected_encoding: str | None = None
        if response.content:
            format_reasons, detected_encoding = self._format_checks(
                candidate, response
            )
            reasons.extend(format_reasons)
        return SnapshotValidationResult(
            source_id=candidate.source_id,
            valid=not reasons,
            reasons=list(dict.fromkeys(reasons)),
            size_bytes=len(response.content),
            media_type=media_type,
            detected_encoding=detected_encoding,
        )

    def _format_checks(
        self, candidate: CandidateSource, response: RawHttpResponse
    ) -> tuple[list[str], str | None]:
        if candidate.file_type is FileType.PDF:
            return self._pdf_checks(response.content), None
        if candidate.file_type is FileType.JSON:
            return self._json_checks(response.content), "utf-8"
        text, encoding = self._decode_text(response.content, response.content_type)
        if text is None:
            return ["unreadable-text"], None
        if candidate.file_type is FileType.HTML:
            return self._html_checks(text), encoding
        if len(text.strip()) < self.config.minimum_text_characters:
            return ["insufficient-text-content"], encoding
        return [], encoding

    @staticmethod
    def _pdf_checks(content: bytes) -> list[str]:
        if not content.lstrip().startswith(b"%PDF-"):
            return ["invalid-pdf-magic"]
        return []

    @staticmethod
    def _json_checks(content: bytes) -> list[str]:
        try:
            payload = json.loads(content.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return ["invalid-json"]
        if payload in ({}, []):
            return ["empty-json-payload"]
        if isinstance(payload, dict):
            error_keys = {"error", "errors", "exception", "message"}
            if set(payload).intersection(error_keys) and not set(payload).difference(
                error_keys | {"status", "code"}
            ):
                return ["json-error-response"]
        return []

    def _html_checks(self, text: str) -> list[str]:
        soup = BeautifulSoup(text, "html.parser")
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        visible_text = soup.get_text(" ", strip=True)
        scan = f"{title} {visible_text}"[: self.config.maximum_error_scan_characters]
        normalized = " ".join(scan.casefold().split())
        reasons: list[str] = []
        if re.search(
            r"\b404\b|page not found|not found - 404|页面不存在|頁面不存在",
            normalized,
        ):
            reasons.append("html-error-page")
        if re.search(
            r"access denied|request blocked|\bforbidden\b|拒绝访问|拒絕存取",
            normalized,
        ):
            reasons.append("access-denied-page")
        has_password = soup.find(
            "input", attrs={"type": re.compile("password", re.I)}
        )
        if has_password is not None or re.search(
            r"sign in to continue|log in to continue|请登录后|請登入後", normalized
        ):
            reasons.append("login-page")
        if re.search(r"search results(?: for)?|搜索结果|搜尋結果", normalized):
            reasons.append("search-results-page")
        if len(visible_text.strip()) < self.config.minimum_text_characters:
            reasons.append("insufficient-text-content")
        return reasons

    def _decode_text(
        self, content: bytes, content_type: str
    ) -> tuple[str | None, str | None]:
        declared = re.search(
            r"charset\s*=\s*['\"]?([^;'\"\s]+)",
            content_type,
            flags=re.IGNORECASE,
        )
        encodings = ([declared.group(1)] if declared else []) + list(
            self.config.text_encodings
        )
        seen: set[str] = set()
        for encoding in encodings:
            normalized = encoding.casefold()
            if normalized in seen:
                continue
            seen.add(normalized)
            try:
                return content.decode(encoding, errors="strict"), normalized
            except (LookupError, UnicodeDecodeError):
                continue
        return None, None
