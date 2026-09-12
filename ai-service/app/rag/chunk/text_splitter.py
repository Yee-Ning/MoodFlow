"""LangChain-backed recursive splitting with project-owned token policy."""

from __future__ import annotations

from langchain_text_splitters import RecursiveCharacterTextSplitter

from .config import ChunkConfig
from .token_counter import TokenCounter


class ChineseRecursiveSplitter:
    def __init__(self, config: ChunkConfig, counter: TokenCounter) -> None:
        self.config = config
        self.counter = counter

    def split_parent(self, text: str) -> list[str]:
        return self._split(
            text,
            chunk_size=self.config.target_parent_tokens,
            chunk_overlap=0,
            strip_whitespace=True,
        )

    def split_child(self, text: str) -> list[str]:
        return self._split(
            text,
            chunk_size=self.config.target_child_tokens,
            chunk_overlap=self.config.child_overlap_tokens,
            strip_whitespace=True,
        )

    def split_table_text(self, text: str, *, chunk_size: int) -> list[str]:
        return self._split(
            text,
            chunk_size=max(1, chunk_size),
            chunk_overlap=0,
            strip_whitespace=False,
        )

    def _split(
        self,
        text: str,
        *,
        chunk_size: int,
        chunk_overlap: int,
        strip_whitespace: bool,
    ) -> list[str]:
        splitter = RecursiveCharacterTextSplitter(
            separators=list(self.config.separators),
            keep_separator="end",
            is_separator_regex=False,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            length_function=self.counter.count,
            strip_whitespace=strip_whitespace,
        )
        return [value for value in splitter.split_text(text) if value]
