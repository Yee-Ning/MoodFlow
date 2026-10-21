"""Corpus orchestration for the final offline indexing stage."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence, TypeVar

from app.rag.chunk.models import ChunkType
from app.rag.knowledge.models import KnowledgeEnrichedParentChunk, KnowledgeStatus

from .cache import LocalEmbeddingCache
from .config import IndexingConfig
from .corpus import IndexCorpusReader
from .dense import DENSE_POOLING, DenseEncoder, QwenDenseEncoder
from .models import (
    IndexBuildManifest,
    IndexFailure,
    IndexFailureStage,
    IndexPoint,
    PreparedIndexDocument,
)
from .projection import IndexDocumentProjector
from .release import KnowledgeReleaseValidator
from .store import IndexStore, QdrantIndexStore
from .validator import IndexingValidator
from .writer import IndexBuildWriter


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class IndexRunOutcome:
    summary: dict[str, object]
    projection_path: Path | None
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
        release_manifest_path: Path | None = None,
        output_root: Path | None = None,
        projector: IndexDocumentProjector | None = None,
        encoder: DenseEncoder | None = None,
        store: IndexStore | None = None,
    ) -> None:
        self.project_root = project_root.resolve()
        self.config = config
        self.knowledge_path = knowledge_path.resolve()
        self.release_manifest_path = (
            release_manifest_path.resolve()
            if release_manifest_path is not None
            else None
        )
        self.reader = IndexCorpusReader(
            self.project_root, knowledge_path=self.knowledge_path
        )
        self.output_root = output_root.resolve() if output_root is not None else None
        self.projector = projector
        self.encoder = encoder
        self.store = store

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

        release = KnowledgeReleaseValidator(
            self.project_root,
            knowledge_path=self.knowledge_path,
            manifest_path=self.release_manifest_path,
        ).validate()
        config = self.config.for_release(release.release_id)
        projector = self.projector or IndexDocumentProjector(config)
        validator = IndexingValidator(config)
        corpus = self.reader.load()
        if len(corpus.chunks) != release.chunk_count:
            raise ValueError(
                "knowledge_release_chunk_count_mismatch:"
                f"expected={release.chunk_count}:actual={len(corpus.chunks)}"
            )
        all_eligible_chunks = []
        excluded_count = 0
        parents_with_children = {chunk.parent_id for chunk in corpus.children}
        for chunk in corpus.chunks:
            if isinstance(chunk, KnowledgeEnrichedParentChunk) and (
                chunk.chunk_type is not ChunkType.STRUCTURED_RECORD
                or chunk.chunk_id in parents_with_children
            ):
                continue
            status = chunk.knowledge.quality.status
            eligible = status in {KnowledgeStatus.SUCCESS, KnowledgeStatus.SKIPPED}
            if status is KnowledgeStatus.PARTIAL:
                eligible = self.config.index_partial_knowledge
            if eligible:
                all_eligible_chunks.append(chunk)
            else:
                excluded_count += 1
        eligible_count = len(all_eligible_chunks)
        eligible_chunks = all_eligible_chunks
        if limit is not None:
            eligible_chunks = eligible_chunks[:limit]

        failures: list[IndexFailure] = []
        documents: list[PreparedIndexDocument] = []
        for chunk in eligible_chunks:
            try:
                if isinstance(chunk, KnowledgeEnrichedParentChunk):
                    document = projector.project_record(
                        chunk, source_release_id=release.release_id
                    )
                else:
                    document = projector.project(
                        chunk,
                        corpus.parents[chunk.parent_id],
                        source_release_id=release.release_id,
                    )
                validator.validate_document(document)
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
                    ready_for_retrieval=False,
                    source_release_id=release.release_id,
                    parent_count=len(corpus.parents),
                    child_count=len(corpus.children),
                    eligible_count=eligible_count,
                    selected_count=len(eligible_chunks),
                    projected_count=len(documents),
                    excluded_count=excluded_count,
                    indexed_count=0,
                    skipped_count=0,
                    embedding_cache_path=None,
                    embedding_cache_hit_count=0,
                    embedding_computed_count=0,
                    failed_count=len(failures),
                    pruned_count=0,
                    collection_point_count=None,
                    config=config,
                ),
                projection_path=None,
                manifest_path=None,
                failures_path=None,
                failures=failures,
            )

        output_root = self.output_root or (
            self.project_root
            / "data"
            / "indexing"
            / config.indexing_version
            / release.release_id
            / config.projection_version
        )
        writer = IndexBuildWriter(self.project_root, output_root)
        projection_path, projection_fingerprint = writer.write_projection(documents)

        store = self.store or QdrantIndexStore(config)
        self._retry(store.ensure_collection)
        cache_path = (
            self.project_root / "data" / "indexing" / "embedding_cache" / "v1"
            / "embeddings.sqlite3"
        )
        with LocalEmbeddingCache(cache_path) as cache:
            # Look locally first; Qdrant hashes still decide whether to upsert.
            cached = self._cached_points(documents, cache, validator)
            existing = self._retry(
                lambda: store.existing_hashes([item.point_id for item in documents])
            )
            pending = [
                item for item in documents if existing.get(item.point_id) != item.input_hash
            ]
            skipped_count = len(documents) - len(pending)
            uncached = [item for item in pending if item.input_hash not in cached]
            computed: list[IndexPoint] = []
            embedding_failures: list[IndexFailure] = []
            if uncached:
                encoder = self.encoder or QwenDenseEncoder(config.dense)
                computed, embedding_failures = self._embed(uncached, encoder, validator, cache)
            points = [cached[item.input_hash] for item in pending if item.input_hash in cached]
            points.extend(computed)
        warnings = list(cache.warnings)
        failures.extend(embedding_failures)
        indexed_count, upsert_failures = self._upsert(points, store)
        failures.extend(upsert_failures)

        pruned_count = 0
        if should_prune and not failures:
            stale = sorted(
                self._retry(store.all_point_ids) - {item.point_id for item in documents}
            )
            for batch in self._batches(stale, config.qdrant.upsert_batch_size):
                self._retry(lambda batch=batch: store.delete_points(batch))
            pruned_count = len(stale)
        elif should_prune:
            warnings.append("stale_prune_skipped_due_to_failures")

        try:
            collection_count = self._retry(store.point_count)
        except Exception as exc:
            collection_count = None
            warnings.append(f"collection_point_count_unavailable:{self._error(exc)}")
        if limit is not None:
            warnings.append("limited_build_not_release_ready")
        if limit is None and not failures and collection_count != len(documents):
            failures.append(
                IndexFailure(
                    chunk_id="__collection__",
                    stage=IndexFailureStage.VERIFICATION,
                    error=(
                        "collection_point_count_mismatch:"
                        f"expected={len(documents)}:actual={collection_count}"
                    ),
                )
            )
        status = "partial" if failures or limit is not None else "completed"
        ready_for_retrieval = (
            status == "completed" and collection_count == len(documents)
        )
        manifest = IndexBuildManifest(
            status=status,
            ready_for_retrieval=ready_for_retrieval,
            created_at=datetime.now(timezone.utc),
            indexing_version=config.indexing_version,
            schema_version=config.schema_version,
            projection_version=config.projection_version,
            source_path=self._relative(self.knowledge_path),
            source_fingerprint=release.knowledge_fingerprint,
            source_release_id=release.release_id,
            source_release_manifest_path=self._relative(release.manifest_path),
            source_release_manifest_fingerprint=release.manifest_fingerprint,
            config_fingerprint=self._config_fingerprint(config),
            projection_path=self._relative(projection_path),
            projection_fingerprint=projection_fingerprint,
            collection_name=config.qdrant.collection_name,
            dense_vector_name=config.dense.vector_name,
            dense_model=config.dense.model_name,
            dense_model_revision=config.dense.model_revision,
            dense_dimension=config.dense.dimension,
            dense_distance=config.dense.distance,
            dense_normalized=config.dense.normalize,
            dense_max_length=config.dense.max_length,
            dense_pooling=DENSE_POOLING,
            bm25_vector_name=config.bm25.vector_name,
            bm25_model=config.bm25.model_name,
            bm25_options=config.bm25.document_options(),
            bm25_idf_modifier=config.bm25.idf_modifier,
            hnsw_m=config.qdrant.hnsw_m,
            hnsw_ef_construct=config.qdrant.hnsw_ef_construct,
            parent_count=len(corpus.parents),
            child_count=len(corpus.children),
            eligible_count=eligible_count,
            selected_count=len(eligible_chunks),
            projected_count=len(documents),
            excluded_count=excluded_count,
            limited_to=limit,
            indexed_count=indexed_count,
            skipped_count=skipped_count,
            embedding_cache_path=self._relative(cache_path),
            embedding_cache_hit_count=len(cached),
            embedding_computed_count=len(computed),
            failed_count=len(failures),
            pruned_count=pruned_count,
            collection_point_count=collection_count,
            warnings=warnings,
        )
        manifest_path, failures_path = writer.write(manifest, failures)
        return IndexRunOutcome(
            summary=self._summary(
                status=manifest.status,
                ready_for_retrieval=manifest.ready_for_retrieval,
                source_release_id=manifest.source_release_id,
                parent_count=manifest.parent_count,
                child_count=manifest.child_count,
                eligible_count=manifest.eligible_count,
                selected_count=manifest.selected_count,
                projected_count=manifest.projected_count,
                excluded_count=manifest.excluded_count,
                indexed_count=manifest.indexed_count,
                skipped_count=manifest.skipped_count,
                embedding_cache_path=manifest.embedding_cache_path,
                embedding_cache_hit_count=manifest.embedding_cache_hit_count,
                embedding_computed_count=manifest.embedding_computed_count,
                failed_count=manifest.failed_count,
                pruned_count=manifest.pruned_count,
                collection_point_count=manifest.collection_point_count,
                config=config,
                warnings=manifest.warnings,
            ),
            projection_path=projection_path,
            manifest_path=manifest_path,
            failures_path=failures_path,
            failures=failures,
        )

    @staticmethod
    def _cached_points(
        documents: list[PreparedIndexDocument],
        cache: LocalEmbeddingCache,
        validator: IndexingValidator,
    ) -> dict[str, IndexPoint]:
        points: dict[str, IndexPoint] = {}
        for document in documents:
            vector = cache.get(document.input_hash)
            if vector is None:
                continue
            try:
                point = IndexPoint(document=document, dense_vector=vector)
                validator.validate_point(point)
                points[document.input_hash] = point
            except ValueError:
                cache.invalidate(document.input_hash)
        return points

    def _embed(
        self,
        documents: list[PreparedIndexDocument],
        encoder: DenseEncoder,
        validator: IndexingValidator,
        cache: LocalEmbeddingCache,
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
                validated = self._validated_points(batch, vectors, validator)
                # Persist each successful batch before the next model call or upsert.
                cache.put(validated)
                points.extend(validated)
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
                        validated = self._validated_points([item], vectors, validator)
                        cache.put(validated)
                        points.extend(validated)
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
        validator: IndexingValidator,
    ) -> list[IndexPoint]:
        if len(vectors) != len(documents):
            raise ValueError(
                f"embedding_count_mismatch:expected={len(documents)}:actual={len(vectors)}"
            )
        points: list[IndexPoint] = []
        for document, vector in zip(documents, vectors, strict=True):
            point = IndexPoint(document=document, dense_vector=list(vector))
            validator.validate_point(point)
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

    @staticmethod
    def _config_fingerprint(config: IndexingConfig) -> str:
        encoded = json.dumps(
            config.model_dump(mode="json", by_alias=True),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return "sha256:" + hashlib.sha256(encoded).hexdigest()

    def _relative(self, path: Path) -> str:
        return path.relative_to(self.project_root).as_posix()

    def _summary(
        self, *, config: IndexingConfig, **counts: object
    ) -> dict[str, object]:
        return {
            **counts,
            "indexing_version": config.indexing_version,
            "collection_name": config.qdrant.collection_name,
            "dense_model": config.dense.model_name,
            "dense_dimension": config.dense.dimension,
            "bm25_model": config.bm25.model_name,
        }
