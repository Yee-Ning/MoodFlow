"""Backend adapters for PDF Parse + Normalize V1.

Third-party parser objects never escape this module.  Every adapter returns the
same parser-independent result and optional immutable output artifacts.
"""

from __future__ import annotations

import mimetypes
import json
import re
import subprocess
import tempfile
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from importlib import import_module as standard_import_module
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Protocol

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


class MinerURunner(Protocol):
    def run(
        self, parse_input: ParseInput, output_directory: Path, allow_ocr: bool
    ) -> None: ...


MinerUCommandBuilder = Callable[[Path, Path, bool], list[str]]


@dataclass(frozen=True, slots=True)
class SubprocessMinerURunner:
    version_label = "lazy-cli"
    executable: str = "mineru"
    timeout_seconds: float = 900.0
    backend: str = "pipeline"
    command_builder: MinerUCommandBuilder | None = None

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("MinerU timeout must be positive")

    def run(
        self, parse_input: ParseInput, output_directory: Path, allow_ocr: bool
    ) -> None:
        output_directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="music-rag-mineru-input-") as temp_dir:
            input_path = Path(temp_dir) / f"{parse_input.snapshot.source_id}.pdf"
            input_path.write_bytes(parse_input.raw_bytes)
            command = (
                self.command_builder(input_path, output_directory, allow_ocr)
                if self.command_builder is not None
                else [
                    self.executable,
                    "-p",
                    str(input_path),
                    "-o",
                    str(output_directory),
                    "-b",
                    self.backend,
                ]
            )
            try:
                completed = subprocess.run(
                    command,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                )
            except FileNotFoundError as exc:
                raise ParserError("mineru_backend_unavailable") from exc
            except subprocess.TimeoutExpired as exc:
                raise ParserError("mineru_backend_timeout") from exc
            if completed.returncode != 0:
                raise ParserError("mineru_backend_failed")


@dataclass(frozen=True, slots=True)
class MinerUAdapterConfig:
    max_structured_metadata_chars: int = 1_000_000

    def __post_init__(self) -> None:
        if self.max_structured_metadata_chars <= 0:
            raise ValueError("MinerU metadata limit must be positive")


