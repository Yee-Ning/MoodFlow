"""Transactional local dense-vector cache keyed by the full index input hash."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Sequence

from .models import IndexPoint


class LocalEmbeddingCache:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.warnings: list[str] = []
        self._connection: sqlite3.Connection | None = None

    def __enter__(self) -> LocalEmbeddingCache:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(self.path, timeout=1)
            with self._connection:
                self._connection.execute(
                    "CREATE TABLE IF NOT EXISTS dense_embeddings_v1 "
                    "(input_hash TEXT PRIMARY KEY, dense_vector TEXT NOT NULL)"
                )
        except (OSError, sqlite3.Error) as exc:
            self._unavailable(exc)
        return self

    def __exit__(self, *args: object) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def get(self, input_hash: str) -> list[float] | None:
        if self._connection is None:
            return None
        try:
            row = self._connection.execute(
                "SELECT dense_vector FROM dense_embeddings_v1 WHERE input_hash = ?",
                (input_hash,),
            ).fetchone()
        except sqlite3.Error as exc:
            self._unavailable(exc)
            return None
        if row is None:
            return None
        try:
            vector = json.loads(row[0])
            if not isinstance(vector, list) or not all(
                isinstance(value, (int, float)) and not isinstance(value, bool)
                for value in vector
            ):
                raise ValueError("cached_vector_invalid")
            return vector
        except (TypeError, ValueError):
            self.invalidate(input_hash)
            return None

    def put(self, points: Sequence[IndexPoint]) -> None:
        if self._connection is None or not points:
            return
        try:
            with self._connection:
                self._connection.executemany(
                    "INSERT OR REPLACE INTO dense_embeddings_v1 "
                    "(input_hash, dense_vector) VALUES (?, ?)",
                    [
                        (point.document.input_hash,
                         json.dumps(point.dense_vector, allow_nan=False))
                        for point in points
                    ],
                )
        except sqlite3.Error as exc:
            self._unavailable(exc)

    def invalidate(self, input_hash: str) -> None:
        warning = "embedding_cache_entry_invalid"
        if warning not in self.warnings:
            self.warnings.append(warning)
        if self._connection is None:
            return
        try:
            with self._connection:
                self._connection.execute(
                    "DELETE FROM dense_embeddings_v1 WHERE input_hash = ?", (input_hash,)
                )
        except sqlite3.Error as exc:
            self._unavailable(exc)

    def _unavailable(self, exc: Exception) -> None:
        self.warnings.append(f"embedding_cache_unavailable:{type(exc).__name__}")
        if self._connection is not None:
            self._connection.close()
            self._connection = None
