"""Format-specific parser adapters."""

from .html import HtmlParser
from .json import JsonParser
from .markdown import MarkdownParser
from .text import TxtParser

__all__ = ["HtmlParser", "JsonParser", "MarkdownParser", "TxtParser"]
