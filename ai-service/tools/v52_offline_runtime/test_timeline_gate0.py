"""Offline ranking, filter attribution, deduplicated counts and read-only scope."""

import os

import pytest

from tools.v52_offline_runtime import timeline_gate0 as diagnosis


@pytest.mark.parametrize("payload,expected", [({}, True), ({"year": 2003}, True), ({"year": 2017}, False),
    ({"min_mentioned_year": 2000, "max_mentioned_year": 2026}, True)])
def test_year_overlap_or_all_missing(payload, expected):
    year = {"should": [{"key": "year", "range": {"gte": 2003, "lte": 2003}},
        {"must": [{"key": "min_mentioned_year", "range": {"lte": 2003}}, {"key": "max_mentioned_year", "range": {"gte": 2003}}]},
        {"must": [{"is_empty": {"key": key}} for key in ("year", "min_mentioned_year", "max_mentioned_year")]}]}
    assert diagnosis.matches(payload, year) == expected


def test_entity_failure_is_not_year_failure():
    payload = {"entity_ids": ["album:the_era"], "min_mentioned_year": 2000, "max_mentioned_year": 2026}
    assert not diagnosis.matches(payload, {"key": "entity_ids", "match": {"any": ["artist:jay_chou"]}})
    assert diagnosis.matches(payload, {"must": [{"key": "min_mentioned_year", "range": {"lte": 2012}},
                                                       {"key": "max_mentioned_year", "range": {"gte": 2008}}]})


def test_gold_alignment_keeps_anchor_only_parents_out_of_denominator():
    case = {"relevant_parent_ids": ["new"], "anchors": [{"parent_id": "old"}]}
    assert diagnosis.gold_alignment(case) == {"anchors_without_relevant_parent": ["old"], "relevant_parents_without_anchor": ["new"]}


def test_cause_counts_deduplicate_parents_without_summing_overlapping_codes():
    rows = [{"case_id": "first", "parent_id": "shared", "stage": "rrf_below40",
             "attribution": {"established": ["F-RRF"], "hypotheses": ["A", "B"]}},
            {"case_id": "second", "parent_id": "shared", "stage": "filter_excluded",
             "attribution": {"established": ["F-ENTITY"], "hypotheses": ["A"]}}]
    result = diagnosis.summary(rows)
    assert result["gold_case_parent_pairs"] == 2 and result["unique_gold_parents"] == 1
    assert result["categories"]["A"]["hypothesis_count"] == 1
    assert result["categories"]["F"]["established_count"] == 1


def test_no_invented_original_question_leg():
    assert len(diagnosis.EXPECTED_LEGS) == 7
    with pytest.raises(ValueError, match="seven_frozen_legs"):
        diagnosis.fuse([])


def test_missing_native_tokens_are_unknown_not_zero():
    assert diagnosis.saved_overlap("unknown", [], {}) is None


def test_audit_forbids_network_source_writes_and_secret_reads(tmp_path):
    guard = diagnosis.ReadOnlyAudit(tmp_path / "output")
    for event, args in [("socket.connect", ()), ("subprocess.Popen", ()),
                        ("open", (str(tmp_path / "source.json"), "w", os.O_WRONLY)),
                        ("open", (str(tmp_path / ".env"), "r", os.O_RDONLY))]:
        with pytest.raises(RuntimeError):
            guard.audit(event, args)
    guard.audit("open", (str(tmp_path / "output/evidence.json"), "w", os.O_WRONLY))
    guard.audit("open", (str(tmp_path / "source.json"), "r", os.O_RDONLY))


def test_real_twenty_pairs_reconcile_and_same_release_unfiltered_ranks_are_missing():
    evidence = diagnosis.analyze()
    assert evidence["summary"]["stage_pairs"] == {
        "rrf_below40": 8, "not_observed_any_top80": 3, "hit_rrf40": 7, "filter_excluded": 2}
    assert evidence["summary"]["unique_R40_missed_parents"] == 9
    assert evidence["summary"]["unique_gold_parents"] == 13
    for row in evidence["gold_rows"]:
        assert all(child["year_pass"] for child in row["filter_eligibility"])
        assert all(anchor["parent_contains"] and anchor["specified_child_contains"] for anchor in row["anchors"])
        if row["case_id"] in {"rag-v0-036", "rag-v0-037", "rag-v0-039"}:
            assert row["before_year_RRF_rank"] is None
    release = next(row for row in evidence["gold_rows"] if row["case_id"] == "rag-v0-040" and row["parent_id"].startswith("parent-bb32"))
    assert [item["overlap"]["parent_shared_terms"] for item in release["saved_native_overlap"]] == [0, 0, 0]
    assert [item["overlap"]["child_shared_terms"][0]["shared"] for item in release["saved_native_overlap"]] == [1, 1, 2]
    assert evidence["cases"]["rag-v0-039"]["gold_alignment"] == {
        "anchors_without_relevant_parent": ["parent-74225d7e77f81f8051ea39c7"],
        "relevant_parents_without_anchor": ["parent-a03c56f88de76eda774a6e18", "parent-a9a1ce0be198f68e5eae8ffc"]}
