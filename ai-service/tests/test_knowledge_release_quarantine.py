import json
import tempfile
from pathlib import Path

import pytest

import run_knowledge_release
from app.rag.clean.io import atomic_write_json, atomic_write_jsonl
from app.rag.enrichment.models import EnrichedChildChunk, EntityTypeHint
from app.rag.indexing.release import KnowledgeReleaseValidator
from app.rag.knowledge import (
    KnowledgeConfig,
    KnowledgePipeline,
    KnowledgeReleaseBuilder,
)
from app.rag.knowledge.config import DEFAULT_KNOWLEDGE_CONFIG_PATH
from app.rag.knowledge.entity_linking.registry import DEFAULT_ENTITY_REGISTRY_PATH
from app.rag.knowledge.models import KnowledgeStats, KnowledgeStatus
from app.rag.knowledge.qa import sha256_file
from app.rag.knowledge.report import KnowledgeQualityReporter
from app.rag.knowledge.verification.models import VerificationStatus
from app.rag.knowledge.writer import KnowledgeDatasetWriter
from tests.test_rag_knowledge import _enriched


PROJECT_ROOT = Path(__file__).resolve().parents[1]
NON_SUPPORTED = [s for s in VerificationStatus if s is not VerificationStatus.SUPPORTED]


@pytest.fixture
def root():
    with tempfile.TemporaryDirectory(dir=PROJECT_ROOT) as temporary:
        yield Path(temporary)


async def _fixture(root, statuses=None, *, bad_level="parent"):
    statuses = statuses or [
        VerificationStatus.SUPPORTED,
        VerificationStatus.PARTIALLY_SUPPORTED,
        VerificationStatus.INSUFFICIENT_EVIDENCE,
    ]
    sources, chunks = [], []
    pipeline = KnowledgePipeline(KnowledgeConfig(llm_enabled=False))
    for index, status in enumerate(statuses):
        parent = _enriched(
            "2000年周杰伦发行首张专辑《Jay》。",
            chunk_id=f"parent-{index}",
            mentions=[("周杰伦", EntityTypeHint.ARTIST), ("Jay", EntityTypeHint.ALBUM)],
        )
        parent = parent.model_copy(update={"doc_id": f"doc-{index}"})
        child = EnrichedChildChunk(
            **{
                **parent.model_dump(),
                "chunk_id": f"child-{index}",
                "parent_id": parent.chunk_id,
                "retrieval_content": parent.content,
            }
        )
        for source in (parent, child):
            result = await pipeline.process(source)
            assert result.knowledge.claims
            is_bad_level = (source.parent_id is None) == (bad_level == "parent")
            if status is not VerificationStatus.SUPPORTED and is_bad_level:
                claims = [
                    item.model_copy(
                        update={
                            "verification": item.verification.model_copy(
                                update={
                                    "status": status,
                                    "reason": "synthetic_non_supported_verdict",
                                }
                            )
                        }
                    )
                    for item in result.knowledge.claims
                ]
                result = result.model_copy(
                    update={
                        "knowledge": result.knowledge.model_copy(
                            update={
                                "claims": claims,
                                "stats": KnowledgeStats.build(
                                    result.knowledge.entities, claims
                                ),
                                "quality": result.knowledge.quality.model_copy(
                                    update={"status": KnowledgeStatus.PARTIAL}
                                ),
                            }
                        )
                    }
                )
            sources.append(source)
            chunks.append(result)
    enriched = root / "enriched.jsonl"
    atomic_write_jsonl(enriched, (source.model_dump(mode="json") for source in sources))
    _persist(root, chunks)
    builder = KnowledgeReleaseBuilder(
        PROJECT_ROOT,
        source_root=root / "working",
        output_root=root / "v2",
        review_root=root / "review",
        enriched_path=enriched,
        config_path=DEFAULT_KNOWLEDGE_CONFIG_PATH,
        registry_path=DEFAULT_ENTITY_REGISTRY_PATH,
        claim_sample_size=1,
        entity_sample_size=1,
    )
    return builder, chunks


def _persist(root, chunks):
    KnowledgeDatasetWriter(PROJECT_ROOT, root / "working").write(chunks)
    KnowledgeQualityReporter(PROJECT_ROOT, root / "working").write(chunks)


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _rows(path):
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _approve(builder):
    manifest = _read(builder.review_root / "qa_sampling_manifest.json")
    atomic_write_json(
        builder.review_root / "qa_review_attestation.json",
        {
            "attestation_version": "v1",
            "review_status": "approved",
            "reviewer": "synthetic-reviewer",
            "claim_sample_sha256": manifest["claim_sample_sha256"],
            "entity_link_sample_sha256": manifest["entity_link_sample_sha256"],
            "claim_reviewed_count": manifest["claim_sample_count"],
            "entity_link_reviewed_count": manifest["entity_link_sample_count"],
            "generation_mode": "rule_only",
            "rejected_sample_ids": [],
            "release_selection_sha256": manifest["release_selection_sha256"],
        },
    )


