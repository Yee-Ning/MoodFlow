"""Offline Metadata Enrichment V1 for Music RAG chunks."""

from .config import (
    DEFAULT_ENRICHMENT_CONFIG_PATH,
    EnrichmentConfig,
    load_enrichment_config,
)
from .corpus import EnrichmentCorpusReader, latest_chunked_root
from .inheritance import EnrichmentContext, MetadataInheritanceResolver
from .merger import MetadataMerger
from .models import (
    ChunkEnrichment,
    ContentType,
    EnrichedChildChunk,
    EnrichedChunk,
    EnrichedParentChunk,
    EnrichmentStatus,
    EntityMention,
    EntityTypeHint,
    MentionSource,
    PrimarySubject,
    SemanticStatus,
    SemanticEnrichmentOutput,
    StructuralStatus,
)
from .pipeline import MetadataEnrichmentPipeline
from .rule_enricher import RuleEnricher
from .semantic_enricher import (
    ExistingLLMSemanticRuntime,
    SemanticEnricher,
    SemanticEnrichmentError,
    SemanticEnrichmentRuntime,
)
from .service import EnrichmentRunOutcome, MetadataEnrichmentService
from .temporal import TemporalExtractor

__all__ = [
    "DEFAULT_ENRICHMENT_CONFIG_PATH",
    "ChunkEnrichment",
    "ContentType",
    "EnrichedChildChunk",
    "EnrichedChunk",
    "EnrichedParentChunk",
    "EnrichmentConfig",
    "EnrichmentCorpusReader",
    "EnrichmentContext",
    "EnrichmentRunOutcome",
    "EnrichmentStatus",
    "EntityMention",
    "EntityTypeHint",
    "ExistingLLMSemanticRuntime",
    "MentionSource",
    "MetadataEnrichmentPipeline",
    "MetadataEnrichmentService",
    "MetadataInheritanceResolver",
    "MetadataMerger",
    "PrimarySubject",
    "RuleEnricher",
    "SemanticStatus",
    "SemanticEnricher",
    "SemanticEnrichmentError",
    "SemanticEnrichmentOutput",
    "SemanticEnrichmentRuntime",
    "StructuralStatus",
    "TemporalExtractor",
    "latest_chunked_root",
    "load_enrichment_config",
]
