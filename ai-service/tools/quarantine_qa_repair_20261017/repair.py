"""West-approved QA repair. New artifacts only; never release, index, or sign."""

# ruff: noqa: E402
# This standalone CLI adds ai-service to sys.path before application imports.

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
AI_ROOT = Path(__file__).resolve().parents[2]
REPO = AI_ROOT.parent
sys.path.insert(0, str(AI_ROOT))

from app.rag.knowledge.claims.ids import build_claim_id
from app.rag.knowledge.claims.models import ClaimCandidate
from app.rag.knowledge.config import KnowledgeConfig
from app.rag.knowledge.entity_linking.grounded import (
    GroundedEntityLinker,
    film_document,
)
from app.rag.knowledge.grounded_qa import (
    GroundedKnowledgeQASampler,
    validate_entity_sample,
)
from app.rag.knowledge.hashing import knowledge_input_hash
from app.rag.knowledge.models import KnowledgeEntities, KnowledgeStats
from app.rag.knowledge.qa import sha256_file
from app.rag.knowledge.report import KnowledgeQualityReporter
from app.rag.knowledge.validator import KnowledgeValidator
from app.rag.knowledge.writer import KnowledgeDatasetWriter


GATE0 = (
    REPO / "workspace/moodflow-review/quarantine-qa-rootcause-gate0-20261017/audit.json"
)
OUTPUT_NAMESPACE = AI_ROOT / "data/reruns/jay-chou-quarantine-qa-repair-20261017"
REMOVED = {"claim:774e57110a1b561698eb9843", "claim:cd2df754ff07569d40444e25"}
REVISED = "claim:f7e6b7638a0687f650df325e"
GOLD_HASHES = {
    "rag_eval_gold_v4.frozen.jsonl": "1ca311c731b83c0fd0f3ab95dcf31d06a95565e54e631e174e09dd890b81ad4e",
    "rag_eval_gold_v5.frozen.jsonl": "b708332729177a095cb0993381bc9b3ffaef7808846e61bbdb8cbd0877afbfad",
}


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_rows(path):
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def write_rows(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(
                json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
            )


def canonical_sha(value):
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def tree_summary(root):
    files = [
        {
            "path": p.relative_to(root).as_posix(),
            "bytes": p.stat().st_size,
            "sha256": sha256_file(p).removeprefix("sha256:"),
        }
        for p in sorted(
            (p for p in root.rglob("*") if p.is_file()),
            key=lambda p: p.relative_to(root).as_posix(),
        )
    ]
    return {
        "file_count": len(files),
        "bytes": sum(p["bytes"] for p in files),
        "sha256": canonical_sha(files),
    }


def check_protected(audit):
    checked = {}
    for name in ["old_run", "new_candidate"]:
        baseline = audit["integrity_baseline"][name]
        actual = tree_summary(Path(baseline["root"]))
        if actual != {key: baseline[key] for key in actual}:
            raise ValueError(f"protected_run_changed:{name}")
        checked[name] = actual
    for filename, expected in GOLD_HASHES.items():
        if sha256_file(AI_ROOT / "evals/datasets" / filename) != "sha256:" + expected:
            raise ValueError(f"protected_gold_changed:{filename}")
    # Prior uncommitted recovery tools are inputs, not part of this repair.
    for row in audit["integrity_baseline"]["protected_code"]:
        if sha256_file(Path(row["path"])) != "sha256:" + row["sha256"]:
            raise ValueError("prior_recovery_code_changed")
    return {**checked, "gold_sha256": GOLD_HASHES}


def guard_output(path, *, must_be_new=True):
    resolved = path.resolve()
    namespace = OUTPUT_NAMESPACE.resolve()
    if not resolved.is_relative_to(namespace) or resolved == namespace:
        raise ValueError("output_must_be_in_new_repair_namespace")
    if must_be_new and resolved.exists():
        raise ValueError("refuse_overwriting_existing_repair_run")
    return resolved


def revise_claims(rows, audit):
    """Only the three exact approved IDs can change; retain all other objects."""
    # Metadata/text are immutable inputs; copy only the objects this function edits.
    result = [{**row, "knowledge": copy.deepcopy(row["knowledge"])} for row in rows]
    by_id = {row["chunk_id"]: row for row in result}
    if len(by_id) != len(result):
        raise ValueError("duplicate_input_chunk_id")
    original = {
        item["claim"]["claim_id"]: item
        for row in rows
        for item in row["knowledge"]["claims"]
    }
    if (
        len(original) != 75
        or sum(len(row["knowledge"]["claims"]) for row in rows) != 75
        or not (REMOVED | {REVISED}) <= original.keys()
    ):
        raise ValueError("approved_claim_scope_not_exact")
    proposal = next(row for row in audit["claim_findings"] if row["ordinal"] == "C074")
    span = proposal["proposed_evidence_span"]
    decisions = []
    for row in result:
        retained = []
        for item in row["knowledge"]["claims"]:
            old_id = item["claim"]["claim_id"]
            if old_id in REMOVED:
                decisions.append(
                    {
                        "old_claim_id": old_id,
                        "action": "removed_weak_support",
                        "chunk_id": row["chunk_id"],
                        "old_verified_claim": copy.deepcopy(item),
                    }
                )
                continue
            if old_id == REVISED:
                parent = by_id[row["parent_id"]]
                if (
                    row["content"].count(span) != 1
                    or parent["content"].count(span) != 1
                ):
                    raise ValueError("C074_replacement_not_unique_in_parent_and_child")
                if item.get("grounding") is not None:
                    raise ValueError(
                        "unexpected_C074_grounding_requires_explicit_revision"
                    )
                old_item = copy.deepcopy(item)
                claim = item["claim"]
                claim["evidence_span"] = span
                candidate = ClaimCandidate.model_validate(
                    {
                        k: v
                        for k, v in claim.items()
                        if k not in {"claim_id", "source_chunk_id", "extraction_source"}
                    }
                )
                claim["claim_id"] = build_claim_id(candidate, row["chunk_id"])
                item["verification"]["evidence_span"] = span
                decisions.append(
                    {
                        "old_claim_id": old_id,
                        "new_claim_id": claim["claim_id"],
                        "action": "narrowed_to_jay_1979_winner_entry",
                        "chunk_id": row["chunk_id"],
                        "parent_id": row["parent_id"],
                        "old_verified_claim": old_item,
                        "new_verified_claim": copy.deepcopy(item),
                    }
                )
            retained.append(item)
        row["knowledge"]["claims"] = retained
    after = {
        item["claim"]["claim_id"]: item
        for row in result
        for item in row["knowledge"]["claims"]
    }
    unchanged_ids = set(original) - REMOVED - {REVISED}
    if len(decisions) != 3 or len(after) != 73 or len(unchanged_ids) != 72:
        raise ValueError("claim_revision_count_mismatch")
    if any(original[key] != after.get(key) for key in unchanged_ids):
        raise ValueError("carried_claim_mutated")
    return result, decisions, sorted(unchanged_ids)


def selected_scope_dispositions(audit, audits, chunks):
    dispositions = []
    for old in audit["entity_findings"]:
        sample = old["sample"]
        current = audits[sample["chunk_id"]]
        same_entity_body = [
            m for m in current.body_matches if m["entity_id"] == sample["entity_id"]
        ]
        if same_entity_body:
            raise ValueError(f"returned_sample_still_locally_linked:{old['ordinal']}")
        dispositions.append(
            {
                "old_ordinal": old["ordinal"],
                "sample_id": sample["sample_id"],
                "chunk_id": sample["chunk_id"],
                "entity_id": sample["entity_id"],
                "source_id": old["source_id"],
                "old_disposition": old["tt_disposition"],
                "new_disposition": "rejected_as_local_sample",
                "reason": old["root_cause"],
                "rule_excluded_reason": current.excluded_reason,
                "rule_rejections": list(current.rejected),
                "body_sha256": current.body_sha256,
                "original_body_retained": chunks[sample["chunk_id"]].content
                is not None,
                "review_status": "pending_West_verification",
            }
        )
    return dispositions


def render_packet(
    output, source, claim_changes, entities, supplements, dispositions, chunks
):
    quality = read_json(output / "data/knowledge/v1/knowledge_quality_report.json")
    lines = [
        "# Knowledge QA 修复审阅包（Gate 1，新包，待 West 签字）",
        "",
        "本包待审：3项claim处置（撤下2、收窄引用1）、新Entity样本、文档覆盖补充项、10存疑结论。",
        "72个已通过claims按精确ID和字段哈希沿用用户提供的tt审计结论；详见carried-claim-review.json。",
        "执行授权与QA签字分开记录；当前包未签字，未创建release/collection。",
        "机器门禁：registry_candidate_resolution_ratio="
        + str(quality["entity_linking"]["registry_candidate_resolution_ratio"])
        + "；既有门槛=0.95；pre_embedding_ready="
        + str(quality["quality_gates"]["pre_embedding_ready"])
        + "。人审本包不代替该门禁；未通过时不能冻结或索引。",
        "",
        f"原包（只读）：{source.as_posix()}/qa/knowledge-review-packet.md",
        "",
        "## 3项claim处置",
        "",
    ]
    for change in claim_changes:
        old = change["old_verified_claim"]["claim"]
        label = {
            "claim:774e57110a1b561698eb9843": "C034",
            "claim:cd2df754ff07569d40444e25": "C063",
            REVISED: "C074",
        }[change["old_claim_id"]]
        lines += [
            f"### 原{label} · {change['action']}",
            "",
            f"旧ID：{change['old_claim_id']}",
            "",
            "旧陈述：" + old["statement"],
            "",
            "旧引用：",
            "```text",
            old["evidence_span"],
            "```",
            "",
        ]
        if change.get("new_verified_claim"):
            new = change["new_verified_claim"]["claim"]
            lines += [
                f"新ID：{new['claim_id']}；Parent={change['parent_id']}；Child={change['chunk_id']}",
                "",
                "新引用：",
                "```text",
                new["evidence_span"],
                "```",
                "",
            ]
        else:
            lines += [
                "处置：撤下弱支撑断言；未替换、未润色为另一个未经审查的事实。",
                "",
            ]
        lines += ["复核：待 West。", ""]
    for title, prefix, rows in [
        ("Entity", "E", entities),
        ("文档覆盖补充", "S", supplements),
    ]:
        lines += [f"## {title}（{len(rows)}）", ""]
        for number, row in enumerate(rows, 1):
            chunk = chunks[row["chunk_id"]]
            validate_entity_sample(row, chunk)
            source_meta = chunk.metadata.get("document_metadata", {}).get(
                "source_metadata", {}
            )
            visible = {
                key: source_meta[key]
                for key in [
                    "title",
                    "artist",
                    "album",
                    "song",
                    "source_type",
                    "original_url",
                ]
                if key in source_meta
            }
            lines += [
                f"### {prefix}{number:03} · {row['sample_id']}",
                "",
                f"source={chunk.enrichment.source.source_id}；document={chunk.doc_id}；Parent={chunk.parent_id or chunk.chunk_id}；chunk={chunk.chunk_id}",
                "",
                f"entity={row['entity_id']}；name={row['canonical_name']}；type={row['entity_type']}",
                "",
                "matched_surfaces="
                + json.dumps(row["matched_surfaces"], ensure_ascii=False),
                "",
                "正文定位（Python字符offset，end不含）：",
                "```json",
                json.dumps(row["body_matches"], ensure_ascii=False, indent=2),
                "```",
                "",
                "文档上下文（不作为正文提及）：",
                "```json",
                json.dumps(
                    {
                        "title": chunk.metadata.get("title"),
                        "section_path": chunk.section_path,
                        "source_metadata": visible,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
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
    lines += [
        "## 原10项存疑：当前样本均退回采样层",
        "",
        "保留原文与可能正确的文档语义；以下是局部样本无效结论，待West复核。",
        "",
        "| 原编号 | sample / chunk / entity | 处置理由 |",
        "| --- | --- | --- |",
    ]
    for row in dispositions:
        if row["old_disposition"] == "uncertain":
            lines.append(
                f"| {row['old_ordinal']} | {row['sample_id']} / {row['chunk_id']} / {row['entity_id']} | {row['reason'].replace('|', '／')} |"
            )
    lines += [
        "",
        "全部36项旧样本精确处置、旧→新样本编号、增删链接和依赖差异见qa目录sidecar。",
        "",
        "停止点：West审阅本包并签字之前，不生成冻结release或collection。",
    ]
    packet = output / "qa/knowledge-review-packet-v2.md"
    packet.parent.mkdir(parents=True, exist_ok=True)
    with packet.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines).rstrip() + "\n")
    return packet


def build(output):
    output = guard_output(output)
    audit = read_json(GATE0)
    before = check_protected(audit)
    source = Path(audit["run_root"])
    source_path = source / "data/knowledge/v1/knowledge_enriched_chunks.jsonl"
    original_rows = read_rows(source_path)
    revised_rows, changes, carried = revise_claims(original_rows, audit)
    original = KnowledgeDatasetWriter(
        source, source / "data/knowledge/v1"
    ).load_existing()
    by_raw = {row["chunk_id"]: row for row in revised_rows}
    film_docs = frozenset(
        chunk.doc_id for chunk in original.values() if film_document(chunk.content)
    )
    config = KnowledgeConfig(entity_linking_version="v6", llm_enabled=False)
    linker = GroundedEntityLinker(config, film_documents=film_docs)
    models, audits, deltas, dependencies = [], {}, [], []
    validator = KnowledgeValidator()
    for old in original.values():
        proof = linker.link_with_evidence(old)
        audits[old.chunk_id] = proof
        entities = KnowledgeEntities(
            linked_entities=proof.result.linked_entities,
            unresolved_mentions=proof.result.unresolved_mentions,
        )
        raw = by_raw[old.chunk_id]
        raw["knowledge"]["entities"] = entities.model_dump(mode="json")
        model = type(old).model_validate(raw)
        provenance = model.knowledge.provenance.model_copy(
            update={
                "entity_linking_version": "v6",
                "knowledge_input_hash": knowledge_input_hash(model, config),
            }
        )
        model = model.model_copy(
            update={
                "knowledge": model.knowledge.model_copy(
                    update={
                        "provenance": provenance,
                        "stats": KnowledgeStats.build(entities, model.knowledge.claims),
                    }
                )
            }
        )
        validator.validate(old, model)
        models.append(model)
        old_ids = {
            entity.entity_id for entity in old.knowledge.entities.linked_entities
        }
        new_ids = {entity.entity_id for entity in proof.result.linked_entities}
        if old_ids != new_ids:
            deltas.append(
                {
                    "chunk_id": old.chunk_id,
                    "source_id": old.enrichment.source.source_id,
                    "removed_entity_ids": sorted(old_ids - new_ids),
                    "added_entity_ids": sorted(new_ids - old_ids),
                    "excluded_reason": proof.excluded_reason,
                }
            )
        for claim in model.knowledge.claims:
            for argument in [
                claim.claim.subject_entity_id,
                claim.claim.object_entity_id,
            ]:
                if argument and argument not in new_ids:
                    dependencies.append(
                        {
                            "chunk_id": old.chunk_id,
                            "claim_id": claim.claim.claim_id,
                            "entity_id": argument,
                            "scope": "retained_claim_argument_association",
                            "claim_preserved": True,
                            "promoted_to_local_link": False,
                            "reason": "approved_claim_argument_not_resolved_as_body_local_entity_by_v6",
                            "evidence_span": claim.claim.evidence_span,
                        }
                    )
    validator.validate_parent_references(models)
    chunks = {model.chunk_id: model for model in models}
    dispositions = selected_scope_dispositions(audit, audits, chunks)
    if len(models) != 1959 or len(changes) != 3:
        raise ValueError("unexpected_derived_scope")
    output.mkdir(parents=True)
    write_json(
        output / "config_snapshot/knowledge-v6-rule-only.json",
        config.model_dump(mode="json"),
    )
    writer = KnowledgeDatasetWriter(output, output / "data/knowledge/v1")
    writer.write(models)
    KnowledgeQualityReporter(output, output / "data/knowledge/v1").write(models)
    write_rows(
        output / "qa/entity-link-grounding-v6.jsonl",
        [proof.payload() for proof in audits.values()],
    )
    write_json(
        output / "qa/claim-changes.json",
        {
            "authorization": "West explicitly approved both tiers and these dispositions in current thread",
            "changes": changes,
        },
    )
    write_json(
        output / "qa/carried-claim-review.json",
        {
            "count": 72,
            "claim_ids": carried,
            "verified_claim_objects_unchanged": True,
            "source": "tt audit supplied by user; West approved carry over",
            "new_West_signature": False,
        },
    )
    write_json(
        output / "qa/old-sample-dispositions.json",
        {"count": 36, "uncertain_count": 10, "samples": dispositions},
    )
    write_json(
        output / "qa/entity-link-delta.json",
        {"changed_chunks": len(deltas), "changes": deltas},
    )
    write_json(
        output / "qa/claim-argument-associations.json",
        {"count": len(dependencies), "associations": dependencies},
    )
    sampler = GroundedKnowledgeQASampler(audits)
    review_root = output / "data/knowledge/review"
    _, _, manifest_path, manifest = sampler.write(models, review_root)
    entities = read_rows(review_root / "entity_link_review_sample.jsonl")
    claims = read_rows(review_root / "claim_review_sample.jsonl")
    old_claim_rows = read_rows(
        source / "data/knowledge/review/claim_review_sample.jsonl"
    )
    unchanged = [
        row
        for row in old_claim_rows
        if row["sample_id"].replace("claim-review:", "claim:") in carried
    ]
    current_unchanged = [
        row
        for row in claims
        if row["sample_id"].replace("claim-review:", "claim:") in carried
    ]
    if (
        unchanged != current_unchanged
        or canonical_sha(unchanged)
        != audit["unchanged_claims"]["canonical_rows_sha256"]
    ):
        raise ValueError("carried_72_QA_rows_changed")
    decisions = read_json(source / "qa/document-decisions.json")["documents"]
    new_docs = {
        row["document_id"] for row in decisions if row["status"] == "passed_document_qa"
    }
    covered = {row["document_id"] for row in claims + entities}
    population = sampler.population_rows(models)
    supplements, gaps = [], []
    for doc_id in sorted(new_docs - covered):
        candidates = [row for row in population if row["document_id"] == doc_id]
        if candidates:
            supplements.append(min(candidates, key=sampler._stable_row_key))
        else:
            gaps.append(
                {
                    "document_id": doc_id,
                    "reason": "no_eligible_body_grounded_entity_sample",
                }
            )
    write_json(
        review_root / "document-coverage-supplement-v2.json",
        {
            "samples": supplements,
            "coverage_gaps": gaps,
            "target_new_documents": len(new_docs),
            "quarantine_retained": [
                row["source_id"] for row in decisions if row["status"] == "quarantined"
            ],
        },
    )
    for row in entities + supplements:
        validate_entity_sample(row, chunks[row["chunk_id"]])
    old_samples = read_rows(
        source / "data/knowledge/review/entity_link_review_sample.jsonl"
    )
    old_supp = read_json(
        source / "data/knowledge/review/document_coverage_supplement.json"
    )["samples"]
    old_by_id = {
        row["sample_id"]: f"{prefix}{i:03}"
        for prefix, seq in [("E", old_samples), ("S", old_supp)]
        for i, row in enumerate(seq, 1)
    }
    crosswalk = [
        {
            "sample_id": row["sample_id"],
            "old_ordinal": old_by_id.get(row["sample_id"]),
            "new_ordinal": f"{prefix}{i:03}",
            "review_status": "pending_new_entity_review",
        }
        for prefix, seq in [("E", entities), ("S", supplements)]
        for i, row in enumerate(seq, 1)
    ]
    write_json(output / "qa/sample-crosswalk.json", {"samples": crosswalk})
    packet = render_packet(
        output, source, changes, entities, supplements, dispositions, chunks
    )
    counts = {
        "sources": len({m.enrichment.source.source_id for m in models}),
        "parents": sum(m.parent_id is None for m in models),
        "children": sum(m.parent_id is not None for m in models),
        "claims": len(claims),
        "carried_claims": 72,
        "removed_claims": 2,
        "revised_claims": 1,
        "old_link_pairs": sum(
            len(c.knowledge.entities.linked_entities) for c in original.values()
        ),
        "new_local_link_pairs": sum(m.knowledge.stats.entity_count for m in models),
        "entity_samples": len(entities),
        "supplement_samples": len(supplements),
        "coverage_gaps": len(gaps),
        "claim_argument_associations": len(dependencies),
    }
    write_json(
        review_root / "qa_review_attestation.v2.template.json",
        {
            "status": "pending",
            "reviewer": None,
            "signature_source": None,
            "expected": {
                "claim_change_decisions": 3,
                "carried_tt_claims": 72,
                "entity_samples": len(entities),
                "supplement_samples": len(supplements),
                "uncertain_dispositions": 10,
            },
            "reviewed": {
                "claim_change_decisions": 0,
                "entity_samples": 0,
                "supplement_samples": 0,
                "uncertain_dispositions": 0,
            },
            "sampling_manifest_sha256": sha256_file(manifest_path),
            "packet_sha256": sha256_file(packet),
            "claim_changes_sha256": sha256_file(output / "qa/claim-changes.json"),
            "claim_argument_associations_sha256": sha256_file(
                output / "qa/claim-argument-associations.json"
            ),
            "known_issues_auto_accepted": [],
            "publish_authorized": False,
        },
    )
    after = check_protected(audit)
    if before != after:
        raise ValueError("protected_inputs_mutated_during_repair")
    code_paths = [
        Path(__file__),
        AI_ROOT / "app/rag/knowledge/entity_linking/grounded.py",
        AI_ROOT / "app/rag/knowledge/grounded_qa.py",
    ]
    code_snapshots = []
    for index, path in enumerate(code_paths):
        snapshot = output / "config_snapshot" / f"code-{index}-{path.name}"
        with snapshot.open("xb") as handle:
            handle.write(path.read_bytes())
        code_snapshots.append(
            {
                "path": snapshot.as_posix(),
                "sha256": sha256_file(snapshot),
                "source_path": path.as_posix(),
            }
        )
    input_manifest = {
        "source_knowledge": {
            "path": source_path.as_posix(),
            "sha256": sha256_file(source_path),
        },
        "gate0_audit": {"path": GATE0.as_posix(), "sha256": sha256_file(GATE0)},
        "code": code_snapshots,
    }
    files = [
        {"path": p.relative_to(output).as_posix(), "sha256": sha256_file(p)}
        for p in sorted(output.rglob("*"))
        if p.is_file()
    ]
    write_json(
        output / "repair-manifest.json",
        {
            "schema_version": "qa-repair-v2",
            "status": "pending_West_QA_signoff",
            "release_id": None,
            "collection": None,
            "production_changed": False,
            "inputs": input_manifest,
            "counts": counts,
            "film_document_conflicts": sorted(film_docs),
            "sampling": manifest,
            "protected_before": before,
            "protected_after": after,
            "files": files,
            "packet": packet.as_posix(),
            "reuse": "all raw/normalized/clean/chunk/enrichment artifacts reused by hash; only entity results recomputed and 3 approved claim decisions applied",
        },
    )
    validate(output)
    return counts


def validate(output):
    output = guard_output(output, must_be_new=False)
    manifest = read_json(output / "repair-manifest.json")
    if (
        manifest["status"] != "pending_West_QA_signoff"
        or manifest["release_id"]
        or manifest["collection"]
    ):
        raise ValueError("repair_must_stop_before_release")
    for row in manifest["files"]:
        if sha256_file(output / row["path"]) != row["sha256"]:
            raise ValueError(f"repair_output_hash_mismatch:{row['path']}")
    for item in [
        manifest["inputs"]["source_knowledge"],
        manifest["inputs"]["gate0_audit"],
        *manifest["inputs"]["code"],
    ]:
        if sha256_file(Path(item["path"])) != item["sha256"]:
            raise ValueError("repair_input_or_code_changed")
    check_protected(read_json(GATE0))
    chunks = KnowledgeDatasetWriter(
        output, output / "data/knowledge/v1"
    ).load_existing()
    rows = read_rows(output / "data/knowledge/review/entity_link_review_sample.jsonl")
    supplements = read_json(
        output / "data/knowledge/review/document-coverage-supplement-v2.json"
    )["samples"]
    for row in rows + supplements:
        validate_entity_sample(row, chunks[row["chunk_id"]])
    if len(chunks) != 1959 or len(
        {row["sample_id"] for row in rows + supplements}
    ) != len(rows + supplements):
        raise ValueError("invalid_chunk_or_sample_count")
    if (output / "data/knowledge/v2/knowledge_release_manifest.json").exists() or (
        output / "data/knowledge/review/qa_review_attestation.json"
    ).exists():
        raise ValueError("release_or_signature_created_without_West")
    return manifest["counts"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["build", "validate"])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    counts = build(args.output) if args.stage == "build" else validate(args.output)
    print(
        json.dumps(
            {
                "stage": args.stage,
                "counts": counts,
                "status": "pending_West_QA_signoff",
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
