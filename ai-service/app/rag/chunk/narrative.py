"""Structure-first parent/child chunking for narrative Blocks."""

from __future__ import annotations

from pydantic import JsonValue

from .config import ChunkConfig
from .context import breadcrumb
from .internal import ChildDraft, NarrativePart, NarrativeRun, ParentDraft
from .models import ChunkType
from .text_splitter import ChineseRecursiveSplitter
from .token_counter import TokenCounter


class NarrativeChunker:
    def __init__(self, config: ChunkConfig, counter: TokenCounter) -> None:
        self.config = config
        self.counter = counter
        self.splitter = ChineseRecursiveSplitter(config, counter)

    def chunk(
        self,
        run: NarrativeRun,
        *,
        document_title: str | None,
        base_metadata: dict[str, JsonValue],
    ) -> list[ParentDraft]:
        groups = self._parent_groups(run.parts)
        parents: list[ParentDraft] = []
        for parent_offset, group in enumerate(groups):
            content = self.config.content_joiner.join(
                part.content for part in group if part.content
            ).strip()
            if not content:
                continue
            metadata: dict[str, JsonValue] = {
                **base_metadata,
                "source_block_ids": [part.block_id for part in group],
                "source_block_types": [part.block_type for part in group],
                "source_block_metadata": [
                    {
                        "block_id": part.block_id,
                        "metadata": part.metadata,
                    }
                    for part in group
                ],
            }
            child_texts = self._merge_small(self.splitter.split_child(content))
            parents.append(
                ParentDraft(
                    order_key=(run.order_index, parent_offset),
                    chunk_type=ChunkType.NARRATIVE,
                    section_path=run.section_path,
                    breadcrumb=breadcrumb(document_title, run.section_path),
                    content=content,
                    metadata=metadata,
                    children=[
                        ChildDraft(content=value, metadata=dict(metadata))
                        for value in child_texts
                    ],
                )
            )
        return parents

    def _parent_groups(
        self, parts: tuple[NarrativePart, ...]
    ) -> list[list[NarrativePart]]:
        expanded: list[NarrativePart] = []
        for part in parts:
            if self.counter.count(part.content) <= self.config.max_parent_tokens:
                expanded.append(part)
                continue
            for content in self.splitter.split_parent(part.content):
                expanded.append(
                    NarrativePart(
                        order_index=part.order_index,
                        block_id=part.block_id,
                        block_type=part.block_type,
                        content=content,
                        metadata=part.metadata,
                    )
                )

        groups: list[list[NarrativePart]] = []
        current: list[NarrativePart] = []
        for part in expanded:
            candidate = self.config.content_joiner.join(
                [*(value.content for value in current), part.content]
            )
            if (
                current
                and self.counter.count(candidate) > self.config.target_parent_tokens
            ):
                groups.append(current)
                current = []
            current.append(part)
            current_content = self.config.content_joiner.join(
                value.content for value in current
            )
            if self.counter.count(current_content) >= self.config.target_parent_tokens:
                groups.append(current)
                current = []
        if current:
            groups.append(current)
        return groups

    def _merge_small(self, values: list[str]) -> list[str]:
        chunks = [value.strip() for value in values if value.strip()]
        index = 0
        while index < len(chunks):
            if self.counter.count(chunks[index]) >= self.config.min_child_tokens:
                index += 1
                continue
            if index > 0:
                merged = self.config.content_joiner.join(
                    (chunks[index - 1], chunks[index])
                )
                if self.counter.count(merged) <= self.config.max_child_tokens:
                    chunks[index - 1] = merged
                    del chunks[index]
                    continue
            if index + 1 < len(chunks):
                merged = self.config.content_joiner.join(
                    (chunks[index], chunks[index + 1])
                )
                if self.counter.count(merged) <= self.config.max_child_tokens:
                    chunks[index] = merged
                    del chunks[index + 1]
                    continue
            index += 1
        return chunks
