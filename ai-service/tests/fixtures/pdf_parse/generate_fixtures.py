"""Generate deterministic PDF parser fixtures; not used by the test runtime."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from reportlab.platypus import Table, TableStyle


OUTPUT_ROOT = Path(__file__).resolve().parent / "raw"


def _canvas(path: Path) -> canvas.Canvas:
    path.parent.mkdir(parents=True, exist_ok=True)
    return canvas.Canvas(
        str(path),
        pagesize=letter,
        pageCompression=1,
        invariant=1,
    )


def generate_simple_text() -> None:
    target = OUTPUT_ROOT / "simple-text.pdf"
    pdf = _canvas(target)
    width, height = letter
    pages = (
        (
            "Music Production Notes",
            [
                "Fantasy is used here as a deterministic native-text fixture.",
                "The page contains headings and paragraphs encoded as PDF text.",
                "It verifies page metadata without enabling optical recognition.",
                "The content is synthetic test data and not a knowledge source.",
            ],
        ),
        (
            "Arrangement and Collaboration",
            [
                "This second page provides enough characters for stable metrics.",
                "Composer, producer, and lyricist roles remain separate concepts.",
                "Evidence coordinates must retain the one-based source page.",
                "No cleaning, chunking, or embedding occurs in this fixture.",
            ],
        ),
    )
    for title, paragraphs in pages:
        pdf.setFont("Helvetica-Bold", 20)
        pdf.drawString(54, height - 72, title)
        text = pdf.beginText(54, height - 112)
        text.setFont("Helvetica", 11)
        text.setLeading(18)
        for paragraph in paragraphs:
            text.textLine(paragraph)
            text.textLine("")
        pdf.drawText(text)
        pdf.setFont("Helvetica", 8)
        pdf.drawRightString(width - 54, 36, "Native text PDF fixture")
        pdf.showPage()
    pdf.save()


def generate_complex_layout() -> None:
    target = OUTPUT_ROOT / "complex-layout.pdf"
    pdf = _canvas(target)
    width, height = letter
    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawString(48, height - 54, "Two-column Album Production Study")

    left = pdf.beginText(48, height - 96)
    left.setFont("Helvetica", 9)
    left.setLeading(14)
    for line in (
        "LEFT COLUMN - BACKGROUND",
        "The production team considered album sequencing.",
        "Instrumentation and rhythm were reviewed separately.",
        "This block stays entirely left of the page midpoint.",
        "A second paragraph strengthens the column signal.",
    ):
        left.textLine(line)
    pdf.drawText(left)

    right = pdf.beginText(width / 2 + 18, height - 96)
    right.setFont("Helvetica", 9)
    right.setLeading(14)
    for line in (
        "RIGHT COLUMN - FINDINGS",
        "The arrangement combines several musical textures.",
        "Credits retain composer and lyricist relationships.",
        "This block stays entirely right of the midpoint.",
        "A second paragraph strengthens the column signal.",
    ):
        right.textLine(line)
    pdf.drawText(right)

    table = Table(
        [
            ["Album", "Year", "Focus"],
            ["Fantasy", "2001", "Style"],
            ["Ye Hui Mei", "2003", "Production"],
            ["Common Jasmine Orange", "2004", "Influence"],
        ],
        colWidths=[170, 70, 180],
        rowHeights=24,
    )
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 1, colors.black),
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    table.wrapOn(pdf, width, height)
    table.drawOn(pdf, 48, height - 350)
    pdf.showPage()
    pdf.save()


def generate_scanned() -> None:
    target = OUTPUT_ROOT / "scanned.pdf"
    image = Image.new("RGB", (1200, 1600), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=32)
    lines = (
        "SCANNED MUSIC INTERVIEW",
        "This page is one raster image.",
        "It intentionally has no native PDF text layer.",
        "OCR must only be requested on the scanned path.",
        "Names, album titles, years, and punctuation need review.",
    )
    y = 180
    for line in lines:
        draw.text((120, y), line, fill="black", font=font)
        y += 110
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)

    pdf = _canvas(target)
    width, height = letter
    pdf.drawImage(
        ImageReader(buffer),
        36,
        36,
        width=width - 72,
        height=height - 72,
        preserveAspectRatio=True,
        anchor="c",
    )
    pdf.showPage()
    pdf.save()


if __name__ == "__main__":
    generate_simple_text()
    generate_complex_layout()
    generate_scanned()
