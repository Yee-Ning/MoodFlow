"""Evaluation-only contracts; Gold labels never enter the runtime adapter."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agent.foundation.subgraphs.worldline.models import (
    MusicKnowledgeDocument, WorldLineGraph,
)
from app.rag.query_understanding.models import SearchPlan
from evals.worldline_gold_v1_models import GoldSourceLedgerEntry, content_sha256


Family = Literal["era", "scene", "influence"]
SCHEMA_VERSION = "semantic-relations-evaluation-v1"
PREDICATES = {
    "era": {"belongs_to_era", "representative_of_era", "temporal_association",
            "career_phase", "style_era_association"},
    "scene": {"belongs_to_scene", "representative_of_scene", "scene_association",
              "style_scene_association", "emerged_from_scene"},
    "influence": {"influenced", "inspired", "shaped_style", "cited_as_influence"},
}
CATEGORIES = {"positive", "negative", "weak_evidence", "negation", "conflict",
              "direction_sensitive", "confusable", "isolation", "retrieval_missing"}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Review(StrictModel):
    status: Literal["pending", "approved", "rejected", "synthetic"]
    method: Literal["pending_human", "human", "synthetic"]
    reviewer: str | None = None
    reviewed_at: str | None = None
    record_path: str | None = None
    record_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    notes: str = ""


class Span(StrictModel):
    source_id: str
    document_id: str
    chunk_id: str
    claim_id: str
    exact_span: str = Field(min_length=1)
    span_start: int = Field(ge=0)
    span_end: int = Field(gt=0)
    stance: Literal["support", "contradiction", "context"] = "support"


class ExpectedRelation(StrictModel):
    source_entity: str
    target_entity: str
    predicate: str
    evidence_claim_ids: list[str] = Field(min_length=1)
    # Explicit field equality includes null; omission does not constrain the field.
    fields: dict = Field(default_factory=dict)
    conflict_flags: list[str] = Field(default_factory=list)

    @property
    def key(self) -> tuple[str, str, str]:
        return self.source_entity, self.target_entity, self.predicate


class SemanticGoldCase(StrictModel):
    schema_version: Literal["semantic-relations-evaluation-v1"] = SCHEMA_VERSION
    case_id: str = Field(min_length=1)
    dataset_kind: Literal["real_source", "synthetic"]
    query: str = Field(min_length=1)
    relation_type: Family
    source_entity: str
    target_entity: str
    label: Literal["positive", "negative"]
    categories: list[str] = Field(min_length=1)
    expected_relations: list[ExpectedRelation]
    supporting_evidence: list[Span] = Field(min_length=1)
    review: Review
    rationale: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_labels(self):
        if not set(self.categories) <= CATEGORIES:
            raise ValueError("unknown_case_category")
        if self.label not in self.categories or len(self.categories) != len(set(self.categories)):
            raise ValueError("case_categories_label_mismatch_or_duplicate")
        if (self.label == "positive") != bool(self.expected_relations):
            raise ValueError("label_relation_mismatch")
        keys = [item.key for item in self.expected_relations]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate_expected_relation")
        evidence_ids = {item.claim_id for item in self.supporting_evidence}
        for relation in self.expected_relations:
            if relation.predicate not in PREDICATES[self.relation_type]:
                raise ValueError("expected_relation_family_mismatch")
            if not set(relation.evidence_claim_ids) <= evidence_ids:
                raise ValueError("expected_evidence_missing")
        if self.dataset_kind == "real_source":
            if self.review.status == "synthetic" or self.review.method == "synthetic":
                raise ValueError("real_review_cannot_be_synthetic")
            if self.review.status == "approved" and not (
                self.review.method == "human" and self.review.reviewer
                and self.review.reviewed_at and self.review.record_path
                and self.review.record_sha256
            ):
                raise ValueError("human_review_record_required")
        elif self.review.status != "synthetic" or self.review.method != "synthetic":
            raise ValueError("synthetic_review_required")
        return self

    @property
    def cohort(self) -> str:
        if self.dataset_kind == "synthetic":
            return "synthetic_regression"
        return ("approved_real_baseline" if self.review.status == "approved"
                else "pending_real_diagnostic")


class ReplayInput(StrictModel):
    """Frozen retrieved context and identities, stored independently of Gold."""

    case_id: str
    query: str
    search_plan: SearchPlan
    graph: WorldLineGraph
    documents: list[MusicKnowledgeDocument]
    input_kind: Literal["real_source_annotation", "synthetic_adversarial"]
    retrieval_mode: Literal["frozen_context_replay"] = "frozen_context_replay"


class SourceSnapshot(GoldSourceLedgerEntry):
    origin: Literal["real_source", "synthetic"]
    retrieved_at: str
    locator: str
    capture_method: Literal["verbatim_web_excerpt", "synthetic_fixture"]


def read_jsonl(path: Path, model):
    rows = [model.model_validate(json.loads(line))
            for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows:
        raise ValueError(f"empty_dataset_file:{path.name}")
    return rows


def file_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def load_suite(directory: Path):
    """Verify freeze, review receipts, provenance and input/label separation."""

    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    required = {"gold.jsonl", "inputs.jsonl", "sources.jsonl", "schema.json"}
    if set(manifest["files"]) != required:
        raise ValueError("freeze_files_mismatch")
    for filename, digest in manifest["files"].items():
        if file_hash(directory / filename) != digest:
            raise ValueError(f"freeze_hash_mismatch:{filename}")
    cases = read_jsonl(directory / "gold.jsonl", SemanticGoldCase)
    inputs = read_jsonl(directory / "inputs.jsonl", ReplayInput)
    sources = read_jsonl(directory / "sources.jsonl", SourceSnapshot)
    for rows in (cases, inputs, sources):
        ids = [getattr(row, "case_id", None) or row.source_id for row in rows]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate_record_id")
    input_map = {item.case_id: item for item in inputs}
    source_map = {item.source_id: item for item in sources}
    if set(input_map) != {case.case_id for case in cases}:
        raise ValueError("input_gold_ids_mismatch")
    for case in cases:
        replay = input_map[case.case_id]
        if case.query != replay.query or case.query != replay.search_plan.original_query:
            raise ValueError("input_query_mismatch")
        if (case.dataset_kind == "real_source") != (replay.input_kind == "real_source_annotation"):
            raise ValueError("input_kind_mismatch")
        if case.review.status == "approved":
            receipt = (directory / case.review.record_path).resolve()
            if not receipt.is_relative_to(directory.resolve()):
                raise ValueError("review_receipt_outside_suite")
            if file_hash(receipt) != case.review.record_sha256:
                raise ValueError("review_receipt_hash_mismatch")
            decision = json.loads(receipt.read_text(encoding="utf-8"))
            label_hash = content_sha256(json.dumps(
                case.model_dump(mode="json", exclude={"review"}), sort_keys=True,
                ensure_ascii=False,
            ))
            if (decision.get("case_id") != case.case_id
                    or decision.get("label_sha256") != label_hash
                    or decision.get("reviewer") != case.review.reviewer
                    or decision.get("reviewed_at") != case.review.reviewed_at
                    or decision.get("status") != "approved"):
                raise ValueError("review_receipt_label_mismatch")
        for span in case.supporting_evidence:
            source = source_map[span.source_id]
            if source.origin != case.dataset_kind:
                raise ValueError("source_origin_mismatch")
            if source.content[span.span_start:span.span_end] != span.exact_span:
                raise ValueError("gold_source_span_mismatch")
            if source.origin == "real_source" and (
                not source.url or source.capture_method != "verbatim_web_excerpt"
            ):
                raise ValueError("real_source_provenance_required")
        for document in replay.documents:
            source = source_map[document.metadata["snapshot_source_id"]]
            if (document.content != source.content or document.title != source.title
                    or document.source != source.publisher
                    or str(document.url or "") != str(source.url or "")):
                raise ValueError("input_source_snapshot_mismatch")
            if source.origin != case.dataset_kind:
                raise ValueError("input_source_origin_mismatch")
            if case.dataset_kind == "real_source":
                for claim in document.metadata.get("supported_claims", []):
                    if claim["evidence_span"] not in source.content:
                        raise ValueError("real_input_claim_span_mismatch")
    return cases, input_map, source_map, manifest
