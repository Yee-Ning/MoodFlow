from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from qdrant_client import QdrantClient, models

from app.rag.chunk.models import ChildChunk, ChunkType, ParentChunk
from app.rag.enrichment.models import (
    ChunkEnrichment,
    ContentMetadata,
    ContentType,
    EnrichedChildChunk,
    EnrichedParentChunk,
    EnrichmentStatus,
    ProvenanceMetadata,
    QualityMetadata,
    SemanticMetadata,
    SourceMetadata,
    TemporalMetadata,
)
from app.rag.indexing import (
    DenseEmbeddingConfig,
    IndexCorpusError,
    IndexCorpusReader,
    IndexDocumentProjector,
    IndexingConfig,
    IndexingValidator,
    OfflineIndexingService,
    PayloadIndexConfig,
    QdrantIndexConfig,
    QdrantIndexStore,
    QwenDenseEncoder,
)
from app.rag.knowledge.claims.models import (
    Claim,
    ClaimPredicate,
    ClaimSource,
    ClaimTime,
)
from app.rag.knowledge.entity_linking.models import CanonicalEntity, EntityType
from app.rag.knowledge.models import (
    KnowledgeEnrichedChildChunk,
    KnowledgeEnrichedParentChunk,
    KnowledgeEntities,
    KnowledgeEnrichment,
    KnowledgeProvenance,
    KnowledgeQuality,
    KnowledgeStats,
    KnowledgeStatus,
)
from app.rag.knowledge.verification.models import (
    ClaimVerification,
    VerificationStatus,
    VerifiedClaim,
)
from run_index import main as run_index_main


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _config(**updates: object) -> IndexingConfig:
    config = IndexingConfig(
        retry_count=0,
        embedding_batch_size=8,
        dense=DenseEmbeddingConfig(
            model_revision="97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3",
            dimension=32,
            local_files_only=True,
        ),
        qdrant=QdrantIndexConfig(
            collection_name="test_music_knowledge_v1",
            upsert_batch_size=8,
            payload_indexes=(PayloadIndexConfig(field="doc_id", schema="keyword"),),
        ),
    )
    return config.model_copy(update=updates)


def _enrichment() -> ChunkEnrichment:
    return ChunkEnrichment(
        content=ContentMetadata(
            language="zh-CN", content_type=ContentType.RELEASE_EVENT
        ),
        semantic=SemanticMetadata(topics=["专辑发行"], keywords=["周杰伦", "Jay"]),
        temporal=TemporalMetadata(year=2000, raw_mentions=["2000年"]),
        source=SourceMetadata(
            source_id="source-1",
            source_type="article",
            source_name="测试来源",
            source_url="https://example.com/music",
        ),
        quality=QualityMetadata(enrichment_status=EnrichmentStatus.SUCCESS),
        provenance=ProvenanceMetadata(
            enrichment_version="v1",
            schema_version="v1",
            prompt_version="v1",
            input_hash="sha256:" + "1" * 64,
        ),
    )


def _knowledge() -> KnowledgeEnrichment:
    entities = KnowledgeEntities(
        linked_entities=[
            CanonicalEntity(
                entity_id="artist:jay_chou",
                entity_type=EntityType.ARTIST,
                canonical_name="周杰伦",
                aliases=["周董", "Jay Chou"],
                confidence=0.99,
            )
        ]
    )
    claim = Claim(
        claim_id="claim:" + "a" * 24,
        statement="周杰伦于2000年发行专辑Jay",
        subject_entity_id="artist:jay_chou",
        predicate=ClaimPredicate.RELEASED,
        object_value="Jay",
        time=ClaimTime(year=2000, raw="2000年"),
        source_chunk_id="child-1",
        evidence_span="2000年，周杰伦发行专辑《Jay》。",
        confidence=0.98,
        extraction_source=ClaimSource.RULE,
    )
    claims = [
        VerifiedClaim(
            claim=claim,
            verification=ClaimVerification(
                status=VerificationStatus.SUPPORTED,
                confidence=0.99,
                evidence_span=claim.evidence_span,
                reason="source_explicitly_supports_claim",
            ),
        )
    ]
    return KnowledgeEnrichment(
        entities=entities,
        claims=claims,
        stats=KnowledgeStats.build(entities, claims),
        quality=KnowledgeQuality(status=KnowledgeStatus.SUCCESS),
        provenance=KnowledgeProvenance(
            entity_linking_version="v1",
            claim_extraction_version="v1",
            claim_verification_version="v1",
            registry_version="v1",
            prompt_version="v1",
            knowledge_input_hash="sha256:" + "2" * 64,
        ),
    )


