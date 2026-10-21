"""SequenceMatcher-based line-level diff tracking for Stage 3 text cleaning."""

from __future__ import annotations

from difflib import SequenceMatcher
from .models import LineDiffStats


def calculate_line_diff(before: str, after: str) -> tuple[LineDiffStats, dict[str, str]]:
    """Compute exact line-level differences using SequenceMatcher."""
    before_lines = before.splitlines()
    after_lines = after.splitlines()

    matcher = SequenceMatcher(None, before_lines, after_lines)
    matching_blocks = matcher.get_matching_blocks()

    unchanged_lines = sum(block.size for block in matching_blocks)
    deleted_lines = max(0, len(before_lines) - unchanged_lines)
    added_lines = max(0, len(after_lines) - unchanged_lines)
    total_lines = len(before_lines) + len(after_lines)
    diff_ratio = (
        round(1.0 - matcher.ratio(), 6)
        if total_lines > 0
        else 0.0
    )

    stats = LineDiffStats(
        before_lines=len(before_lines),
        after_lines=len(after_lines),
        deleted_lines=deleted_lines,
        added_lines=added_lines,
        unchanged_lines=unchanged_lines,
        diff_ratio=diff_ratio,
    )

    # Generate sample of deleted and modified lines for audit reporting
    diff_samples: list[str] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "delete":
            for line in before_lines[i1:i2]:
                if line.strip():
                    diff_samples.append(f"- {line}")
        elif tag == "replace":
            for line in before_lines[i1:i2]:
                if line.strip():
                    diff_samples.append(f"- {line}")
            for line in after_lines[j1:j2]:
                if line.strip():
                    diff_samples.append(f"+ {line}")
        elif tag == "insert":
            for line in after_lines[j1:j2]:
                if line.strip():
                    diff_samples.append(f"+ {line}")

    sample_summary = {
        "diff_preview": "\n".join(diff_samples[:30]),
        "total_diff_events": len(diff_samples),
    }

    return stats, sample_summary
