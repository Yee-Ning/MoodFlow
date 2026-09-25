from datetime import UTC, datetime
import json
from pathlib import Path
import shutil

import pytest

from evals.worldline_gold_v1_loader import load_worldline_gold_dataset
from evals.worldline_gold_v1_promoter import (
    DEFAULT_IDENTITY_CANDIDATES,
    DEFAULT_RECORDS,
    DEFAULT_SOURCE_CANDIDATES,
    DEFAULT_TRACKING,
    GoldPromotionError,
    promote_worldline_gold_v1,
)


PROMOTION_TIME = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


def _promote(tmp_path: Path, **overrides):
    dataset = tmp_path / "worldline_gold_v1.jsonl"
    ledger = tmp_path / "worldline_gold_v1_sources.jsonl"
    result = promote_worldline_gold_v1(
        tracking_path=overrides.get("tracking_path", DEFAULT_TRACKING),
        records_dir=overrides.get("records_dir", DEFAULT_RECORDS),
        source_candidates_path=overrides.get(
            "source_candidates_path",
            DEFAULT_SOURCE_CANDIDATES,
        ),
        identity_candidates_path=overrides.get(
            "identity_candidates_path",
            DEFAULT_IDENTITY_CANDIDATES,
        ),
        dataset_path=dataset,
        source_ledger_path=ledger,
        promotion_time=PROMOTION_TIME,
    )
    return result, dataset, ledger


def _copy_review_workspace(tmp_path: Path) -> Path:
    destination = tmp_path / "review"
    shutil.copytree(DEFAULT_TRACKING.parents[1], destination)
    return destination


def test_promoter_selects_only_fully_reviewed_candidates(tmp_path: Path) -> None:
    result, dataset_path, ledger_path = _promote(tmp_path)
    loaded = load_worldline_gold_dataset(dataset_path, ledger_path)

    assert result.promoted_case_count == 8
    assert result.promoted_source_count == 7
    assert result.pending_count == 6
    assert result.rejected_count == 0
    assert len(loaded.approved_cases) == 8
    assert len(loaded.sources) == 7
    assert all(case.review.promotion_time == PROMOTION_TIME for case in loaded.cases)
    assert all(case.review.reviewer_notes for case in loaded.cases)
    assert all(
        case.artist.catalog_reference is not None
        and (
            case.artist.catalog_reference.name.casefold()
            == case.artist.canonical_name.casefold()
            or case.artist.catalog_reference.name.casefold()
            in {alias.casefold() for alias in case.artist.aliases}
        )
        for case in loaded.cases
    )
    assert {
        case.artist.catalog_reference.node_id
        for case in loaded.cases
        if case.artist.catalog_reference is not None
    } == {
        "netease:artist:6452",
        "netease:artist:7763",
        "netease:artist:2116",
        "netease:artist:37995",
        "netease:artist:40424",
        "netease:artist:48436",
        "netease:artist:18122",
        "netease:artist:126682",
    }
    raw_cases = [
        json.loads(line)
        for line in dataset_path.read_text(encoding="utf-8").splitlines()
    ]
    assert all("relation" in case for case in raw_cases)
    assert all("semantic_relation" not in case for case in raw_cases)
    assert not any(
        "jolin-tsai" in case["case_id"] or "nujabes" in case["case_id"]
        for case in raw_cases
    )


def test_promoter_does_not_promote_unresolved_identity(tmp_path: Path) -> None:
    workspace = _copy_review_workspace(tmp_path)
    tracking = workspace / "candidate_review" / "candidate_review_tracking.jsonl"
    rows = [
        json.loads(line)
        for line in tracking.read_text(encoding="utf-8").splitlines()
    ]
    for row in rows:
        if row["candidate_id"] == "wl-candidate-v1-jay-chou":
            row["identity_status"] = "unresolved"
    tracking.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )

    result, dataset_path, _ = _promote(
        tmp_path / "output",
        tracking_path=tracking,
        records_dir=workspace / "candidate_review" / "records",
        source_candidates_path=(
            workspace / "source_review" / "batch_a_primary_source_candidates.jsonl"
        ),
        identity_candidates_path=(
            workspace
            / "identity_review"
            / "batch_a_primary_catalog_candidates.jsonl"
        ),
    )

    assert result.promoted_case_count == 7
    assert result.rejected_count == 1
    assert "wl-candidate-v1-jay-chou" in result.rejected_candidate_ids
    assert "jay-chou" not in dataset_path.read_text(encoding="utf-8")


def test_promoter_rejects_missing_evidence_before_writing(tmp_path: Path) -> None:
    workspace = _copy_review_workspace(tmp_path)
    record = workspace / "candidate_review" / "records" / "jay-chou.json"
    payload = json.loads(record.read_text(encoding="utf-8"))
    payload["evidence"]["evidence_text"] = ""
    record.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    output = tmp_path / "output"
    dataset = output / "worldline_gold_v1.jsonl"
    ledger = output / "worldline_gold_v1_sources.jsonl"

    with pytest.raises(GoldPromotionError, match="record_invalid"):
        promote_worldline_gold_v1(
            tracking_path=workspace
            / "candidate_review"
            / "candidate_review_tracking.jsonl",
            records_dir=workspace / "candidate_review" / "records",
            source_candidates_path=(
                workspace
                / "source_review"
                / "batch_a_primary_source_candidates.jsonl"
            ),
            identity_candidates_path=(
                workspace
                / "identity_review"
                / "batch_a_primary_catalog_candidates.jsonl"
            ),
            dataset_path=dataset,
            source_ledger_path=ledger,
            promotion_time=PROMOTION_TIME,
        )

    assert not dataset.exists()
    assert not ledger.exists()


def test_promoter_rejects_source_hash_mismatch(tmp_path: Path) -> None:
    workspace = _copy_review_workspace(tmp_path)
    source_path = (
        workspace / "source_review" / "batch_a_primary_source_candidates.jsonl"
    )
    rows = [
        json.loads(line)
        for line in source_path.read_text(encoding="utf-8").splitlines()
    ]
    rows[0]["evidence_candidate"]["content_hash"] = "0" * 64
    source_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )

    with pytest.raises(GoldPromotionError, match="source_mismatch"):
        _promote(
            tmp_path / "output",
            tracking_path=(
                workspace / "candidate_review" / "candidate_review_tracking.jsonl"
            ),
            records_dir=workspace / "candidate_review" / "records",
            source_candidates_path=source_path,
            identity_candidates_path=(
                workspace
                / "identity_review"
                / "batch_a_primary_catalog_candidates.jsonl"
            ),
        )


def test_promoter_does_not_validate_pending_candidate_payload(tmp_path: Path) -> None:
    workspace = _copy_review_workspace(tmp_path)
    record = workspace / "candidate_review" / "records" / "jolin-tsai.json"
    payload = json.loads(record.read_text(encoding="utf-8"))
    payload["evidence"]["evidence_text"] = ""
    record.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    result, _, _ = _promote(
        tmp_path / "output",
        tracking_path=(
            workspace / "candidate_review" / "candidate_review_tracking.jsonl"
        ),
        records_dir=workspace / "candidate_review" / "records",
        source_candidates_path=(
            workspace / "source_review" / "batch_a_primary_source_candidates.jsonl"
        ),
        identity_candidates_path=(
            workspace
            / "identity_review"
            / "batch_a_primary_catalog_candidates.jsonl"
        ),
    )

    assert result.promoted_case_count == 8
    assert "wl-candidate-v1-jolin-tsai" in result.pending_candidate_ids
