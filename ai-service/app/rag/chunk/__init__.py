"""Structure-aware offline Chunk V1 for the Music RAG corpus."""

from .config import (
    DEFAULT_CHUNK_CONFIG_PATH,
    ChunkConfig,
    TokenCounterBackend,
    TokenCounterConfig,
    load_chunk_config,
)
from .corpus import ChunkCorpusReader, ChunkSourceDocument, latest_cleaned_root
from .engine import ChunkEngine, ChunkInputError
from .models import (
    ChildChunk,
    ChunkedDocument,
    ChunkStats,
    ChunkType,
    ChunkValidationReport,
    ParentChunk,
    TableChunkData,
    TableHeaderState,
    TableRowFragment,
)
from .service import ChunkPipelineService, ChunkRunOutcome, ChunkValidationError
from .token_counter import (
    CallableTokenCounter,
    HuggingFaceTokenCounter,
    TokenCounter,
    UnicodeHeuristicTokenCounter,
    build_token_counter,
)

__all__ = [
    "DEFAULT_CHUNK_CONFIG_PATH",
    "CallableTokenCounter",
    "ChildChunk",
    "ChunkConfig",
    "ChunkCorpusReader",
    "ChunkEngine",
    "ChunkInputError",
    "ChunkPipelineService",
    "ChunkRunOutcome",
    "ChunkSourceDocument",
    "ChunkStats",
    "ChunkType",
    "ChunkValidationError",
    "ChunkValidationReport",
    "ChunkedDocument",
    "HuggingFaceTokenCounter",
    "ParentChunk",
    "TableChunkData",
    "TableHeaderState",
    "TableRowFragment",
    "TokenCounter",
    "TokenCounterBackend",
    "TokenCounterConfig",
    "UnicodeHeuristicTokenCounter",
    "build_token_counter",
    "latest_cleaned_root",
    "load_chunk_config",
]