class MinerUAdapter(PDFParserAdapter):
    parser_name = "mineru"

    def __init__(
        self,
        cache_root: Path,
        *,
        runner: MinerURunner | None = None,
        config: MinerUAdapterConfig | None = None,
    ) -> None:
        self.cache_root = cache_root.resolve()
        self.runner = runner or SubprocessMinerURunner()
        self.config = config or MinerUAdapterConfig()

    @property
    def parser_version(self) -> str:
        backend = getattr(self.runner, "version_label", "custom-runner")
        return f"{backend}+a{self.adapter_version}"

    def parse(
        self, parse_input: ParseInput, inspection: PDFInspection
    ) -> PDFAdapterOutput:
        cache_directory = self._cache_directory(parse_input)
        allow_ocr = inspection.suspected_scan
        try:
            if not self._has_output(cache_directory):
                self.runner.run(parse_input, cache_directory, allow_ocr)
            content_path = self._first_named(
                cache_directory,
                ("content_list_v2.json", "content_list.json"),
            ) or self._first_suffix(cache_directory, "_content_list.json")
            middle_path = self._first_named(
                cache_directory, ("middle.json", "layout.json")
            ) or self._first_suffix(cache_directory, "_middle.json")
            markdown_path = self._first_named(
                cache_directory, ("full.md",)
            ) or self._first_suffix(cache_directory, ".md")
            content_list = self._load_json(content_path) if content_path else []
            middle = self._load_json(middle_path) if middle_path else {}
            markdown = (
                markdown_path.read_text(encoding="utf-8") if markdown_path else ""
            )
            if not isinstance(content_list, list):
                raise ParserError("mineru_structured_output_invalid")
            result, artifacts = self._map_result(
                parse_input,
                cache_directory,
                content_list,
                middle,
                markdown,
                allow_ocr,
            )
        except ParserError:
            raise
        except Exception as exc:
            raise ParserError("mineru_parse_failed") from exc

        return PDFAdapterOutput(
            parser_name=self.parser_name,
            parser_version=self.parser_version,
            result=result,
            artifacts=artifacts,
            backend_metadata={
                "profile": inspection.profile.value,
                "cache_path": cache_directory.as_posix(),
                "structured_item_count": len(content_list),
            },
            ocr_used=allow_ocr,
        )

    def _cache_directory(self, parse_input: ParseInput) -> Path:
        digest = parse_input.snapshot.content_hash.removeprefix("sha256:")[:20]
        return self.cache_root / parse_input.snapshot.source_id / digest

    @classmethod
    def _has_output(cls, cache_directory: Path) -> bool:
        return (
            cls._first_named(
                cache_directory,
                ("content_list_v2.json", "content_list.json"),
            )
            is not None
            or cls._first_suffix(cache_directory, "_content_list.json") is not None
            or cls._first_suffix(cache_directory, ".md") is not None
        )

    @staticmethod
    def _first_named(root: Path, names: tuple[str, ...]) -> Path | None:
        if not root.exists():
            return None
        for name in names:
            matches = sorted(root.rglob(name))
            if matches:
                return matches[0]
        return None

    @staticmethod
    def _first_suffix(root: Path, suffix: str) -> Path | None:
        if not root.exists():
            return None
        return next(iter(sorted(root.rglob(f"*{suffix}"))), None)

    @staticmethod
    def _load_json(path: Path) -> object:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ParserError("mineru_structured_output_invalid") from exc

    def _map_result(
        self,
        parse_input: ParseInput,
        cache_directory: Path,
        content_list: list[object],
        middle: object,
        markdown: str,
        allow_ocr: bool,
    ) -> tuple[ParserResult, list[OutputArtifact]]:
        blocks: list[DocumentBlock] = []
        tables: list[DocumentTable] = []
        images: list[DocumentImage] = []
        artifacts: list[OutputArtifact] = []
        heading_by_level: dict[int, str] = {}
        title: str | None = None

        for item in content_list:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type", "other")).lower()
            block_type, text, level, block_metadata = self._block_value(item)
            if not text and block_type is not BlockType.IMAGE:
                continue
            order_index = len(blocks)
            block_id = f"{parse_input.snapshot.source_id}-b{order_index:06d}"
            parent_id = self._parent_id(
                block_type, level, block_id, heading_by_level
            )
            block = DocumentBlock(
                block_id=block_id,
                block_type=block_type,
                text=text,
                level=level,
                order_index=order_index,
                parent_block_id=parent_id,
                metadata=block_metadata,
            )
            blocks.append(block)
            if title is None and block_type is BlockType.HEADING:
                title = text
            if block_type is BlockType.TABLE:
                rows = item.get("table_rows", [])
                tables.append(
                    DocumentTable(
                        table_id=f"{parse_input.snapshot.source_id}-t{len(tables):06d}",
                        page_number=block_metadata["page_number"],
                        bbox=self._bbox_tuple(block_metadata.get("bbox")),
                        markdown=text,
                        rows=(
                            [[str(cell) for cell in row] for row in rows if isinstance(row, list)]
                            if isinstance(rows, list)
                            else []
                        ),
                        metadata={"source_block_id": block_id},
                    )
                )
            if block_type is BlockType.IMAGE:
                image_path = self._safe_output_path(
                    cache_directory, str(item.get("img_path", ""))
                )
                if image_path is not None and image_path.is_file():
                    relative_path = f"images/{image_path.name}"
                    content_type = mimetypes.guess_type(image_path.name)[0] or "image/png"
                    artifacts.append(
                        OutputArtifact(
                            relative_path=relative_path,
                            content=image_path.read_bytes(),
                            content_type=content_type,
                        )
                    )
                    images.append(
                        DocumentImage(
                            image_id=f"{parse_input.snapshot.source_id}-i{len(images):06d}",
                            page_number=block_metadata["page_number"],
                            bbox=self._bbox_tuple(block_metadata.get("bbox")),
                            path=relative_path,
                            content_type=content_type,
                            caption=text or None,
                            metadata={"source_block_id": block_id},
                        )
                    )

        if not blocks and markdown.strip():
            parsed = MarkdownParser().parse(
                parse_input.model_copy(update={"raw_bytes": markdown.encode("utf-8")})
            )
            blocks = [
                block.model_copy(update={"metadata": {**block.metadata, "page_number": 1}})
                for block in parsed.blocks
            ]
            title = parsed.title
        if not blocks:
            raise ParserError("mineru_output_empty")
        return (
            ParserResult(
                title=title,
                language="und",
                blocks=blocks,
                tables=tables,
                images=images,
                metadata={
                    "mineru": {
                        "content_list": self._bounded(content_list),
                        "middle": self._bounded(middle),
                        "markdown_available": bool(markdown.strip()),
                    },
                    "ocr_used": allow_ocr,
                },
            ),
            artifacts,
        )

    @staticmethod
    def _block_value(
        item: dict[str, object]
    ) -> tuple[BlockType, str, int | None, dict[str, JsonValue]]:
        item_type = str(item.get("type", "other")).lower()
        mapping = {
            "title": BlockType.HEADING,
            "text": BlockType.PARAGRAPH,
            "paragraph": BlockType.PARAGRAPH,
            "list": BlockType.LIST,
            "table": BlockType.TABLE,
            "image": BlockType.IMAGE,
            "code": BlockType.CODE,
        }
        block_type = mapping.get(item_type, BlockType.OTHER)
        level = None
        if block_type is BlockType.HEADING:
            raw_level = item.get("text_level", 1)
            level = min(6, max(1, int(raw_level) if isinstance(raw_level, int) else 1))
        if block_type is BlockType.LIST:
            raw_items = item.get("list_items", [])
            items = [str(value) for value in raw_items] if isinstance(raw_items, list) else []
            text = "\n".join(items)
        elif block_type is BlockType.TABLE:
            text = str(item.get("table_body", ""))
            items = []
        elif block_type is BlockType.IMAGE:
            raw_caption = item.get("image_caption", [])
            if isinstance(raw_caption, list):
                text = " ".join(str(value) for value in raw_caption)
            else:
                text = str(raw_caption or "")
            items = []
        else:
            text = str(item.get("text", ""))
            items = []
        if isinstance(item.get("page_idx"), int):
            page_number = int(item["page_idx"]) + 1
        elif isinstance(item.get("page_number"), int):
            page_number = int(item["page_number"])
        else:
            page_number = 1
        metadata: dict[str, JsonValue] = {
            "page_number": max(1, page_number),
            "backend_type": item_type,
        }
        bbox = item.get("bbox")
        if isinstance(bbox, list) and len(bbox) == 4:
            metadata["bbox"] = [float(value) for value in bbox]
        if block_type is BlockType.LIST:
            metadata.update({"items": items, "ordered": False})
        if block_type is BlockType.TABLE:
            rows = item.get("table_rows", [])
            if isinstance(rows, list):
                metadata["rows"] = PyMuPDF4LLMAdapter._json_safe(rows)
        if block_type is BlockType.IMAGE:
            metadata.update(
                {
                    "src": f"images/{Path(str(item.get('img_path', ''))).name}",
                    "alt": text,
                }
            )
        return block_type, text, level, metadata

    @staticmethod
    def _parent_id(
        block_type: BlockType,
        level: int | None,
        block_id: str,
        headings: dict[int, str],
    ) -> str | None:
        if block_type is BlockType.HEADING and level is not None:
            lower = [candidate for candidate in headings if candidate < level]
            parent = headings[max(lower)] if lower else None
            for candidate in list(headings):
                if candidate >= level:
                    del headings[candidate]
            headings[level] = block_id
            return parent
        return headings[max(headings)] if headings else None

    @staticmethod
    def _bbox_tuple(value: JsonValue | None) -> tuple[float, float, float, float] | None:
        if isinstance(value, list) and len(value) == 4:
            return tuple(float(item) for item in value)
        return None

    @staticmethod
    def _safe_output_path(root: Path, value: str) -> Path | None:
        if not value:
            return None
        candidate = (root / value).resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError:
            return None
        return candidate

    def _bounded(self, value: object) -> JsonValue:
        safe = PyMuPDF4LLMAdapter._json_safe(value)
        serialized = json.dumps(safe, ensure_ascii=False, separators=(",", ":"))
        if len(serialized) <= self.config.max_structured_metadata_chars:
            return safe
        return {
            "truncated": True,
            "original_chars": len(serialized),
            "preview": serialized[: self.config.max_structured_metadata_chars],
        }


class DoclingRuntimeOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    markdown: str
    structured: dict[str, JsonValue]
    artifacts: list[OutputArtifact] = Field(default_factory=list)


class DoclingRuntime(Protocol):
    def convert(
        self, raw_bytes: bytes, filename: str, allow_ocr: bool
    ) -> DoclingRuntimeOutput: ...


@dataclass(frozen=True, slots=True)
class LocalDoclingRuntime:
    timeout_seconds: float = 900.0
    import_module: Callable[[str], object] = standard_import_module

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("Docling timeout must be positive")

    def convert(
        self, raw_bytes: bytes, filename: str, allow_ocr: bool
    ) -> DoclingRuntimeOutput:
        try:
            from io import BytesIO

            converter_module = self.import_module("docling.document_converter")
            base_models = self.import_module("docling.datamodel.base_models")
            pipeline_module = self.import_module("docling.datamodel.pipeline_options")
            options = pipeline_module.PdfPipelineOptions()
            options.do_ocr = allow_ocr
            options.document_timeout = self.timeout_seconds
            converter = converter_module.DocumentConverter(
                format_options={
                    base_models.InputFormat.PDF: converter_module.PdfFormatOption(
                        pipeline_options=options
                    )
                }
            )
            stream = base_models.DocumentStream(
                name=filename, stream=BytesIO(raw_bytes)
            )
            result = converter.convert(stream)
            has_timeout_errors = getattr(result, "has_timeout_errors", None)
            if callable(has_timeout_errors) and has_timeout_errors():
                raise ParserError("docling_backend_timeout")
            document = result.document
            return DoclingRuntimeOutput(
                markdown=document.export_to_markdown(),
                structured=PyMuPDF4LLMAdapter._json_safe(
                    document.export_to_dict()
                ),
            )
        except (ImportError, ModuleNotFoundError) as exc:
            raise ParserError("docling_backend_unavailable") from exc
        except ParserError:
            raise
        except Exception as exc:
            raise ParserError("docling_parse_failed") from exc


