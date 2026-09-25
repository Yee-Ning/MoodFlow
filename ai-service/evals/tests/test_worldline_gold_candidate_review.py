from hashlib import sha256
import json
from pathlib import Path

import pytest

from evals.worldline_gold_v1.candidate_review_tools import (
    CANDIDATE_POOL,
    CANDIDATE_TEMPLATE,
    CandidateReviewValidationError,
    generate_workbook,
    validate_candidate_pool,
    validate_candidate_review_record,
    validate_evidence_snapshot,
    validate_workspace,
)


EXPECTED_NAMES = {
    "周杰伦",
    "蔡依林",
    "G.E.M. 邓紫棋",
    "陈奕迅",
    "MC HotDog",
    "Kendrick Lamar",
    "Nas",
    "Beyoncé",
    "Alicia Keys",
    "Common",
    "Nujabes",
    "宇多田光",
    "Epik High",
    "Dynamic Duo",
}


def test_batch_a_candidate_pool_is_pending_and_complete() -> None:
    rows = validate_candidate_pool()

    assert len(rows) == 14
    assert {row["canonical_name"] for row in rows} == EXPECTED_NAMES
    assert all(row["candidate_status"] == "pending_review" for row in rows)
    assert all(row["is_gold"] is False for row in rows)
    assert all(row["relation_type"] == "associated_with" for row in rows)
    assert {row["region"] for row in rows} == {
        "cjk_chinese",
        "western",
        "japanese",
        "korean",
    }
    assert validate_workspace() == {
        "candidate_count": 14,
        "pending_count": 14,
        "exclusion_count": 0,
    }


def test_candidate_pool_rejects_premature_outcome(tmp_path: Path) -> None:
    rows = [
        json.loads(line)
        for line in CANDIDATE_POOL.read_text(encoding="utf-8").splitlines()
    ]
    rows[0]["candidate_status"] = "accepted"
    path = tmp_path / "candidate_pool.jsonl"
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )

    with pytest.raises(
        CandidateReviewValidationError,
        match="premature_status",
    ):
        validate_candidate_pool(path)


def test_workbook_generation_keeps_all_outcomes_unassigned(
    tmp_path: Path,
) -> None:
    path = generate_workbook(tmp_path / "workbook.md")
    content = path.read_text(encoding="utf-8")

    assert content.count("Gold status: **not assigned**") == 14
    assert content.count("Final outcome: **leave blank") == 14
    assert "accepted" not in content.casefold()
    assert "rejected" not in content.casefold()
    assert not content.endswith("\n\n")


def test_evidence_snapshot_validator_checks_span_and_hash() -> None:
    text = "Artist is associated with Hip-Hop."
    exact = "associated with Hip-Hop"
    start = text.index(exact)
    value = {
        "source": {"content_hash": sha256(text.encode("utf-8")).hexdigest()},
        "evidence": {
            "evidence_text": text,
            "exact_span": exact,
            "span_start": start,
            "span_end": start + len(exact),
        },
    }

    validate_evidence_snapshot(value)

    value["source"]["content_hash"] = "0" * 64
    with pytest.raises(
        CandidateReviewValidationError,
        match="hash_mismatch",
    ):
        validate_evidence_snapshot(value)


def test_filled_review_record_validates_without_final_outcome(
    tmp_path: Path,
) -> None:
    value = json.loads(CANDIDATE_TEMPLATE.read_text(encoding="utf-8"))
    text = "Example Artist is associated with Hip-Hop."
    exact = "associated with Hip-Hop"
    start = text.index(exact)
    value["artist"] = {
        "candidate_id": "wl-candidate-v1-example-artist",
        "canonical_name": "Example Artist",
        "aliases": [],
        "region": "western",
    }
    value["relation_hypothesis"]["target_genre"] = {
        "canonical_id": "genre:hip_hop",
        "canonical_name": "Hip-Hop",
        "registry_version": "v2",
    }
    value["source"] = {
        "source_id": "source:example",
        "fixture_source_id": "fixture:example",
        "title": "Example source",
        "publisher": "Example publisher",
        "url": "https://example.com/artist",
        "local_provenance": None,
        "source_type": "editorial",
        "language": "en",
        "content_hash": sha256(text.encode("utf-8")).hexdigest(),
    }
    value["evidence"] = {
        "evidence_text": text,
        "exact_span": exact,
        "span_start": start,
        "span_end": start + len(exact),
    }
    value["identity"] = {
        "knowledge_entity_id": "artist:example_artist",
        "catalog_reference": {
            "provider": "netease",
            "entity_id": "123",
            "entity_type": "artist",
            "name": "Example Artist",
        },
        "identity_status": "resolved",
    }
    value["review"]["relation_review_status"] = "reviewed"
    value["review"]["identity_review_status"] = "reviewed"
    path = tmp_path / "record.json"
    path.write_text(json.dumps(value), encoding="utf-8")

    loaded = validate_candidate_review_record(path)

    assert loaded["candidate_status"] == "pending_review"
    assert loaded["final_outcome"] is None

    value["final_outcome"] = "accepted"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(
        CandidateReviewValidationError,
        match="outcome_forbidden",
    ):
        validate_candidate_review_record(path)
