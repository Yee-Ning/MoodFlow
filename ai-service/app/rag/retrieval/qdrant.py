"""Async Qdrant Query API boundary for dense and BM25 retrieval legs."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Sequence
from typing import Protocol

from qdrant_client import AsyncQdrantClient, models

from .binding import RetrievalIndexBinding
from .config import RetrievalConfig
from .fusion import LegSearchResult, RawSearchHit
from .legs import RetrievalLeg


PAYLOAD_FIELDS = [
    "chunk_id",
    "parent_id",
    "doc_id",
    "chunk_index",
    "section_path",
    "breadcrumb",
    "content",
    "retrieval_content",
    "token_count",
    "parent_context",
    "source_id",
    "source_name",
    "source_url",
    "entity_ids",
    "entity_types",
    "supported_claims",
    "knowledge_status",
    "content_type",
    "language",
]


class HybridSearchBackend(Protocol):
    async def validate_binding(self, binding: RetrievalIndexBinding) -> None: ...

    async def search(
        self,
        binding: RetrievalIndexBinding,
        legs: Sequence[RetrievalLeg],
        query_filter: models.Filter | None,
        *,
        limit: int,
    ) -> list[LegSearchResult]: ...


class QdrantHybridSearchBackend:
    def __init__(
        self,
        config: RetrievalConfig,
        *,
        client: AsyncQdrantClient | None = None,
    ) -> None:
        self.config = config
        api_key = os.getenv(config.qdrant.api_key_env) or None
        self.client = client or AsyncQdrantClient(
            url=config.qdrant.url,
            api_key=api_key,
            timeout=config.qdrant.timeout_seconds,
            prefer_grpc=config.qdrant.prefer_grpc,
        )

    async def validate_binding(self, binding: RetrievalIndexBinding) -> None:
        server = await self.client.info()
        if self._version_tuple(server.version) < self._version_tuple(
            self.config.qdrant.minimum_server_version
        ):
            raise ValueError("retrieval_qdrant_server_too_old")
        info = await self.client.get_collection(binding.profile.collection_name)
        vectors = info.config.params.vectors
        if not isinstance(vectors, dict):
            raise ValueError("retrieval_qdrant_dense_schema_not_named")
        dense = vectors.get(self.config.dense.vector_name)
        if dense is None or dense.size != self.config.dense.dimension:
            raise ValueError("retrieval_qdrant_dense_schema_mismatch")
        sparse = info.config.params.sparse_vectors or {}
        if self.config.bm25.vector_name not in sparse:
            raise ValueError("retrieval_qdrant_bm25_schema_mismatch")
        if info.points_count != binding.manifest.projected_count:
            raise ValueError("retrieval_qdrant_point_count_mismatch")

    async def search(
        self,
        binding: RetrievalIndexBinding,
        legs: Sequence[RetrievalLeg],
        query_filter: models.Filter | None,
        *,
        limit: int,
    ) -> list[LegSearchResult]:
        requests = [self._request(leg, query_filter, limit) for leg in legs]
        try:
            responses = await self.client.query_batch_points(
                collection_name=binding.profile.collection_name,
                requests=requests,
                timeout=max(1, int(self.config.qdrant.timeout_seconds)),
            )
            if len(responses) != len(legs):
                raise RuntimeError("qdrant batch response count mismatch")
            return [
                LegSearchResult(leg=leg, hits=self._hits(response.points))
                for leg, response in zip(legs, responses, strict=True)
            ]
        except Exception:
            return await self._search_individually(
                binding,
                legs,
                query_filter,
                limit=limit,
            )

    async def _search_individually(
        self,
        binding: RetrievalIndexBinding,
        legs: Sequence[RetrievalLeg],
        query_filter: models.Filter | None,
        *,
        limit: int,
    ) -> list[LegSearchResult]:
        async def run(leg: RetrievalLeg) -> LegSearchResult:
            request = self._request(leg, query_filter, limit)
            try:
                response = await self.client.query_points(
                    collection_name=binding.profile.collection_name,
                    query=request.query,
                    using=request.using,
                    query_filter=request.filter,
                    search_params=request.params,
                    limit=request.limit or limit,
                    with_payload=request.with_payload or False,
                    with_vectors=request.with_vector or False,
                    timeout=max(1, int(self.config.qdrant.timeout_seconds)),
                )
            except Exception:
                return LegSearchResult(
                    leg=leg,
                    hits=[],
                    error_code=f"{leg.channel.value}_query_failed",
                )
            return LegSearchResult(leg=leg, hits=self._hits(response.points))

        return list(await asyncio.gather(*(run(leg) for leg in legs)))

    def _request(
        self,
        leg: RetrievalLeg,
        query_filter: models.Filter | None,
        limit: int,
    ) -> models.QueryRequest:
        if leg.channel.value == "dense":
            if leg.dense_vector is None:
                raise ValueError("dense_retrieval_leg_has_no_vector")
            query: list[float] | models.Document = leg.dense_vector
            vector_name = self.config.dense.vector_name
            params = models.SearchParams(hnsw_ef=self.config.qdrant.hnsw_ef)
        else:
            query = models.Document(
                text=leg.text,
                model=self.config.bm25.model_name,
                options=self.config.bm25.query_options(),
            )
            vector_name = self.config.bm25.vector_name
            params = None
        return models.QueryRequest(
            query=query,
            using=vector_name,
            filter=query_filter,
            params=params,
            limit=limit,
            with_payload=PAYLOAD_FIELDS,
            with_vector=False,
        )

    @staticmethod
    def _hits(points) -> list[RawSearchHit]:
        return [
            RawSearchHit(
                point_id=str(point.id),
                score=float(point.score),
                payload=dict(point.payload or {}),
            )
            for point in points
        ]

    @staticmethod
    def _version_tuple(value: str) -> tuple[int, int, int]:
        try:
            result = tuple(int(part.split("-")[0]) for part in value.split(".")[:3])
        except ValueError as exc:
            raise ValueError("retrieval_qdrant_server_version_invalid") from exc
        if len(result) != 3:
            raise ValueError("retrieval_qdrant_server_version_invalid")
        return result  # type: ignore[return-value]