def _parent() -> KnowledgeEnrichedParentChunk:
    parent = ParentChunk(
        doc_id="doc-1",
        chunk_id="parent-1",
        chunk_index=0,
        chunk_type=ChunkType.NARRATIVE,
        section_path=["专辑"],
        breadcrumb="周杰伦 > 专辑",
        content="2000年，周杰伦发行专辑《Jay》。",
        token_count=18,
    )
    enriched = EnrichedParentChunk(**parent.model_dump(), enrichment=_enrichment())
    return KnowledgeEnrichedParentChunk(**enriched.model_dump(), knowledge=_knowledge())


def _child(
    *, chunk_id: str = "child-1", content: str = "2000年，周杰伦发行专辑《Jay》。"
) -> KnowledgeEnrichedChildChunk:
    child = ChildChunk(
        doc_id="doc-1",
        chunk_id=chunk_id,
        parent_id="parent-1",
        chunk_index=0,
        chunk_type=ChunkType.NARRATIVE,
        section_path=["专辑"],
        breadcrumb="周杰伦 > 专辑",
        content=content,
        retrieval_content=f"周杰伦 > 专辑\n\n{content}",
        token_count=max(1, len(content)),
    )
    enriched = EnrichedChildChunk(**child.model_dump(), enrichment=_enrichment())
    knowledge = _knowledge()
    if chunk_id != "child-1":
        claims = [
            item.model_copy(
                update={
                    "claim": item.claim.model_copy(update={"source_chunk_id": chunk_id})
                }
            )
            for item in knowledge.claims
        ]
        knowledge = knowledge.model_copy(
            update={
                "claims": claims,
                "stats": KnowledgeStats.build(knowledge.entities, claims),
            }
        )
    return KnowledgeEnrichedChildChunk(**enriched.model_dump(), knowledge=knowledge)


