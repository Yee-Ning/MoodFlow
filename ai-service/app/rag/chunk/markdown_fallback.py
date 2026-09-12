"""Markdown-only fallback used only when no normalized Blocks exist."""

from __future__ import annotations

from langchain_text_splitters import MarkdownHeaderTextSplitter

from .internal import NarrativePart, NarrativeRun


class MarkdownFallbackSectioner:
    _headers = [("#" * level, f"h{level}") for level in range(1, 7)]

    def __init__(self) -> None:
        self.splitter = MarkdownHeaderTextSplitter(
            headers_to_split_on=self._headers,
            return_each_line=False,
            strip_headers=True,
        )

    def sections(self, cleaned_text: str) -> tuple[NarrativeRun, ...]:
        runs: list[NarrativeRun] = []
        for index, section in enumerate(self.splitter.split_text(cleaned_text)):
            content = section.page_content.strip()
            if not content:
                continue
            path = tuple(
                str(section.metadata[key]).strip()
                for _, key in self._headers
                if section.metadata.get(key)
            )
            runs.append(
                NarrativeRun(
                    order_index=index,
                    section_path=path,
                    parts=(
                        NarrativePart(
                            order_index=index,
                            block_id=f"markdown-fallback-{index:06d}",
                            block_type="markdown_fallback",
                            content=content,
                            metadata={"markdown_fallback": True},
                        ),
                    ),
                )
            )
        return tuple(runs)
