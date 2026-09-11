"""Small, parser-independent helpers for preserving table structure."""

from __future__ import annotations

from collections.abc import Sequence

from bs4 import BeautifulSoup

from .models import DocumentBlock, DocumentTable, JsonValue


def normalized_table_rows(value: JsonValue | object) -> list[list[str]]:
    """Return JSON-compatible row data as a rectangular-ready string matrix."""

    if not isinstance(value, list):
        return []
    return [
        [_string_value(cell) for cell in row]
        for row in value
        if isinstance(row, list)
    ]


def extract_html_table(markup: str) -> tuple[list[list[str]], bool]:
    """Extract rows from one backend-emitted HTML table without guessing spans."""

    if "<table" not in markup.casefold():
        return [], False
    soup = BeautifulSoup(markup, "html.parser")
    table = soup.find("table")
    if table is None:
        return [], False
    rows = [
        [cell.get_text(" ", strip=True) for cell in row.find_all(("th", "td"), recursive=False)]
        for row in table.find_all("tr")
    ]
    return [row for row in rows if row], table.find("th") is not None


def render_table_markdown(
    rows: Sequence[Sequence[object]], *, has_header: bool = True
) -> str:
    """Render rows deterministically as a GitHub-style Markdown table."""

    normalized = [
        [_escape_table_cell(_string_value(cell)) for cell in row] for row in rows
    ]
    if not normalized:
        return ""
    column_count = max(len(row) for row in normalized)
    if column_count == 0:
        return ""
    padded = [row + [""] * (column_count - len(row)) for row in normalized]
    if has_header:
        lines = [padded[0], ["---"] * column_count, *padded[1:]]
    else:
        # Markdown requires a header row. Keep every source row as data and use a
        # blank synthetic header rather than promoting the first row to a header.
        lines = [[""] * column_count, ["---"] * column_count, *padded]
    return "\n".join(f"| {' | '.join(row)} |" for row in lines)


def document_tables_from_blocks(blocks: Sequence[DocumentBlock]) -> list[DocumentTable]:
    """Mirror table blocks into the shared top-level table collection."""

    tables: list[DocumentTable] = []
    for block in blocks:
        if block.block_type.value != "table":
            continue
        rows = normalized_table_rows(block.metadata.get("rows"))
        if not rows:
            rows, html_has_header = extract_html_table(block.text)
        else:
            html_has_header = False
        page_number = block.metadata.get("page_number")
        bbox = block.metadata.get("bbox")
        metadata: dict[str, JsonValue] = {"source_block_id": block.block_id}
        for key in ("has_header", "table_kind_hint"):
            value = block.metadata.get(key)
            if isinstance(value, (str, bool)):
                metadata[key] = value
        if "has_header" not in metadata and html_has_header:
            metadata["has_header"] = True
        tables.append(
            DocumentTable(
                table_id=block.block_id,
                page_number=(
                    page_number
                    if isinstance(page_number, int) and not isinstance(page_number, bool)
                    else None
                ),
                bbox=(
                    tuple(float(value) for value in bbox)
                    if isinstance(bbox, list)
                    and len(bbox) == 4
                    and all(isinstance(value, (int, float)) for value in bbox)
                    else None
                ),
                markdown=(
                    render_table_markdown(
                        rows,
                        has_header=block.metadata.get("has_header") is not False,
                    )
                    if rows
                    else block.text
                ),
                rows=rows,
                metadata=metadata,
            )
        )
    return tables


def _escape_table_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\r\n", "<br>").replace(
        "\r", "<br>"
    ).replace("\n", "<br>")


def _string_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)
