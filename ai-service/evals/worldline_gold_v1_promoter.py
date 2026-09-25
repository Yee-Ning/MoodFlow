"""Promote fully reviewed Batch A candidates into WorldLine Gold V1."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from typing import Any

from evals.worldline_gold_v1.candidate_review_tools import (
    CandidateReviewValidationError,
    validate_candidate_review_record,
)
from evals.worldline_gold_v1_loader import load_worldline_gold_dataset
from evals.worldline_gold_v1_models import (
    DATASET_KIND,
    DATASET_VERSION,
    SCHEMA_VERSION,
    GoldCase,
    GoldSourceLedgerEntry,
    content_sha256,
)


SERVICE_ROOT = Path(__file__).resolve().parents[1]
REVIEW_ROOT = SERVICE_ROOT / "evals" / "worldline_gold_v1"
DEFAULT_TRACKING = REVIEW_ROOT / "candidate_review" / "candidate_review_tracking.jsonl"
DEFAULT_RECORDS = REVIEW_ROOT / "candidate_review" / "records"
DEFAULT_SOURCE_CANDIDATES = (
    REVIEW_ROOT / "source_review" / "batch_a_primary_source_candidates.jsonl"
)
DEFAULT_IDENTITY_CANDIDATES = (
    REVIEW_ROOT / "identity_review" / "batch_a_primary_catalog_candidates.jsonl"
)
DEFAULT_DATASET = SERVICE_ROOT / "evals" / "datasets" / "worldline_gold_v1.jsonl"
DEFAULT_SOURCE_LEDGER = (
    SERVICE_ROOT / "evals" / "fixtures" / "worldline_gold_v1_sources.jsonl"
)

PASS = "pass"
RESOLVED = "resolved"
FAILURE_STATUSES = {"fail", "failed", "reject", "rejected"}
CATALOG_REFERENCE = re.compile(
    r"(?:^|[;\s])catalog_reference=([a-z0-9][a-z0-9_-]*:artist:[^;\s.]+)"
)


class GoldPromotionError(ValueError):
    """Raised before output commit when reviewed inputs are inconsistent."""


@dataclass(frozen=True)
class GoldPromotionResult:
    promoted_case_count: int
    promoted_source_count: int
    pending_count: int
    rejected_count: int
    promoted_candidate_ids: tuple[str, ...]
    pending_candidate_ids: tuple[str, ...]
    rejected_candidate_ids: tuple[str, ...]
    dataset_path: str
    source_ledger_path: str
    dataset_sha256: str
    source_ledger_sha256: str
    promotion_time: str


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GoldPromotionError(f"gold_promotion_json_invalid:{path}") from exc
    if not isinstance(value, dict):
        raise GoldPromotionError(f"gold_promotion_json_object_required:{path}")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise GoldPromotionError(f"gold_promotion_input_missing:{path}")
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise GoldPromotionError(
                f"gold_promotion_jsonl_invalid:{path}:{line_number}"
            ) from exc
        if not isinstance(value, dict):
            raise GoldPromotionError(
                f"gold_promotion_jsonl_object_required:{path}:{line_number}"
            )
        rows.append(value)
    if not rows:
        raise GoldPromotionError(f"gold_promotion_jsonl_empty:{path}")
    return rows


def _index(rows: list[dict[str, Any]], *, kind: str) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        candidate_id = row.get("candidate_id")
        if not isinstance(candidate_id, str) or not candidate_id:
            raise GoldPromotionError(f"gold_promotion_{kind}_candidate_id_missing")
        if candidate_id in indexed:
            raise GoldPromotionError(
                f"gold_promotion_{kind}_candidate_duplicate:{candidate_id}"
            )
        indexed[candidate_id] = row
    return indexed


def _classify_tracking(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    selected: list[dict[str, Any]] = []
    pending: list[str] = []
    rejected: list[str] = []
    seen: set[str] = set()
    for row in rows:
        candidate_id = row.get("candidate_id")
        if not isinstance(candidate_id, str) or not candidate_id:
            raise GoldPromotionError("gold_promotion_tracking_candidate_id_missing")
        if candidate_id in seen:
            raise GoldPromotionError(
                f"gold_promotion_tracking_candidate_duplicate:{candidate_id}"
            )
        seen.add(candidate_id)
        relation = row.get("relation_status")
        evidence = row.get("evidence_status")
        identity = row.get("identity_status")
        if relation == PASS and evidence == PASS and identity == RESOLVED:
            selected.append(row)
        elif relation in FAILURE_STATUSES or evidence in FAILURE_STATUSES or identity in {
            "ambiguous",
            "unresolved",
        }:
            rejected.append(candidate_id)
        else:
            pending.append(candidate_id)
    return selected, pending, rejected


def _catalog_reference_from_notes(candidate_id: str, notes: object) -> str:
    if not isinstance(notes, str):
        raise GoldPromotionError(
            f"gold_promotion_reviewer_notes_missing:{candidate_id}"
        )
    match = CATALOG_REFERENCE.search(notes)
    if match is None:
        raise GoldPromotionError(
            f"gold_promotion_catalog_reference_missing:{candidate_id}"
        )
    return match.group(1)


def _knowledge_entity_id(value: object, *, candidate_id: str) -> str:
    if not isinstance(value, str) or not value.startswith("artist:"):
        raise GoldPromotionError(
            f"gold_promotion_knowledge_entity_invalid:{candidate_id}"
        )
    slug = re.sub(r"[^a-z0-9_]+", "_", value.split(":", 1)[1].casefold())
    slug = slug.strip("_")
    if not slug:
        raise GoldPromotionError(
            f"gold_promotion_knowledge_entity_invalid:{candidate_id}"
        )
    return f"artist:{slug}"


def _fixture_source_id(source_id: str) -> str:
    digest = sha256(source_id.encode("utf-8")).hexdigest()[:24]
    return f"fixture:worldline-gold-v1:{digest}"


def _case_id(candidate_id: str, genre_id: str) -> str:
    candidate_slug = candidate_id.removeprefix("wl-candidate-v1-")
    genre_slug = genre_id.removeprefix("genre:").replace("_", "-")
    return f"wl-gold-v1-{candidate_slug}-{genre_slug}"


def _validate_source_inputs(
    candidate_id: str,
    record: dict[str, Any],
    source_candidate: dict[str, Any],
) -> None:
    source = record.get("source")
    evidence = record.get("evidence")
    candidate_source = source_candidate.get("source")
    candidate_evidence = source_candidate.get("evidence_candidate")
    if not all(
        isinstance(value, dict)
        for value in (source, evidence, candidate_source, candidate_evidence)
    ):
        raise GoldPromotionError(f"gold_promotion_evidence_missing:{candidate_id}")
    comparisons = {
        "source_id": (source.get("source_id"), candidate_source.get("source_id")),
        "title": (source.get("title"), candidate_source.get("title")),
        "publisher": (source.get("publisher"), candidate_source.get("publisher")),
        "url": (source.get("url"), candidate_source.get("url")),
        "source_type": (
            source.get("source_type"),
            candidate_source.get("source_type"),
        ),
        "language": (source.get("language"), candidate_source.get("language")),
        "exact_span": (
            evidence.get("exact_span"),
            candidate_evidence.get("exact_span"),
        ),
        "content_hash": (
            source.get("content_hash"),
            candidate_evidence.get("content_hash"),
        ),
    }
    mismatch = next(
        (
            field
            for field, (record_value, ledger_value) in comparisons.items()
            if record_value != ledger_value
        ),
        None,
    )
    if mismatch is not None:
        raise GoldPromotionError(
            f"gold_promotion_source_mismatch:{candidate_id}:{mismatch}"
        )
    text = evidence.get("evidence_text")
    exact_span = evidence.get("exact_span")
    start = evidence.get("span_start")
    end = evidence.get("span_end")
    if (
        not isinstance(text, str)
        or not text
        or not isinstance(exact_span, str)
        or not exact_span
        or not isinstance(start, int)
        or not isinstance(end, int)
        or text[start:end] != exact_span
        or source.get("content_hash") != content_sha256(text)
    ):
        raise GoldPromotionError(
            f"gold_promotion_evidence_integrity_invalid:{candidate_id}"
        )


def _catalog_payload(
    candidate_id: str,
    record: dict[str, Any],
    identity_candidate: dict[str, Any],
    tracking: dict[str, Any],
) -> dict[str, str]:
    confirmed = _catalog_reference_from_notes(
        candidate_id,
        tracking.get("reviewer_notes"),
    )
    primary = identity_candidate.get("primary_candidate")
    if not isinstance(primary, dict):
        raise GoldPromotionError(
            f"gold_promotion_identity_candidate_missing:{candidate_id}"
        )
    if primary.get("catalog_reference_candidate") != confirmed:
        raise GoldPromotionError(
            f"gold_promotion_catalog_reference_mismatch:{candidate_id}"
        )
    identity = record.get("identity")
    candidates = identity.get("catalog_candidates") if isinstance(identity, dict) else None
    if not isinstance(candidates, list) or not any(
        isinstance(item, dict)
        and item.get("catalog_reference_candidate") == confirmed
        for item in candidates
    ):
        raise GoldPromotionError(
            f"gold_promotion_record_catalog_candidate_missing:{candidate_id}"
        )
    parts = confirmed.split(":", 2)
    if len(parts) != 3 or parts[1] != "artist" or not parts[2]:
        raise GoldPromotionError(
            f"gold_promotion_catalog_reference_invalid:{candidate_id}"
        )
    name = primary.get("name")
    if not isinstance(name, str) or not name:
        raise GoldPromotionError(
            f"gold_promotion_catalog_name_missing:{candidate_id}"
        )
    return {
        "provider": parts[0],
        "entity_id": parts[2],
        "entity_type": "artist",
        "name": name,
    }


def _build_case_and_source(
    tracking: dict[str, Any],
    record: dict[str, Any],
    source_candidate: dict[str, Any],
    identity_candidate: dict[str, Any],
    promotion_time: datetime,
) -> tuple[GoldCase, GoldSourceLedgerEntry]:
    candidate_id = tracking["candidate_id"]
    _validate_source_inputs(candidate_id, record, source_candidate)
    artist = record.get("artist")
    relation = record.get("relation_hypothesis")
    genre = relation.get("target_genre") if isinstance(relation, dict) else None
    source = record.get("source")
    evidence = record.get("evidence")
    identity = record.get("identity")
    if not all(
        isinstance(value, dict)
        for value in (artist, relation, genre, source, evidence, identity)
    ):
        raise GoldPromotionError(f"gold_promotion_record_incomplete:{candidate_id}")
    knowledge_id = _knowledge_entity_id(
        identity.get("knowledge_entity_id"),
        candidate_id=candidate_id,
    )
    catalog = _catalog_payload(
        candidate_id,
        record,
        identity_candidate,
        tracking,
    )
    source_id = source["source_id"]
    fixture_source_id = _fixture_source_id(source_id)
    aliases = []
    canonical_name = artist.get("canonical_name")
    if not isinstance(canonical_name, str) or not canonical_name:
        raise GoldPromotionError(f"gold_promotion_artist_name_missing:{candidate_id}")
    canonical_key = canonical_name.strip().casefold()
    for alias in artist.get("aliases", []):
        if not isinstance(alias, str) or not alias.strip():
            raise GoldPromotionError(f"gold_promotion_artist_alias_invalid:{candidate_id}")
        if alias.strip().casefold() != canonical_key and alias not in aliases:
            aliases.append(alias)
    catalog_name = catalog["name"]
    if (
        catalog_name.strip().casefold() != canonical_key
        and catalog_name not in aliases
    ):
        aliases.append(catalog_name)
    review_notes = tracking.get("reviewer_notes")
    if not isinstance(review_notes, str) or not review_notes:
        raise GoldPromotionError(f"gold_promotion_reviewer_notes_missing:{candidate_id}")
    case = GoldCase.model_validate(
        {
            "schema_version": SCHEMA_VERSION,
            "dataset_version": DATASET_VERSION,
            "dataset_kind": DATASET_KIND,
            "case_id": _case_id(candidate_id, genre["canonical_id"]),
            "region": artist["region"],
            "tags": ["accepted", "artist_to_genre", artist["region"]],
            "artist": {
                "canonical_name": canonical_name,
                "knowledge_entity_id": knowledge_id,
                "aliases": aliases,
                "registry_scope": "fixture_only_registry",
                "catalog_reference": catalog,
            },
            "relation": {
                "source_entity_id": knowledge_id,
                "target_genre": genre,
                "relation_type": relation["relation_type"],
            },
            "evidence": [
                {
                    "source_id": source_id,
                    "source_title": source["title"],
                    "publisher": source["publisher"],
                    "source_url": source["url"],
                    "source_type": source["source_type"],
                    "language": source["language"],
                    "exact_span": evidence["exact_span"],
                    "evidence_text": evidence["evidence_text"],
                    "span_start": evidence["span_start"],
                    "span_end": evidence["span_end"],
                    "content_sha256": source["content_hash"],
                    "fixture_source_id": fixture_source_id,
                }
            ],
            "expected": {
                "outcome": "accepted",
                "reason": "human_review_pass",
                "runtime_relation": "associated_with",
                "runtime_rejection_reason": None,
            },
            "identity": {
                "knowledge_status": "resolved",
                "catalog_status": "resolved",
                "ambiguity_status": "unambiguous",
                "expected_catalog_node_id": (
                    f"{catalog['provider']}:artist:{catalog['entity_id']}"
                ),
                "identity_evidence_source_ids": [source_id],
            },
            "review": {
                "status": "approved",
                "method": "batch_a_human_review_promotion",
                "relation_reviewer": "batch-a-human-review",
                "identity_reviewer": "batch-a-human-review",
                "adjudicator": None,
                "reviewed_at": promotion_time,
                "notes": review_notes,
                "reviewer_notes": review_notes,
                "promotion_time": promotion_time,
            },
        }
    )
    ledger_entry = GoldSourceLedgerEntry.model_validate(
        {
            "source_id": source_id,
            "fixture_source_id": fixture_source_id,
            "title": source["title"],
            "publisher": source["publisher"],
            "url": source["url"],
            "provenance": f"batch_a_candidate:{candidate_id}",
            "language": source["language"],
            "source_type": source["source_type"],
            "content": evidence["evidence_text"],
            "content_hash": source["content_hash"],
        }
    )
    return case, ledger_entry


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def promote_worldline_gold_v1(
    *,
    tracking_path: Path = DEFAULT_TRACKING,
    records_dir: Path = DEFAULT_RECORDS,
    source_candidates_path: Path = DEFAULT_SOURCE_CANDIDATES,
    identity_candidates_path: Path = DEFAULT_IDENTITY_CANDIDATES,
    dataset_path: Path = DEFAULT_DATASET,
    source_ledger_path: Path = DEFAULT_SOURCE_LEDGER,
    promotion_time: datetime | None = None,
) -> GoldPromotionResult:
    """Validate reviewed inputs, then replace both Gold outputs."""

    promoted_at = promotion_time or datetime.now(UTC)
    if promoted_at.tzinfo is None:
        raise GoldPromotionError("gold_promotion_time_timezone_required")
    tracking_rows = _read_jsonl(tracking_path)
    selected, pending_ids, rejected_ids = _classify_tracking(tracking_rows)
    source_candidates = _index(
        _read_jsonl(source_candidates_path),
        kind="source",
    )
    identity_candidates = _index(
        _read_jsonl(identity_candidates_path),
        kind="identity",
    )
    records: dict[str, tuple[Path, dict[str, Any]]] = {}
    if not records_dir.is_dir():
        raise GoldPromotionError(f"gold_promotion_records_missing:{records_dir}")
    for path in sorted(records_dir.glob("*.json")):
        value = _read_json(path)
        candidate_id = value.get("artist", {}).get("candidate_id")
        if not isinstance(candidate_id, str) or not candidate_id:
            raise GoldPromotionError(f"gold_promotion_record_candidate_missing:{path}")
        if candidate_id in records:
            raise GoldPromotionError(
                f"gold_promotion_record_candidate_duplicate:{candidate_id}"
            )
        records[candidate_id] = (path, value)

    cases: list[GoldCase] = []
    ledger_by_source_id: dict[str, GoldSourceLedgerEntry] = {}
    for tracking in selected:
        candidate_id = tracking["candidate_id"]
        record_entry = records.get(candidate_id)
        source_candidate = source_candidates.get(candidate_id)
        identity_candidate = identity_candidates.get(candidate_id)
        if (
            record_entry is None
            or source_candidate is None
            or identity_candidate is None
        ):
            raise GoldPromotionError(
                f"gold_promotion_selected_input_missing:{candidate_id}"
            )
        record_path, record = record_entry
        try:
            validate_candidate_review_record(record_path)
        except CandidateReviewValidationError as exc:
            raise GoldPromotionError(
                f"gold_promotion_record_invalid:{candidate_id}:{exc}"
            ) from exc
        case, ledger_entry = _build_case_and_source(
            tracking,
            record,
            source_candidate,
            identity_candidate,
            promoted_at,
        )
        existing = ledger_by_source_id.get(ledger_entry.source_id)
        if existing is not None:
            comparable_existing = existing.model_dump(mode="json")
            comparable_new = ledger_entry.model_dump(mode="json")
            comparable_existing["provenance"] = None
            comparable_new["provenance"] = None
            if comparable_existing != comparable_new:
                raise GoldPromotionError(
                    f"gold_promotion_source_payload_conflict:{ledger_entry.source_id}"
                )
        else:
            ledger_by_source_id[ledger_entry.source_id] = ledger_entry
        cases.append(case)

    if not cases:
        raise GoldPromotionError("gold_promotion_no_eligible_cases")
    ordered_cases = sorted(cases, key=lambda item: item.case_id)
    ordered_sources = sorted(
        ledger_by_source_id.values(),
        key=lambda item: item.source_id,
    )
    dataset_temp = dataset_path.with_suffix(dataset_path.suffix + ".tmp")
    ledger_temp = source_ledger_path.with_suffix(source_ledger_path.suffix + ".tmp")
    try:
        _write_jsonl(
            dataset_temp,
            [item.model_dump(mode="json", by_alias=True) for item in ordered_cases],
        )
        _write_jsonl(
            ledger_temp,
            [item.model_dump(mode="json") for item in ordered_sources],
        )
        loaded = load_worldline_gold_dataset(dataset_temp, ledger_temp)
        if len(loaded.approved_cases) != len(ordered_cases):
            raise GoldPromotionError("gold_promotion_approved_count_mismatch")
        dataset_path.parent.mkdir(parents=True, exist_ok=True)
        source_ledger_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(dataset_temp, dataset_path)
        os.replace(ledger_temp, source_ledger_path)
    finally:
        dataset_temp.unlink(missing_ok=True)
        ledger_temp.unlink(missing_ok=True)

    return GoldPromotionResult(
        promoted_case_count=len(ordered_cases),
        promoted_source_count=len(ordered_sources),
        pending_count=len(pending_ids),
        rejected_count=len(rejected_ids),
        promoted_candidate_ids=tuple(
            sorted(row["candidate_id"] for row in selected)
        ),
        pending_candidate_ids=tuple(sorted(pending_ids)),
        rejected_candidate_ids=tuple(sorted(rejected_ids)),
        dataset_path=str(dataset_path),
        source_ledger_path=str(source_ledger_path),
        dataset_sha256=loaded.dataset_sha256,
        source_ledger_sha256=loaded.source_ledger_sha256,
        promotion_time=promoted_at.astimezone(UTC).isoformat(),
    )


def _parse_time(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("promotion time must include a timezone")
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tracking", type=Path, default=DEFAULT_TRACKING)
    parser.add_argument("--records", type=Path, default=DEFAULT_RECORDS)
    parser.add_argument(
        "--source-candidates",
        type=Path,
        default=DEFAULT_SOURCE_CANDIDATES,
    )
    parser.add_argument(
        "--identity-candidates",
        type=Path,
        default=DEFAULT_IDENTITY_CANDIDATES,
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument(
        "--source-ledger",
        type=Path,
        default=DEFAULT_SOURCE_LEDGER,
    )
    parser.add_argument("--promotion-time")
    args = parser.parse_args()
    result = promote_worldline_gold_v1(
        tracking_path=args.tracking,
        records_dir=args.records,
        source_candidates_path=args.source_candidates,
        identity_candidates_path=args.identity_candidates,
        dataset_path=args.dataset,
        source_ledger_path=args.source_ledger,
        promotion_time=_parse_time(args.promotion_time),
    )
    print(json.dumps(asdict(result), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