@pytest.mark.asyncio
async def test_mixed_batch_releases_good_documents_with_complete_hashed_artifacts(root):
    builder, chunks = await _fixture(root)
    prepared = builder.prepare_review()
    assert prepared.summary["input_document_count"] == 3
    assert prepared.summary["quarantined_document_count"] == 2
    _approve(builder)
    first = builder.finalize()
    assert first.status == "created" and builder.finalize().status == "unchanged"
    released = _rows(builder.output_root / "knowledge_enriched_chunks.jsonl")
    assert {row["doc_id"] for row in released} == {"doc-0"}
    assert len(released) == 2
    corpus_manifest = _rows(builder.output_root / "knowledge_corpus_manifest.jsonl")
    assert len(corpus_manifest) == 1 and corpus_manifest[0]["chunk_count"] == 2
    assert (
        corpus_manifest[0]["knowledge_chunks_path"]
        == (builder.output_root / "knowledge_enriched_chunks.jsonl")
        .relative_to(PROJECT_ROOT)
        .as_posix()
    )
    manifest = _read(builder.output_root / "knowledge_release_manifest.json")
    assert manifest["quality"]["all_claims_supported"] is True
    assert manifest["quality"]["pre_embedding_release_ready"] is True
    assert manifest["quality"]["chunk_count"] == 2
    assert manifest["manual_review"]["quarantined_document_count"] == 2
    for filename, expected in manifest["artifacts"].items():
        assert sha256_file(builder.output_root / filename) == expected
    review = _rows(builder.output_root / "manual_review_documents.jsonl")
    assert {row["document_id"] for row in review} == {"doc-1", "doc-2"}
    assert all(row["disposition"] == "quarantined" for row in review)
    report = _read(builder.output_root / "knowledge_quality_report.json")
    assert (
        report["document_count"] == 1
        and report["manual_review"]["input_document_count"] == 3
    )
    assert [chunk.model_dump(mode="json") for chunk in chunks] == _rows(
        builder.source_root / "knowledge_enriched_chunks.jsonl"
    )
    assert (
        KnowledgeReleaseValidator(
            PROJECT_ROOT,
            knowledge_path=builder.output_root / "knowledge_enriched_chunks.jsonl",
        )
        .validate()
        .chunk_count
        == 2
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", NON_SUPPORTED)
@pytest.mark.parametrize("bad_level", ["parent", "child"])
async def test_every_non_supported_verdict_quarantines_the_whole_document(
    root, status, bad_level
):
    builder, _ = await _fixture(
        root, [VerificationStatus.SUPPORTED, status], bad_level=bad_level
    )
    result = builder.prepare_review()
    (row,) = result.summary["manual_review_documents"]
    assert row["chunk_ids"] == ["child-1", "parent-1"]
    assert row["problem_claims"][0]["verification_status"] == status.value
    assert (
        result.summary["output_document_count"]
        == result.summary["quarantined_document_count"]
        == 1
    )


@pytest.mark.asyncio
async def test_explicit_override_preserves_verdicts_and_is_not_sticky(root):
    builder, chunks = await _fixture(root)
    original = {
        path.name: path.read_bytes()
        for path in builder.source_root.iterdir()
        if path.is_file()
    }
    result = builder.prepare_review(include_manual_review=True)
    assert result.summary["quarantined_document_count"] == 0
    assert result.summary["output_document_count"] == 3
    _approve(builder)
    outcome = builder.finalize(include_manual_review=True)
    assert outcome.summary["pre_embedding_release_ready"] is False
    assert outcome.summary["all_claims_supported"] is False
    released = _rows(builder.output_root / "knowledge_enriched_chunks.jsonl")
    assert released == [chunk.model_dump(mode="json") for chunk in chunks]
    manifest = _read(builder.output_root / "knowledge_release_manifest.json")
    assert manifest["quality"]["all_claims_supported"] is False
    assert all(
        row["disposition"] == "included_by_flag"
        for row in manifest["manual_review"]["manual_review_documents"]
    )
    with pytest.raises(ValueError, match="knowledge_release_not_embedding_ready"):
        KnowledgeReleaseValidator(
            PROJECT_ROOT,
            knowledge_path=builder.output_root / "knowledge_enriched_chunks.jsonl",
        ).validate()
    default = builder.prepare_review()
    assert default.summary["output_document_count"] == 1
    assert default.summary["quarantined_document_count"] == 2
    assert all(
        row["disposition"] == "quarantined"
        for row in default.summary["manual_review_documents"]
    )
    assert original == {
        path.name: path.read_bytes()
        for path in builder.source_root.iterdir()
        if path.is_file()
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("initial_override", [False, True])
async def test_changing_admission_flag_requires_matching_review(root, initial_override):
    builder, _ = await _fixture(root)
    builder.prepare_review(include_manual_review=initial_override)
    _approve(builder)
    with pytest.raises(ValueError, match="sample_stale|review_selection_stale"):
        builder.finalize(include_manual_review=not initial_override)
    assert not builder.output_root.exists()


@pytest.mark.asyncio
async def test_review_binds_excluded_source_even_when_samples_do_not_change(root):
    builder, chunks = await _fixture(root)
    builder.prepare_review()
    _approve(builder)
    bad = chunks[2]
    claims = [
        item.model_copy(
            update={
                "verification": item.verification.model_copy(
                    update={"reason": "changed_review_reason"}
                )
            }
        )
        for item in bad.knowledge.claims
    ]
    chunks[2] = bad.model_copy(
        update={"knowledge": bad.knowledge.model_copy(update={"claims": claims})}
    )
    _persist(root, chunks)
    with pytest.raises(ValueError, match="review_selection_stale"):
        builder.finalize()


@pytest.mark.asyncio
async def test_all_quarantined_is_reported_without_an_empty_release(root):
    builder, _ = await _fixture(root, [VerificationStatus.PARTIALLY_SUPPORTED])
    outcome = builder.prepare_review()
    assert (
        outcome.status == "quarantined"
        and outcome.summary["output_document_count"] == 0
    )
    assert (
        _read(builder.review_root / "release_selection_report.json")["manual_review"][
            "quarantined_document_count"
        ]
        == 1
    )
    with pytest.raises(ValueError, match="no_eligible_documents"):
        builder.finalize()
    assert not builder.output_root.exists()
    builder.prepare_review(include_manual_review=True)
    _approve(builder)
    assert builder.finalize(include_manual_review=True).status == "created"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "artifact", ["knowledge_corpus_manifest.jsonl", "knowledge_quality_report.json"]
)
async def test_source_integrity_checks_still_apply_before_quarantine(root, artifact):
    builder, _ = await _fixture(root)
    (builder.source_root / artifact).write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="stale"):
        builder.prepare_review()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["failed", "not_ready", "ungrounded"])
