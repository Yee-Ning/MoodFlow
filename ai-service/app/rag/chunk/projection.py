"""Project Clean text onto immutable normalized blocks without parsing Markdown."""

from __future__ import annotations

from difflib import SequenceMatcher

from app.rag.parse_normalize.models import DocumentBlock, NormalizedDocument
from app.rag.parse_normalize.normalizer import MarkdownNormalizer


class CleanBlockProjector:
    """Use a line diff to retain Clean's edits while preserving Block boundaries."""

    def __init__(self) -> None:
        self._normalizer = MarkdownNormalizer()

    def project(
        self, document: NormalizedDocument, cleaned_text: str
    ) -> dict[str, str]:
        source_lines, owners = self._owned_normalized_lines(document.blocks)
        clean_lines = self._normalize_newlines(cleaned_text).splitlines()
        projected: dict[str, list[str]] = {
            block.block_id: [] for block in document.blocks
        }
        matcher = SequenceMatcher(None, source_lines, clean_lines, autojunk=False)
        for (
            tag,
            source_start,
            source_end,
            clean_start,
            clean_end,
        ) in matcher.get_opcodes():
            if tag == "equal":
                for offset, clean_line in enumerate(clean_lines[clean_start:clean_end]):
                    owner = owners[source_start + offset]
                    if owner is not None:
                        projected[owner].append(clean_line)
                continue
            if tag == "replace":
                replacement_owners = {
                    owner
                    for owner in owners[source_start:source_end]
                    if owner is not None
                }
                if len(replacement_owners) == 1:
                    owner = next(iter(replacement_owners))
                    projected[owner].extend(clean_lines[clean_start:clean_end])
                continue
            if tag == "insert":
                owner = self._nearest_owner(owners, source_start)
                if owner is not None:
                    projected[owner].extend(clean_lines[clean_start:clean_end])

        return {
            block_id: "\n".join(lines).strip("\n")
            for block_id, lines in projected.items()
        }

    def _owned_normalized_lines(
        self, blocks: list[DocumentBlock]
    ) -> tuple[list[str], list[str | None]]:
        lines: list[str] = []
        owners: list[str | None] = []
        rendered_blocks: list[tuple[DocumentBlock, list[str]]] = []
        for block in blocks:
            rendered = self._normalizer.render([block]).rstrip("\n")
            if rendered:
                rendered_blocks.append((block, rendered.splitlines()))
        for index, (block, block_lines) in enumerate(rendered_blocks):
            if index:
                lines.append("")
                owners.append(None)
            lines.extend(block_lines)
            owners.extend([block.block_id] * len(block_lines))
        return lines, owners

    @staticmethod
    def _nearest_owner(owners: list[str | None], index: int) -> str | None:
        for candidate in range(index - 1, -1, -1):
            if owners[candidate] is not None:
                return owners[candidate]
        for candidate in range(index, len(owners)):
            if owners[candidate] is not None:
                return owners[candidate]
        return None

    @staticmethod
    def _normalize_newlines(text: str) -> str:
        return text.replace("\r\n", "\n").replace("\r", "\n")
