"""PDF inspection, adapters, routing, and quality-gated cascade."""

from .adapters import (
    PDFAdapterOutput,
    PDFParserAdapter,
    PyMuPDF4LLMAdapter,
    VLMPageParser,
)
from .inspector import PDFInspectionError, PDFInspector, PDFInspectorConfig
from .models import (
    PDFBackend,
    PDFInspection,
    PDFPageInspection,
    PDFParserMode,
    PDFProfile,
)
from .router import PDFParserRouter

__all__ = [
    "PDFBackend",
    "PDFAdapterOutput",
    "PDFInspection",
    "PDFInspectionError",
    "PDFInspector",
    "PDFInspectorConfig",
    "PDFPageInspection",
    "PDFParserMode",
    "PDFParserAdapter",
    "PDFParserRouter",
    "PDFProfile",
    "PyMuPDF4LLMAdapter",
    "VLMPageParser",
]
