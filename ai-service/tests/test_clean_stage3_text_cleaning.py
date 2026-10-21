"""Unit tests for Stage 3: Text Content Cleaning and SequenceMatcher Line Diff."""

from __future__ import annotations

from app.rag.clean.diff_tracker import calculate_line_diff


def test_sequence_matcher_computes_exact_line_diff():
    before = """
Line 1: Keep this
Line 2: Delete this noise line
Line 3: Keep this too
Line 4: Replace old text
Line 5: Final keep
""".strip()

    after = """
Line 1: Keep this
Line 3: Keep this too
Line 4: Replace new text
Line 5: Final keep
Line 6: Added line
""".strip()

    stats, sample_summary = calculate_line_diff(before, after)
    assert stats.before_lines == 5
    assert stats.after_lines == 5
    assert stats.deleted_lines >= 1
    assert stats.added_lines >= 1
    assert stats.unchanged_lines >= 3
    assert 0.0 < stats.diff_ratio < 1.0

    preview = sample_summary["diff_preview"]
    assert "- Line 2: Delete this noise line" in preview
    assert "+ Line 6: Added line" in preview


def test_sequence_matcher_identical_text_has_zero_diff():
    text = "Line A\nLine B\nLine C"
    stats, sample_summary = calculate_line_diff(text, text)
    assert stats.deleted_lines == 0
    assert stats.added_lines == 0
    assert stats.unchanged_lines == 3
    assert stats.diff_ratio == 0.0
    assert sample_summary["total_diff_events"] == 0
