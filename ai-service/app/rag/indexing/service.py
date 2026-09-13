"""Corpus orchestration for the final offline indexing stage."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence, TypeVar

from app.rag.knowledge.models import KnowledgeStatus

from .config import IndexingConfig
from .corpus import IndexCorpusReader
from .dense import DenseEncoder, QwenDenseEncoder
from .models import (
    IndexBuildManifest,
    IndexFailure,
    IndexFailureStage,
    IndexPoint,
    PreparedIndexDocument,
)
from .projection import IndexDocumentProjector
from .store import IndexStore, QdrantIndexStore
from .validator import IndexingValidator
from .writer import IndexBuildWriter


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class IndexRunOutcome:
    summary: dict[str, object]
    manifest_path: Path | None
    failures_path: Path | None
    failures: list[IndexFailure]


class OfflineIndexingService:
    def __init__(
        self,
        project_root: Path,
        config: IndexingConfig,
        *,
        knowledge_path: Path,
        output_root: Path | None = None,
        projector: IndexDocumentProjector | None = None,
        encoder: DenseEncoder | None = None,
        store: IndexStore | None = None,
    ) -> None:
        self.project_root = project_root.resolve()
        self.config = config
        self.knowledge_path = knowledge_path.resolve()
        self.reader = IndexCorpusReader(
            self.project_root, knowledge_path=self.knowledge_path
        )
        self.output_root = (
            output_root.resolve()
            if output_root is not None
            else self.project_root / "data" / "indexing" / config.indexing_version
        )
        self.projector = projector or IndexDocumentProjector(config)
        self.encoder = encoder
        self.store = store
        self.validator = IndexingValidator(config)

    def run(
        self,
        *,
        limit: int | None = None,
        validate_only: bool = False,
        prune_stale: bool | None = None,
    ) -> IndexRunOutcome:
        if limit is not None and limit < 1:
            raise ValueError("index limit must be positive")
        should_prune = (
            self.config.qdrant.prune_stale if prune_stale is None else prune_stale
        )
        if limit is not None and should_prune:
            raise ValueError("cannot prune stale points during a limited run")
        if validate_only and should_prune:
            raise ValueError("cannot prune stale points during validation-only run")

        corpus = self.reader.load()
        eligible_chunks = []
        excluded_count = 0
        for chunk in corpus.children:
            status = chunk.knowledge.quality.status
            eligible = status in {KnowledgeStatus.SUCCESS, KnowledgeStatus.SKIPPED}
            if status is KnowledgeStatus.PARTIAL:
                eligible = self.config.index_partial_knowledge
            if eligible:
                eligible_chunks.append(chunk)
            else:
                excluded_count += 1
        if limit is not None:
            eligible_chunks = eligible_chunks[:limit]

        failures: list[IndexFailure] = []
        documents: list[PreparedIndexDocument] = []
        for chunk in eligible_chunks:
            try:
                document = self.projector.project(
                    chunk,
                    corpus.parents[chunk.parent_id],
                )
                self.validator.validate_document(document)
                documents.append(document)
            except Exception as exc:
                failures.append(
                    IndexFailure(
                        chunk_id=chunk.chunk_id,
                        stage=IndexFailureStage.PROJECTION,
                        error=self._error(exc),
                    )
                )

        if validate_only:
            return IndexRunOutcome(
                summary=self._summary(
                    status="validated" if not failures else "partial",
                    parent_count=len(corpus.parents),
                    child_count=len(corpus.children),
                    eligible_count=len(eligible_chunks),
                    excluded_count=excluded_count,
                    indexed_count=0,
                    skipped_count=0,
                    failed_count=len(failures),
                    pruned_count=0,
                    collection_point_count=None,
                ),
                manifest_path=None,
                failures_path=None,
                failures=failures,
            )

        store = self.store or QdrantIndexStore(self.config)
        encoder = self.encoder or QwenDenseEncoder(self.config.dense)
        self._retry(store.ensure_collection)
        existing = self._retry(
            lambda: store.existing_hashes([item.point_id for item in documents])
        )
        pending = [
            item for item in documents if existing.get(item.point_id) != item.input_hash
        ]
        skipped_count = len(documents) - len(pending)
        points, embedding_failures = self._embed(pending, encoder)
        failures.extend(embedding_failures)
        indexed_count, upsert_failures = self._upsert(points, store)
        failures.extend(upsert_failures)

        warnings: list[str] = []
        pruned_count = 0
        if should_prune and not failures:
            stale = sorted(
                self._retry(store.all_point_ids) - {item.point_id for item in documents}
            )
            for batch in self._batches(stale, self.config.qdrant.upsert_batch_size):
                self._retry(lambda batch=batch: store.delete_points(batch))
            pruned_count = len(stale)
        elif should_prune:
            warnings.append("stale_prune_skipped_due_to_failures")

        try:
            collection_count = self._retry(store.point_count)
        except Exception as exc:
            collection_count = None
            warnings.append(f"collection_point_count_unavailable:{self._error(exc)}")
        manifest = IndexBuildManifest(
            status="partial" if failures else "completed",
            created_at=datetime.now(timezone.utc),
            indexing_version=self.config.indexing_version,
            schema_version=self.config.schema_version,
            projection_version=self.config.projection_version,
            source_path=self.knowledge_path.relative_to(self.project_root).as_posix(),
            source_fingerprint=self._source_fingerprint(),
            collection_name=self.config.qdrant.collection_name,
            dense_vector_name=self.config.dense.vector_name,
            dense_model=self.config.dense.model_name,
            dense_model_revision=self.config.dense.model_revision,
            dense_dimension=self.config.dense.dimension,
            dense_distance=self.config.dense.distance,
            dense_normalized=self.config.dense.normalize,
            bm25_vector_name=self.config.bm25.vector_name,
            bm25_model=self.config.bm25.model_name,
            bm25_options=self.config.bm25.document_options(),
            hnsw_m=self.config.qdrant.hnsw_m,
            hnsw_ef_construct=self.config.qdrant.hnsw_ef_construct,
            parent_count=len(corpus.parents),
            child_count=len(corpus.children),
            eligible_count=len(eligible_chunks),
            excluded_count=excluded_count,
            indexed_count=indexed_count,
            skipped_count=skipped_count,
            failed_count=len(failures),
            pruned_count=pruned_count,
            collection_point_count=collection_count,
            warnings=warnings,
        )
        manifest_path, failures_path = IndexBuildWriter(
            self.project_root, self.output_root
        ).write(manifest, failures)
        return IndexRunOutcome(
            summary=self._summary(
                status=manifest.status,
                parent_count=manifest.parent_count,
                child_count=manifest.child_count,
                eligible_count=manifest.eligible_count,
                excluded_count=manifest.excluded_count,
                indexed_count=manifest.indexed_count,
                skipped_count=manifest.skipped_count,
                failed_count=manifest.failed_count,
                pruned_count=manifest.pruned_count,
                collection_point_count=manifest.collection_point_count,
            ),
            manifest_path=manifest_path,
            failures_path=failures_path,
            failures=failures,
        )

    def _embed(
        self, documents: list[PreparedIndexDocument], encoder: DenseEncoder
    ) -> tuple[list[IndexPoint], list[IndexFailure]]:
        points: list[IndexPoint] = []
        failures: list[IndexFailure] = []
        for batch in self._batches(documents, self.config.embedding_batch_size):
            try:
                vectors = self._retry(
                    lambda batch=batch: encoder.encode_documents(
                        [item.dense_text for item in batch]
                    )
                )
                points.extend(self._validated_points(batch, vectors))
            except Exception as batch_error:
                if len(batch) == 1:
                    item = batch[0]
                    failures.append(
                        IndexFailure(
                            chunk_id=item.chunk_id,
                            point_id=item.point_id,
                            stage=IndexFailureStage.EMBEDDING,
                            error=self._error(batch_error),
                        )
                    )
                    continue
                for item in batch:
                    try:
                        vectors = self._retry(
                            lambda item=item: encoder.encode_documents(
                                [item.dense_text]
                            )
                        )
                        points.extend(self._validated_points([item], vectors))
                    except Exception as exc:
                        failures.append(
                            IndexFailure(
                                chunk_id=item.chunk_id,
                                point_id=item.point_id,
                                stage=IndexFailureStage.EMBEDDING,
                                error=self._error(exc),
                            )
                        )
        return points, failures

    def _validated_points(
        self,
        documents: Sequence[PreparedIndexDocument],
        vectors: Sequence[Sequence[float]],
    ) -> list[IndexPoint]:
        if len(vectors) != len(documents):
            raise ValueError(
                f"embedding_count_mismatch:expected={len(documents)}:actual={len(vectors)}"
            )
        points: list[IndexPoint] = []
        for document, vector in zip(documents, vectors, strict=True):
            point = IndexPoint(document=document, dense_vector=list(vector))
            self.validator.validate_point(point)
            points.append(point)
        return points

    def _upsert(
        self, points: list[IndexPoint], store: IndexStore
    ) -> tuple[int, list[IndexFailure]]:
        succeeded = 0
        failures: list[IndexFailure] = []
        for batch in self._batches(points, self.config.qdrant.upsert_batch_size):
            batch_success, batch_failures = self._upsert_isolated(batch, store)
            succeeded += batch_success
            failures.extend(batch_failures)
        return succeeded, failures

    def _upsert_isolated(
        self, points: Sequence[IndexPoint], store: IndexStore
    ) -> tuple[int, list[IndexFailure]]:
        if not points:
            return 0, []
        try:
            self._retry(lambda: store.upsert(points))
            return len(points), []
        except Exception as exc:
            if len(points) > 1:
                middle = len(points) // 2
                left_count, left_failures = self._upsert_isolated(
                    points[:middle], store
                )
                right_count, right_failures = self._upsert_isolated(
                    points[middle:], store
                )
                return left_count + right_count, left_failures + right_failures
            item = points[0].document
            return 0, [
                IndexFailure(
                    chunk_id=item.chunk_id,
                    point_id=item.point_id,
                    stage=IndexFailureStage.UPSERT,
                    error=self._error(exc),
                )
            ]

    def _retry(self, operation: Callable[[], T]) -> T:
        last_error: Exception | None = None
        for _ in range(self.config.retry_count + 1):
            try:
                return operation()
            except Exception as exc:
                last_error = exc
        assert last_error is not None
        raise last_error

    @staticmethod
    def _batches(values: Sequence[T], size: int) -> list[Sequence[T]]:
        return [
            values[offset : offset + size] for offset in range(0, len(values), size)
        ]

    @staticmethod
    def _error(exc: Exception) -> str:
        return (str(exc).splitlines()[0] or exc.__class__.__name__)[:1_000]

    def _source_fingerprint(self) -> str:
        return "sha256:" + hashlib.sha256(self.knowledge_path.read_bytes()).hexdigest()

    def _summary(self, **counts: object) -> dict[str, object]:
        return {
            **counts,
            "indexing_version": self.config.indexing_version,
            "collection_name": self.config.qdrant.collection_name,
            "dense_model": self.config.dense.model_name,
            "dense_dimension": self.config.dense.dimension,
            "bm25_model": self.config.bm25.model_name,
        }
