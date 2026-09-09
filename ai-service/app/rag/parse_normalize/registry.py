"""Parser registration and resolution."""

from collections.abc import Iterable

from .base import BaseParser, ParseNormalizeError
from .models import DetectedFileType


class DuplicateParserError(ParseNormalizeError):
    """More than one parser was registered for the same file type."""


class ParserNotRegisteredError(ParseNormalizeError):
    """No parser is registered for a detected file type."""


class ParserRegistry:
    def __init__(self, parsers: Iterable[BaseParser] = ()) -> None:
        self._parsers: dict[DetectedFileType, BaseParser] = {}
        for parser in parsers:
            self.register(parser)

    def register(self, parser: BaseParser) -> None:
        for file_type in parser.supported_file_types:
            if file_type in self._parsers:
                raise DuplicateParserError(
                    f"parser already registered for {file_type.value}"
                )
        for file_type in parser.supported_file_types:
            self._parsers[file_type] = parser

    def resolve(self, file_type: DetectedFileType) -> BaseParser:
        try:
            return self._parsers[file_type]
        except KeyError as exc:
            raise ParserNotRegisteredError(
                f"no parser registered for {file_type.value}"
            ) from exc