class DoclingAdapter(PDFParserAdapter):
    parser_name = "docling"

    def __init__(self, runtime: DoclingRuntime | None = None) -> None:
        self.runtime = runtime or LocalDoclingRuntime()

    @property
    def parser_version(self) -> str:
        try:
            backend_version = version("docling")
        except PackageNotFoundError:
            backend_version = "lazy"
        return f"{backend_version}+a{self.adapter_version}"

    def parse(
        self, parse_input: ParseInput, inspection: PDFInspection
    ) -> PDFAdapterOutput:
        allow_ocr = inspection.suspected_scan or not inspection.native_text_available
        runtime_output = self.runtime.convert(
            parse_input.raw_bytes,
            f"{parse_input.snapshot.source_id}.pdf",
            allow_ocr,
        )
        result = self._map_result(parse_input, runtime_output, allow_ocr)
        return PDFAdapterOutput(
            parser_name=self.parser_name,
            parser_version=self.parser_version,
            result=result,
            artifacts=runtime_output.artifacts,
            backend_metadata={
                "profile": inspection.profile.value,
                "structured_export": True,
            },
            ocr_used=allow_ocr,
        )

    def _map_result(
        self,
        parse_input: ParseInput,
        runtime_output: DoclingRuntimeOutput,
        allow_ocr: bool,
    ) -> ParserResult:
        structured = runtime_output.structured
        blocks: list[DocumentBlock] = []
        tables: list[DocumentTable] = []
        images: list[DocumentImage] = []
        title = self._text_value(structured.get("name"))

        raw_texts = structured.get("texts", [])
        if isinstance(raw_texts, list):
            for item in raw_texts:
                if not isinstance(item, dict):
                    continue
                text = self._text_value(item.get("text"))
                if not text:
                    continue
                label = self._text_value(item.get("label")) or "text"
                is_heading = label in {"title", "section_header", "heading"}
                level_value = item.get("level", 1)
                level = (
                    min(6, max(1, level_value))
                    if is_heading and isinstance(level_value, int)
                    else (1 if is_heading else None)
                )
                self._append_docling_block(
                    blocks,
                    parse_input.snapshot.source_id,
                    BlockType.HEADING if is_heading else BlockType.PARAGRAPH,
                    text,
                    level,
                    item,
                )
        raw_tables = structured.get("tables", [])
        if isinstance(raw_tables, list):
            for item in raw_tables:
                if not isinstance(item, dict):
                    continue
                rows = self._docling_rows(item)
                markdown = self._table_markdown(rows)
                block = self._append_docling_block(
                    blocks,
                    parse_input.snapshot.source_id,
                    BlockType.TABLE,
                    markdown,
                    None,
                    item,
                    extra_metadata={"rows": rows},
                )
                tables.append(
                    DocumentTable(
                        table_id=f"{parse_input.snapshot.source_id}-t{len(tables):06d}",
                        page_number=block.metadata.get("page_number", 1),
                        bbox=MinerUAdapter._bbox_tuple(block.metadata.get("bbox")),
                        markdown=markdown,
                        rows=rows,
                        metadata={"source_block_id": block.block_id},
                    )
                )
        artifacts_by_name = {
            Path(artifact.relative_path).name: artifact
            for artifact in runtime_output.artifacts
            if artifact.content_type.startswith("image/")
        }
        raw_pictures = structured.get("pictures", [])
        if isinstance(raw_pictures, list):
            for item in raw_pictures:
                if not isinstance(item, dict):
                    continue
                caption = self._text_value(item.get("caption")) or ""
                uri = self._text_value(item.get("uri")) or ""
                name = Path(uri).name if uri else ""
                artifact = artifacts_by_name.get(name)
                relative_path = artifact.relative_path if artifact else uri
                block = self._append_docling_block(
                    blocks,
                    parse_input.snapshot.source_id,
                    BlockType.IMAGE,
                    caption,
                    None,
                    item,
                    extra_metadata={
                        "src": relative_path,
                        "alt": caption,
                    },
                )
                if artifact is not None:
                    images.append(
                        DocumentImage(
                            image_id=(
                                f"{parse_input.snapshot.source_id}-i{len(images):06d}"
                            ),
                            page_number=block.metadata.get("page_number", 1),
                            bbox=MinerUAdapter._bbox_tuple(
                                block.metadata.get("bbox")
                            ),
                            path=artifact.relative_path,
                            content_type=artifact.content_type,
                            caption=caption or None,
                            metadata={"source_block_id": block.block_id},
                        )
                    )
        if not blocks and runtime_output.markdown.strip():
            parsed = MarkdownParser().parse(
                parse_input.model_copy(
                    update={"raw_bytes": runtime_output.markdown.encode("utf-8")}
                )
            )
            blocks = [
                block.model_copy(update={"metadata": {**block.metadata, "page_number": 1}})
                for block in parsed.blocks
            ]
            title = title or parsed.title
        if not blocks:
            raise ParserError("docling_output_empty")
        return ParserResult(
            title=title,
            language="und",
            blocks=blocks,
            tables=tables,
            images=images,
            metadata={
                "docling": {"structured_document": structured},
                "ocr_used": allow_ocr,
            },
        )

    @classmethod
    def _append_docling_block(
        cls,
        blocks: list[DocumentBlock],
        source_id: str,
        block_type: BlockType,
        text: str,
        level: int | None,
        item: dict[str, JsonValue],
        *,
        extra_metadata: dict[str, JsonValue] | None = None,
    ) -> DocumentBlock:
        metadata = cls._provenance(item)
        metadata["backend_label"] = cls._text_value(item.get("label")) or "other"
        if extra_metadata:
            metadata.update(extra_metadata)
        block = DocumentBlock(
            block_id=f"{source_id}-b{len(blocks):06d}",
            block_type=block_type,
            text=text,
            level=level,
            order_index=len(blocks),
            metadata=metadata,
        )
        blocks.append(block)
        return block

    @staticmethod
    def _provenance(item: dict[str, JsonValue]) -> dict[str, JsonValue]:
        metadata: dict[str, JsonValue] = {"page_number": 1}
        prov = item.get("prov", [])
        if not isinstance(prov, list) or not prov or not isinstance(prov[0], dict):
            return metadata
        first = prov[0]
        page_number = first.get("page_no", 1)
        if isinstance(page_number, int):
            metadata["page_number"] = max(1, page_number)
        bbox = first.get("bbox")
        if isinstance(bbox, dict):
            values = [bbox.get(key) for key in ("l", "t", "r", "b")]
            if all(isinstance(value, (int, float)) for value in values):
                metadata["bbox"] = values
        elif isinstance(bbox, list) and len(bbox) == 4:
            metadata["bbox"] = bbox
        return metadata

    @staticmethod
    def _docling_rows(item: dict[str, JsonValue]) -> list[list[str]]:
        data = item.get("data", {})
        grid = data.get("grid", []) if isinstance(data, dict) else []
        if not isinstance(grid, list):
            return []
        rows: list[list[str]] = []
        for row in grid:
            if not isinstance(row, list):
                continue
            rows.append(
                [
                    str(cell.get("text", "")) if isinstance(cell, dict) else str(cell)
                    for cell in row
                ]
            )
        return rows

    @staticmethod
    def _table_markdown(rows: list[list[str]]) -> str:
        if not rows:
            return ""
        width = max(len(row) for row in rows)
        padded = [row + [""] * (width - len(row)) for row in rows]
        lines = [f"| {' | '.join(padded[0])} |", f"| {' | '.join(['---'] * width)} |"]
        lines.extend(f"| {' | '.join(row)} |" for row in padded[1:])
        return "\n".join(lines)

    @staticmethod
    def _text_value(value: JsonValue | None) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None
