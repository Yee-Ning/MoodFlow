from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from app.rag.indexing.dense import DenseEncoder, QwenDenseEncoder
from app.rag.retrieval.config import load_retrieval_config


class MemoryEmbedder(Protocol):
    @property
    def dimension(self) -> int: ...

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class QwenMemoryEmbedder:
    """Reuse MoodFlow's pinned dense model without coupling memory to RAG storage."""

    QUERY_INSTRUCTION = (
        "Retrieve prior user experiences and feedback relevant to the current request."
    )

    def __init__(self, encoder: DenseEncoder | None = None) -> None:
        self.config = load_retrieval_config().dense
        self._encoder = encoder or QwenDenseEncoder(self.config)  # type: ignore[arg-type]

    @property
    def dimension(self) -> int:
        return self.config.dimension

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return self._encoder.encode_documents(texts)

    def embed_query(self, text: str) -> list[float]:
        normalized = " ".join(text.split())
        if not normalized:
            raise ValueError("memory query must not be blank")
        formatted = f"Instruct: {self.QUERY_INSTRUCTION}\nQuery: {normalized}"
        vectors = self._encoder.encode_documents([formatted])
        if len(vectors) != 1 or len(vectors[0]) != self.dimension:
            raise ValueError("memory query embedding dimension mismatch")
        return vectors[0]
