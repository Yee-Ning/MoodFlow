"""Read pending-review state without modifying immutable parse artifacts."""

import json
from pathlib import Path

from .models import NormalizedDocument, ParseStatus


def normalized_review_reasons(document: NormalizedDocument, path: Path) -> list[str]:
    statuses = [document.metadata.get("parse_status")]
    report_path = path.parent / "parse_report.json"
    if report_path.exists():
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if not isinstance(report, dict):
            raise ValueError("parse_review_report_invalid")
        statuses.append(report.get("status"))
    return (
        ["parse_manual_review"] if ParseStatus.MANUAL_REVIEW.value in statuses else []
    )
