"""Reproducible stratified sampling for noise discovery and human review."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .config import SamplingConfig
from .corpus import MISSING_DIMENSION, NormalizedDocumentContext
from .io import atomic_write_json


class SampleManifestRow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    selection_rank: int = Field(ge=1)
    document_id: str
    source_id: str
    source: str
    source_type: str | None = None
    document_type: str | None = None
    length_bucket: str
    text_length: int = Field(ge=0)
    normalized_path: str
    stratum: dict[str, str]


class SamplingManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    manifest_version: str = "v0.1"
    algorithm: str = "stable_hash_stratified_round_robin_v1"
    seed: int
    requested_size: int = Field(ge=1)
    actual_size: int = Field(ge=0)
    corpus_document_count: int = Field(ge=0)
    dimensions_requested: list[str]
    dimensions_used: list[str]
    degraded_dimensions: dict[str, str]
    documents: list[SampleManifestRow]


class StratifiedSampler:
    """Sample across available source, document type, and length strata."""

    def __init__(self, config: SamplingConfig) -> None:
        self.config = config

    def sample(
        self, documents: list[NormalizedDocumentContext]
    ) -> SamplingManifest:
        dimensions = ["source", "document_type", "length_bucket"]
        degraded: dict[str, str] = {}
        use_document_type = any(item.document_type for item in documents)
        use_source_type = False
        if not use_document_type:
            degraded["document_type"] = (
                "missing_for_all_documents; source_type_used_as_an_independent_"
                "fallback_dimension_when_available"
            )
            use_source_type = any(item.source_type for item in documents)
        dimensions_used = ["source"]
        if use_document_type:
            dimensions_used.append("document_type")
        elif use_source_type:
            dimensions_used.append("source_type")
        dimensions_used.append("length_bucket")

        strata: defaultdict[tuple[str, ...], list[NormalizedDocumentContext]] = (
            defaultdict(list)
        )
        for item in documents:
            bucket = self.length_bucket(len(item.document.markdown))
            values = [item.source]
            if use_document_type:
                values.append(item.document_type or MISSING_DIMENSION)
            elif use_source_type:
                values.append(item.source_type or MISSING_DIMENSION)
            values.append(bucket)
            strata[tuple(values)].append(item)

        for values in strata.values():
            values.sort(
                key=lambda item: self._stable_score(
                    f"document|{item.document.document_id}"
                )
            )
        ordered_strata = sorted(
            strata,
            key=lambda key: self._stable_score("stratum|" + "|".join(key)),
        )
        selected: list[tuple[tuple[str, ...], NormalizedDocumentContext]] = []
        target = min(self.config.sample_size, len(documents))
        depth = 0
        while len(selected) < target:
            selected_this_round = False
            for key in ordered_strata:
                values = strata[key]
                if depth >= len(values):
                    continue
                selected.append((key, values[depth]))
                selected_this_round = True
                if len(selected) == target:
                    break
            if not selected_this_round:
                break
            depth += 1

        rows: list[SampleManifestRow] = []
        for rank, (key, item) in enumerate(selected, 1):
            rows.append(
                SampleManifestRow(
                    selection_rank=rank,
                    document_id=item.document.document_id,
                    source_id=item.document.source_id,
                    source=item.source,
                    source_type=item.source_type,
                    document_type=item.document_type,
                    length_bucket=self.length_bucket(len(item.document.markdown)),
                    text_length=len(item.document.markdown),
                    normalized_path=item.relative_normalized_path,
                    stratum=dict(zip(dimensions_used, key, strict=True)),
                )
            )
        return SamplingManifest(
            seed=self.config.seed,
            requested_size=self.config.sample_size,
            actual_size=len(rows),
            corpus_document_count=len(documents),
            dimensions_requested=dimensions,
            dimensions_used=dimensions_used,
            degraded_dimensions=degraded,
            documents=rows,
        )

    def length_bucket(self, length: int) -> str:
        for bucket in self.config.length_buckets:
            if bucket.max_chars is None or length <= bucket.max_chars:
                return bucket.name
        raise AssertionError("validated length bucket config must be exhaustive")

    def _stable_score(self, value: str) -> str:
        material = f"{self.config.seed}|{value}".encode("utf-8")
        return hashlib.sha256(material).hexdigest()

    def sample_by_source_type_and_site(
        self,
        documents: list[NormalizedDocumentContext],
        samples_per_stratum: int | None = None,
    ) -> SamplingManifest:
        """Stratified sampling by source_type (HTML/PDF/JSON) x site (source)."""
        limit_per_stratum = (
            samples_per_stratum
            if samples_per_stratum is not None
            else (self.config.samples_per_stratum or 2)
        )
        dimensions_used = ["source_type", "source"]

        strata: defaultdict[tuple[str, str], list[NormalizedDocumentContext]] = (
            defaultdict(list)
        )
        for item in documents:
            source_type = item.source_type or MISSING_DIMENSION
            source = item.source or MISSING_DIMENSION
            strata[(source_type, source)].append(item)

        for values in strata.values():
            values.sort(
                key=lambda item: self._stable_score(
                    f"document|{item.document.document_id}"
                )
            )

        selected: list[tuple[tuple[str, str], NormalizedDocumentContext]] = []
        ordered_strata = sorted(
            strata,
            key=lambda key: self._stable_score("stratum|" + "|".join(key)),
        )

        for key in ordered_strata:
            items_in_stratum = strata[key]
            for item in items_in_stratum[:limit_per_stratum]:
                selected.append((key, item))

        rows: list[SampleManifestRow] = []
        for rank, (key, item) in enumerate(selected, 1):
            rows.append(
                SampleManifestRow(
                    selection_rank=rank,
                    document_id=item.document.document_id,
                    source_id=item.document.source_id,
                    source=item.source,
                    source_type=item.source_type,
                    document_type=item.document_type,
                    length_bucket=self.length_bucket(len(item.document.markdown)),
                    text_length=len(item.document.markdown),
                    normalized_path=item.relative_normalized_path,
                    stratum=dict(zip(dimensions_used, key, strict=True)),
                )
            )

        return SamplingManifest(
            seed=self.config.seed,
            requested_size=len(ordered_strata) * limit_per_stratum,
            actual_size=len(rows),
            corpus_document_count=len(documents),
            dimensions_requested=["source_type", "source"],
            dimensions_used=dimensions_used,
            degraded_dimensions={},
            documents=rows,
        )

    @staticmethod
    def detect_new_strata(
        existing_manifest: SamplingManifest,
        documents: list[NormalizedDocumentContext],
    ) -> list[dict[str, str]]:
        """Identify new (source_type, source) strata not present in existing sample."""
        existing_strata = {
            (
                row.stratum.get("source_type", row.source_type or MISSING_DIMENSION),
                row.stratum.get("source", row.source or MISSING_DIMENSION),
            )
            for row in existing_manifest.documents
        }
        current_strata = {
            (
                item.source_type or MISSING_DIMENSION,
                item.source or MISSING_DIMENSION,
            )
            for item in documents
        }
        new_keys = sorted(current_strata - existing_strata)
        return [
            {"source_type": st, "source": s}
            for st, s in new_keys
        ]

    def resample_if_new_strata(
        self,
        existing_manifest: SamplingManifest,
        documents: list[NormalizedDocumentContext],
        samples_per_stratum: int | None = None,
    ) -> SamplingManifest | None:
        """Trigger re-sampling when a new source or new source type is detected."""
        new_strata = self.detect_new_strata(existing_manifest, documents)
        if not new_strata:
            return None
        return self.sample_by_source_type_and_site(
            documents, samples_per_stratum=samples_per_stratum
        )


def write_sampling_manifest(manifest: SamplingManifest, output_path: Path) -> None:
    atomic_write_json(output_path, manifest.model_dump(mode="json"))


def load_sampling_manifest(path: Path) -> SamplingManifest:
    return SamplingManifest.model_validate_json(path.read_text(encoding="utf-8"))
