"""Low-cost, native-text-only PDF inspection."""

from __future__ import annotations

from dataclasses import dataclass

from app.rag.parse_normalize.base import ParseNormalizeError

from .models import PDFInspection, PDFPageInspection, PDFProfile


class PDFInspectionError(ParseNormalizeError):
    """The raw bytes could not be inspected as a usable PDF."""


@dataclass(frozen=True, slots=True)
class PDFInspectorConfig:
    empty_page_max_chars: int = 20
    native_text_min_total_chars: int = 1
    scan_min_empty_page_ratio: float = 0.70
    scan_min_images_per_page: float = 0.50
    scan_no_text_min_images: int = 1
    simple_min_average_chars_per_page: float = 80.0
    simple_max_empty_page_ratio: float = 0.20
    multi_column_min_blocks_per_side: int = 2
    multi_column_min_block_chars: int = 20
    multi_column_min_page_ratio: float = 0.30
    multi_column_center_tolerance_ratio: float = 0.02
    table_min_count: int = 1
    table_min_horizontal_segments: int = 2
    table_min_vertical_segments: int = 2
    table_axis_tolerance: float = 1.0
    complex_min_images_per_page: float = 1.50

    def __post_init__(self) -> None:
        non_negative = (
            "empty_page_max_chars",
            "native_text_min_total_chars",
            "scan_min_images_per_page",
            "scan_no_text_min_images",
            "simple_min_average_chars_per_page",
            "multi_column_min_blocks_per_side",
            "multi_column_min_block_chars",
            "table_min_count",
            "table_min_horizontal_segments",
            "table_min_vertical_segments",
            "table_axis_tolerance",
            "complex_min_images_per_page",
        )
        if any(getattr(self, name) < 0 for name in non_negative):
            raise ValueError("PDF inspector numeric thresholds must be non-negative")
        ratio_names = (
            "scan_min_empty_page_ratio",
            "simple_max_empty_page_ratio",
            "multi_column_min_page_ratio",
            "multi_column_center_tolerance_ratio",
        )
        if any(not 0 <= getattr(self, name) <= 1 for name in ratio_names):
            raise ValueError("PDF inspector ratios must be between zero and one")


class PDFInspector:
    def __init__(self, config: PDFInspectorConfig | None = None) -> None:
        self.config = config or PDFInspectorConfig()

    def inspect(self, raw_bytes: bytes) -> PDFInspection:
        try:
            import pymupdf

            with pymupdf.open(stream=raw_bytes, filetype="pdf") as document:
                if document.page_count <= 0 or document.needs_pass:
                    raise PDFInspectionError("pdf_inspection_failed: unusable_pdf")
                pages: list[PDFPageInspection] = []
                extracted_text_chars = 0
                image_count = 0
                for page_index, page in enumerate(document):
                    text = page.get_text("text").strip()
                    text_chars = len(text)
                    extracted_text_chars += text_chars
                    page_image_count = len(page.get_images(full=True))
                    image_count += page_image_count
                    multi_column = self._has_multiple_columns(page)
                    table_hint = self._has_table_hint(page)
                    pages.append(
                        PDFPageInspection(
                            page_number=page_index + 1,
                            text_chars=text_chars,
                            empty_text=text_chars < self.config.empty_page_max_chars,
                            image_count=page_image_count,
                            suspected_multi_column=multi_column,
                            table_hint=table_hint,
                        )
                    )
                page_count = document.page_count
                empty_ratio = sum(page.empty_text for page in pages) / page_count
                average_chars = extracted_text_chars / page_count
                native_text = (
                    extracted_text_chars >= self.config.native_text_min_total_chars
                )
                images_per_page = image_count / page_count
                suspected_scan = (
                    empty_ratio >= self.config.scan_min_empty_page_ratio
                    and images_per_page >= self.config.scan_min_images_per_page
                ) or (
                    not native_text
                    and image_count >= self.config.scan_no_text_min_images
                )
                multi_column_ratio = (
                    sum(page.suspected_multi_column for page in pages) / page_count
                )
                suspected_multi_column = (
                    multi_column_ratio >= self.config.multi_column_min_page_ratio
                )
                table_hint = (
                    sum(page.table_hint for page in pages)
                    >= self.config.table_min_count
                )
                profile = self._profile(
                    suspected_scan=suspected_scan,
                    suspected_multi_column=suspected_multi_column,
                    table_hint=table_hint,
                    images_per_page=images_per_page,
                    native_text=native_text,
                    average_chars=average_chars,
                    empty_ratio=empty_ratio,
                )
                metadata = {
                    str(key): self._json_scalar(value)
                    for key, value in (document.metadata or {}).items()
                }
                return PDFInspection(
                    profile=profile,
                    page_count=page_count,
                    native_text_available=native_text,
                    extracted_text_chars=extracted_text_chars,
                    average_chars_per_page=average_chars,
                    empty_text_page_ratio=empty_ratio,
                    image_count=image_count,
                    suspected_scan=suspected_scan,
                    suspected_multi_column=suspected_multi_column,
                    table_hint=table_hint,
                    document_metadata=metadata,
                    pages=pages,
                )
        except PDFInspectionError:
            raise
        except Exception as exc:
            raise PDFInspectionError("pdf_inspection_failed") from exc

    def _profile(
        self,
        *,
        suspected_scan: bool,
        suspected_multi_column: bool,
        table_hint: bool,
        images_per_page: float,
        native_text: bool,
        average_chars: float,
        empty_ratio: float,
    ) -> PDFProfile:
        if suspected_scan:
            return PDFProfile.SCANNED
        if (
            suspected_multi_column
            or table_hint
            or images_per_page >= self.config.complex_min_images_per_page
        ):
            return PDFProfile.COMPLEX_LAYOUT
        if (
            native_text
            and average_chars >= self.config.simple_min_average_chars_per_page
            and empty_ratio <= self.config.simple_max_empty_page_ratio
        ):
            return PDFProfile.SIMPLE_TEXT
        return PDFProfile.UNKNOWN

    def _has_multiple_columns(self, page: object) -> bool:
        width = float(page.rect.width)
        midpoint = width / 2
        tolerance = width * self.config.multi_column_center_tolerance_ratio
        left = 0
        right = 0
        for block in page.get_text("blocks"):
            x0, _, x1, _, text, *_ = block
            if len(str(text).strip()) < self.config.multi_column_min_block_chars:
                continue
            if float(x1) <= midpoint + tolerance:
                left += 1
            elif float(x0) >= midpoint - tolerance:
                right += 1
        return (
            left >= self.config.multi_column_min_blocks_per_side
            and right >= self.config.multi_column_min_blocks_per_side
        )

    def _has_table_hint(self, page: object) -> bool:
        try:
            if len(page.find_tables().tables) >= self.config.table_min_count:
                return True
        except Exception:
            pass
        horizontal = 0
        vertical = 0
        for drawing in page.get_drawings():
            for item in drawing.get("items", []):
                if not item or item[0] != "l":
                    continue
                start, end = item[1], item[2]
                if (
                    abs(float(start.y) - float(end.y))
                    < self.config.table_axis_tolerance
                ):
                    horizontal += 1
                if (
                    abs(float(start.x) - float(end.x))
                    < self.config.table_axis_tolerance
                ):
                    vertical += 1
        return (
            horizontal >= self.config.table_min_horizontal_segments
            and vertical >= self.config.table_min_vertical_segments
        )

    @staticmethod
    def _json_scalar(value: object) -> str | int | float | bool | None:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        return str(value)
