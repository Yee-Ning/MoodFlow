"""Validate Batch A candidates and generate a pending human-review workbook."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any


WORKSPACE_ROOT = Path(__file__).resolve().parent
CANDIDATE_POOL = WORKSPACE_ROOT / "candidate_review" / "candidate_pool.jsonl"
EXCLUSION_LOG = (
    WORKSPACE_ROOT / "candidate_review" / "candidate_exclusions.jsonl"
)
CANDIDATE_TEMPLATE = (
    WORKSPACE_ROOT / "templates" / "worldline_gold_candidate_template.json"
)
EXCLUSION_TEMPLATE = (
    WORKSPACE_ROOT / "templates" / "candidate_exclusion_template.json"
)
WORKBOOK = (
    WORKSPACE_ROOT / "candidate_review" / "candidate_review_workbook.md"
)

REGISTERED_GENRES = {
    "genre:hip_hop": "Hip-Hop",
    "genre:jazz": "Jazz",
    "genre:jazz_hip_hop": "Jazz Hip-Hop",
    "genre:r_and_b": "R&B",
    "genre:c_pop": "C-pop",
}
REGIONS = {"cjk_chinese", "western", "japanese", "korean"}
EXCLUSION_REASONS = {
    "genre_not_registered",
    "weak_source",
    "no_direct_evidence",
    "identity_ambiguous",
    "catalog_missing",
}
CANDIDATE_ID = re.compile(r"^wl-candidate-v1-[a-z0-9][a-z0-9-]*$")


class CandidateReviewValidationError(ValueError):
    """Raised when preparation data could leak an invalid or premature label."""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CandidateReviewValidationError(
            f"candidate_review_json_invalid:{path}"
        ) from exc
    if not isinstance(value, dict):
        raise CandidateReviewValidationError(
            f"candidate_review_json_object_required:{path}"
        )
    return value


def _read_jsonl(path: Path, *, allow_empty: bool) -> list[dict[str, Any]]:
    if not path.is_file():
        raise CandidateReviewValidationError(
            f"candidate_review_jsonl_missing:{path}"
        )
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
            raise CandidateReviewValidationError(
                f"candidate_review_jsonl_invalid:{path}:{line_number}"
            ) from exc
        if not isinstance(value, dict):
            raise CandidateReviewValidationError(
                f"candidate_review_jsonl_object_required:{path}:{line_number}"
            )
        rows.append(value)
    if not rows and not allow_empty:
        raise CandidateReviewValidationError(
            f"candidate_review_jsonl_empty:{path}"
        )
    return rows


def validate_candidate_pool(path: Path = CANDIDATE_POOL) -> list[dict[str, Any]]:
    rows = _read_jsonl(path, allow_empty=False)
    if len(rows) != 14:
        raise CandidateReviewValidationError(
            f"candidate_review_pool_size:{len(rows)}"
        )
    seen: set[str] = set()
    for row in rows:
        candidate_id = row.get("candidate_id")
        if not isinstance(candidate_id, str) or not CANDIDATE_ID.fullmatch(
            candidate_id
        ):
            raise CandidateReviewValidationError(
                f"candidate_review_id_invalid:{candidate_id}"
            )
        if candidate_id in seen:
            raise CandidateReviewValidationError(
                f"candidate_review_id_duplicate:{candidate_id}"
            )
        seen.add(candidate_id)
        if not isinstance(row.get("canonical_name"), str) or not row[
            "canonical_name"
        ].strip():
            raise CandidateReviewValidationError(
                f"candidate_review_name_missing:{candidate_id}"
            )
        aliases = row.get("aliases")
        if not isinstance(aliases, list) or any(
            not isinstance(alias, str) or not alias.strip() for alias in aliases
        ):
            raise CandidateReviewValidationError(
                f"candidate_review_aliases_invalid:{candidate_id}"
            )
        if row.get("region") not in REGIONS:
            raise CandidateReviewValidationError(
                f"candidate_review_region_invalid:{candidate_id}"
            )
        genre = row.get("target_genre")
        if not isinstance(genre, dict):
            raise CandidateReviewValidationError(
                f"candidate_review_genre_missing:{candidate_id}"
            )
        genre_id = genre.get("canonical_id")
        if (
            genre_id not in REGISTERED_GENRES
            or genre.get("canonical_name") != REGISTERED_GENRES[genre_id]
            or genre.get("registry_version") != "v2"
        ):
            raise CandidateReviewValidationError(
                f"candidate_review_genre_invalid:{candidate_id}"
            )
        if row.get("relation_type") != "associated_with":
            raise CandidateReviewValidationError(
                f"candidate_review_relation_invalid:{candidate_id}"
            )
        if row.get("priority") not in {"primary", "backup"}:
            raise CandidateReviewValidationError(
                f"candidate_review_priority_invalid:{candidate_id}"
            )
        if row.get("candidate_status") != "pending_review":
            raise CandidateReviewValidationError(
                f"candidate_review_premature_status:{candidate_id}"
            )
        if row.get("is_gold") is not False:
            raise CandidateReviewValidationError(
                f"candidate_review_gold_flag_forbidden:{candidate_id}"
            )
        if any(key in row for key in ("outcome", "accepted", "rejected")):
            raise CandidateReviewValidationError(
                f"candidate_review_premature_outcome:{candidate_id}"
            )
    return rows


def validate_candidate_template(path: Path = CANDIDATE_TEMPLATE) -> None:
    value = _read_json(path)
    if value.get("record_type") != "worldline_gold_candidate_review":
        raise CandidateReviewValidationError("candidate_template_record_type")
    if value.get("candidate_status") != "pending_review":
        raise CandidateReviewValidationError("candidate_template_status")
    relation = value.get("relation_hypothesis")
    if not isinstance(relation, dict) or relation.get("relation_type") != (
        "associated_with"
    ):
        raise CandidateReviewValidationError("candidate_template_relation")
    review = value.get("review")
    if not isinstance(review, dict):
        raise CandidateReviewValidationError("candidate_template_review")
    if review.get("relation_review_status") != "pending":
        raise CandidateReviewValidationError("candidate_template_relation_status")
    if review.get("identity_review_status") != "pending":
        raise CandidateReviewValidationError("candidate_template_identity_status")
    if value.get("final_outcome") is not None:
        raise CandidateReviewValidationError("candidate_template_outcome_forbidden")


def validate_exclusions(path: Path = EXCLUSION_LOG) -> list[dict[str, Any]]:
    rows = _read_jsonl(path, allow_empty=True)
    seen: set[str] = set()
    for row in rows:
        candidate_id = row.get("candidate_id")
        if not isinstance(candidate_id, str) or not CANDIDATE_ID.fullmatch(
            candidate_id
        ):
            raise CandidateReviewValidationError(
                f"candidate_exclusion_id_invalid:{candidate_id}"
            )
        if candidate_id in seen:
            raise CandidateReviewValidationError(
                f"candidate_exclusion_duplicate:{candidate_id}"
            )
        seen.add(candidate_id)
        if row.get("reason") not in EXCLUSION_REASONS:
            raise CandidateReviewValidationError(
                f"candidate_exclusion_reason_invalid:{candidate_id}"
            )
        if not row.get("reviewer") or not row.get("reviewed_at"):
            raise CandidateReviewValidationError(
                f"candidate_exclusion_review_missing:{candidate_id}"
            )
    return rows


def validate_evidence_snapshot(value: dict[str, Any]) -> None:
    """Validate a filled source/evidence snapshot without deciding its outcome."""

    source = value.get("source")
    evidence = value.get("evidence")
    if not isinstance(source, dict) or not isinstance(evidence, dict):
        raise CandidateReviewValidationError("candidate_snapshot_shape_invalid")
    text = evidence.get("evidence_text")
    exact = evidence.get("exact_span")
    start = evidence.get("span_start")
    end = evidence.get("span_end")
    digest = source.get("content_hash")
    if not isinstance(text, str) or not isinstance(exact, str):
        raise CandidateReviewValidationError("candidate_snapshot_text_missing")
    if not isinstance(start, int) or not isinstance(end, int):
        raise CandidateReviewValidationError("candidate_snapshot_offsets_missing")
    if text[start:end] != exact:
        raise CandidateReviewValidationError("candidate_snapshot_span_mismatch")
    if digest != sha256(text.encode("utf-8")).hexdigest():
        raise CandidateReviewValidationError("candidate_snapshot_hash_mismatch")


def validate_candidate_review_record(path: Path) -> dict[str, Any]:
    """Validate one human-filled preparation record without assigning Gold."""

    value = _read_json(path)
    if value.get("record_type") != "worldline_gold_candidate_review":
        raise CandidateReviewValidationError("candidate_record_type_invalid")
    if value.get("candidate_status") != "pending_review":
        raise CandidateReviewValidationError("candidate_record_status_forbidden")
    if value.get("final_outcome") is not None:
        raise CandidateReviewValidationError("candidate_record_outcome_forbidden")

    artist = value.get("artist")
    if not isinstance(artist, dict) or not CANDIDATE_ID.fullmatch(
        str(artist.get("candidate_id", ""))
    ):
        raise CandidateReviewValidationError("candidate_record_artist_invalid")
    if artist.get("region") not in REGIONS:
        raise CandidateReviewValidationError("candidate_record_region_invalid")

    relation = value.get("relation_hypothesis")
    genre = relation.get("target_genre") if isinstance(relation, dict) else None
    genre_id = genre.get("canonical_id") if isinstance(genre, dict) else None
    if (
        not isinstance(relation, dict)
        or relation.get("relation_type") != "associated_with"
        or genre_id not in REGISTERED_GENRES
        or genre.get("canonical_name") != REGISTERED_GENRES[genre_id]
        or genre.get("registry_version") != "v2"
    ):
        raise CandidateReviewValidationError("candidate_record_relation_invalid")

    review = value.get("review")
    if not isinstance(review, dict):
        raise CandidateReviewValidationError("candidate_record_review_invalid")
    for field in ("relation_review_status", "identity_review_status"):
        if review.get(field) not in {"pending", "reviewed"}:
            raise CandidateReviewValidationError(
                f"candidate_record_review_status_invalid:{field}"
            )

    source = value.get("source")
    evidence = value.get("evidence")
    has_snapshot = isinstance(source, dict) and any(
        source.get(field)
        for field in (
            "source_id",
            "fixture_source_id",
            "title",
            "publisher",
            "url",
            "local_provenance",
            "source_type",
            "language",
            "content_hash",
        )
    )
    if has_snapshot:
        required_source = (
            "source_id",
            "fixture_source_id",
            "title",
            "publisher",
            "source_type",
            "language",
            "content_hash",
        )
        if any(not source.get(field) for field in required_source):
            raise CandidateReviewValidationError(
                "candidate_record_source_incomplete"
            )
        if not source.get("url") and not source.get("local_provenance"):
            raise CandidateReviewValidationError(
                "candidate_record_source_location_missing"
            )
        if not isinstance(evidence, dict):
            raise CandidateReviewValidationError(
                "candidate_record_evidence_missing"
            )
        validate_evidence_snapshot(value)

    identity = value.get("identity")
    if not isinstance(identity, dict) or identity.get("identity_status") not in {
        "pending",
        "resolved",
        "ambiguous",
        "unresolved",
    }:
        raise CandidateReviewValidationError("candidate_record_identity_invalid")
    if identity.get("identity_status") == "resolved" and (
        not identity.get("knowledge_entity_id")
        or not isinstance(identity.get("catalog_reference"), dict)
    ):
        raise CandidateReviewValidationError(
            "candidate_record_resolved_identity_incomplete"
        )
    return value


def validate_workspace() -> dict[str, int]:
    candidates = validate_candidate_pool()
    validate_candidate_template()
    exclusions = validate_exclusions()
    exclusion_template = _read_json(EXCLUSION_TEMPLATE)
    reason_hint = exclusion_template.get("reason")
    if not isinstance(reason_hint, str) or not all(
        reason in reason_hint for reason in sorted(EXCLUSION_REASONS)
    ):
        raise CandidateReviewValidationError("candidate_exclusion_template_reasons")
    return {
        "candidate_count": len(candidates),
        "pending_count": sum(
            row["candidate_status"] == "pending_review" for row in candidates
        ),
        "exclusion_count": len(exclusions),
    }


def render_workbook(rows: list[dict[str, Any]]) -> str:
    lines = [
        "# WorldLine Gold V1 Batch A Candidate Review Workbook",
        "",
        "> All entries are discovery hypotheses with `pending_review` status. This workbook contains no Gold decision.",
        "",
        "## Candidate Summary",
        "",
        "| Candidate | Region | Genre hypothesis | Priority | Status |",
        "|---|---|---|---|---|",
    ]
    lines.extend(
        f"| {row['canonical_name']} | {row['region']} | {row['target_genre']['canonical_name']} | {row['priority']} | {row['candidate_status']} |"
        for row in rows
    )
    lines.extend(["", "## Review Sheets", ""])
    for index, row in enumerate(rows, start=1):
        aliases = ", ".join(row["aliases"]) or "None recorded"
        lines.extend(
            [
                f"### {index}. {row['canonical_name']}",
                "",
                f"- Candidate ID: `{row['candidate_id']}`",
                f"- Region: `{row['region']}`",
                f"- Aliases to verify: {aliases}",
                f"- Relation hypothesis: `artist → {row['target_genre']['canonical_id']} / associated_with`",
                f"- Priority: `{row['priority']}`",
                "- Candidate status: `pending_review`",
                "- Gold status: **not assigned**",
                "",
                "#### Source review",
                "",
                "- [ ] Eligible source located",
                "- [ ] Source authenticity and publisher checked",
                "- [ ] Bounded source content can be frozen",
                "- [ ] Direct Artist–Genre evidence located",
                "- [ ] Exact span and offsets verified",
                "- Source ID:",
                "- Source URL or provenance:",
                "- Reviewer notes:",
                "",
                "#### Identity review",
                "",
                "- [ ] Knowledge name and aliases checked",
                "- [ ] Region and representative works checked",
                "- [ ] Catalog Artist page and raw ID checked",
                "- [ ] Same-name collision checked",
                "- Preparation identity status: `pending`",
                "- Knowledge entity ID:",
                "- Catalog reference:",
                "- Reviewer notes:",
                "",
                "#### Preparation decision",
                "",
                "- Relation review status: `pending`",
                "- Identity review status: `pending`",
                "- Final outcome: **leave blank in Batch A preparation**",
                "",
            ]
        )
    return "\n".join(lines) + "\n"


def generate_workbook(path: Path = WORKBOOK) -> Path:
    rows = validate_candidate_pool()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_workbook(rows), encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("validate", "generate-workbook", "validate-record"),
    )
    parser.add_argument("--record", type=Path)
    args = parser.parse_args()
    if args.command == "generate-workbook":
        output = generate_workbook()
        result = {"workbook": str(output), **validate_workspace()}
    elif args.command == "validate-record":
        if args.record is None:
            parser.error("--record is required for validate-record")
        value = validate_candidate_review_record(args.record)
        result = {
            "candidate_id": value["artist"]["candidate_id"],
            "candidate_status": value["candidate_status"],
            "final_outcome": value["final_outcome"],
        }
    else:
        result = validate_workspace()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
