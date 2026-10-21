"""Offline, immutable-input replay of West's approved 150 B1 entries."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.rag.knowledge.config import KnowledgeConfig
from app.rag.knowledge.entity_linking.b1_scope import B1Scope, B1ScopeEntry
from app.rag.knowledge.entity_linking.grounded import (
    GroundedEntityLinker,
    film_document,
)
from app.rag.knowledge.grounded_qa import validate_entity_sample
from app.rag.knowledge.hashing import knowledge_input_hash
from app.rag.knowledge.models import (
    KnowledgeEnrichedChildChunk,
    KnowledgeEnrichedParentChunk,
    KnowledgeEntities,
    KnowledgeStats,
)
from app.rag.knowledge.report import KnowledgeQualityReporter
from app.rag.knowledge.validator import KnowledgeValidator


AI_ROOT = Path(__file__).resolve().parents[1]
REPO = AI_ROOT.parent
REVIEW = AI_ROOT / "tests/fixtures/linker_b1_review_20261019.json"
OUTPUT_NAMESPACE = AI_ROOT / "data/reruns/jay-chou-linker-b1-20261019"


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for data in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(data)
    return digest.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def read_rows(path):
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def build_scope(review):
    """Strip all reviewed answers; only immutable occurrence bindings may execute."""
    return B1Scope(
        [
            B1ScopeEntry.model_validate(
                {key: row[key] for key in B1ScopeEntry.model_fields}
            )
            for row in review["rows"]
            if row["subclass"] == "B1"
        ]
    )


def metrics(rows):
    linked = sum(r["knowledge"]["stats"]["entity_count"] for r in rows)
    unresolved = sum(
        bool(m["candidate_entity_ids"])
        for r in rows
        for m in r["knowledge"]["entities"]["unresolved_mentions"]
    )
    return {
        "document_count": len({r["doc_id"] for r in rows}),
        "chunk_count": len(rows),
        "linked": linked,
        "unresolved": unresolved,
        "denominator": linked + unresolved,
        "ratio": linked / (linked + unresolved) if linked + unresolved else 1.0,
    }


def validate_delta(before, after, scope_entries, proof):
    old = before.model_dump(mode="json")
    new = after.model_dump(mode="json")
    old_knowledge, new_knowledge = old.pop("knowledge"), new.pop("knowledge")
    if old != new:
        raise ValueError("b1_nonknowledge_mutated")
    for field in ("claims", "quality"):
        if old_knowledge[field] != new_knowledge[field]:
            raise ValueError("b1_preserved_knowledge_mutated")
    old_entities = {
        e["entity_id"]: e for e in old_knowledge["entities"]["linked_entities"]
    }
    new_entities = {
        e["entity_id"]: e for e in new_knowledge["entities"]["linked_entities"]
    }
    if any(new_entities.get(key) != value for key, value in old_entities.items()):
        raise ValueError("b1_existing_link_mutated")
    old_u = {
        u["normalized_text"]: u
        for u in old_knowledge["entities"]["unresolved_mentions"]
    }
    new_u = {
        u["normalized_text"]: u
        for u in new_knowledge["entities"]["unresolved_mentions"]
    }
    allowed = {e.normalized_text: e for e in scope_entries}
    if new_u.keys() - old_u.keys():
        raise ValueError("b1_unexpected_unresolved_added")
    if any(
        new_u.get(key) != value for key, value in old_u.items() if key not in allowed
    ):
        raise ValueError("b1_out_of_scope_unresolved_mutated")
    if any(new_u[key] != old_u[key] for key in new_u):
        raise ValueError("b1_retained_unresolved_mutated")
    resolved = [d for d in proof.b1_decisions if d["status"] == "resolved_b1"]
    removed = set(old_u) - set(new_u)
    if removed != {d["normalized_text"] for d in resolved}:
        raise ValueError("b1_removed_unresolved_without_proof")
    added = set(new_entities) - set(old_entities)
    if added != {d["entity_id"] for d in resolved}:
        raise ValueError("b1_added_link_without_proof")
    for decision in resolved:
        entry = allowed[decision["normalized_text"]]
        if decision["entity_id"] not in entry.candidate_entity_ids:
            raise ValueError("b1_target_outside_original_candidates")
    for entity_id in added:
        matches = [m for m in proof.body_matches if m["entity_id"] == entity_id]
        row = {
            "chunk_id": after.chunk_id,
            "document_id": after.doc_id,
            "entity_id": entity_id,
            "body_matches": matches,
            "matched_surfaces": sorted({m["quote"] for m in matches}),
        }
        validate_entity_sample(row, after)
        for match in matches:
            left, right = match["evidence_start"], match["evidence_end"]
            if not (0 <= left < right <= len(after.content)):
                raise ValueError("b1_evidence_offset_invalid")
            if after.content[left:right] != match["evidence_quote"]:
                raise ValueError("b1_evidence_quote_mismatch")


def replay_chunk(raw, linker, scope):
    cls = (
        KnowledgeEnrichedParentChunk
        if raw["parent_id"] is None
        else KnowledgeEnrichedChildChunk
    )
    before = cls.model_validate(raw)
    baseline = linker._link_v6_with_evidence(before)
    if (
        baseline.result.model_dump(
            mode="json", include={"linked_entities", "unresolved_mentions"}
        )
        != raw["knowledge"]["entities"]
    ):
        raise ValueError(f"v6_baseline_entity_mismatch:{before.chunk_id}")
    proof = linker.link_with_evidence(before)
    validate_proof_preservation(baseline, proof)
    entities = KnowledgeEntities(
        linked_entities=proof.result.linked_entities,
        unresolved_mentions=proof.result.unresolved_mentions,
    )
    knowledge = before.knowledge.model_copy(
        update={
            "entities": entities,
            "stats": KnowledgeStats.build(entities, before.knowledge.claims),
            "provenance": before.knowledge.provenance.model_copy(
                update={
                    "entity_linking_version": "v7",
                    "knowledge_input_hash": knowledge_input_hash(before, linker.config),
                }
            ),
        }
    )
    after = before.model_copy(update={"knowledge": knowledge})
    KnowledgeValidator().validate(before, after)
    validate_delta(before, after, scope.for_chunk(before.chunk_id), proof)
    return after, proof, baseline


def validate_proof_preservation(baseline, proof):
    if proof.body_matches[: len(baseline.body_matches)] != baseline.body_matches:
        raise ValueError("b1_existing_body_evidence_mutated")
    moved = {
        (match["start"], match["end"])
        for match in proof.body_matches[len(baseline.body_matches) :]
    }
    expected_rejected = tuple(
        r for r in baseline.rejected if (r["start"], r["end"]) not in moved
    )
    if proof.rejected != expected_rejected:
        raise ValueError("b1_unresolved_body_evidence_mutated")
    if (
        proof.body_sha256 != baseline.body_sha256
        or proof.context_only_mentions != baseline.context_only_mentions
        or proof.excluded_reason != baseline.excluded_reason
    ):
        raise ValueError("b1_body_context_mutated")


def guard_output(path, namespace=OUTPUT_NAMESPACE):
    target, boundary = Path(path).resolve(), Path(namespace).resolve()
    if target == boundary or not target.is_relative_to(boundary):
        raise ValueError("b1_output_outside_namespace")
    if target.exists():
        raise ValueError("b1_output_already_exists")
    return target


def write_json(path, value):
    with Path(path).open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def write_rows(path, values):
    with Path(path).open("x", encoding="utf-8", newline="\n") as handle:
        for value in values:
            handle.write(
                json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"
            )


def replay(output, cohort="all", review_path=REVIEW):
    output = guard_output(output)
    review = read_json(review_path)
    protected = {str(Path(review_path).resolve()): sha256_file(review_path)}
    for prefix in ("source", "decisions", "sidecar"):
        source = REPO / review[f"{prefix}_path"]
        if sha256_file(source) != review[f"{prefix}_sha256"]:
            raise ValueError(f"b1_input_hash_mismatch:{prefix}")
        protected[str(source)] = review[f"{prefix}_sha256"]
    registry_root = AI_ROOT / "app/rag/knowledge/entity_linking/registry"
    for path in registry_root.glob("*.yaml"):
        protected[str(path)] = sha256_file(path)
    default_config = AI_ROOT / "app/rag/knowledge/config/v19.yaml"
    protected[str(default_config)] = sha256_file(default_config)
    all_scope = build_scope(review)
    if len(all_scope.entries) != 150:
        raise ValueError("b1_expected_150_approved_entries")
    decisions = read_json(REPO / review["decisions_path"])
    new_ids = {
        d["document_id"]
        for d in decisions["documents"]
        if d["status"] == "passed_document_qa"
    }
    if len(new_ids) != 26:
        raise ValueError("b1_cohort_definition_mismatch")
    raw_rows = [
        row
        for row in read_rows(REPO / review["source_path"])
        if cohort == "all" or (row["doc_id"] in new_ids) == (cohort == "new26")
    ]
    film_docs = frozenset(
        row["doc_id"] for row in raw_rows if film_document(row["content"])
    )
    linker = GroundedEntityLinker(
        KnowledgeConfig(entity_linking_version="v7", llm_enabled=False),
        b1_scope=all_scope,
        film_documents=film_docs,
    )
    models, proofs, before_proofs = [], [], []
    original_proofs = {
        row["chunk_id"]: row for row in read_rows(REPO / review["sidecar_path"])
    }
    for raw in raw_rows:
        model, proof, baseline = replay_chunk(raw, linker, all_scope)
        if baseline.payload() != original_proofs[model.chunk_id]:
            raise ValueError(f"v6_frozen_proof_mismatch:{model.chunk_id}")
        models.append(model)
        proofs.append(proof)
        before_proofs.append(baseline)
    KnowledgeValidator().validate_parent_references(models)
    after_rows = [model.model_dump(mode="json") for model in models]
    applicable = {
        (entry.chunk_id, entry.normalized_text)
        for entry in all_scope.entries.values()
        if any(row["chunk_id"] == entry.chunk_id for row in raw_rows)
    }
    applied = {
        (proof.chunk_id, d["normalized_text"])
        for proof in proofs
        for d in proof.b1_decisions
    }
    if applied != applicable:
        raise ValueError("b1_scope_entries_not_all_examined")
    by_review = {r["audit_id"]: r for r in review["rows"]}
    groups = {}
    wrong_targets = []
    for proof in proofs:
        for decision in proof.b1_decisions:
            reviewed = by_review[decision["audit_id"]]
            group = reviewed["normalized_text"]
            item = groups.setdefault(
                group, {"before": 0, "resolved": 0, "remaining": 0}
            )
            item["before"] += 1
            item[
                "resolved" if decision["status"] == "resolved_b1" else "remaining"
            ] += 1
            # Reviewed answers are used only AFTER inference, never fed to it.
            if (
                decision["status"] == "resolved_b1"
                and decision["entity_id"] not in reviewed["expected_targets"]
            ):
                wrong_targets.append(decision["audit_id"])
    if wrong_targets:
        raise ValueError("b1_review_target_mismatch:" + ",".join(wrong_targets))
    if any(sha256_file(path) != digest for path, digest in protected.items()):
        raise ValueError("b1_protected_input_changed")
    result = {
        "cohort": cohort,
        "before": metrics(raw_rows),
        "after": metrics(after_rows),
        "groups": groups,
        "scope_sha256": all_scope.sha256,
        "scope_count": 150,
        "applied_scope_count": sum(g["before"] for g in groups.values()),
        "resolved_count": sum(g["resolved"] for g in groups.values()),
        "kept_unresolved": [
            d for p in proofs for d in p.b1_decisions if d["status"] != "resolved_b1"
        ],
        "checks": {
            "v6_entity_results_identical": True,
            "v6_frozen_body_proofs_identical": True,
            "existing_body_evidence_unchanged": True,
            "outside_scope_rejections_unchanged": True,
            "existing_links_unchanged": True,
            "outside_scope_unresolved_unchanged": True,
            "claims_and_source_unchanged": True,
            "body_evidence_valid": True,
            "protected_input_hashes_unchanged": True,
            "reviewed_targets_match": True,
        },
        "protected_inputs": protected,
        "human_signoff": "pending West; no release frozen or index built",
    }
    # Nothing is written until every row and all preservation checks pass.
    output.mkdir(parents=True)
    write_json(output / "scope.json", all_scope.payload())
    write_json(output / "replay-report.json", result)
    write_json(
        output / "config-v7-rule-only.json", linker.config.model_dump(mode="json")
    )
    write_rows(output / "knowledge_enriched_chunks.jsonl", after_rows)
    write_rows(output / "entity-link-grounding-v7.jsonl", (p.payload() for p in proofs))
    write_rows(
        output / "entity-link-grounding-v6-before.jsonl",
        (p.payload() for p in before_proofs),
    )
    write_rows(
        output / "b1-decisions.jsonl",
        ({"chunk_id": p.chunk_id, **d} for p in proofs for d in p.b1_decisions),
    )
    quality = KnowledgeQualityReporter(output, output).build(models)
    write_json(output / "knowledge_quality_report.json", quality)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cohort", choices=("new26", "old22", "all"), default="all")
    args = parser.parse_args()
    result = replay(args.output, args.cohort)
    print(
        json.dumps(
            {
                key: result[key]
                for key in ("cohort", "before", "after", "resolved_count", "groups")
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
