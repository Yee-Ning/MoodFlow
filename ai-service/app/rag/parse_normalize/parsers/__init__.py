"""Format-specific parser adapters."""

from .html import HtmlParser
from .markdown import MarkdownParser
from .text import TxtParser

__all__ = ["HtmlParser", "MarkdownParser", "TxtParser"]
