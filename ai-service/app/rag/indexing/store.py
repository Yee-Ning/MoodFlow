"""Qdrant write-only boundary for the offline indexing pipeline."""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Protocol

from qdrant_client import QdrantClient, models

from .config import IndexingConfig
from .models import IndexPoint


class IndexStore(Protocol):
    def ensure_collection(self) -> None: ...

    def existing_hashes(self, point_ids: Sequence[str]) -> dict[str, str]: ...

    def upsert(self, points: Sequence[IndexPoint]) -> None: ...

    def all_point_ids(self) -> set[str]: ...

    def delete_points(self, point_ids: Sequence[str]) -> None: ...

    def point_count(self) -> int | None: ...


class QdrantIndexStore:
    """Creates and writes the collection; intentionally exposes no search API."""

    def __init__(
        self,
        config: IndexingConfig,
        *,
        client: QdrantClient | None = None,
    ) -> None:
        self.config = config
        qdrant = config.qdrant
        api_key = os.getenv(qdrant.api_key_env) or None
        self.client = client or QdrantClient(
            url=qdrant.url,
            api_key=api_key,
            timeout=qdrant.timeout_seconds,
            prefer_grpc=qdrant.prefer_grpc,
        )

    def ensure_collection(self) -> None:
        self._validate_server_version()
        name = self.config.qdrant.collection_name
        if not self.client.collection_exists(name):
            self.client.create_collection(
                collection_name=name,
                vectors_config={
                    self.config.dense.vector_name: models.VectorParams(
                        size=self.config.dense.dimension,
                        distance=models.Distance.COSINE,
                        hnsw_config=models.HnswConfigDiff(
                            m=self.config.qdrant.hnsw_m,
                            ef_construct=self.config.qdrant.hnsw_ef_construct,
                        ),
                    )
                },
                sparse_vectors_config={
                    self.config.bm25.vector_name: models.SparseVectorParams(
                        index=models.SparseIndexParams(),
                        modifier=(
                            models.Modifier.IDF
                            if self.config.bm25.idf_modifier
                            else None
                        ),
                    )
                },
            )
        self._validate_collection_schema()
        self._ensure_payload_indexes()

    def existing_hashes(self, point_ids: Sequence[str]) -> dict[str, str]:
        result: dict[str, str] = {}
        size = self.config.qdrant.upsert_batch_size
        for offset in range(0, len(point_ids), size):
            records = self.client.retrieve(
                collection_name=self.config.qdrant.collection_name,
                ids=list(point_ids[offset : offset + size]),
                with_payload=["index_input_hash"],
                with_vectors=False,
            )
            for record in records:
                value = (record.payload or {}).get("index_input_hash")
                if isinstance(value, str):
                    result[str(record.id)] = value
        return result

    def upsert(self, points: Sequence[IndexPoint]) -> None:
        qdrant_points = [
            models.PointStruct(
                id=point.document.point_id,
                vector={
                    self.config.dense.vector_name: point.dense_vector,
                    self.config.bm25.vector_name: models.Document(
                        text=point.document.bm25_text,
                        model=self.config.bm25.model_name,
                        options=self.config.bm25.document_options(),
                    ),
                },
                payload=point.document.payload,
            )
            for point in points
        ]
        self.client.upsert(
            collection_name=self.config.qdrant.collection_name,
            points=qdrant_points,
            wait=True,
        )

    def all_point_ids(self) -> set[str]:
        result: set[str] = set()
        offset: int | str | None = None
        while True:
            records, offset = self.client.scroll(
                collection_name=self.config.qdrant.collection_name,
                limit=256,
                offset=offset,
                with_payload=False,
                with_vectors=False,
            )
            result.update(str(record.id) for record in records)
            if offset is None:
                return result

    def delete_points(self, point_ids: Sequence[str]) -> None:
        if not point_ids:
            return
        self.client.delete(
            collection_name=self.config.qdrant.collection_name,
            points_selector=list(point_ids),
            wait=True,
        )

    def point_count(self) -> int | None:
        return self.client.get_collection(
            self.config.qdrant.collection_name
        ).points_count

    def _validate_server_version(self) -> None:
        actual = self.client.info().version
        minimum = self.config.qdrant.minimum_server_version
        if self._version_tuple(actual) < self._version_tuple(minimum):
            raise ValueError(f"qdrant_server_too_old:minimum={minimum}:actual={actual}")

    def _validate_collection_schema(self) -> None:
        info = self.client.get_collection(self.config.qdrant.collection_name)
        vectors = info.config.params.vectors
        if not isinstance(vectors, dict):
            raise ValueError("qdrant_dense_schema_not_named")
        dense = vectors.get(self.config.dense.vector_name)
        if dense is None:
            raise ValueError("qdrant_dense_vector_missing")
        if dense.size != self.config.dense.dimension:
            raise ValueError(
                "qdrant_dense_dimension_mismatch:"
                f"expected={self.config.dense.dimension}:actual={dense.size}"
            )
        if dense.distance is not models.Distance.COSINE:
            raise ValueError("qdrant_dense_distance_mismatch")
        if dense.hnsw_config is None:
            raise ValueError("qdrant_dense_hnsw_config_missing")
        if dense.hnsw_config.m != self.config.qdrant.hnsw_m:
            raise ValueError("qdrant_dense_hnsw_m_mismatch")
        if dense.hnsw_config.ef_construct != self.config.qdrant.hnsw_ef_construct:
            raise ValueError("qdrant_dense_hnsw_ef_construct_mismatch")
        sparse = info.config.params.sparse_vectors or {}
        sparse_config = sparse.get(self.config.bm25.vector_name)
        if sparse_config is None:
            raise ValueError("qdrant_bm25_vector_missing")
        expected_modifier = (
            models.Modifier.IDF if self.config.bm25.idf_modifier else None
        )
        if sparse_config.modifier != expected_modifier:
            raise ValueError("qdrant_bm25_modifier_mismatch")

    def _ensure_payload_indexes(self) -> None:
        info = self.client.get_collection(self.config.qdrant.collection_name)
        existing = info.payload_schema
        schemas = {
            "keyword": models.PayloadSchemaType.KEYWORD,
            "integer": models.PayloadSchemaType.INTEGER,
            "bool": models.PayloadSchemaType.BOOL,
            "datetime": models.PayloadSchemaType.DATETIME,
            "uuid": models.PayloadSchemaType.UUID,
        }
        for item in self.config.qdrant.payload_indexes:
            expected = schemas[item.data_type]
            current = existing.get(item.field)
            if current is not None:
                if current.data_type != expected:
                    raise ValueError(f"qdrant_payload_index_mismatch:{item.field}")
                continue
            self.client.create_payload_index(
                collection_name=self.config.qdrant.collection_name,
                field_name=item.field,
                field_schema=expected,
                wait=True,
            )

    @staticmethod
    def _version_tuple(value: str) -> tuple[int, int, int]:
        parts = value.split(".")[:3]
        try:
            parsed = tuple(int(part.split("-")[0]) for part in parts)
        except ValueError as exc:
            raise ValueError(f"qdrant_version_invalid:{value}") from exc
        if len(parsed) != 3:
            raise ValueError(f"qdrant_version_invalid:{value}")
        return parsed  # type: ignore[return-value]
