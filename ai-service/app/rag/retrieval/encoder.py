"""Qwen3 query encoding with a versioned retrieval instruction."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from app.rag.indexing.dense import DenseEncoder, QwenDenseEncoder

from .config import DenseQueryConfig


class QueryEncoder(Protocol):
    def encode_queries(self, texts: Sequence[str]) -> list[list[float]]: ...


class QwenQueryEncoder:
    """Reuse the indexed model while keeping query formatting explicit."""

    def __init__(
        self,
        config: DenseQueryConfig,
        *,
        encoder: DenseEncoder | None = None,
    ) -> None:
        self.config = config
        self._encoder = encoder or QwenDenseEncoder(config)  # type: ignore[arg-type]

    def encode_queries(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        formatted = [self._format_query(text) for text in texts]
        vectors = self._encoder.encode_documents(formatted)
        if len(vectors) != len(texts):
            raise ValueError("dense_query_embedding_count_mismatch")
        if any(len(vector) != self.config.dimension for vector in vectors):
            raise ValueError("dense_query_embedding_dimension_mismatch")
        return vectors

    def _format_query(self, text: str) -> str:
        normalized = " ".join(text.split())
        if not normalized:
            raise ValueError("dense_query_text_blank")
        return f"Instruct: {self.config.query_instruction}\nQuery: {normalized}"
