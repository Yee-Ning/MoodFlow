"""Pure orchestration for Structure-aware Chunk V1."""

from __future__ import annotations

import hashlib

from pydantic import JsonValue

from app.rag.clean.models import CleanedDocument
from app.rag.parse_normalize.models import NormalizedDocument

from .config import ChunkConfig
from .context import retrieval_content
from .ids import stable_chunk_id
from .markdown_fallback import MarkdownFallbackSectioner
from .models import (
    ChildChunk,
    ChunkedDocument,
    ParentChunk,
    TableHeaderState,
)
from .narrative import NarrativeChunker
from .projection import CleanBlockProjector
from .routing import ChunkRouter
from .stats import build_chunk_stats
from .table import TableChunker
from .token_counter import TokenCounter, build_token_counter
from .validator import ChunkValidator


class ChunkInputError(ValueError):
    pass


class ChunkEngine:
    def __init__(
        self,
        config: ChunkConfig | None = None,
        *,
        token_counter: TokenCounter | None = None,
    ) -> None:
        self.config = config or ChunkConfig()
        self.counter = token_counter or build_token_counter(self.config.token_counter)
        self.projector = CleanBlockProjector()
        self.router = ChunkRouter()
        self.markdown_fallback = MarkdownFallbackSectioner()
        self.narrative_chunker = NarrativeChunker(self.config, self.counter)
        self.table_chunker = TableChunker(self.config, self.counter)
        self.validator = ChunkValidator(self.config)

    def chunk(
        self, cleaned: CleanedDocument, normalized: NormalizedDocument
    ) -> ChunkedDocument:
        self._validate_input_pair(cleaned, normalized)
        projected = self.projector.project(normalized, cleaned.cleaned_text)
        routes = self.router.route(normalized, projected)
        narrative_runs = (
            routes.narrative_runs
            if normalized.blocks
            else self.markdown_fallback.sections(cleaned.cleaned_text)
        )
        base_metadata = self._base_metadata(cleaned, normalized)
        drafts = []
        emitted_table_ids: set[str] = set()
        for run in narrative_runs:
            drafts.extend(
                self.narrative_chunker.chunk(
                    run,
                    document_title=cleaned.title,
                    base_metadata=base_metadata,
                )
            )

        table_by_id = {table.table_id: table for table in normalized.tables}
        block_by_id = {block.block_id: block for block in normalized.blocks}
        for routed in routes.tables:
            table = table_by_id[routed.table_id]
            source_block = (
                block_by_id.get(routed.source_block_id)
                if routed.source_block_id is not None
                else None
            )
            table_drafts = self.table_chunker.chunk(
                routed,
                table,
                source_block=source_block,
                document_title=cleaned.title,
                base_metadata=base_metadata,
            )
            drafts.extend(table_drafts)
            if table_drafts:
                emitted_table_ids.add(table.table_id)
        drafts.sort(key=lambda item: item.order_key)

        parents: list[ParentChunk] = []
        children: list[ChildChunk] = []
        for parent_index, draft in enumerate(drafts):
            parent_chunk_id = stable_chunk_id(
                level="parent",
                doc_id=cleaned.document_id,
                section_path=draft.section_path,
                parent_index=parent_index,
                child_index=None,
                content=draft.content,
            )
            parent = ParentChunk(
                doc_id=cleaned.document_id,
                chunk_id=parent_chunk_id,
                chunk_index=parent_index,
                chunk_type=draft.chunk_type,
                section_path=list(draft.section_path),
                breadcrumb=draft.breadcrumb,
                content=draft.content,
                token_count=self.counter.count(draft.content),
                metadata=draft.metadata,
                table_data=draft.table_data,
            )
            parents.append(parent)
            for local_child_index, child_draft in enumerate(draft.children):
                child_chunk_id = stable_chunk_id(
                    level="child",
                    doc_id=cleaned.document_id,
                    section_path=draft.section_path,
                    parent_index=parent_index,
                    child_index=local_child_index,
                    content=child_draft.content,
                )
                schema = None
                if child_draft.table_data is not None and (
                    child_draft.table_data.header_state is TableHeaderState.NONE
                    or child_draft.metadata.get("table_header_context_mode")
                    == "payload_and_schema"
                ):
                    source_table = table_by_id[child_draft.table_data.table_id]
                    column_count = max(
                        (len(row) for row in source_table.rows), default=0
                    )
                    schema = TableChunker._schema(column_count)
                metadata = {
                    **child_draft.metadata,
                    "parent_chunk_index": parent_index,
                    "child_index_in_parent": local_child_index,
                }
                children.append(
                    ChildChunk(
                        doc_id=cleaned.document_id,
                        chunk_id=child_chunk_id,
                        parent_id=parent_chunk_id,
                        chunk_index=len(children),
                        chunk_type=draft.chunk_type,
                        section_path=list(draft.section_path),
                        breadcrumb=draft.breadcrumb,
                        content=child_draft.content,
                        token_count=self.counter.count(child_draft.content),
                        metadata=metadata,
                        retrieval_content=retrieval_content(
                            draft.breadcrumb,
                            child_draft.content,
                            schema=schema,
                        ),
                        table_data=child_draft.table_data,
                    )
                )

        children = [
            child.model_copy(
                update={
                    "previous_chunk_id": (
                        children[index - 1].chunk_id if index else None
                    ),
                    "next_chunk_id": (
                        children[index + 1].chunk_id
                        if index + 1 < len(children)
                        else None
                    ),
                }
            )
            for index, child in enumerate(children)
        ]
        source_tables = [
            table
            for table in normalized.tables
            if table.table_id in emitted_table_ids and table.rows
        ]
        validation = self.validator.validate(
            cleaned, parents, children, source_tables, block_by_id
        )
        stats = build_chunk_stats(parents, children, source_tables, validation)
        return ChunkedDocument(
            document_id=cleaned.document_id,
            source_id=cleaned.source_id,
            chunk_version=self.config.chunk_version,
            config_fingerprint=self.config.fingerprint(),
            token_counter=self.counter.name,
            parents=parents,
            children=children,
            validation=validation,
            stats=stats,
        )

    @staticmethod
    def _validate_input_pair(
        cleaned: CleanedDocument, normalized: NormalizedDocument
    ) -> None:
        if cleaned.document_id != normalized.document_id:
            raise ChunkInputError("clean_normalized_document_id_mismatch")
        if cleaned.source_id != normalized.source_id:
            raise ChunkInputError("clean_normalized_source_id_mismatch")
        normalized_hash = (
            "sha256:" + hashlib.sha256(normalized.markdown.encode("utf-8")).hexdigest()
        )
        if cleaned.normalized_content_hash != normalized_hash:
            raise ChunkInputError("clean_normalized_content_hash_mismatch")

    @staticmethod
    def _base_metadata(
        cleaned: CleanedDocument, normalized: NormalizedDocument
    ) -> dict[str, JsonValue]:
        return {
            "document_metadata": cleaned.metadata,
            "source_id": cleaned.source_id,
            "source": cleaned.source,
            "source_type": cleaned.source_type,
            "document_type": cleaned.document_type,
            "title": cleaned.title,
            "author": cleaned.author,
            "published_at": (
                cleaned.published_at.isoformat()
                if cleaned.published_at is not None
                else None
            ),
            "language": cleaned.language,
            "original_file_type": cleaned.original_file_type,
            "corpus_version": cleaned.corpus_version,
            "clean_version": cleaned.clean_version,
            "rule_registry_checksum": cleaned.rule_registry_checksum,
            "normalized_path": cleaned.normalized_path,
            "normalized_content_hash": cleaned.normalized_content_hash,
            "parser_name": normalized.parser_name,
            "parser_version": normalized.parser_version,
            "normalization_version": normalized.normalization_version,
        }
