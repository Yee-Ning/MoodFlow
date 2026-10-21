"""Freeze West-approved recovery artifacts using the existing release builder.

Operator-only entry point. Never changes defaults, production bindings or Qdrant.
Entity inference is performed separately by replay_linker_b1.py.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

AI_ROOT = Path(__file__).resolve().parents[1]
if str(AI_ROOT) not in sys.path:
    sys.path.insert(0, str(AI_ROOT))

from app.rag.clean.io import atomic_write_json  # noqa: E402
from app.rag.knowledge.entity_linking.grounded import GroundedLinkAudit  # noqa: E402
from app.rag.knowledge.grounded_qa import GroundedKnowledgeQASampler  # noqa: E402
from app.rag.knowledge.qa import sha256_file  # noqa: E402
from app.rag.knowledge.release import KnowledgeReleaseBuilder  # noqa: E402
from app.rag.knowledge.report import KnowledgeQualityReporter  # noqa: E402
from app.rag.knowledge.writer import KnowledgeDatasetWriter  # noqa: E402

REPO = AI_ROOT.parent
QA_ROOT = (
    AI_ROOT / "data/reruns/jay-chou-quarantine-qa-repair-20261017/gate1-v2-20261017-03"
)
RECOVERY = (
    AI_ROOT / "data/reruns/jay-chou-quarantine-recovery-20261017/gate1-20261017-152344"
)
APPROVED = AI_ROOT / "data/reruns/jay-chou-linker-b1-20261019/merged48-run01"
OLD = (
    AI_ROOT
    / "data/reruns/jay-chou-b-20261016-182958/full-20261016-213514/data/knowledge/v2"
)
NAMESPACE = AI_ROOT / "data/reruns/jay-chou-release-freeze-20261019"
PACKET_SHA = "sha256:bac0f75a27365450e342d08348c84f1731d14a80164351e75013639bf314a131"
QUARANTINE = {*(f"jc-v1-{n:03}" for n in range(38, 45)), "jc-v2-048"}


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_rows(path: Path):
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def approved_sample_rows(population: list[dict], approved: list[dict]) -> list[dict]:
    by_id = {row["sample_id"]: row for row in population}
    if len(by_id) != len(population):
        raise ValueError("duplicate_sample_population")
    if len({row["sample_id"] for row in approved}) != len(approved):
        raise ValueError("duplicate_approved_sample")
    for row in approved:
        if by_id.get(row["sample_id"]) != row:
            raise ValueError("approved_qa_sample_changed:" + row["sample_id"])
    return approved


def validate_inventory(chunks, old_ids, decisions):
    new_ids = {
        d["document_id"]
        for d in decisions["documents"]
        if d["status"] == "passed_document_qa"
    }
    quarantine = [d for d in decisions["documents"] if d["status"] == "quarantined"]
    if {d["source_id"] for d in quarantine} != QUARANTINE or len(quarantine) != 8:
        raise ValueError("quarantine_inventory_mismatch")
    if len(old_ids) != 22 or len(new_ids) != 26 or old_ids & new_ids:
        raise ValueError("cohort_inventory_mismatch")
    actual = {c.doc_id for c in chunks}
    if actual != old_ids | new_ids:
        raise ValueError("release_document_inventory_mismatch")
    if any(c.enrichment.source.source_id in QUARANTINE for c in chunks):
        raise ValueError("quarantined_source_in_release")
    return {
        "old_document_count": 22,
        "recovered_document_count": 26,
        "document_count": len(actual),
        "parent_count": sum(c.parent_id is None for c in chunks),
        "child_count": sum(c.parent_id is not None for c in chunks),
        "quarantined_sources": sorted(QUARANTINE),
    }


def validate_replay(chunks, approved):
    by_id = {c.chunk_id: c for c in chunks}
    if len(by_id) != len(chunks) or set(by_id) != set(approved):
        raise ValueError("approved_replay_chunk_set_changed")
    for chunk_id, chunk in by_id.items():
        if chunk != approved[chunk_id]:
            raise ValueError("approved_replay_chunk_changed:" + chunk_id)


class CarriedQASampler(GroundedKnowledgeQASampler):
    def __init__(self, audits, *, claims, entities):
        super().__init__(audits)
        self.approved_claims = claims
        self.approved_entities = entities

    def claim_rows(self, chunks):
        return approved_sample_rows(super().claim_rows(chunks), self.approved_claims)

    def entity_rows(self, chunks):
        return approved_sample_rows(
            self.population_rows(chunks), self.approved_entities
        )

    def write(self, chunks, output_root):
        claim, entity, path, manifest = super().write(chunks, output_root)
        manifest["selection"] = (
            "carry_West_approved_v2_samples_after_exact_body_proof_validation"
        )
        manifest["review_status"] = "approved"
        manifest["additional_approved_b1_records"] = 150
        manifest["additional_unique_chunk_entity_links"] = 148
        atomic_write_json(path, manifest)
        return claim, entity, path, manifest


def freeze(replay_root: Path, output: Path):
    output = output.resolve()
    if not output.is_relative_to(NAMESPACE.resolve()) or output == NAMESPACE.resolve():
        raise ValueError("freeze_output_outside_namespace")
    if output.exists():
        raise ValueError("freeze_output_already_exists")
    replay_root = replay_root.resolve()
    if not replay_root.is_relative_to(
        (AI_ROOT / "data/reruns/jay-chou-linker-b1-20261019").resolve()
    ):
        raise ValueError("replay_outside_b1_namespace")
    packet = QA_ROOT / "qa/knowledge-review-packet-v2.md"
    if sha256_file(packet) != PACKET_SHA:
        raise ValueError("approved_qa_packet_changed")
    chunks = list(KnowledgeDatasetWriter(AI_ROOT, replay_root).load_existing().values())
    approved = KnowledgeDatasetWriter(AI_ROOT, APPROVED).load_existing()
    validate_replay(chunks, approved)
    old_ids = {
        c.doc_id for c in KnowledgeDatasetWriter(AI_ROOT, OLD).load_existing().values()
    }
    inventory = validate_inventory(
        chunks, old_ids, read_json(RECOVERY / "qa/document-decisions.json")
    )
    if (len(chunks), inventory["parent_count"], inventory["child_count"]) != (
        1959,
        689,
        1270,
    ):
        raise ValueError("release_chunk_counts_mismatch")
    decisions = read_rows(replay_root / "b1-decisions.jsonl")
    if len(decisions) != 150 or any(d["status"] != "resolved_b1" for d in decisions):
        raise ValueError("approved_b1_not_fully_resolved")
    if read_rows(replay_root / "entity-link-grounding-v7.jsonl") != read_rows(
        APPROVED / "entity-link-grounding-v7.jsonl"
    ):
        raise ValueError("approved_v7_proofs_changed")
    proofs = {}
    for row in read_rows(replay_root / "entity-link-grounding-v7.jsonl"):
        chunk = next(c for c in chunks if c.chunk_id == row["chunk_id"])
        proofs[chunk.chunk_id] = GroundedLinkAudit(
            result=chunk.knowledge.entities,
            **{k: tuple(v) if isinstance(v, list) else v for k, v in row.items()},
        )
    qa = QA_ROOT / "data/knowledge/review"
    sampler = CarriedQASampler(
        proofs,
        claims=read_rows(qa / "claim_review_sample.jsonl"),
        entities=read_rows(qa / "entity_link_review_sample.jsonl"),
    )
    sampler.claim_rows(chunks)
    population = sampler.population_rows(chunks)
    approved_sample_rows(population, sampler.approved_entities)
    supplement = read_json(qa / "document-coverage-supplement-v2.json")
    approved_sample_rows(population, supplement["samples"])
    protected_paths = [
        packet,
        QA_ROOT / "repair-manifest.json",
        qa / "claim_review_sample.jsonl",
        qa / "entity_link_review_sample.jsonl",
        qa / "document-coverage-supplement-v2.json",
        RECOVERY / "qa/document-decisions.json",
        RECOVERY / "data/enriched/v1/enriched_chunks.jsonl",
        replay_root / "knowledge_enriched_chunks.jsonl",
        replay_root / "entity-link-grounding-v7.jsonl",
        replay_root / "scope.json",
        replay_root / "b1-decisions.jsonl",
        APPROVED / "knowledge_enriched_chunks.jsonl",
    ]
    evidence = [
        {"path": p.relative_to(AI_ROOT).as_posix(), "sha256": sha256_file(p)}
        for p in protected_paths
    ]
    working = output / "working/v1"
    KnowledgeDatasetWriter(AI_ROOT, working).write(chunks)
    KnowledgeQualityReporter(AI_ROOT, working).write(chunks)
    builder = KnowledgeReleaseBuilder(
        AI_ROOT,
        source_root=working,
        output_root=output / "data/knowledge/v2",
        review_root=output / "qa",
        enriched_path=RECOVERY / "data/enriched/v1/enriched_chunks.jsonl",
        config_path=replay_root / "config-v7-rule-only.json",
    )
    builder.sampler = sampler
    builder.prepare_review()
    manifest = read_json(output / "qa/qa_sampling_manifest.json")
    attestation = {
        "attestation_version": "v1",
        "review_status": "approved",
        "reviewer": "West",
        "claim_sample_sha256": manifest["claim_sample_sha256"],
        "entity_link_sample_sha256": manifest["entity_link_sample_sha256"],
        "claim_reviewed_count": manifest["claim_sample_count"],
        "entity_link_reviewed_count": manifest["entity_link_sample_count"],
        "generation_mode": "rule_only",
        "release_selection_sha256": manifest["release_selection_sha256"],
        "rejected_sample_ids": [],
        "inventory": inventory,
        "authorization_source": "Direct user instruction in this conversation, 2026-10-19 Asia/Shanghai",
        "authorization_quote": "West 已批准 150 个新链接，不再抽查；QA 签字（West 已签 v2 包‘全部通过’）仍然有效；执行冻结",
        "approval_carried_forward": True,
        "historical_pending_templates_unchanged": True,
        "approved_b1_records": 150,
        "new_unique_links": 148,
        "qa_v2_entity_sample_count": 100,
        "qa_v2_supplement_count": len(supplement["samples"]),
        "approval_binding_evidence": evidence,
    }
    atomic_write_json(output / "qa/qa_review_attestation.json", attestation)
    shutil.copyfile(
        qa / "document-coverage-supplement-v2.json",
        output / "qa/document-coverage-supplement-v2.json",
    )
    outcome = builder.finalize()
    if any(sha256_file(AI_ROOT / e["path"]) != e["sha256"] for e in evidence):
        raise ValueError("freeze_input_changed")
    result = {
        "status": outcome.status,
        "release_id": outcome.release_id,
        "inventory": inventory,
        **outcome.summary,
        "release_manifest_sha256": sha256_file(
            builder.output_root / "knowledge_release_manifest.json"
        ),
        "qa_v2_samples_and_supplement_identical": True,
        "v7_replay_equals_West_approved_Gate1": True,
        "parse_clean_chunk_enrichment_recomputed": 0,
        "reused_upstream_chunk_count": len(chunks),
        "evidence": evidence,
    }
    atomic_write_json(output / "freeze-result.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(
        json.dumps(freeze(args.replay_root, args.output), ensure_ascii=False, indent=2)
    )


if __name__ == "__main__":
    main()
