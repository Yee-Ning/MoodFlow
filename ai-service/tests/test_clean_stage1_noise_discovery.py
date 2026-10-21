"""Unit tests for Clean Stage 1: Noise Discovery, Candidate Schema, Review, and Drift Monitoring."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.rag.clean.config import (
    ConceptDriftConfig,
    NoiseDiscoveryConfig,
    SamplingConfig,
)
from app.rag.clean.corpus import NormalizedDocumentContext
from app.rag.clean.discovery import (
    CandidateNoisePattern,
    CandidateRecommendedAction,
    NoiseDiscoveryBatchResult,
    NoiseDiscoveryService,
)
from app.rag.clean.drift import ConceptDriftEvent, ConceptDriftMonitor
from app.rag.clean.review import (
    CandidateReviewEntry,
    CandidateReviewFile,
    CandidateReviewManager,
)
from app.rag.clean.sampling import StratifiedSampler
from tests.clean_helpers import normalized_document


def make_context(
    doc_id: str,
    source: str,
    source_type: str,
    text: str,
    tmp_path: Path,
) -> NormalizedDocumentContext:
    doc_path = tmp_path / f"{doc_id}.json"
    norm_doc = normalized_document(
        doc_id,
        text,
        title=f"Title {doc_id}",
        publisher=source,
        source_type=source_type,
    )
    return NormalizedDocumentContext(
        document=norm_doc,
        normalized_path=doc_path,
        relative_normalized_path=doc_path.name,
        source=source,
        source_type=source_type,
        document_type="news",
        document_type_origin=None,
        document_type_sidecar_version=None,
        document_type_sidecar_checksum=None,
    )


def test_stratified_sampling_by_source_type_and_site(tmp_path):
    docs = [
        make_context("doc-html-1", "siteA", "HTML", "article 1", tmp_path),
        make_context("doc-html-2", "siteA", "HTML", "article 2", tmp_path),
        make_context("doc-html-3", "siteA", "HTML", "article 3", tmp_path),
        make_context("doc-pdf-1", "siteA", "PDF", "pdf doc 1", tmp_path),
        make_context("doc-pdf-2", "siteA", "PDF", "pdf doc 2", tmp_path),
        make_context("doc-json-1", "siteB", "JSON", "json doc 1", tmp_path),
    ]
    sampler = StratifiedSampler(
        SamplingConfig(
            seed=42,
            sample_size=10,
            samples_per_stratum=2,
            length_buckets=[
                {"name": "short", "max_chars": 500},
                {"name": "long", "max_chars": None},
            ],
        )
    )
    manifest = sampler.sample_by_source_type_and_site(docs, samples_per_stratum=2)
    # Strata are: (HTML, siteA), (PDF, siteA), (JSON, siteB)
    assert manifest.actual_size == 5  # 2 from HTML/siteA, 2 from PDF/siteA, 1 from JSON/siteB
    strata_counts = {}
    for doc in manifest.documents:
        key = (doc.stratum["source_type"], doc.stratum["source"])
        strata_counts[key] = strata_counts.get(key, 0) + 1

    assert strata_counts[("HTML", "siteA")] == 2
    assert strata_counts[("PDF", "siteA")] == 2
    assert strata_counts[("JSON", "siteB")] == 1


def test_resampling_triggered_on_new_strata(tmp_path):
    initial_docs = [
        make_context("doc-1", "siteA", "HTML", "article 1", tmp_path),
    ]
    sampler = StratifiedSampler(
        SamplingConfig(
            seed=42,
            sample_size=10,
            length_buckets=[{"name": "any", "max_chars": None}],
        )
    )
    manifest1 = sampler.sample_by_source_type_and_site(initial_docs, samples_per_stratum=1)
    assert len(manifest1.documents) == 1

    # Same docs -> no new strata
    assert sampler.detect_new_strata(manifest1, initial_docs) == []
    assert sampler.resample_if_new_strata(manifest1, initial_docs) is None

    # New docs with new source_type and site
    extended_docs = list(initial_docs) + [
        make_context("doc-2", "siteB", "PDF", "pdf article", tmp_path),
    ]
    new_strata = sampler.detect_new_strata(manifest1, extended_docs)
    assert len(new_strata) == 1
    assert new_strata[0] == {"source_type": "PDF", "source": "siteB"}

    manifest2 = sampler.resample_if_new_strata(manifest1, extended_docs, samples_per_stratum=1)
    assert manifest2 is not None
    assert manifest2.actual_size == 2


@pytest.mark.asyncio
async def test_llm_noise_discovery_with_schema_positive_negative_examples(tmp_path):
    docs = [
        make_context("doc-1", "siteA", "HTML", "noise text in doc", tmp_path),
    ]
    sampler = StratifiedSampler(
        SamplingConfig(
            seed=42,
            sample_size=1,
            length_buckets=[{"name": "any", "max_chars": None}],
        )
    )
    manifest = sampler.sample(docs)

    candidate = CandidateNoisePattern(
        pattern_or_example="^相关推荐$",
        noise_type="navigation_recommendation",
        recommended_action=CandidateRecommendedAction.DROP_LINE,
        applicable_source=["siteA"],
        applicable_document_type=["news"],
        reason="Repeated recommendation header",
        confidence=0.95,
        false_positive_risk="low",
        uncertain=False,
        evidence_document_ids=[docs[0].document.document_id],
        positive_examples=["相关推荐", "相关阅读"],
        negative_examples=["推荐专辑：七里香", "推荐指数：5星"],
        priority=80,
        pattern="^相关(?:推荐|阅读)$",
    )

    mock_runtime = AsyncMock()
    mock_runtime.analyze.return_value = NoiseDiscoveryBatchResult(
        candidates=[candidate],
        batch_notes=["Observed UI block"],
    )

    service = NoiseDiscoveryService(
        NoiseDiscoveryConfig(prompt_version="v0.2", batch_size=2, max_candidates_per_batch=5),
        clean_version="v0.2",
        max_text_chars_per_document=1000,
        runtime=mock_runtime,
    )

    result = await service.discover(docs, manifest)
    assert result["candidate_count"] == 1
    c_out = result["candidate_rules"][0]
    assert c_out["positive_examples"] == ["相关推荐", "相关阅读"]
    assert c_out["negative_examples"] == ["推荐专辑：七里香", "推荐指数：5星"]
    assert c_out["priority"] == 80
    assert c_out["pattern"] == "^相关(?:推荐|阅读)$"
    assert result["registry_modified"] is False
    assert result["human_review_required"] is True


def test_candidate_review_and_approval_workflow(tmp_path):
    review_path = tmp_path / "candidate_review.yaml"
    entry = CandidateReviewEntry(
        candidate_id="cand-001",
        review_status="unreviewed",
        pattern_or_example="^点击下载客户端$",
        noise_type="app_download",
        recommended_action=CandidateRecommendedAction.DROP_LINE,
        applicable_source=["siteA"],
        applicable_document_type=[],
        reason="App promotion line",
        confidence=0.9,
        false_positive_risk="low",
        uncertain=False,
        evidence_document_ids=["doc-1"],
        positive_examples=["点击下载客户端"],
        negative_examples=["客户端版本信息"],
        priority=120,
        pattern="^点击下载客户端$",
    )
    review_file = CandidateReviewFile(clean_version="v0.2", candidates=[entry])
    CandidateReviewManager.save_review_file(review_file, review_path)

    loaded = CandidateReviewManager.load_review_file(review_path)
    assert loaded.candidates[0].review_status == "unreviewed"

    approved = CandidateReviewManager.approve_candidate(
        loaded,
        "cand-001",
        reviewed_by="auditor_test",
        notes="Confirmed noise across siteA",
        assigned_priority=115,
    )
    assert approved.candidates[0].review_status == "approved"
    assert approved.candidates[0].reviewed_by == "auditor_test"
    assert approved.candidates[0].priority == 115

    rules = CandidateReviewManager.convert_approved_to_rules(approved)
    assert len(rules) == 1
    assert rules[0].action == "DROP_LINE"
    assert rules[0].priority == 115
    assert rules[0].pattern == "^点击下载客户端$"
    assert rules[0].scope["type"] == "SOURCE_SPECIFIC"
    assert rules[0].scope["sources"] == ["siteA"]


def test_concept_drift_monitor_logging_and_hooks():
    monitor = ConceptDriftMonitor(
        ConceptDriftConfig(
            enabled=True,
            min_retention_ratio=0.30,
            max_retention_ratio=1.05,
        )
    )
    events: list[ConceptDriftEvent] = []
    monitor.register_hook(events.append)

    # Normal cleaning: 1000 -> 800 chars (retention 0.8)
    e1 = monitor.evaluate("a" * 1000, "a" * 800, document_id="doc-normal", source="test")
    assert not e1.drift_detected
    assert len(e1.reasons) == 0

    # Excessive deletion: 1000 -> 100 chars (retention 0.1 < 0.3)
    e2 = monitor.evaluate("a" * 1000, "a" * 100, document_id="doc-drop", source="test")
    assert e2.drift_detected
    assert any("length_ratio_too_low" in r for r in e2.reasons)

    # Text expansion anomaly: 1000 -> 1200 chars (retention 1.2 > 1.05)
    e3 = monitor.evaluate("a" * 1000, "a" * 1200, document_id="doc-expand", source="test")
    assert e3.drift_detected
    assert any("length_ratio_expanded" in r for r in e3.reasons)

    assert len(events) == 3