def _write_corpus(root: Path, children: list[KnowledgeEnrichedChildChunk]) -> Path:
    path = root / "knowledge.jsonl"
    rows = [_parent(), *children]
    path.write_text(
        "".join(
            json.dumps(row.model_dump(mode="json"), ensure_ascii=False) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )
    return path


class StaticEncoder:
    def __init__(self, failing_text: str | None = None) -> None:
        self.failing_text = failing_text
        self.calls = 0

    def encode_documents(self, texts):  # type: ignore[no-untyped-def]
        self.calls += 1
        if self.failing_text and any(self.failing_text in item for item in texts):
            raise RuntimeError("isolated_embedding_failure")
        return [[1.0, *([0.0] * 31)] for _ in texts]


class MemoryStore:
    def __init__(self, failing_chunk: str | None = None) -> None:
        self.failing_chunk = failing_chunk
        self.points = {}
        self.ensure_calls = 0
        self.deleted: list[str] = []

    def ensure_collection(self) -> None:
        self.ensure_calls += 1

    def existing_hashes(self, point_ids):  # type: ignore[no-untyped-def]
        return {
            point_id: self.points[point_id].document.input_hash
            for point_id in point_ids
            if point_id in self.points
        }

    def upsert(self, points):  # type: ignore[no-untyped-def]
        if self.failing_chunk and any(
            point.document.chunk_id == self.failing_chunk for point in points
        ):
            raise RuntimeError("isolated_upsert_failure")
        for point in points:
            self.points[point.document.point_id] = point

    def all_point_ids(self) -> set[str]:
        return set(self.points)

    def delete_points(self, point_ids):  # type: ignore[no-untyped-def]
        self.deleted.extend(point_ids)
        for point_id in point_ids:
            self.points.pop(point_id, None)

    def point_count(self) -> int:
        return len(self.points)


class ProjectionTests(unittest.TestCase):
    def test_child_projection_is_deterministic_and_keeps_dense_text_natural(
        self,
    ) -> None:
        projector = IndexDocumentProjector(_config())
        chunk = _child()
        parent = _parent()

        first = projector.project(chunk, parent)
        second = projector.project(chunk, parent)

        self.assertEqual(first.point_id, second.point_id)
        self.assertEqual(first.input_hash, second.input_hash)
        self.assertEqual(first.dense_text, chunk.retrieval_content)
        self.assertIn("周董", first.bm25_text)
        self.assertIn("Jay Chou", first.bm25_text)
        self.assertNotIn("source_explicitly_supports_claim", first.bm25_text)
        self.assertEqual(first.payload["record_type"], "child")
        self.assertEqual(first.payload["parent_id"], "parent-1")
        self.assertEqual(first.payload["parent_context"]["chunk_id"], "parent-1")
        self.assertEqual(first.payload["parent_context"]["content"], parent.content)
        self.assertEqual(
            first.payload["supported_claim_ids"], ["claim:" + "a" * 24]
        )

    def test_content_change_changes_index_input_hash(self) -> None:
        projector = IndexDocumentProjector(_config())
        parent = _parent()

        original = projector.project(_child(), parent)
        changed = projector.project(
            _child(content="周杰伦发行了另一张专辑。"), parent
        )

        self.assertNotEqual(original.input_hash, changed.input_hash)

    def test_parent_content_change_changes_index_input_hash(self) -> None:
        projector = IndexDocumentProjector(_config())
        parent = _parent()

        original = projector.project(_child(), parent)
        changed = projector.project(
            _child(), parent.model_copy(update={"content": "更新后的完整父块上下文。"})
        )

        self.assertNotEqual(original.input_hash, changed.input_hash)

    def test_runtime_only_dense_options_do_not_change_index_input_hash(self) -> None:
        base = _config()
        runtime_changed = base.model_copy(
            update={
                "dense": base.dense.model_copy(
                    update={"device": "cuda", "local_files_only": False}
                )
            }
        )

        original = IndexDocumentProjector(base).project(_child(), _parent())
        changed = IndexDocumentProjector(runtime_changed).project(_child(), _parent())

        self.assertEqual(original.input_hash, changed.input_hash)

    def test_parent_mismatch_is_rejected(self) -> None:
        parent = _parent().model_copy(update={"chunk_id": "parent-other"})

        with self.assertRaisesRegex(ValueError, "parent_id_mismatch"):
            IndexDocumentProjector(_config()).project(_child(), parent)

    def test_validator_rejects_missing_parent_context(self) -> None:
        document = IndexDocumentProjector(_config()).project(_child(), _parent())
        payload = dict(document.payload)
        payload.pop("parent_context")

        with self.assertRaisesRegex(ValueError, "payload_parent_context_missing"):
            IndexingValidator(_config()).validate_document(
                document.model_copy(update={"payload": payload})
            )


class DensePoolingTests(unittest.TestCase):
    def test_last_token_pool_supports_left_and_right_padding(self) -> None:
        import torch

        hidden = torch.tensor(
            [
                [[1.0, 0.0], [2.0, 0.0], [3.0, 0.0]],
                [[4.0, 0.0], [5.0, 0.0], [6.0, 0.0]],
            ]
        )
        left_mask = torch.tensor([[0, 1, 1], [0, 0, 1]])
        right_mask = torch.tensor([[1, 1, 0], [1, 0, 0]])

        left = QwenDenseEncoder._last_token_pool(hidden, left_mask, torch)
        right = QwenDenseEncoder._last_token_pool(hidden, right_mask, torch)

        self.assertEqual(left.tolist(), [[3.0, 0.0], [6.0, 0.0]])
        self.assertEqual(right.tolist(), [[2.0, 0.0], [4.0, 0.0]])


class CorpusTests(unittest.TestCase):
    def test_missing_parent_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT) as temporary:
            root = Path(temporary)
            path = root / "knowledge.jsonl"
            path.write_text(
                json.dumps(_child().model_dump(mode="json"), ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(IndexCorpusError, "missing_parent"):
                IndexCorpusReader(root, knowledge_path=path).load()


class OfflineIndexingServiceTests(unittest.TestCase):
    def test_rerun_skips_unchanged_points_before_embedding(self) -> None:
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT) as temporary:
            root = Path(temporary)
            knowledge_path = _write_corpus(root, [_child()])
            encoder = StaticEncoder()
            store = MemoryStore()
            service = OfflineIndexingService(
                root,
                _config(),
                knowledge_path=knowledge_path,
                output_root=root / "output",
                encoder=encoder,
                store=store,
            )

            first = service.run()
            second = service.run()

            self.assertEqual(first.summary["indexed_count"], 1)
            self.assertEqual(second.summary["indexed_count"], 0)
            self.assertEqual(second.summary["skipped_count"], 1)
            self.assertEqual(encoder.calls, 1)
            self.assertTrue(second.manifest_path and second.manifest_path.is_file())

    def test_embedding_failure_does_not_drop_other_child(self) -> None:
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT) as temporary:
            root = Path(temporary)
            knowledge_path = _write_corpus(
                root,
                [
                    _child(chunk_id="child-good"),
                    _child(chunk_id="child-bad", content="触发向量失败"),
                ],
            )
            store = MemoryStore()
            outcome = OfflineIndexingService(
                root,
                _config(),
                knowledge_path=knowledge_path,
                output_root=root / "output",
                encoder=StaticEncoder("触发向量失败"),
                store=store,
            ).run()

            self.assertEqual(outcome.summary["indexed_count"], 1)
            self.assertEqual(outcome.summary["failed_count"], 1)
            self.assertEqual(outcome.failures[0].stage.value, "embedding")
            self.assertEqual(len(store.points), 1)

    def test_upsert_failure_isolated_by_splitting_batch(self) -> None:
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT) as temporary:
            root = Path(temporary)
            knowledge_path = _write_corpus(
                root,
                [_child(chunk_id="child-good"), _child(chunk_id="child-bad")],
            )
            store = MemoryStore(failing_chunk="child-bad")
            outcome = OfflineIndexingService(
                root,
                _config(),
                knowledge_path=knowledge_path,
                output_root=root / "output",
                encoder=StaticEncoder(),
                store=store,
            ).run()

            self.assertEqual(outcome.summary["indexed_count"], 1)
            self.assertEqual(outcome.summary["failed_count"], 1)
            self.assertEqual(outcome.failures[0].stage.value, "upsert")

    def test_prune_requires_full_success_and_full_corpus(self) -> None:
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT) as temporary:
            root = Path(temporary)
            knowledge_path = _write_corpus(root, [_child()])
            service = OfflineIndexingService(
                root,
                _config(),
                knowledge_path=knowledge_path,
                encoder=StaticEncoder(),
                store=MemoryStore(),
            )

            with self.assertRaisesRegex(ValueError, "limited run"):
                service.run(limit=1, prune_stale=True)
            with self.assertRaisesRegex(ValueError, "validation-only"):
                service.run(validate_only=True, prune_stale=True)

    def test_successful_full_run_can_prune_stale_points(self) -> None:
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT) as temporary:
            root = Path(temporary)
            knowledge_path = _write_corpus(root, [_child()])
            store = MemoryStore()
            store.points["00000000-0000-5000-8000-000000000000"] = object()
            outcome = OfflineIndexingService(
                root,
                _config(),
                knowledge_path=knowledge_path,
                output_root=root / "output",
                encoder=StaticEncoder(),
                store=store,
            ).run(prune_stale=True)

            self.assertEqual(outcome.summary["pruned_count"], 1)
            self.assertEqual(store.deleted, ["00000000-0000-5000-8000-000000000000"])

    def test_validate_only_never_initializes_model_or_qdrant(self) -> None:
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT) as temporary:
            root = Path(temporary)
            knowledge_path = _write_corpus(root, [_child()])
            outcome = OfflineIndexingService(
                root,
                _config(),
                knowledge_path=knowledge_path,
            ).run(validate_only=True)

            self.assertEqual(outcome.summary["status"], "validated")
            self.assertIsNone(outcome.manifest_path)


