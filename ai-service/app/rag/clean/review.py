"""Human-in-the-loop review and approval workflow for candidate clean rules."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from .config import CleanPipelineConfig, CleanRuleConfig, load_clean_config
from .discovery import CandidateRecommendedAction
from .errors import RuleRegistryError
from .io import atomic_write_json, atomic_write_text


class CandidateReviewEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str
    review_status: str = Field(default="unreviewed", pattern=r"^(unreviewed|approved|rejected)$")
    reviewed_by: str | None = None
    reviewed_at: str | None = None
    review_notes: str | None = None
    pattern_or_example: str
    noise_type: str
    recommended_action: CandidateRecommendedAction
    applicable_source: list[str] = Field(default_factory=list)
    applicable_document_type: list[str] = Field(default_factory=list)
    reason: str
    confidence: float
    false_positive_risk: str
    uncertain: bool
    evidence_document_ids: list[str]
    positive_examples: list[str] = Field(default_factory=list)
    negative_examples: list[str] = Field(default_factory=list)
    priority: int = 100
    pattern: str | None = None
    replacement: str | None = None


class CandidateReviewFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    file_version: str = "v0.2"
    clean_version: str
    candidates: list[CandidateReviewEntry] = Field(default_factory=list)


class CandidateReviewManager:
    """Manages explicit human approval before rules enter versioned registry."""

    @staticmethod
    def load_review_file(path: Path) -> CandidateReviewFile:
        raw = path.read_bytes()
        try:
            payload = yaml.safe_load(raw) if path.suffix in (".yaml", ".yml") else json.loads(raw)
            return CandidateReviewFile.model_validate(payload)
        except Exception as exc:
            raise RuleRegistryError(f"candidate_review_file_invalid:{path}") from exc

    @staticmethod
    def save_review_file(review_file: CandidateReviewFile, path: Path) -> None:
        data = review_file.model_dump(mode="json")
        if path.suffix in (".yaml", ".yml"):
            text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
            atomic_write_text(path, text)
        else:
            atomic_write_json(path, data)

    @classmethod
    def approve_candidate(
        cls,
        review_file: CandidateReviewFile,
        candidate_id: str,
        *,
        reviewed_by: str,
        notes: str = "",
        assigned_rule_id: str | None = None,
        assigned_priority: int | None = None,
        assigned_pattern: str | None = None,
    ) -> CandidateReviewFile:
        updated_candidates: list[CandidateReviewEntry] = []
        found = False
        now_str = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

        for item in review_file.candidates:
            if item.candidate_id == candidate_id:
                found = True
                updated = item.model_copy(
                    update={
                        "review_status": "approved",
                        "reviewed_by": reviewed_by,
                        "reviewed_at": now_str,
                        "review_notes": notes or item.review_notes,
                        "priority": assigned_priority if assigned_priority is not None else item.priority,
                        "pattern": assigned_pattern if assigned_pattern is not None else item.pattern,
                    }
                )
                updated_candidates.append(updated)
            else:
                updated_candidates.append(item)

        if not found:
            raise RuleRegistryError(f"candidate_id_not_found:{candidate_id}")

        return review_file.model_copy(update={"candidates": updated_candidates})

    @classmethod
    def convert_approved_to_rules(
        cls,
        review_file: CandidateReviewFile,
        *,
        id_prefix: str = "APPROVED_UI_",
    ) -> list[CleanRuleConfig]:
        """Convert explicitly approved candidates into production CleanRuleConfigs."""
        rules: list[CleanRuleConfig] = []
        for idx, item in enumerate(review_file.candidates, 1):
            if item.review_status != "approved":
                continue
            if item.recommended_action == CandidateRecommendedAction.REVIEW_ONLY:
                continue

            rule_id = f"{id_prefix}{idx:03d}"
            action = item.recommended_action.value
            pattern = item.pattern or item.pattern_or_example

            # Construct appropriate scope
            if item.applicable_source:
                scope: dict[str, Any] = {
                    "type": "SOURCE_SPECIFIC",
                    "sources": list(item.applicable_source),
                }
            elif item.applicable_document_type:
                scope = {
                    "type": "DOCUMENT_TYPE_SPECIFIC",
                    "document_types": list(item.applicable_document_type),
                }
            else:
                scope = {"type": "GLOBAL"}

            rule = CleanRuleConfig(
                id=rule_id,
                name=f"approved_{item.candidate_id}",
                description=f"Human-approved candidate {item.candidate_id}: {item.reason[:100]}",
                scope=scope,
                action=action,
                pattern=pattern,
                replacement=item.replacement,
                flags=[],
                priority=item.priority,
                enabled=True,
                safe=scope.get("type") == "GLOBAL",
                reason=f"Approved by {item.reviewed_by or 'human'} with confidence {item.confidence}",
                version=review_file.clean_version,
            )
            rules.append(rule)

        # Sort strictly by priority (lower first), then rule ID
        return sorted(rules, key=lambda r: (r.priority, r.id))

    @classmethod
    def publish_approved_rules(
        cls,
        base_config_path: Path,
        review_file: CandidateReviewFile,
        output_config_path: Path,
    ) -> tuple[CleanPipelineConfig, str]:
        """Write approved rules to a versioned config and compute new SHA256 checksum."""
        base_config, _ = load_clean_config(base_config_path)
        new_rules = cls.convert_approved_to_rules(review_file)

        existing_ids = {r.id for r in base_config.rules}
        combined_rules = list(base_config.rules)
        for rule in new_rules:
            if rule.id in existing_ids:
                raise RuleRegistryError(f"duplicate_approved_rule_id:{rule.id}")
            combined_rules.append(rule)

        # Re-sort all rules by priority then ID
        combined_rules.sort(key=lambda r: (r.priority, r.id))

        payload = base_config.model_dump(mode="json", exclude={"document_type_sidecar", "document_type_sidecar_checksum"})
        payload["rules"] = [r.model_dump(mode="json") for r in combined_rules]

        raw_yaml = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)
        atomic_write_text(output_config_path, raw_yaml)

        return load_clean_config(output_config_path)
