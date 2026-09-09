"""Conservative DOM-to-block adapter for raw HTML snapshots."""

from __future__ import annotations

from datetime import date
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from ..base import BaseParser, ParserError
from ..models import (
    BlockType,
    DetectedFileType,
    DocumentBlock,
    JsonValue,
    ParseInput,
    ParserResult,
)


class HtmlParser(BaseParser):
    parser_name = "html"
    parser_version = "1.0.0"
    supported_file_types = frozenset({DetectedFileType.HTML})

    _SEMANTIC_TAGS = (
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "p",
        "ul",
        "ol",
        "blockquote",
        "pre",
        "table",
        "img",
    )
    _ATOMIC_ANCESTORS = {"ul", "ol", "blockquote", "pre", "table"}

    def parse(self, parse_input: ParseInput) -> ParserResult:
        try:
            soup = BeautifulSoup(parse_input.raw_bytes, "html.parser")
        except Exception as exc:
            raise ParserError("html_dom_parse_failed") from exc

        for tag in soup.find_all(("script", "style", "noscript")):
            tag.decompose()

        page_metadata = self._page_metadata(soup)
        title = self._title(soup, page_metadata)
        author = self._first_text(page_metadata, "author", "article:author")
        published_at = self._published_date(page_metadata)
        language = self._language(soup)
        body = soup.body or soup
        blocks = self._blocks(
            body,
            parse_input.snapshot.source_id,
            str(parse_input.snapshot.original_url),
        )
        metadata: dict[str, JsonValue] = {
            "meta": page_metadata,
            "source_encoding": soup.original_encoding,
        }
        canonical = soup.find("link", rel="canonical")
        if isinstance(canonical, Tag) and canonical.get("href"):
            metadata["canonical_url"] = urljoin(
                str(parse_input.snapshot.original_url), str(canonical["href"])
            )
        return ParserResult(
            title=title,
            author=author,
            published_at=published_at,
            language=language,
            blocks=blocks,
            metadata=metadata,
        )

    def _blocks(self, body: Tag, source_id: str, base_url: str) -> list[DocumentBlock]:
        blocks: list[DocumentBlock] = []
        headings: dict[int, str] = {}
        for tag in body.find_all(self._SEMANTIC_TAGS):
            if self._inside_atomic_container(tag):
                continue
            block_type, text, level, metadata = self._tag_content(tag, base_url)
            if block_type is None or (not text and block_type is not BlockType.IMAGE):
                continue
            order_index = len(blocks)
            block_id = f"{source_id}-b{order_index:06d}"
            if block_type is BlockType.HEADING:
                assert level is not None
                parent_id = self._nearest_heading(headings, level - 1)
                headings = {
                    heading_level: heading_id
                    for heading_level, heading_id in headings.items()
                    if heading_level < level
                }
                headings[level] = block_id
            else:
                parent_id = self._nearest_heading(headings, 6)
            blocks.append(
                DocumentBlock(
                    block_id=block_id,
                    block_type=block_type,
                    text=text,
                    level=level,
                    order_index=order_index,
                    parent_block_id=parent_id,
                    metadata=metadata,
                )
            )
        return blocks

    def _tag_content(
        self, tag: Tag, base_url: str
    ) -> tuple[BlockType | None, str, int | None, dict[str, JsonValue]]:
        name = tag.name.lower()
        if name.startswith("h") and len(name) == 2 and name[1].isdigit():
            return BlockType.HEADING, self._text(tag), int(name[1]), {}
        if name == "p":
            links = [
                urljoin(base_url, str(link["href"]))
                for link in tag.find_all("a", href=True)
            ]
            return BlockType.PARAGRAPH, self._text(tag), None, {"links": links}
        if name in {"ul", "ol"}:
            items = [self._text(item) for item in tag.find_all("li", recursive=False)]
            items = [item for item in items if item]
            return BlockType.LIST, "\n".join(items), None, {
                "ordered": name == "ol",
                "items": items,
            }
        if name == "blockquote":
            return BlockType.QUOTE, self._text(tag), None, {}
        if name == "pre":
            code = tag.find("code")
            target = code if isinstance(code, Tag) else tag
            classes = target.get("class", []) if isinstance(target, Tag) else []
            language = next(
                (
                    str(value).removeprefix("language-")
                    for value in classes
                    if str(value).startswith("language-")
                ),
                "",
            )
            return BlockType.CODE, target.get_text(), None, {"language": language}
        if name == "table":
            rows = [
                [self._text(cell) for cell in row.find_all(("th", "td"), recursive=False)]
                for row in tag.find_all("tr")
            ]
            rows = [row for row in rows if row]
            return BlockType.TABLE, "\n".join(" | ".join(row) for row in rows), None, {
                "rows": rows
            }
        if name == "img":
            src = urljoin(base_url, str(tag.get("src", "")))
            alt = str(tag.get("alt", ""))
            return BlockType.IMAGE, alt, None, {
                "src": src,
                "alt": alt,
                "title": str(tag.get("title", "")),
            }
        return None, "", None, {}

    def _inside_atomic_container(self, tag: Tag) -> bool:
        parent = tag.parent
        while isinstance(parent, Tag):
            if parent.name in self._ATOMIC_ANCESTORS:
                return True
            parent = parent.parent
        return False

    @staticmethod
    def _nearest_heading(headings: dict[int, str], maximum_level: int) -> str | None:
        levels = [level for level in headings if level <= maximum_level]
        return headings[max(levels)] if levels else None

    @staticmethod
    def _text(tag: Tag) -> str:
        return " ".join(tag.get_text("", strip=False).split())

    @staticmethod
    def _page_metadata(soup: BeautifulSoup) -> dict[str, JsonValue]:
        metadata: dict[str, JsonValue] = {}
        for tag in soup.find_all("meta"):
            key = tag.get("name") or tag.get("property") or tag.get("itemprop")
            content = tag.get("content")
            if not key or content is None:
                continue
            normalized_key = str(key).strip().lower()
            normalized_content = str(content).strip()
            existing = metadata.get(normalized_key)
            if existing is None:
                metadata[normalized_key] = normalized_content
            elif isinstance(existing, list):
                existing.append(normalized_content)
            else:
                metadata[normalized_key] = [existing, normalized_content]
        return metadata

    @staticmethod
    def _first_text(metadata: dict[str, JsonValue], *keys: str) -> str | None:
        for key in keys:
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    def _title(self, soup: BeautifulSoup, metadata: dict[str, JsonValue]) -> str | None:
        if soup.title and soup.title.string:
            value = soup.title.string.strip()
            if value:
                return value
        return self._first_text(metadata, "og:title", "twitter:title")

    def _published_date(self, metadata: dict[str, JsonValue]) -> date | None:
        value = self._first_text(
            metadata, "article:published_time", "date", "publication_date"
        )
        if value is None:
            return None
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None

    @staticmethod
    def _language(soup: BeautifulSoup) -> str:
        html = soup.find("html")
        if isinstance(html, Tag):
            language = str(html.get("lang", "")).strip()
            if language:
                return language
        return "und"
