"""Backend adapters for PDF Parse + Normalize V1.

Third-party parser objects never escape this module.  Every adapter returns the
same parser-independent result and optional immutable output artifacts.
"""

from __future__ import annotations

import mimetypes
import re
import tempfile
from abc import ABC, abstractmethod
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from app.rag.parse_normalize.base import ParserError
from app.rag.parse_normalize.models import (
    BlockType,
    DocumentBlock,
    DocumentImage,
    DocumentTable,
    OutputArtifact,
    ParseInput,
    ParserResult,
)
from app.rag.parse_normalize.parsers.markdown import MarkdownParser

from .models import PDFInspection


class PDFAdapterOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    parser_name: str = Field(min_length=1, max_length=100)
    parser_version: str = Field(min_length=1, max_length=50)
    result: ParserResult
    artifacts: list[OutputArtifact] = Field(default_factory=list)
    backend_metadata: dict[str, JsonValue] = Field(default_factory=dict)
    ocr_used: bool = False


class PDFParserAdapter(ABC):
    parser_name: str
    adapter_version = "1.0.0"

    @property
    def parser_version(self) -> str:
        return self.adapter_version

    @abstractmethod
    def parse(
        self, parse_input: ParseInput, inspection: PDFInspection
    ) -> PDFAdapterOutput:
        """Convert one complete PDF into the shared parser result."""

    @staticmethod
    def parse_page(parse_input: object, page_number: int) -> PDFAdapterOutput:
        """Reserved V1 extension point for a future mixed-backend page merge."""

        raise ParserError("page_level_fallback_not_implemented")

    @staticmethod
    def reparse_failed_pages(
        parse_input: object, page_numbers: list[int]
    ) -> PDFAdapterOutput:
        """Reserved V1 extension point for selective OCR or re-parsing."""

        raise ParserError("page_level_fallback_not_implemented")


class VLMPageParser:
    """Explicitly disabled placeholder; V1 never sends pages to a VLM."""

    enabled = False

    def parse_page(self, parse_input: object, page_number: int) -> PDFAdapterOutput:
        raise ParserError("vlm_page_parser_disabled")


