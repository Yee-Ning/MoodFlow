"""Exact row isolation, source anchoring and pending-signature release discipline."""

import json

import pytest

from tools.gold_release_v5_3 import build_candidate as build


@pytest.fixture(scope="module")
def source():
    return build.SOURCE.read_bytes(), build.projection()


def test_exact_two_case_edit_and_forty_eight_byte_identical_lines(source):
    original, parents = source
    candidate = build.candidate_bytes(original, parents)
    result = build.verify(original, candidate, parents)
    assert {row["case_id"] for row in result["changed_rows"]} == build.CHANGED
    assert len(result["unchanged_rows"]) == 48
    assert result["unchanged_48_rows_sha256"]["v52"] == result["unchanged_48_rows_sha256"]["v53"]
    assert result["v53_file_sha256"] is None and result["v53_signed_by"] is None
    assert result["all_parent_lists_identical"] and result["all_ground_truths_identical"]


def test_039_exact_quotes_cover_both_current_gold_parents(source):
    original, parents = source
    result = build.verify(original, build.candidate_bytes(original, parents), parents)
    assert len(result["039_anchors"]) == 5
    assert {row["parent_id"] for row in result["039_anchors"]} == {build.ACTIVITY_PARENT, build.ALBUM_PARENT}
    assert all(row["parent_and_child_exact_match"] for row in result["039_anchors"])
    changed = json.loads(build.replacements(original, parents)["rag-v0-039"])
    assert all(term not in " ".join(changed["required_facts"]) for term in ("DejaVu", "餐厅", "KTV"))


def test_036_removes_only_its_background_reference_and_preserves_040(source):
    original, parents = source
    result = build.verify(original, build.candidate_bytes(original, parents), parents)
    case036 = json.loads(build.replacements(original, parents)["rag-v0-036"])
    assert len(case036["required_facts"]) == 2 and len(case036["anchors"]) == 4
    assert all(anchor["parent_id"] != build.BACKGROUND_PARENT for anchor in case036["anchors"])
    case040 = next(row for row in result["036_background_other_case_references_preserved"] if row["case_id"] == "rag-v0-040")
    assert case040["is_gold"] and case040["anchor_ids"] == ["chopin-year"]


def test_unrelated_line_change_is_rejected(source):
    original, parents = source
    candidate = build.candidate_bytes(original, parents)
    first = json.loads(candidate.splitlines()[0])
    altered = candidate.replace(first["question"].encode(), "不允许修改的问题".encode(), 1)
    with pytest.raises(ValueError, match="exact_approved"):
        build.verify(original, altered, parents)


def test_missing_quote_fails_before_any_candidate_write(source):
    _, parents = source
    with pytest.raises(ValueError, match="exact_quote_missing"):
        build.anchor("invalid", build.ACTIVITY_PARENT, "not present", "逐字不存在的锚点", parents)


def test_schema_and_old_signoff_provenance_are_preserved(source):
    original, parents = source
    old = {row["case_id"]: row for row in build.rows(original)}
    for cid, line in build.replacements(original, parents).items():
        row = json.loads(line)
        assert row["schema_version"] == "v5" and row["frozen_by"] == "West"
        assert row["frozen_at"] == old[cid]["frozen_at"]
        assert "background" not in str(row["anchors"])


def test_unsigned_candidate_full_sha256_is_never_computed(source, monkeypatch):
    original, parents = source
    candidate = build.candidate_bytes(original, parents)
    original_sha256 = build.hashlib.sha256

    def checked_sha256(value):
        if value == candidate:
            pytest.fail("V5.3 full SHA256 must wait for West signoff")
        return original_sha256(value)

    monkeypatch.setattr(build.hashlib, "sha256", checked_sha256)
    assert build.verify(original, candidate, parents)["v53_file_sha256"] is None
