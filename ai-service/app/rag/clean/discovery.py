"""LLM-assisted candidate noise discovery over a reviewed sample only."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from .config import NoiseDiscoveryConfig
from .corpus import NormalizedDocumentContext
from .errors import NoiseDiscoveryError
from .io import atomic_write_json
from .sampling import SamplingManifest


NOISE_DISCOVERY_PROMPT = """You discover candidate noise patterns in sampled music-domain documents.

This is discovery only. Never clean the corpus, never rewrite a document, and never
claim that a candidate is an approved production rule. A human must review every
candidate before it can enter the Rule Registry.

Safety requirements:
1. High frequency does not mean noise.
2. Do not remove genuine domain facts.
3. Dates, numbers, ranks, track numbers, BPM, sales, song titles, album titles,
   artist names, company names, credits, and chart results are potentially important
   by default.
4. Suggest deletion or replacement only for content clearly attributable to page
   templates, navigation, advertisements, promotions, sharing controls,
   recommendations, copyright notices, UI copy, or residual HTML.
5. Mark uncertain=true when evidence is ambiguous. Do not recommend aggressive
   deletion for uncertain content.
6. Every candidate must state a pattern or exact example, noise_type,
   recommended_action, applicable_source, applicable_document_type, reason,
   confidence, false_positive_risk, and evidence_document_ids.
7. Rules must generalize by source or document type. Never create an entity-specific
   rule for a particular artist, song, album, award, or chart entry.
8. An absent document_type is unknown; do not infer one from source_type.
"""


class CandidateNoisePattern(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    pattern_or_example: str = Field(min_length=1, max_length=2_000)
    noise_type: str = Field(min_length=1, max_length=100)
    recommended_action: str = Field(min_length=1, max_length=100)
    applicable_source: list[str]
    applicable_document_type: list[str]
    reason: str = Field(min_length=1, max_length=2_000)
    confidence: float = Field(ge=0, le=1)
    false_positive_risk: str = Field(min_length=1, max_length=1_000)
    uncertain: bool
    evidence_document_ids: list[str] = Field(min_length=1)


class NoiseDiscoveryBatchResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidates: list[CandidateNoisePattern]
    batch_notes: list[str]


class NoiseDiscoveryRuntime(Protocol):
    async def analyze(
        self,
        documents: list[dict[str, object]],
        *,
        max_candidates: int,
    ) -> NoiseDiscoveryBatchResult: ...


class ExistingLLMNoiseDiscoveryRuntime:
    """Reuse MoodFlow's configured structured-output LLM client."""

    async def analyze(
        self,
        documents: list[dict[str, object]],
        *,
        max_candidates: int,
    ) -> NoiseDiscoveryBatchResult:
        from app.llm.client import create_structured_response

        response = await create_structured_response(
            [
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "maximum_candidates": max_candidates,
                            "sample_documents": documents,
                        },
                        ensure_ascii=False,
                    ),
                }
            ],
            NOISE_DISCOVERY_PROMPT,
            NoiseDiscoveryBatchResult.model_json_schema(),
            schema_name="clean_noise_candidates_v0_1",
        )
        if response.status != "completed" or not response.output_text:
            raise NoiseDiscoveryError("noise_discovery_llm_incomplete")
        try:
            return NoiseDiscoveryBatchResult.model_validate_json(
                response.output_text
            )
        except ValueError as exc:
            raise NoiseDiscoveryError("noise_discovery_llm_invalid_output") from exc


class NoiseDiscoveryService:
    def __init__(
        self,
        config: NoiseDiscoveryConfig,
        *,
        clean_version: str,
        max_text_chars_per_document: int,
        runtime: NoiseDiscoveryRuntime,
    ) -> None:
        self.config = config
        self.clean_version = clean_version
        self.max_text_chars_per_document = max_text_chars_per_document
        self.runtime = runtime

    async def discover(
        self,
        documents: Sequence[NormalizedDocumentContext],
        sampling_manifest: SamplingManifest,
    ) -> dict[str, object]:
        by_id = {item.document.document_id: item for item in documents}
        selected: list[NormalizedDocumentContext] = []
        for row in sampling_manifest.documents:
            item = by_id.get(row.document_id)
            if item is None:
                raise NoiseDiscoveryError(
                    f"sampling_document_not_found:{row.document_id}"
                )
            selected.append(item)

        analyzed_ids = {item.document.document_id for item in selected}
        candidate_rows: list[dict[str, object]] = []
        batch_notes: list[dict[str, object]] = []
        for batch_index, batch in enumerate(
            self._batches(selected, self.config.batch_size), 1
        ):
            payloads = [self._document_payload(item) for item in batch]
            result = await self.runtime.analyze(
                payloads,
                max_candidates=self.config.max_candidates_per_batch,
            )
            batch_ids = {item.document.document_id for item in batch}
            for candidate in result.candidates:
                evidence = set(candidate.evidence_document_ids)
                if not evidence or not evidence.issubset(batch_ids):
                    raise NoiseDiscoveryError(
                        f"candidate_evidence_outside_batch:{batch_index}"
                    )
                payload = candidate.model_dump(mode="json")
                candidate_id = "candidate-" + hashlib.sha256(
                    json.dumps(
                        payload,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()[:16]
                candidate_rows.append(
                    {
                        "candidate_id": candidate_id,
                        "review_status": "unreviewed",
                        "analysis_batch": batch_index,
                        **payload,
                    }
                )
            batch_notes.append(
                {
                    "analysis_batch": batch_index,
                    "document_ids": sorted(batch_ids),
                    "notes": result.batch_notes,
                }
            )
        manifest_hash = hashlib.sha256(
            sampling_manifest.model_dump_json().encode("utf-8")
        ).hexdigest()
        return {
            "report_version": "v0.1",
            "clean_version": self.clean_version,
            "prompt_version": self.config.prompt_version,
            "sampling_manifest_sha256": f"sha256:{manifest_hash}",
            "sample_document_count": len(analyzed_ids),
            "analyzed_document_ids": sorted(analyzed_ids),
            "candidate_count": len(candidate_rows),
            "candidate_rules": candidate_rows,
            "batch_notes": batch_notes,
            "registry_modified": False,
            "human_review_required": True,
            "workflow_status": "candidate_discovery_only",
        }

    def _document_payload(
        self, item: NormalizedDocumentContext
    ) -> dict[str, object]:
        text = item.document.markdown
        truncated = len(text) > self.max_text_chars_per_document
        if truncated:
            text = text[: self.max_text_chars_per_document] + "\n[TRUNCATED]\n"
        return {
            "document_id": item.document.document_id,
            "source_id": item.document.source_id,
            "source": item.source,
            "source_type": item.source_type,
            "document_type": item.document_type,
            "title": item.document.title,
            "text": text,
            "text_truncated": truncated,
        }

    @staticmethod
    def _batches(
        values: Sequence[NormalizedDocumentContext], size: int
    ) -> list[list[NormalizedDocumentContext]]:
        return [list(values[index : index + size]) for index in range(0, len(values), size)]


def write_candidate_rules_report(report: dict[str, object], path: Path) -> None:
    atomic_write_json(path, report)
