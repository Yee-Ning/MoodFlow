"""Stage 4: Cross-Document Template Deduplication using SimHash and MinHash."""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from pydantic import BaseModel, ConfigDict, Field

from .config import TemplateDeduplicationConfig
from .models import StageMetrics
from .structural import StructuralCleaner


def _simhash(text: str, hash_bits: int = 64) -> int:
    """Compute 64-bit SimHash for text using character n-grams."""
    normalized = re.sub(r"\s+", " ", text).strip().lower()
    if not normalized:
        return 0

    tokens = [normalized[i : i + 3] for i in range(max(1, len(normalized) - 2))]
    if not tokens:
        tokens = [normalized]

    v = [0] * hash_bits
    for token in tokens:
        digest = hashlib.md5(token.encode("utf-8")).digest()
        token_hash = int.from_bytes(digest[:8], byteorder="big")
        for i in range(hash_bits):
            bit = (token_hash >> i) & 1
            v[i] += 1 if bit else -1

    fingerprint = 0
    for i in range(hash_bits):
        if v[i] > 0:
            fingerprint |= 1 << i
    return fingerprint


def _hamming_distance(h1: int, h2: int) -> int:
    x = (h1 ^ h2) & ((1 << 64) - 1)
    return bin(x).count("1")


def _simhash_similarity(h1: int, h2: int, hash_bits: int = 64) -> float:
    dist = _hamming_distance(h1, h2)
    return 1.0 - (dist / hash_bits)


class TemplateBlockCluster(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    representative_text: str
    simhash: int
    matched_document_ids: list[str]
    frequency: int = Field(ge=0)


class TemplateDeduplicationOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cleaned_text: str
    before_chars: int = Field(ge=0)
    after_chars: int = Field(ge=0)
    removed_chars: int = Field(ge=0)
    removed_blocks: int = Field(ge=0)
    template_clusters_hit: list[str] = Field(default_factory=list)
    stage_metrics: StageMetrics


class TemplateDeduplicator:
    """Detects and removes recurring boilerplate blocks (copyright, related links, notices) across documents within the same site."""

    def __init__(self, config: TemplateDeduplicationConfig | None = None) -> None:
        self.config = config or TemplateDeduplicationConfig()
        # site -> list of template clusters
        self._site_templates: dict[str, list[TemplateBlockCluster]] = defaultdict(list)

    def train_site_templates(
        self,
        documents: list[tuple[str, str, str]],  # (doc_id, site/source, text)
    ) -> dict[str, list[TemplateBlockCluster]]:
        """Index repeated template blocks across documents sharing the same site."""
        if not self.config.enabled:
            return {}

        site_doc_blocks: dict[str, list[tuple[str, str, int]]] = defaultdict(list)
        for doc_id, site, text in documents:
            blocks = re.split(r"\n\s*\n", text)
            for block in blocks:
                trimmed = block.strip()
                if (
                    len(trimmed) < self.config.min_block_chars
                    or len(trimmed) > self.config.max_block_chars
                ):
                    continue
                # Do not treat tables or lists as boilerplate templates
                if StructuralCleaner.is_table(trimmed) or StructuralCleaner.is_list(trimmed):
                    continue
                h = _simhash(trimmed)
                site_doc_blocks[site].append((doc_id, trimmed, h))

        trained: dict[str, list[TemplateBlockCluster]] = defaultdict(list)

        for site, block_entries in site_doc_blocks.items():
            clusters: list[dict[str, object]] = []

            for doc_id, text, h in block_entries:
                matched_cluster = None
                for cluster in clusters:
                    sim = _simhash_similarity(h, int(cluster["simhash"]))
                    if sim >= self.config.similarity_threshold:
                        matched_cluster = cluster
                        break

                if matched_cluster is not None:
                    matched_cluster["doc_ids"].add(doc_id)
                else:
                    clusters.append(
                        {
                            "representative_text": text,
                            "simhash": h,
                            "doc_ids": {doc_id},
                        }
                    )

            # Filter clusters that meet the minimum document frequency threshold
            for cluster in clusters:
                doc_ids = cluster["doc_ids"]
                if len(doc_ids) >= self.config.min_doc_frequency:
                    trained[site].append(
                        TemplateBlockCluster(
                            representative_text=str(cluster["representative_text"]),
                            simhash=int(cluster["simhash"]),
                            matched_document_ids=sorted(doc_ids),
                            frequency=len(doc_ids),
                        )
                    )

        self._site_templates = trained
        return trained

    def deduplicate(
        self,
        text: str,
        *,
        doc_id: str,
        site: str,
    ) -> TemplateDeduplicationOutcome:
        before_chars = len(text)
        if not self.config.enabled or not text or site not in self._site_templates:
            return TemplateDeduplicationOutcome(
                cleaned_text=text,
                before_chars=before_chars,
                after_chars=before_chars,
                removed_chars=0,
                removed_blocks=0,
                template_clusters_hit=[],
                stage_metrics=StageMetrics(
                    stage_index=4,
                    stage_name="template_deduplication",
                    before_chars=before_chars,
                    after_chars=before_chars,
                    removed_chars=0,
                    removed_blocks=0,
                    details={"matched_template_blocks": 0},
                ),
            )

        templates = self._site_templates[site]
        blocks = re.split(r"\n\s*\n", text)
        retained_blocks: list[str] = []
        removed_blocks_count = 0
        hits: list[str] = []

        for block in blocks:
            trimmed = block.strip()
            if (
                len(trimmed) < self.config.min_block_chars
                or len(trimmed) > self.config.max_block_chars
                or StructuralCleaner.is_table(trimmed)
                or StructuralCleaner.is_list(trimmed)
            ):
                retained_blocks.append(block)
                continue

            h = _simhash(trimmed)
            is_template = False
            for t in templates:
                sim = _simhash_similarity(h, t.simhash)
                if sim >= self.config.similarity_threshold:
                    is_template = True
                    hits.append(t.representative_text[:60])
                    break

            if is_template:
                removed_blocks_count += 1
            else:
                retained_blocks.append(block)

        if removed_blocks_count == 0:
            result_text = text
        else:
            result_text = "\n\n".join(retained_blocks)
            if text.endswith("\n") and not result_text.endswith("\n"):
                result_text += "\n"

        after_chars = len(result_text)
        removed_chars = max(0, before_chars - after_chars)

        metrics = StageMetrics(
            stage_index=4,
            stage_name="template_deduplication",
            before_chars=before_chars,
            after_chars=after_chars,
            removed_chars=removed_chars,
            removed_blocks=removed_blocks_count,
            details={
                "matched_template_blocks": removed_blocks_count,
                "site": site,
            },
        )

        return TemplateDeduplicationOutcome(
            cleaned_text=result_text,
            before_chars=before_chars,
            after_chars=after_chars,
            removed_chars=removed_chars,
            removed_blocks=removed_blocks_count,
            template_clusters_hit=hits,
            stage_metrics=metrics,
        )