async def test_unrelated_validation_and_quality_gates_are_preserved(root, failure):
    builder, chunks = await _fixture(root, [VerificationStatus.SUPPORTED])
    chunk = chunks[0]
    if failure == "failed":
        chunks[0] = chunk.model_copy(
            update={
                "knowledge": chunk.knowledge.model_copy(
                    update={
                        "quality": chunk.knowledge.quality.model_copy(
                            update={"status": KnowledgeStatus.FAILED}
                        ),
                    }
                )
            }
        )
        error = "quality_gate_failed"
    elif failure == "not_ready":
        enrichment = chunk.enrichment.model_copy(
            update={
                "quality": chunk.enrichment.quality.model_copy(
                    update={"index_ready": False}
                )
            }
        )
        chunks[0] = chunk.model_copy(update={"enrichment": enrichment})
        sources = _rows(builder.enriched_path)
        sources[0]["enrichment"] = enrichment.model_dump(mode="json")
        atomic_write_jsonl(builder.enriched_path, sources)
        error = "enrichment_not_ready"
    else:
        claims = [
            item.model_copy(
                update={
                    "claim": item.claim.model_copy(
                        update={"evidence_span": "不在原文的证据"}
                    )
                }
            )
            for item in chunk.knowledge.claims
        ]
        chunks[0] = chunk.model_copy(
            update={"knowledge": chunk.knowledge.model_copy(update={"claims": claims})}
        )
        error = "claim_evidence_not_grounded"
    _persist(root, chunks)
    with pytest.raises(ValueError, match=error):
        builder.prepare_review()


@pytest.mark.asyncio
async def test_attestation_must_bind_the_release_selection(root):
    builder, _ = await _fixture(root)
    builder.prepare_review()
    _approve(builder)
    path = builder.review_root / "qa_review_attestation.json"
    payload = _read(path)
    payload.pop("release_selection_sha256")
    atomic_write_json(path, payload)
    with pytest.raises(ValueError, match="attestation_invalid"):
        builder.finalize()


@pytest.mark.asyncio
@pytest.mark.parametrize("override", [False, True])
async def test_cli_uses_an_explicit_per_invocation_override(root, capsys, override):
    builder, _ = await _fixture(root)
    args = [
        "--source-root",
        str(builder.source_root),
        "--output-root",
        str(builder.output_root),
        "--review-root",
        str(builder.review_root),
        "--enriched-path",
        str(builder.enriched_path),
        "--claim-sample-size",
        "1",
        "--entity-sample-size",
        "1",
    ]
    if override:
        args.append("--include-manual-review")
    assert (
        run_knowledge_release.main(
            ["--prepare-review", *args], project_root=PROJECT_ROOT
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["include_manual_review"] == override
    _approve(builder)
    assert (
        run_knowledge_release.main(["--finalize", *args], project_root=PROJECT_ROOT)
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["quarantined_document_count"] == (0 if override else 2)
    assert payload["pre_embedding_release_ready"] == (not override)