class PyMuPDF4LLMAdapter(PDFParserAdapter):
    parser_name = "pymupdf4llm"

    def __init__(self, markdown_parser: MarkdownParser | None = None) -> None:
        self._markdown_parser = markdown_parser or MarkdownParser()

    @property
    def parser_version(self) -> str:
        try:
            backend_version = version("pymupdf4llm")
        except PackageNotFoundError:
            backend_version = "unavailable"
        return f"{backend_version}+a{self.adapter_version}"

    def parse(
        self, parse_input: ParseInput, inspection: PDFInspection
    ) -> PDFAdapterOutput:
        try:
            import pymupdf
            import pymupdf4llm

            with tempfile.TemporaryDirectory(prefix="music-rag-pdf-") as temp_dir:
                image_root = Path(temp_dir)
                with pymupdf.open(
                    stream=parse_input.raw_bytes, filetype="pdf"
                ) as document:
                    chunks = pymupdf4llm.to_markdown(
                        document,
                        filename=f"{parse_input.snapshot.source_id}.pdf",
                        page_chunks=True,
                        use_ocr=False,
                        force_ocr=False,
                        force_text=True,
                        write_images=True,
                        embed_images=False,
                        image_path=str(image_root),
                        image_format="png",
                        show_progress=False,
                    )
                if not isinstance(chunks, list):
                    raise ValueError("page_chunks result was not a list")
                artifacts = self._image_artifacts(image_root)
                result = self._result(parse_input, chunks, artifacts)
        except ParserError:
            raise
        except Exception as exc:
            raise ParserError("pymupdf4llm_parse_failed") from exc

        return PDFAdapterOutput(
            parser_name=self.parser_name,
            parser_version=self.parser_version,
            result=result,
            artifacts=artifacts,
            backend_metadata={
                "page_count": inspection.page_count,
                "profile": inspection.profile.value,
                "native_text_only": True,
                "extracted_image_count": len(artifacts),
            },
            ocr_used=False,
        )

    def _result(
        self,
        parse_input: ParseInput,
        chunks: list[object],
        artifacts: list[OutputArtifact],
    ) -> ParserResult:
        blocks: list[DocumentBlock] = []
        tables: list[DocumentTable] = []
        page_metadata: list[JsonValue] = []
        document_metadata: dict[str, JsonValue] = {}

        for page_index, raw_chunk in enumerate(chunks):
            if not isinstance(raw_chunk, dict):
                continue
            metadata = self._json_safe(raw_chunk.get("metadata", {}))
            boxes = raw_chunk.get("page_boxes", [])
            markdown = self._rewrite_image_paths(
                str(raw_chunk.get("text", "")), artifacts
            )
            page_input = parse_input.model_copy(
                update={"raw_bytes": markdown.encode("utf-8")}
            )
            parsed_page = self._markdown_parser.parse(page_input)
            page_number = self._page_number(metadata, page_index + 1)
            page_blocks = self._remap_blocks(
                parsed_page.blocks,
                boxes if isinstance(boxes, list) else [],
                page_number,
                parse_input.snapshot.source_id,
                len(blocks),
            )
            blocks.extend(page_blocks)
            tables.extend(self._tables(page_blocks, parse_input.snapshot.source_id))
            if not document_metadata and isinstance(metadata, dict):
                document_metadata = metadata
            page_metadata.append(
                {
                    "page_number": page_number,
                    "toc_items": self._json_safe(raw_chunk.get("toc_items", [])),
                    "page_boxes": self._json_safe(boxes),
                }
            )

        images = self._images(artifacts, blocks, parse_input.snapshot.source_id)
        return ParserResult(
            title=self._optional_text(document_metadata.get("title")),
            author=self._optional_text(document_metadata.get("author")),
            language="und",
            blocks=blocks,
            tables=tables,
            images=images,
            metadata={
                "pdf_metadata": document_metadata,
                "pages": page_metadata,
                "ocr_used": False,
            },
        )

    @classmethod
    def _remap_blocks(
        cls,
        page_blocks: list[DocumentBlock],
        boxes: list[object],
        page_number: int,
        source_id: str,
        start_index: int,
    ) -> list[DocumentBlock]:
        id_map = {
            block.block_id: f"{source_id}-b{start_index + offset:06d}"
            for offset, block in enumerate(page_blocks)
        }
        remapped: list[DocumentBlock] = []
        for offset, block in enumerate(page_blocks):
            metadata = dict(block.metadata)
            metadata["page_number"] = page_number
            bbox = cls._box_at(boxes, offset)
            if bbox is not None:
                metadata["bbox"] = bbox
            text = block.text
            if block.block_type is BlockType.HEADING:
                text = cls._plain_heading(text)
            remapped.append(
                block.model_copy(
                    update={
                        "block_id": id_map[block.block_id],
                        "text": text,
                        "order_index": start_index + offset,
                        "parent_block_id": id_map.get(block.parent_block_id),
                        "metadata": metadata,
                    }
                )
            )
        return remapped

    @staticmethod
    def _plain_heading(value: str) -> str:
        match = re.fullmatch(r"\*\*(.+)\*\*", value.strip())
        return match.group(1).strip() if match else value

    @staticmethod
    def _box_at(boxes: list[object], index: int) -> list[float] | None:
        if index >= len(boxes) or not isinstance(boxes[index], dict):
            return None
        bbox = boxes[index].get("bbox")
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            return None
        try:
            return [float(value) for value in bbox]
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _tables(
        blocks: list[DocumentBlock], source_id: str
    ) -> list[DocumentTable]:
        tables: list[DocumentTable] = []
        for block in blocks:
            if block.block_type is not BlockType.TABLE:
                continue
            rows = block.metadata.get("rows", [])
            page_number = block.metadata.get("page_number")
            bbox = block.metadata.get("bbox")
            tables.append(
                DocumentTable(
                    table_id=f"{source_id}-t{len(tables):06d}",
                    page_number=page_number if isinstance(page_number, int) else None,
                    bbox=tuple(bbox) if isinstance(bbox, list) and len(bbox) == 4 else None,
                    markdown=block.text,
                    rows=(
                        [[str(cell) for cell in row] for row in rows if isinstance(row, list)]
                        if isinstance(rows, list)
                        else []
                    ),
                    metadata={"source_block_id": block.block_id},
                )
            )
        return tables

    @staticmethod
    def _images(
        artifacts: list[OutputArtifact],
        blocks: list[DocumentBlock],
        source_id: str,
    ) -> list[DocumentImage]:
        page_by_name: dict[str, int] = {}
        for block in blocks:
            src = block.metadata.get("src")
            page_number = block.metadata.get("page_number")
            if isinstance(src, str) and isinstance(page_number, int):
                page_by_name[Path(src).name] = page_number
        return [
            DocumentImage(
                image_id=f"{source_id}-i{index:06d}",
                page_number=page_by_name.get(Path(artifact.relative_path).name),
                path=artifact.relative_path,
                content_type=artifact.content_type,
                metadata={"parser_generated": True},
            )
            for index, artifact in enumerate(artifacts)
        ]

    @staticmethod
    def _image_artifacts(image_root: Path) -> list[OutputArtifact]:
        artifacts: list[OutputArtifact] = []
        for path in sorted(item for item in image_root.iterdir() if item.is_file()):
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            if not content_type.startswith("image/"):
                continue
            artifacts.append(
                OutputArtifact(
                    relative_path=f"images/{path.name}",
                    content=path.read_bytes(),
                    content_type=content_type,
                )
            )
        return artifacts

    @staticmethod
    def _rewrite_image_paths(
        markdown: str, artifacts: list[OutputArtifact]
    ) -> str:
        rewritten = markdown
        for artifact in artifacts:
            name = Path(artifact.relative_path).name
            pattern = rf"(?P<prefix>!\[[^\]]*\]\()(?P<path>[^)]*{re.escape(name)})(?P<suffix>\))"
            rewritten = re.sub(
                pattern,
                lambda match: f"{match.group('prefix')}images/{name}{match.group('suffix')}",
                rewritten,
            )
        return rewritten

    @staticmethod
    def _page_number(metadata: JsonValue, fallback: int) -> int:
        if isinstance(metadata, dict):
            value = metadata.get("page_number")
            if isinstance(value, int) and value >= 1:
                return value
        return fallback

    @classmethod
    def _json_safe(cls, value: object) -> JsonValue:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, dict):
            return {str(key): cls._json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._json_safe(item) for item in value]
        return str(value)

    @staticmethod
    def _optional_text(value: JsonValue | None) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None
