"""Compatibility exports for the shared RAG source-context contract."""

from app.rag.source_context import ContextSegment, RelationSourceContext, source_context

__all__ = ["ContextSegment", "RelationSourceContext", "source_context"]
