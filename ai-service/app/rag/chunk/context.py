"""Deterministic retrieval context formatting."""

from __future__ import annotations


def breadcrumb(document_title: str | None, section_path: tuple[str, ...]) -> str:
    values = (
        [document_title.strip()] if document_title and document_title.strip() else []
    )
    values.extend(value.strip() for value in section_path if value.strip())
    return " > ".join(values) or "未命名文档"


def retrieval_content(
    breadcrumb_value: str, content: str, *, schema: str | None = None,
    description: str | None = None,
) -> str:
    values = [breadcrumb_value]
    if schema:
        values.append(schema)
    values.append(content)
    if description:
        values.append(description)
    return "\n\n".join(value for value in values if value)
