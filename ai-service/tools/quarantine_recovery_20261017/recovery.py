"""Run-local recovery of the reviewed 2026-10-17 quarantine partition.

Never run a stage against the historical run or the default data directories.
Document decisions are attributed to their actual reviewer; they do not replace
the separate human Knowledge release attestation.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import sys
from pathlib import Path

AI_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = AI_ROOT.parent
sys.path.insert(0, str(AI_ROOT))
os.environ.setdefault("PYTHON_DOTENV_DISABLED", "1")
os.environ.setdefault("HF_HUB_OFFLINE", "1")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path):
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def write_jsonl(path: Path, values) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in values),
        encoding="utf-8",
        newline="\n",
    )


def guard_run(run: Path) -> dict:
    run = run.resolve()
    namespace = (
        AI_ROOT / "data/reruns/jay-chou-quarantine-recovery-20261017"
    ).resolve()
    if not run.is_relative_to(namespace) or run == namespace:
        raise ValueError("recovery_run_outside_dedicated_namespace")
    manifest = read_json(run / "execution-manifest.json")
    if Path(manifest["run_root"]).resolve() != run:
        raise ValueError("recovery_run_identity_mismatch")
    for row in manifest["protected_inputs"]:
        path = Path(row["path"])
        if not path.is_file() or digest(path) != row["sha256"]:
            raise ValueError(f"protected_input_changed:{path}")
    if digest(Path(manifest["plan_path"])) != manifest["plan_sha256"]:
        raise ValueError("recovery_plan_changed")
    return manifest


def clean_preview(run: Path) -> None:
    from app.rag.clean.config import load_clean_config
    from app.rag.clean.service import CleanPipelineService

    manifest = guard_run(run)
    plan = read_json(Path(manifest["plan_path"]))
    targets = {row["source_id"] for row in plan["documents"]}
    config, checksum = load_clean_config(AI_ROOT / "app/rag/clean/rules/v0.2.yaml")
    service = CleanPipelineService(run, config, registry_checksum=checksum)
    contexts = service.reader.load_all()
    service.engine.template_deduplicator.train_site_templates(
        [
            (ctx.document.document_id, ctx.source, ctx.document.markdown)
            for ctx in contexts
        ]
    )
    summaries = []
    for context in contexts:
        if context.document.source_id not in targets:
            continue
        result = service.engine.clean(context)
        row = {
            "source_id": context.document.source_id,
            "document_id": context.document.document_id,
            "parse_review_reasons": list(context.review_reasons),
            "report": result.single_report.model_dump(mode="json"),
            "before": result.before_text,
            "after": result.after_text,
        }
        write_json(run / "qa/clean-baseline-preview" / f"{row['source_id']}.json", row)
        summaries.append(
            {
                "source_id": row["source_id"],
                "before": len(result.before_text),
                "after": len(result.after_text),
                "warnings": result.document.warnings,
            }
        )
    write_json(run / "qa/clean-baseline-preview-summary.json", summaries)
    guard_run(run)
    print(json.dumps({"preview_documents": len(summaries)}, ensure_ascii=False))


def block_digest(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


class ReviewedBlockRetention:
    """Protect only reviewed exact blocks; retain normal dedup for other blocks.

    The base deduplicator still uses the original 56-document template context.
    No threshold or global source policy changes. Changes to protected text fail
    closed instead of silently re-applying an old document-level decision.
    """

    def __init__(self, base, protected: dict[str, set[str]]) -> None:
        self.base = base
        self.protected = protected

    def deduplicate(self, text: str, *, doc_id: str, site: str):
        from app.rag.clean.models import StageMetrics
        from app.rag.clean.template_dedup import TemplateDeduplicationOutcome

        protected = self.protected.get(doc_id, set())
        if not protected:
            return self.base.deduplicate(text, doc_id=doc_id, site=site)
        blocks = re.split(r"\n\s*\n", text)
        hashes = {block_digest(block) for block in blocks if block.strip()}
        if not protected.issubset(hashes):
            raise ValueError(f"reviewed_block_missing_or_changed:{doc_id}")
        retained, hits = [], []
        removed = 0
        preserved = 0
        for block in blocks:
            if block_digest(block) in protected:
                retained.append(block)
                preserved += 1
                continue
            result = self.base.deduplicate(block, doc_id=doc_id, site=site)
            removed += result.removed_blocks
            hits.extend(result.template_clusters_hit)
            if result.cleaned_text.strip() or not block.strip():
                retained.append(result.cleaned_text)
        after = text if not removed else "\n\n".join(retained)
        if text.endswith("\n") and not after.endswith("\n"):
            after += "\n"
        return TemplateDeduplicationOutcome(
            cleaned_text=after,
            before_chars=len(text),
            after_chars=len(after),
            removed_chars=max(0, len(text) - len(after)),
            removed_blocks=removed,
            template_clusters_hit=hits,
            stage_metrics=StageMetrics(
                stage_index=4,
                stage_name="template_deduplication",
                before_chars=len(text),
                after_chars=len(after),
                removed_chars=max(0, len(text) - len(after)),
                removed_blocks=removed,
                details={
                    "matched_template_blocks": removed,
                    "reviewed_blocks_retained": preserved,
                    "review_scope": "exact_document_and_block_sha256",
                },
            ),
        )


def validate_document_decision(row: dict, run: Path, *, final: bool) -> None:
    required_status = "passed_document_qa" if final else "approved_for_draft"
    if row.get("status") != required_status or row.get("reviewed_by") != "Codex":
        raise ValueError(f"document_qa_not_passed:{row.get('source_id')}")
    if not row.get("reason") or not row.get("evidence") or not row.get("reviewed_at"):
        raise ValueError(f"document_qa_evidence_missing:{row.get('source_id')}")
    for field, relative in [
        ("normalized_sha256", f"data/normalized/{row['source_id']}/document.json"),
        ("raw_sha256", row["raw_path"]),
    ]:
        path = (run / relative).resolve()
        if not path.is_relative_to(run.resolve()) or digest(path) != row[field]:
            raise ValueError(f"document_qa_input_changed:{row['source_id']}:{field}")
    if final and not row.get("cleaned_text_sha256"):
        raise ValueError(f"clean_qa_hash_missing:{row['source_id']}")


def validate_clean_result(row: dict, text: str, warnings: list[str]) -> None:
    text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if text_hash != row.get("cleaned_text_sha256") or warnings != row.get(
        "accepted_clean_warnings"
    ):
        raise ValueError(f"clean_review_stale:{row['source_id']}")


def recovered_clean(run: Path, *, final: bool) -> None:
    from app.rag.clean.config import load_clean_config
    from app.rag.clean.service import CleanPipelineService
    from app.rag.clean.writer import CleanedDatasetWriter

    manifest = guard_run(run)
    decisions = read_json(run / "qa/document-decisions.json")
    expected = {
        row["source_id"]: row["document_id"]
        for row in read_json(Path(manifest["plan_path"]))["documents"]
    }
    actual = {row["source_id"]: row["document_id"] for row in decisions["documents"]}
    if actual != expected or len(decisions["documents"]) != len(expected):
        raise ValueError("document_qa_set_not_exact_recovery_partition")
    admitted = [row for row in decisions["documents"] if row["status"] != "quarantined"]
    for row in admitted:
        validate_document_decision(row, run, final=final)
    config, checksum = load_clean_config(run / "config_snapshot/clean-v0.3.yaml")
    service = CleanPipelineService(
        run,
        config,
        registry_checksum=checksum,
        cleaned_output_root=run / "data/cleaned/v0.3",
    )
    contexts = service.reader.load_all()
    service.engine.template_deduplicator.train_site_templates(
        [
            (ctx.document.document_id, ctx.source, ctx.document.markdown)
            for ctx in contexts
        ]
    )
    retention = ReviewedBlockRetention(
        service.engine.template_deduplicator,
        {
            row["document_id"]: set(row.get("protected_block_sha256", []))
            for row in admitted
        },
    )
    by_source = {row["source_id"]: row for row in admitted}
    results = []
    for context in contexts:
        row = by_source.get(context.document.source_id)
        if row is None:
            continue
        if context.review_reasons:
            raise ValueError(f"parse_review_unresolved:{row['source_id']}")
        result = service.engine.clean(context, template_deduplicator=retention)
        text_hash = hashlib.sha256(result.after_text.encode("utf-8")).hexdigest()
        if final:
            validate_clean_result(row, result.after_text, result.document.warnings)
        write_json(
            run / "qa/recovered-clean" / f"{row['source_id']}.json",
            {
                "source_id": row["source_id"],
                "document_id": row["document_id"],
                "report": result.single_report.model_dump(mode="json"),
                "cleaned_text_sha256": text_hash,
                "before": result.before_text,
                "after": result.after_text,
            },
        )
        results.append(result)
    if len(results) != len(admitted):
        raise ValueError("admitted_source_missing")
    if final:
        CleanedDatasetWriter(
            run,
            clean_version=config.clean_version,
            registry_checksum=checksum,
            output_root=run / "data/cleaned/v0.3",
        ).write(results)
    guard_run(run)
    print(
        json.dumps(
            {
                "stage": "clean" if final else "draft-clean",
                "documents": len(results),
                "warnings": {
                    r.document.source_id: r.document.warnings
                    for r in results
                    if r.document.warnings
                },
            },
            ensure_ascii=False,
        )
    )


def merge_reusing_bytes(
    original: Path, delta: Path, output: Path, *, id_field="chunk_id"
) -> dict:
    if output.resolve() in {original.resolve(), delta.resolve()}:
        raise ValueError("merge_must_not_overwrite_input")
    old, new = read_jsonl(original), read_jsonl(delta)
    ids = [row[id_field] for row in [*old, *new]]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate_or_overlapping_merge_ids")
    old_bytes, new_bytes = original.read_bytes(), delta.read_bytes()
    merged = (
        old_bytes
        + (b"\n" if old_bytes and not old_bytes.endswith(b"\n") else b"")
        + new_bytes
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(merged)
    if not output.read_bytes().startswith(old_bytes):
        raise ValueError("baseline_bytes_not_preserved")
    return {
        "old_rows_reused": len(old),
        "delta_rows": len(new),
        "total_rows": len(ids),
        "old_sha256": digest(original),
        "delta_sha256": digest(delta),
        "output_sha256": digest(output),
    }


def chunk_delta(run: Path) -> None:
    from app.rag.chunk.config import load_chunk_config
    from app.rag.chunk.service import ChunkPipelineService
    from app.rag.chunk.writer import ChunkDatasetWriter

    guard_run(run)
    decisions = read_json(run / "qa/document-decisions.json")["documents"]
    admitted = {
        row["source_id"]: row for row in decisions if row["status"] != "quarantined"
    }
    for row in admitted.values():
        validate_document_decision(row, run, final=True)
    config = load_chunk_config(run / "config_snapshot/chunk-v1.yaml")
    expected = (1000, 1500, 350, 500, 80, 50)
    actual = tuple(
        getattr(config, field)
        for field in [
            "target_parent_tokens",
            "max_parent_tokens",
            "target_child_tokens",
            "max_child_tokens",
            "min_child_tokens",
            "child_overlap_tokens",
        ]
    )
    if actual != expected:
        raise ValueError("chunk_parameters_changed")
    service = ChunkPipelineService(run, config, cleaned_root=run / "data/cleaned/v0.3")
    sources = service.reader.load_all(include_manual_review=True)
    if {src.cleaned.source_id for src in sources} != set(admitted):
        raise ValueError("chunk_manifest_not_exact_qa_allowlist")
    documents, quarantine = [], []
    for source in sources:
        row = admitted[source.cleaned.source_id]
        validate_clean_result(row, source.cleaned.cleaned_text, source.cleaned.warnings)
        if (
            run / "data/chunked/v1" / source.cleaned.source_id / "chunks.json"
        ).exists():
            raise ValueError(f"refuse_chunk_recomputation:{source.cleaned.source_id}")
        document = service.engine.chunk(source.cleaned, source.normalized)
        if not document.validation.valid:
            quarantine.append(
                {
                    "source_id": document.source_id,
                    "document_id": document.document_id,
                    "stage": "chunk",
                    "reason": "chunk_validation_failed",
                    "validation": document.validation.model_dump(mode="json"),
                }
            )
            continue
        documents.append(document)
    if not documents:
        raise ValueError("no_documents_passed_chunk")
    output = run / "data/chunked/v1"
    manifest = ChunkDatasetWriter(run, output).write(documents)
    shutil.copyfile(manifest, output / "delta_corpus_manifest.jsonl")
    baseline = read_jsonl(run / "baseline/old_chunk_manifest.jsonl")
    delta = read_jsonl(manifest)
    if {x["source_id"] for x in baseline} & {x["source_id"] for x in delta}:
        raise ValueError("baseline_source_in_delta")
    write_jsonl(
        output / "combined_corpus_manifest.jsonl",
        sorted(baseline + delta, key=lambda x: x["document_id"]),
    )
    summary = {
        "stage": "chunk",
        "processed_delta_documents": len(sources),
        "passed_documents": len(documents),
        "parents": sum(x.stats.parent_count for x in documents),
        "children": sum(x.stats.child_count for x in documents),
        "quarantined": quarantine,
        "baseline_recomputed": 0,
        "config_fingerprint": config.fingerprint(),
    }
    write_json(run / "chunk-delta-summary.json", summary)
    guard_run(run)
    print(json.dumps(summary, ensure_ascii=False))


async def enrich_delta(run: Path) -> None:
    from app.rag.enrichment.config import load_enrichment_config
    from app.rag.enrichment.service import MetadataEnrichmentService
    from app.rag.enrichment.writer import EnrichmentDatasetWriter

    guard_run(run)
    config = load_enrichment_config(
        AI_ROOT / "app/rag/enrichment/config/v1.yaml"
    ).model_copy(update={"llm_enabled": False})
    write_json(
        run / "config_snapshot/enrichment-rule-only.json",
        config.model_dump(mode="json"),
    )
    output = run / "data/enriched/delta-v1"
    service = MetadataEnrichmentService(
        run,
        config,
        chunked_root=run / "data/chunked/v1",
        chunked_manifest_path=run / "data/chunked/v1/delta_corpus_manifest.jsonl",
        output_root=output,
    )
    outcome = await service.run(
        validate_only=(output / "enriched_chunks.jsonl").exists(), sample_size=0
    )
    bad_docs = {
        x.doc_id for x in outcome.chunks if not x.enrichment.quality.index_ready
    }
    accepted = [x for x in outcome.chunks if x.doc_id not in bad_docs]
    EnrichmentDatasetWriter(run, run / "data/enriched/accepted-delta").write(accepted)
    summary = {
        **outcome.summary,
        "quarantined_document_ids": sorted(bad_docs),
        "accepted_chunks": len(accepted),
        "baseline_recomputed": 0,
    }
    write_json(run / "enrichment-delta-summary.json", summary)
    guard_run(run)
    print(json.dumps(summary, ensure_ascii=False))


async def knowledge_delta(run: Path) -> None:
    from app.rag.knowledge.config import load_knowledge_config
    from app.rag.knowledge.entity_linking import EntityLinker
    from app.rag.knowledge.entity_linking.registry import EntityRegistry
    from app.rag.knowledge.pipeline import KnowledgePipeline
    from app.rag.knowledge.service import KnowledgePipelineService

    guard_run(run)
    manifest = read_json(run / "execution-manifest.json")
    old = Path(manifest["old_run_root"])
    release = read_json(old / "data/knowledge/v2/knowledge_release_manifest.json")
    for entry in release["source"]["entity_registry_files"]:
        source, dest = old / entry["path"], run / entry["path"]
        if digest(source) != entry["sha256"].removeprefix("sha256:"):
            raise ValueError("baseline_registry_changed")
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists() and digest(dest) != digest(source):
            raise ValueError("recovery_registry_changed")
        if not dest.exists():
            shutil.copyfile(source, dest)
    config = load_knowledge_config(run / "config_snapshot/knowledge-v19-rule-only.yaml")
    if config.llm_enabled:
        raise ValueError("paid_knowledge_not_authorized")
    registry = EntityRegistry.load(run / "config_snapshot/entity_registry/v3.yaml")
    pipeline = KnowledgePipeline(
        config, entity_linker=EntityLinker(config, registry=registry)
    )
    output = run / "data/knowledge/delta-v1"
    service = KnowledgePipelineService(
        run,
        config,
        enriched_path=run / "data/enriched/accepted-delta/enriched_chunks.jsonl",
        output_root=output,
        pipeline=pipeline,
    )
    outcome = await service.run(
        validate_only=(output / "knowledge_enriched_chunks.jsonl").exists(),
        sample_size=0,
    )
    summary = {**outcome.summary, "baseline_recomputed": 0}
    write_json(run / "knowledge-delta-summary.json", summary)
    guard_run(run)
    print(json.dumps(summary, ensure_ascii=False))


def release_builder(run: Path):
    from app.rag.knowledge.release import KnowledgeReleaseBuilder

    return KnowledgeReleaseBuilder(
        run,
        source_root=run / "data/knowledge/v1",
        output_root=run / "data/knowledge/v2",
        review_root=run / "data/knowledge/review",
        enriched_path=run / "data/enriched/v1/enriched_chunks.jsonl",
        config_path=run / "config_snapshot/knowledge-v19-rule-only.yaml",
        registry_path=run / "config_snapshot/entity_registry/v3.yaml",
        claim_sample_size=100,
        entity_sample_size=100,
    )


def assemble_review(run: Path) -> None:
    from app.rag.enrichment.writer import EnrichmentDatasetWriter
    from app.rag.knowledge.writer import KnowledgeDatasetWriter
    from app.rag.knowledge.report import KnowledgeQualityReporter
    from app.rag.knowledge.release_review import select_release_documents

    guard_run(run)
    summaries = {}
    for stage, filename, old_version, delta_root, merged_root in [
        (
            "enrichment",
            "enriched_chunks.jsonl",
            "v1",
            "data/enriched/accepted-delta",
            "data/enriched/v1",
        ),
        (
            "knowledge",
            "knowledge_enriched_chunks.jsonl",
            "v2",
            "data/knowledge/delta-v1",
            "data/knowledge/v1",
        ),
    ]:
        old_rel = "enriched" if stage == "enrichment" else "knowledge"
        original = run / "baseline/data" / old_rel / old_version / filename
        summaries[stage] = merge_reusing_bytes(
            original, run / delta_root / filename, run / merged_root / filename
        )
    ew = EnrichmentDatasetWriter(run, run / "data/enriched/v1")
    enriched = list(ew.load_existing().values())
    write_jsonl(ew.manifest_path, ew._manifest_rows(enriched))
    kw = KnowledgeDatasetWriter(run, run / "data/knowledge/v1")
    knowledge = list(kw.load_existing().values())
    write_jsonl(kw.manifest_path, kw.manifest_rows(knowledge))
    reporter = KnowledgeQualityReporter(run, run / "data/knowledge/v1")
    reporter.write(knowledge)
    selected, review = select_release_documents(knowledge, include_manual_review=False)
    report = reporter.build(selected)
    write_json(run / "merged-reuse-audit.json", summaries)
    write_json(
        run / "knowledge-selection-preflight.json",
        {"manual_review": review, "selected_quality": report},
    )
    outcome = release_builder(run).prepare_review(include_manual_review=False)
    write_json(run / "release-prepare-summary.json", outcome.summary)
    guard_run(run)
    print(
        json.dumps(
            {
                "reuse": summaries,
                "selected_documents": report["document_count"],
                "claims": report["claims"],
                "qa": outcome.summary,
            },
            ensure_ascii=False,
        )
    )


def review_packet(run: Path) -> None:
    """Present unchanged canonical samples, their actual text, and missing doc coverage."""
    from app.rag.knowledge.writer import KnowledgeDatasetWriter
    from app.rag.knowledge.qa import KnowledgeQASampler

    guard_run(run)
    qa_root = run / "data/knowledge/review"
    sampling = read_json(qa_root / "qa_sampling_manifest.json")
    claims = read_jsonl(qa_root / "claim_review_sample.jsonl")
    entities = read_jsonl(qa_root / "entity_link_review_sample.jsonl")
    chunks = KnowledgeDatasetWriter(run, run / "data/knowledge/v1").load_existing()
    doc_decisions = read_json(run / "qa/document-decisions.json")["documents"]
    new_docs = {
        row["document_id"]
        for row in doc_decisions
        if row["status"] == "passed_document_qa"
    }
    covered = {row["document_id"] for row in claims + entities}
    missing = new_docs - covered
    population = KnowledgeQASampler(entity_sample_size=100_000).entity_rows(
        list(chunks.values())
    )
    supplemental = []
    for doc_id in sorted(missing):
        candidates = [row for row in population if row["document_id"] == doc_id]
        if candidates:
            supplemental.append(candidates[0])
        else:
            supplemental.append(
                {
                    "sample_id": f"document-review:{doc_id}",
                    "sample_kind": "document",
                    "document_id": doc_id,
                    "chunk_id": min(
                        key for key, val in chunks.items() if val.doc_id == doc_id
                    ),
                    "note": "No linked entity or claim; document admission QA still required.",
                    "review_status": "pending",
                }
            )
    supplement_path = qa_root / "document_coverage_supplement.json"
    write_json(
        supplement_path,
        {
            "canonical_samples_unchanged": True,
            "missing_new_documents_before_supplement": sorted(missing),
            "samples": supplemental,
        },
    )
    old_attestation = read_json(
        run / "baseline/data/knowledge/v2/qa/qa_review_attestation.json"
    )
    known = old_attestation.get("known_issues", [])
    write_json(
        qa_root / "historical_known_issues.json",
        {
            "source_release_id": "knowledge-v2-e40ab37ffcc7bcf0",
            "source_attestation_sha256": digest(
                run / "baseline/data/knowledge/v2/qa/qa_review_attestation.json"
            ),
            "scope": "Exact unchanged historical chunks and entity links only; no exception for new errors.",
            "known_issues": known,
        },
    )
    known_ids = {row["sample_id"] for row in known}
    template = {
        "attestation_version": "v1",
        "review_status": "pending",
        "reviewer": None,
        "generation_mode": "rule_only",
        "claim_reviewed_count": 0,
        "entity_link_reviewed_count": 0,
        "claim_sample_sha256": sampling["claim_sample_sha256"],
        "entity_link_sample_sha256": sampling["entity_link_sample_sha256"],
        "release_selection_sha256": sampling["release_selection_sha256"],
        "rejected_sample_ids": [],
        "review_notes": None,
        "expected_claim_count": len(claims),
        "expected_entity_link_count": len(entities),
        "document_supplement_sha256": "sha256:" + digest(supplement_path),
        "expected_supplement_count": len(supplemental),
        "supplement_reviewed_count": 0,
        "historical_known_issues_path": "historical_known_issues.json",
        "historical_known_issues": known,
        "signature_status": "Awaiting actual West review; execution authorization is not signoff.",
    }
    template_path = qa_root / "qa_review_attestation.template.json"
    if not template_path.exists():
        write_json(template_path, template)
    lines = [
        "# 隔离文档恢复 · 新 Knowledge QA 审阅包",
        "",
        "状态：等待 West 实际审阅签字；本文件不代签，未生成新 frozen release 或 collection。",
        "",
        f"合并 {len({chunk.doc_id for chunk in chunks.values()})} 篇：旧 22 篇原样复用、新增 {len(new_docs)} 篇。{len(claims)} claims 全为 SUPPORTED；人工仍需检查事实与实体归属。",
        f"审阅范围：{len(claims)} claims（全选）、{len(entities)} entity samples、{len(supplemental)} 个文档覆盖补充项。",
        "",
        "canonical JSONL 保持 pending 原文，审阅结论写入单独 attestation，不编辑样本文件。",
        "",
        "```json",
        json.dumps(
            {
                k: template[k]
                for k in [
                    "claim_sample_sha256",
                    "entity_link_sample_sha256",
                    "release_selection_sha256",
                    "document_supplement_sha256",
                ]
            },
            ensure_ascii=False,
            indent=2,
        ),
        "```",
        "",
        "签字请提供：West 的通过/退回结论、实际 reviewed_count，退回时列 sample_id 与原因。",
        "",
        "旧四条 known issues 仅沿袭原精确 sample/chunk/entity ID 的既有接受依据，详见 historical_known_issues.json；不能类推接受新错误。",
        "",
    ]
    for title, prefix, values in [
        ("Claim", "C", claims),
        ("Entity", "E", entities),
        ("补充", "S", supplemental),
    ]:
        lines += [f"## {title}（{len(values)}）", ""]
        for i, row in enumerate(values, 1):
            chunk = chunks[row["chunk_id"]]
            source_id = chunk.enrichment.source.source_id
            parent_id = chunk.parent_id or chunk.chunk_id
            marker = (
                "；命中原历史 known issue ID" if row["sample_id"] in known_ids else ""
            )
            lines += [
                f"### {prefix}{i:03d} · {row['sample_id']}",
                "",
                f"source={source_id}；document={chunk.doc_id}；Parent={parent_id}；chunk={chunk.chunk_id}{marker}。",
                "",
            ]
            if title == "Claim":
                lines += [
                    "陈述：" + row["statement"],
                    "",
                    f"subject={row.get('subject_entity_id')}；predicate={row.get('predicate')}；object={row.get('object_entity_id') or row.get('object_value')}。",
                    "",
                    "精确引用：",
                    "",
                    "```text",
                    row["evidence_span"],
                    "```",
                    "",
                ]
            else:
                lines += [
                    f"实体：{row.get('canonical_name', '(无实体/claim，审阅正文)')}；type={row.get('entity_type')}；entity_id={row.get('entity_id')}。",
                    "",
                    "matched_surfaces="
                    + json.dumps(row.get("matched_surfaces", []), ensure_ascii=False),
                    "",
                ]
            source_metadata = chunk.metadata.get("source_metadata", {})
            visible = {
                k: source_metadata[k]
                for k in [
                    "title",
                    "artist",
                    "album",
                    "song",
                    "author",
                    "published_at",
                    "source_type",
                    "original_url",
                ]
                if k in source_metadata
            }
            lines += [
                "来源元数据（空 matched_surfaces 时需一并核对）：",
                "",
                "```json",
                json.dumps(visible, ensure_ascii=False, indent=2),
                "```",
                "",
                "本 chunk 完整正文：",
                "",
                "````text",
                chunk.content,
                "````",
                "",
                "审阅：待 West。",
                "",
            ]
    packet = run / "qa/knowledge-review-packet.md"
    packet.parent.mkdir(parents=True, exist_ok=True)
    packet.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    write_json(
        run / "review-packet-manifest.json",
        {
            "packet_path": packet.as_posix(),
            "packet_sha256": digest(packet),
            "canonical_claims": len(claims),
            "canonical_entities": len(entities),
            "supplement_documents": len(supplemental),
            "sampling": sampling,
            "supplement_sha256": digest(supplement_path),
            "template_path": template_path.as_posix(),
            "status": "awaiting_west_human_qa_signoff",
        },
    )
    guard_run(run)
    print(
        json.dumps(
            {
                "packet": packet.as_posix(),
                "claims": len(claims),
                "entities": len(entities),
                "supplement": len(supplemental),
                "signed": False,
            },
            ensure_ascii=False,
        )
    )


def validate_reusable_vector(
    old_row: dict, dense_text: str, old_config: dict, new_config: dict, vector: list
) -> None:
    if old_config != new_config:
        raise ValueError("dense_encoder_settings_changed")
    if old_row["dense_text"].encode("utf-8") != dense_text.encode("utf-8"):
        raise ValueError(f"baseline_dense_text_changed:{old_row['chunk_id']}")
    if len(vector) != new_config["dimension"] or any(
        isinstance(value, bool)
        or not isinstance(value, (float, int))
        or not math.isfinite(value)
        for value in vector
    ):
        raise ValueError("baseline_vector_invalid")
    if new_config.get("normalize") and not math.isclose(
        math.sqrt(sum(value * value for value in vector)),
        1.0,
        rel_tol=1e-3,
        abs_tol=1e-3,
    ):
        raise ValueError("baseline_vector_not_normalized")


def reusable_vectors(run: Path, chunks: dict) -> tuple[dict, list[dict]]:
    """Immutable SQLite reads cannot create journal, WAL, or SHM in the old run."""
    from app.rag.indexing.dense import DENSE_POOLING

    manifest = guard_run(run)
    old = Path(manifest["old_run_root"])
    original_config = read_json(old / "config_snapshot/indexing-v1-local-only.json")[
        "dense"
    ]
    current_config = read_json(run / "config_snapshot/indexing-v1-local-only.json")[
        "dense"
    ]
    old_build = read_json(
        old
        / "data/indexing/v1/knowledge-v2-e40ab37ffcc7bcf0/v4/index_build_manifest.json"
    )
    original_config = {**original_config, "pooling": old_build["dense_pooling"]}
    current_config = {**current_config, "pooling": DENSE_POOLING}
    old_rows = read_jsonl(
        old / "data/indexing/v1/knowledge-v2-e40ab37ffcc7bcf0/v4/index_projection.jsonl"
    )
    cache = old / "data/indexing/embedding_cache/v1/embeddings.sqlite3"
    vectors, audit = {}, []
    with sqlite3.connect(
        f"file:{cache.as_posix()}?mode=ro&immutable=1", uri=True
    ) as conn:
        for row in old_rows:
            chunk = chunks.get(row["chunk_id"])
            if chunk is None:
                raise ValueError(f"baseline_chunk_missing:{row['chunk_id']}")
            entry = conn.execute(
                "SELECT dense_vector FROM dense_embeddings_v1 WHERE input_hash = ?",
                (row["input_hash"],),
            ).fetchone()
            if entry is None:
                raise ValueError(f"baseline_vector_missing:{row['chunk_id']}")
            vector = json.loads(entry[0])
            validate_reusable_vector(
                row,
                chunk.retrieval_content.strip(),
                original_config,
                current_config,
                vector,
            )
            vectors[row["chunk_id"]] = vector
            audit.append(
                {
                    "chunk_id": row["chunk_id"],
                    "old_input_hash": row["input_hash"],
                    "dense_text_sha256": hashlib.sha256(
                        row["dense_text"].encode("utf-8")
                    ).hexdigest(),
                    "dense_vector_sha256": hashlib.sha256(
                        entry[0].encode("utf-8")
                    ).hexdigest(),
                }
            )
    guard_run(run)
    return vectors, audit


def vector_preflight(run: Path) -> None:
    from app.rag.knowledge.writer import KnowledgeDatasetWriter

    guard_run(run)
    chunks = KnowledgeDatasetWriter(run, run / "data/knowledge/v1").load_existing()
    vectors, audit = reusable_vectors(run, chunks)
    write_json(
        run / "vector-reuse-preflight.json",
        {
            "old_vectors_verified": len(vectors),
            "old_cache_open_mode": "mode=ro&immutable=1",
            "new_cache_written": False,
            "new_release_hash_not_yet_assigned": True,
            "mapping": audit,
        },
    )
    print(
        json.dumps({"verified_old_vectors": len(vectors), "new_cache_written": False})
    )


def validate_human_signoff(
    attestation: dict, sampling: dict, supplemental_count: int, supplement_sha256: str
) -> None:
    expected = {
        "attestation_version": "v1",
        "review_status": "approved",
        "reviewer": "West",
        "generation_mode": "rule_only",
        "rejected_sample_ids": [],
        "claim_reviewed_count": sampling["claim_sample_count"],
        "entity_link_reviewed_count": sampling["entity_link_sample_count"],
        "claim_sample_sha256": sampling["claim_sample_sha256"],
        "entity_link_sample_sha256": sampling["entity_link_sample_sha256"],
        "release_selection_sha256": sampling["release_selection_sha256"],
        "document_supplement_sha256": "sha256:" + supplement_sha256,
        "supplement_reviewed_count": supplemental_count,
    }
    if any(attestation.get(key) != value for key, value in expected.items()):
        raise ValueError("west_human_qa_signature_missing_or_stale")
    if not attestation.get("signature_source"):
        raise ValueError("west_human_signature_source_missing")


def publish(run: Path) -> None:
    """Only reachable after actual West signoff is recorded in the new QA root."""
    from urllib.parse import urlparse
    from app.rag.indexing.cache import LocalEmbeddingCache
    from app.rag.indexing.config import load_indexing_config
    from app.rag.indexing.corpus import IndexCorpusReader
    from app.rag.indexing.dense import QwenDenseEncoder
    from app.rag.indexing.models import IndexPoint
    from app.rag.indexing.projection import IndexDocumentProjector
    from app.rag.indexing.service import OfflineIndexingService

    guard_run(run)
    qa = run / "data/knowledge/review"
    attestation = read_json(qa / "qa_review_attestation.json")
    sampling = read_json(qa / "qa_sampling_manifest.json")
    supplement = qa / "document_coverage_supplement.json"
    validate_human_signoff(
        attestation, sampling, len(read_json(supplement)["samples"]), digest(supplement)
    )
    historical = read_json(qa / "historical_known_issues.json")["known_issues"]
    if attestation.get("historical_known_issues") != historical:
        raise ValueError("historical_known_issue_scope_changed_or_missing")
    outcome = release_builder(run).finalize(include_manual_review=False)
    release_id = outcome.release_id
    if not release_id or release_id == "knowledge-v2-e40ab37ffcc7bcf0":
        raise ValueError("new_release_identity_invalid")
    knowledge_path = run / "data/knowledge/v2/knowledge_enriched_chunks.jsonl"
    release_manifest = run / "data/knowledge/v2/knowledge_release_manifest.json"
    config = load_indexing_config(run / "config_snapshot/indexing-v1-local-only.json")
    config = config.model_copy(
        update={
            "qdrant": config.qdrant.model_copy(
                update={
                    "collection_name": "music_knowledge_recovery_20261017",
                    "append_release_hash": True,
                    "prune_stale": False,
                    "api_key_env": "MOODFLOW_RECOVERY_NO_CREDENTIAL",
                }
            )
        }
    )
    endpoint = urlparse(config.qdrant.url)
    if endpoint.scheme != "http" or endpoint.hostname not in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        raise ValueError("nonlocal_index_endpoint_forbidden")
    if not config.dense.local_files_only:
        raise ValueError("remote_model_download_forbidden")
    resolved = config.for_release(release_id)
    if not resolved.qdrant.collection_name.startswith(
        "music_knowledge_recovery_20261017_"
    ):
        raise ValueError("nonrecovery_collection_forbidden")
    corpus = IndexCorpusReader(run, knowledge_path=knowledge_path).load()
    chunks = {x.chunk_id: x for x in corpus.chunks}
    old_vectors, audit = reusable_vectors(run, chunks)
    projector = IndexDocumentProjector(resolved)
    children = {x.chunk_id: x for x in corpus.children}
    points, mapped = [], []
    for row in audit:
        chunk = children[row["chunk_id"]]
        document = projector.project(
            chunk, corpus.parents[chunk.parent_id], source_release_id=release_id
        )
        points.append(
            IndexPoint(document=document, dense_vector=old_vectors[chunk.chunk_id])
        )
        mapped.append({**row, "new_input_hash": document.input_hash})
    cache_path = run / "data/indexing/embedding_cache/v1/embeddings.sqlite3"
    with LocalEmbeddingCache(cache_path) as cache:
        cache.put(points)
        if any(
            cache.get(point.document.input_hash) != point.dense_vector
            for point in points
        ):
            raise ValueError("new_cache_seed_verification_failed")
    write_json(
        run / "vector-reuse-mapping.json", {"release_id": release_id, "mapping": mapped}
    )
    # A cache regression may fail the new run, but must never silently re-embed old documents.
    old_texts = {point.document.dense_text for point in points}

    class DeltaOnlyEncoder:
        def __init__(self):
            self.encoder = QwenDenseEncoder(config.dense)

        def encode_documents(self, texts):
            if any(text in old_texts for text in texts):
                raise ValueError("baseline_reembedding_forbidden")
            return self.encoder.encode_documents(texts)

    result = OfflineIndexingService(
        run,
        config,
        knowledge_path=knowledge_path,
        release_manifest_path=release_manifest,
        output_root=run / "data/indexing/v1" / release_id / "v4",
        encoder=DeltaOnlyEncoder(),
    ).run(prune_stale=False)
    write_json(run / "indexing-summary.json", result.summary)
    guard_run(run)
    if result.summary.get("failed_count"):
        raise ValueError("new_index_has_failures")
    parents_with_children = {chunk.parent_id for chunk in corpus.children}
    expected_points = len(corpus.children) + sum(
        chunk.chunk_type.value == "structured_record"
        and chunk.chunk_id not in parents_with_children
        for chunk in corpus.parents.values()
    )
    if result.summary.get("collection_point_count") != expected_points:
        raise ValueError("new_collection_point_count_mismatch")
    print(
        json.dumps(
            {
                "release_id": release_id,
                "collection": resolved.qdrant.collection_name,
                "indexing": result.summary,
            },
            ensure_ascii=False,
        )
    )


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "stage",
        choices=[
            "preview",
            "draft-clean",
            "clean",
            "chunk",
            "enrich",
            "knowledge",
            "assemble-review",
            "packet",
            "vector-preflight",
            "publish",
        ],
    )
    parser.add_argument("--run", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.stage == "preview":
        clean_preview(args.run.resolve())
    elif args.stage in {"draft-clean", "clean"}:
        recovered_clean(args.run.resolve(), final=args.stage == "clean")
    elif args.stage == "chunk":
        chunk_delta(args.run.resolve())
    elif args.stage == "enrich":
        asyncio.run(enrich_delta(args.run.resolve()))
    elif args.stage == "knowledge":
        asyncio.run(knowledge_delta(args.run.resolve()))
    elif args.stage == "assemble-review":
        assemble_review(args.run.resolve())
    elif args.stage == "packet":
        review_packet(args.run.resolve())
    elif args.stage == "vector-preflight":
        vector_preflight(args.run.resolve())
    elif args.stage == "publish":
        publish(args.run.resolve())


if __name__ == "__main__":
    main()
