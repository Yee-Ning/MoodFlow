"""PDF inspection, adapters, routing, and quality-gated cascade."""

from .adapters import (
    DoclingAdapter,
    DoclingRuntime,
    DoclingRuntimeOutput,
    LocalDoclingRuntime,
    MinerUAdapter,
    MinerUAdapterConfig,
    MinerURunner,
    PDFAdapterOutput,
    PDFParserAdapter,
    PyMuPDF4LLMAdapter,
    SubprocessMinerURunner,
    VLMPageParser,
)
from .cascade import PdfCascadeParser
from .cloud import (
    MinerUCloudConfig,
    MinerUCloudRunner,
    PublicDnsHttpsDownloader,
    PublicDnsResolver,
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
from .quality import (
    PDFParseQualityConfig,
    PDFParseQualityEvaluator,
    PDFQualityAssessment,
)

__all__ = [
    "DoclingAdapter",
    "DoclingRuntime",
    "DoclingRuntimeOutput",
    "LocalDoclingRuntime",
    "MinerUAdapter",
    "MinerUAdapterConfig",
    "MinerUCloudConfig",
    "MinerUCloudRunner",
    "MinerURunner",
    "PDFBackend",
    "PDFAdapterOutput",
    "PDFInspection",
    "PDFInspectionError",
    "PDFInspector",
    "PDFInspectorConfig",
    "PDFPageInspection",
    "PDFParseQualityConfig",
    "PDFParseQualityEvaluator",
    "PDFParserMode",
    "PDFParserAdapter",
    "PDFParserRouter",
    "PDFProfile",
    "PDFQualityAssessment",
    "PdfCascadeParser",
    "PyMuPDF4LLMAdapter",
    "PublicDnsHttpsDownloader",
    "PublicDnsResolver",
    "SubprocessMinerURunner",
    "VLMPageParser",
]
