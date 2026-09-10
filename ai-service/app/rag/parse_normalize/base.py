"""Common parser interface and base errors."""

from abc import ABC, abstractmethod

from .models import DetectedFileType, ParseInput, ParserExecution, ParserResult


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

    def execute(
        self, parse_input: ParseInput, parser_mode: str = "auto"
    ) -> ParserExecution:
        """Run an adapter through the shared execution envelope."""

        return ParserExecution(
            result=self.parse(parse_input),
            selected_parser=self.parser_name,
            selected_parser_version=self.parser_version,
        )

    def fingerprint(self, parser_mode: str = "auto") -> str:
        """Return the stable identity used for repeat-parse skipping."""

        return f"{self.parser_name}:{self.parser_version}:{parser_mode}"

    def supports_parser_mode(self, parser_mode: str) -> bool:
        """Return whether this parser accepts a CLI backend override."""

        return parser_mode == "auto"
