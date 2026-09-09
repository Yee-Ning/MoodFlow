"""Initial backend selection for the PDF cascade."""

from .models import PDFBackend, PDFParserMode, PDFProfile


class PDFParserRouter:
    def initial_parser(
        self, profile: PDFProfile, mode: PDFParserMode
    ) -> PDFBackend:
        if mode is PDFParserMode.PYMUPDF:
            return PDFBackend.PYMUPDF4LLM
        if mode is PDFParserMode.MINERU:
            return PDFBackend.MINERU
        if mode is PDFParserMode.DOCLING:
            return PDFBackend.DOCLING
        if profile in {PDFProfile.COMPLEX_LAYOUT, PDFProfile.SCANNED}:
            return PDFBackend.MINERU
        return PDFBackend.PYMUPDF4LLM