class QdrantStoreTests(unittest.TestCase):
    def test_collection_has_only_dense_and_bm25_named_vectors(self) -> None:
        client = QdrantClient(":memory:")
        config = _config(
            qdrant=_config().qdrant.model_copy(update={"payload_indexes": ()})
        )
        store = QdrantIndexStore(config, client=client)

        store.ensure_collection()
        info = client.get_collection(config.qdrant.collection_name)

        self.assertEqual(set(info.config.params.vectors), {"dense"})
        self.assertEqual(set(info.config.params.sparse_vectors or {}), {"bm25"})
        self.assertEqual(info.config.params.vectors["dense"].size, 32)
        self.assertEqual(
            info.config.params.sparse_vectors["bm25"].modifier,
            models.Modifier.IDF,
        )

    def test_upsert_sends_dense_vector_and_multilingual_bm25_document(self) -> None:
        class CapturingClient:
            def __init__(self) -> None:
                self.points = None

            def upsert(self, *, collection_name, points, wait):  # type: ignore[no-untyped-def]
                self.points = points

        config = _config()
        document = IndexDocumentProjector(config).project(_child(), _parent())
        from app.rag.indexing.models import IndexPoint

        client = CapturingClient()
        QdrantIndexStore(config, client=client).upsert(  # type: ignore[arg-type]
            [IndexPoint(document=document, dense_vector=[1.0, *([0.0] * 31)])]
        )

        self.assertIsNotNone(client.points)
        point = client.points[0]
        self.assertEqual(point.vector["dense"], [1.0, *([0.0] * 31)])
        self.assertEqual(point.vector["bm25"].model, "qdrant/bm25")
        self.assertEqual(point.vector["bm25"].options["tokenizer"], "multilingual")
        self.assertEqual(point.vector["bm25"].options["stemmer"], {"type": "none"})
        self.assertEqual(point.vector["bm25"].options["stopwords"], {})


class IndexingCliTests(unittest.TestCase):
    def test_validate_only_smoke_needs_neither_model_nor_qdrant(self) -> None:
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT) as temporary:
            root = Path(temporary)
            knowledge_path = _write_corpus(root, [_child()])
            output = StringIO()

            with redirect_stdout(output):
                status = run_index_main(
                    [
                        "--config",
                        str(
                            PROJECT_ROOT
                            / "app"
                            / "rag"
                            / "indexing"
                            / "config"
                            / "v1.yaml"
                        ),
                        "--knowledge-path",
                        str(knowledge_path),
                        "--validate-only",
                    ],
                    project_root=root,
                )

            payload = json.loads(output.getvalue())
            self.assertEqual(status, 0)
            self.assertEqual(payload["status"], "validated")
            self.assertEqual(payload["eligible_count"], 1)
            self.assertIsNone(payload["manifest"])


if __name__ == "__main__":
    unittest.main()
