"""Common parser interface and base errors."""

from abc import ABC, abstractmethod

from .models import DetectedFileType, ParseInput, ParserResult


class ParseNormalizeError(Exception):
    """Base class for expected per-source parse failures."""


class ParserError(ParseNormalizeError):
    """A format adapter could not parse its input."""


class BaseParser(ABC):
    parser_name: str
    parser_version: str
    supported_file_types: frozenset[DetectedFileType]

    @abstractmethod
    def parse(self, parse_input: ParseInput) -> ParserResult:
        """Parse immutable input bytes into parser-independent blocks."""
