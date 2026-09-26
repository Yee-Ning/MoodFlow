"""Prepare a new reviewed data revision without importing or running evaluation."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, date, datetime
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from pydantic import Field, model_validator

from evals.semantic_relations_models import (
    ReplayInput, SemanticGoldCase, SourceSnapshot, StrictModel, file_hash, load_suite, read_jsonl,
)
from evals.worldline_gold_v1_models import content_sha256


class ReviewDecision(StrictModel):
    case_id: str = Field(min_length=1)
    status: Literal["approved", "rejected"]
    reason: str = Field(min_length=1)


class HumanReviewBatch(StrictModel):
    reviewer: str = Field(min_length=1)
    reviewed_at: str
    provenance: Literal["explicit_user_review"] = "explicit_user_review"
    decisions: list[ReviewDecision] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_batch(self):
        date.fromisoformat(self.reviewed_at)  # Preserve the supplied date, no invented hour.
        ids = [item.case_id for item in self.decisions]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate_review_decision")
        return self


def label_hash(case: SemanticGoldCase) -> str:
    return content_sha256(json.dumps(
        case.model_dump(mode="json", exclude={"review"}), sort_keys=True, ensure_ascii=False,
    ))


def _write_json(path: Path, value):
    path.write_bytes((json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def _write_rows(path: Path, rows):
    path.write_bytes("".join(
        json.dumps(row.model_dump(mode="json"), ensure_ascii=False, sort_keys=True) + "\n"
        for row in rows
    ).encode("utf-8"))


def _frozen_lines(path: Path, key: str):
    return {json.loads(line)[key]: line for line in path.read_bytes().splitlines(keepends=True)}


def render_pending_review_pack(cases, inputs, sources) -> str:
    lines = ["# Semantic Relations v1 review r1 — 弱证据复审包", "",
             "本包仅包含 4 条新建 pending 候选；尚未运行该数据版本的 Evaluation。",
             "请打开完整来源，核对实体、上下文和所询 predicate；不能根据模型输出审核标签。",
             "proposed negative 只表示给定证据不支持所询关系，不断言现实中关系不存在。",
             "exact span 是真实网页摘录；换行用于字段分隔。来源上下文与候选关系注释严格区分。",
             "supported_claims 中的所询关系是刻意不充分的安全性探针，不是来源已证实的断言。",
             "批准只晋升本包对应的冻结标签；新结论仍需创建新版本，不修改本次 freeze。", ""]
    for case in cases:
        lines.extend([f"## {case.case_id}", "", f"Query: {case.query}",
                      f"relation_type: `{case.relation_type}`; proposed label: **negative**",
                      f"Source → target: `{case.source_entity}` → `{case.target_entity}`",
                      "Proposed final relations: `[]`（对本 query 拒绝输出关系）",
                      f"Rationale: {case.rationale}", ""])
        for span in case.supporting_evidence:
            source = sources[span.source_id]
            claim = next(claim for document in inputs[case.case_id].documents
                         for claim in document.metadata["supported_claims"]
                         if claim["claim_id"] == span.claim_id)
            lines.extend([f"Source: [{source.title}]({source.url}) — {source.publisher}",
                          f"Retrieved at: {source.retrieved_at}; locator: {source.locator}",
                          f"Source ID: `{source.source_id}`; content SHA256: `{source.content_hash}`",
                          f"Exact span: Unicode code-point offsets [{span.span_start}, {span.span_end})",
                          f"Document / chunk / claim: `{span.document_id}` / `{span.chunk_id}` / `{span.claim_id}`",
                          "", "```text", span.exact_span, "```", "",
                          f"候选探针 predicate: `{claim['predicate']}`; "
                          f"subject: `{claim['subject_entity_id']}`; "
                          f"object: `{claim.get('object_entity_id') or claim.get('object_value')}`",
                          "候选置信度为固定测试注释，不代表来源支持度；不得写入生产索引。", ""])
        lines.extend([f"Label SHA256（排除 review）: `{label_hash(case)}`", "",
                      "核对重点：该记录实际支持了什么；它是否明确支持所询关系及其方向；"
                      "是否遗漏了会改变标签的来源上下文。", "",
                      "```text", "reviewer:", "reviewed_at: YYYY-MM-DD",
                      f"case_id: {case.case_id}", "decision: APPROVE / REJECT", "reason:", "```", ""])
    return "\n".join(lines)


def validate_review_revision(directory: Path, base: Path) -> dict:
    """Check approval/exclusion/lineage invariants without running query or scoring code."""
    cases, _, _, manifest = load_suite(directory)
    originals, _, _, original_manifest = load_suite(base)
    if (manifest["parent_manifest_sha256"] != file_hash(base / "manifest.json")
            or manifest["parent_files"] != original_manifest["files"]):
        raise ValueError("review_parent_freeze_mismatch")
    for relative, digest in manifest["review_files"].items():
        path = (directory / relative).resolve()
        if not path.is_relative_to(directory.resolve()) or file_hash(path) != digest:
            raise ValueError(f"review_file_hash_mismatch:{relative}")
    batch = HumanReviewBatch.model_validate_json((directory / "review_decisions.json").read_bytes())
    decisions = {item.case_id: item for item in batch.decisions}
    original_map = {case.case_id: case for case in originals}
    original_real = {case.case_id for case in originals if case.dataset_kind == "real_source"}
    if set(decisions) != original_real:
        raise ValueError("review_decision_ids_mismatch")
    approved = read_jsonl(directory / "approved_gold.jsonl", SemanticGoldCase)
    excluded = read_jsonl(directory / "excluded_cases.jsonl", SemanticGoldCase)
    pending = read_jsonl(directory / "pending_weak_evidence.jsonl", SemanticGoldCase)
    for rows, status in ((approved, "approved"), (excluded, "rejected")):
        ids = [case.case_id for case in rows]
        if len(ids) != len(set(ids)) or set(ids) != {
            key for key, value in decisions.items() if value.status == status
        }:
            raise ValueError("review_view_ids_mismatch")
        for case in rows:
            decision = decisions[case.case_id]
            if (case.review.status != status or case.review.method != "human"
                    or case.review.reviewer != batch.reviewer
                    or case.review.reviewed_at != batch.reviewed_at
                    or case.review.notes != decision.reason
                    or label_hash(case) != label_hash(original_map[case.case_id])):
                raise ValueError("review_changed_original_label_or_decision")
            relative = case.review.record_path
            if relative not in manifest["review_files"]:
                raise ValueError("review_receipt_not_frozen")
            receipt = json.loads((directory / relative).read_text(encoding="utf-8"))
            if (case.review.record_sha256 != file_hash(directory / relative)
                    or receipt["case_id"] != case.case_id or receipt["status"] != status
                    or receipt["label_sha256"] != label_hash(case)
                    or receipt["reviewer"] != batch.reviewer
                    or receipt["reviewed_at"] != batch.reviewed_at
                    or receipt["reason"] != decision.reason
                    or receipt["parent_manifest_sha256"] != manifest["parent_manifest_sha256"]
                    or receipt["parent_case_line_sha256"] != sha256(
                        _frozen_lines(base / "gold.jsonl", "case_id")[case.case_id]).hexdigest()
                    or receipt["replay_input_line_sha256"] != sha256(
                        _frozen_lines(base / "inputs.jsonl", "case_id")[case.case_id]).hexdigest()):
                raise ValueError("review_receipt_lineage_mismatch")
    if any(case.review.status != "pending" or case.review.method != "pending_human"
           or case.review.reviewer or case.review.reviewed_at or case.review.record_path
           or case.review.record_sha256 or case.dataset_kind != "real_source"
           or case.label != "negative" or "weak_evidence" not in case.categories
           for case in pending):
        raise ValueError("pending_view_not_pending_weak_negative")
    included = {case.case_id: case for case in cases}
    combined = [*approved, *pending]
    if len({case.case_id for case in combined}) != len(combined):
        raise ValueError("review_view_duplicate_case")
    if ({case.case_id for case in cases if case.dataset_kind == "real_source"}
            != {case.case_id for case in combined}
            or any(included[case.case_id] != case for case in combined)):
        raise ValueError("review_view_suite_mismatch")
    if ({case.case_id: case for case in cases if case.dataset_kind == "synthetic"}
            != {case.case_id: case for case in originals if case.dataset_kind == "synthetic"}):
        raise ValueError("review_changed_synthetic_regression")
    original_inputs = _frozen_lines(base / "inputs.jsonl", "case_id")
    retained_inputs = _frozen_lines(directory / "inputs.jsonl", "case_id")
    for case in cases:
        if case.case_id in original_map and (
            label_hash(case) != label_hash(original_map[case.case_id])
            or retained_inputs[case.case_id] != original_inputs[case.case_id]
        ):
            raise ValueError("review_changed_retained_labels_or_inputs")
    old_sources = _frozen_lines(base / "sources.jsonl", "source_id")
    new_sources = _frozen_lines(directory / "sources.jsonl", "source_id")
    if any(new_sources.get(key) != line for key, line in old_sources.items()):
        raise ValueError("review_changed_original_sources")
    return {"integrity": "passed", "evaluation_run": False,
            "approved": len(approved), "excluded": len(excluded), "pending": len(pending)}


def prepare_review_revision(
    base: Path, output: Path, batch: HumanReviewBatch,
    pending_cases: list[SemanticGoldCase], pending_inputs: list[ReplayInput],
    pending_sources: list[SourceSnapshot],
) -> dict:
    """Bind explicit decisions, retain replay bytes, exclude rejected rows, freeze new proposals."""
    if output.exists() or output.resolve().is_relative_to(base.resolve()):
        raise ValueError("new_revision_directory_required")
    cases, _, _, manifest = load_suite(base)
    real = {case.case_id: case for case in cases if case.dataset_kind == "real_source"}
    decisions = {item.case_id: item for item in batch.decisions}
    if set(decisions) != set(real) or any(case.review.status != "pending" for case in real.values()):
        raise ValueError("review_batch_must_cover_original_pending_real_cases")
    old_ids = {case.case_id for case in cases}
    ids = [case.case_id for case in pending_cases]
    if not ids or len(ids) != len(set(ids)) or old_ids.intersection(ids):
        raise ValueError("new_unique_pending_ids_required")
    if {item.case_id for item in pending_inputs} != set(ids) or len(pending_inputs) != len(ids):
        raise ValueError("pending_input_ids_mismatch")
    if any(case.dataset_kind != "real_source" or case.review.status != "pending"
           or case.review.method != "pending_human" or case.review.reviewer
           or case.review.reviewed_at or case.review.record_path or case.review.record_sha256
           or case.label != "negative" or "weak_evidence" not in case.categories
           for case in pending_cases):
        raise ValueError("new_weak_cases_must_remain_pending_negative")
    if any(case.query in {old.query for old in real.values()}
           or (case.relation_type == "influence" and case.source_entity == case.target_entity)
           for case in pending_cases):
        raise ValueError("new_probe_query_or_endpoints_invalid")
    source_ids = [item.source_id for item in pending_sources]
    old_source_lines = _frozen_lines(base / "sources.jsonl", "source_id")
    if not source_ids or len(source_ids) != len(set(source_ids)) or set(source_ids) & old_source_lines.keys():
        raise ValueError("new_unique_sources_required")
    if any(item.origin != "real_source" for item in pending_sources):
        raise ValueError("new_real_sources_required")
    if {span.source_id for case in pending_cases for span in case.supporting_evidence} != set(source_ids):
        raise ValueError("pending_sources_mismatch")

    base_manifest_hash = file_hash(base / "manifest.json")
    old_input_lines = _frozen_lines(base / "inputs.jsonl", "case_id")
    old_gold_lines = _frozen_lines(base / "gold.jsonl", "case_id")
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="semantic-review-", dir=output.parent) as staging_name:
        staging = Path(staging_name) / "revision"
        staging.mkdir()
        (staging / "receipts").mkdir()
        _write_json(staging / "review_decisions.json", batch.model_dump(mode="json"))
        _write_json(staging / "review_decisions.schema.json", HumanReviewBatch.model_json_schema())
        approved, excluded, receipt_hashes = [], [], {}
        for case_id, case in real.items():
            decision = decisions[case_id]
            relative = f"receipts/{case_id}.json"
            receipt = {"case_id": case_id, "status": decision.status,
                       "reviewer": batch.reviewer, "reviewed_at": batch.reviewed_at,
                       "reason": decision.reason, "provenance": batch.provenance,
                       "label_sha256": label_hash(case),
                       "parent_manifest_sha256": base_manifest_hash,
                       "parent_case_line_sha256": sha256(old_gold_lines[case_id]).hexdigest(),
                       "replay_input_line_sha256": sha256(old_input_lines[case_id]).hexdigest()}
            _write_json(staging / relative, receipt)
            receipt_hashes[relative] = file_hash(staging / relative)
            raw = case.model_dump(mode="json")
            raw["review"] = {"status": decision.status, "method": "human",
                             "reviewer": batch.reviewer, "reviewed_at": batch.reviewed_at,
                             "record_path": relative, "record_sha256": receipt_hashes[relative],
                             "notes": decision.reason}
            reviewed = SemanticGoldCase.model_validate(raw)
            (approved if decision.status == "approved" else excluded).append(reviewed)
        synthetic = [case for case in cases if case.dataset_kind == "synthetic"]
        _write_rows(staging / "gold.jsonl", [*synthetic, *approved, *pending_cases])
        _write_rows(staging / "approved_gold.jsonl", approved)
        _write_rows(staging / "pending_weak_evidence.jsonl", pending_cases)
        _write_rows(staging / "excluded_cases.jsonl", excluded)
        _write_rows(staging / "inputs.jsonl", pending_inputs)
        (staging / "inputs.jsonl").write_bytes(
            b"".join(old_input_lines[case.case_id] for case in [*synthetic, *approved])
            + (staging / "inputs.jsonl").read_bytes())
        _write_rows(staging / "sources.jsonl", pending_sources)
        (staging / "sources.jsonl").write_bytes(
            b"".join(old_source_lines.values()) + (staging / "sources.jsonl").read_bytes())
        (staging / "schema.json").write_bytes((base / "schema.json").read_bytes())
        (staging / ".gitattributes").write_bytes(
            b"*.json text eol=lf\n*.jsonl text eol=lf\n*.md text eol=lf\n.gitattributes text eol=lf\n")
        summary = {"version": "semantic-relations-v1-review-r1", "evaluation_run": False,
                   "reviewer": batch.reviewer, "reviewed_at": batch.reviewed_at,
                   "approved_real_gold": len(approved), "excluded_rejected": len(excluded),
                   "pending_weak_evidence": len(pending_cases), "synthetic_regression": len(synthetic),
                   "approved_by_relation_type": dict(Counter(case.relation_type for case in approved)),
                   "pending_by_relation_type": dict(Counter(case.relation_type for case in pending_cases)),
                   "approved_case_ids": [case.case_id for case in approved],
                   "excluded_case_ids": [case.case_id for case in excluded],
                   "pending_case_ids": ids,
                   "parent_manifest_sha256": base_manifest_hash,
                   "approved_label_payloads_unchanged": True, "retained_replay_lines_unchanged": True,
                   "scope": "review_preparation_only; no scoring, runtime, index or deployment"}
        _write_json(staging / "review_summary.json", summary)
        pack = render_pending_review_pack(pending_cases,
                                          {item.case_id: item for item in pending_inputs},
                                          {item.source_id: item for item in pending_sources})
        (staging / "human_review_pack.md").write_bytes(pack.encode("utf-8"))
        auxiliary = ["approved_gold.jsonl", "pending_weak_evidence.jsonl", "excluded_cases.jsonl",
                     "review_decisions.json", "review_decisions.schema.json", "review_summary.json",
                     "human_review_pack.md", ".gitattributes"]
        _write_json(staging / "manifest.json", {
            "version": summary["version"], "schema_version": "semantic-relations-evaluation-v1",
            "created_at": datetime.now(UTC).isoformat(), "evaluation_run": False,
            "parent_directory": base.name, "parent_manifest_sha256": base_manifest_hash,
            "parent_files": manifest["files"],
            "files": {name: file_hash(staging / name) for name in manifest["files"]},
            "review_files": {name: file_hash(staging / name) for name in auxiliary} | receipt_hashes,
            "rule": "New review revision only. Do not edit frozen labels to remove runtime failures.",
        })
        validate_review_revision(staging, base)
        if file_hash(base / "manifest.json") != base_manifest_hash:
            raise ValueError("parent_freeze_changed")
        staging.rename(output)
    return summary
