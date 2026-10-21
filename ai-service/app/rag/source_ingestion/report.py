"""Validated raw-corpus manifest and technical source report generation."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .http_client import RawHttpResponse
from .models import CandidateSource, RawSnapshotRecord
from .retry import failure_policy, reason_code
from .v2_models import DiscoveredCandidate, SourceAttemptRecord, SourceAttemptStatus
from .validation import SnapshotValidator


REPORT_CORE_TOPICS = frozenset(
    {
        "创作背景",
        "制作过程",
        "音乐风格",
        "中国风",
        "合作关系",
        "歌手/制作人/作词人采访",
        "专辑背景",
        "歌曲幕后",
        "作品影响",
        "音乐史 / 时代背景",
    }
)

_TOPIC_ALIASES = {
    "重点歌曲创作背景": "创作背景",
    "专辑制作过程": "制作过程",
    "音乐风格演变": "音乐风格",
}


@dataclass(frozen=True, slots=True)
class SourceCorpusReportConfig:
    minimum_snapshots: int = 55
    maximum_snapshots: int = 70
    minimum_pdf_snapshots: int = 5
    maximum_pdf_snapshots: int = 8
    minimum_new_pdf_snapshots: int = 3
    minimum_html_publishers: int = 8
    require_structured_json: bool = True
    required_topics: frozenset[str] = REPORT_CORE_TOPICS


class SourceCorpusReporter:
    def __init__(
        self,
        project_root: Path,
        *,
        config: SourceCorpusReportConfig | None = None,
        validator: SnapshotValidator | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.project_root = project_root.resolve()
        self.raw_directory = self.project_root / "data/raw"
        self.config = config or SourceCorpusReportConfig()
        self.validator = validator or SnapshotValidator()
        self.now = now or (lambda: datetime.now(UTC))

    def write_outputs(
        self,
        *,
        existing_candidates: list[CandidateSource],
        discovered_candidates: list[DiscoveredCandidate],
        attempts: list[SourceAttemptRecord],
    ) -> dict[str, object]:
        all_candidates: dict[str, CandidateSource] = {
            candidate.source_id: candidate for candidate in existing_candidates
        }
        all_candidates.update(
            {candidate.source_id: candidate for candidate in discovered_candidates}
        )
        discovered_by_id = {
            candidate.source_id: candidate for candidate in discovered_candidates
        }
        raw_records = self._load_latest_raw_records()
        entries: list[dict[str, object]] = []
        validation_failures: list[dict[str, object]] = []

        for record in sorted(raw_records.values(), key=lambda item: item.source_id):
            candidate = all_candidates.get(record.source_id)
            if candidate is None:
                validation_failures.append(
                    {
                        "source_id": record.source_id,
                        "reasons": ["missing-candidate-metadata"],
                    }
                )
                continue
            path = self.project_root / record.raw_path
            reasons: list[str] = []
            if not path.is_file():
                reasons.append("missing-raw-file")
            else:
                content = path.read_bytes()
                actual_hash = "sha256:" + hashlib.sha256(content).hexdigest()
                if actual_hash != record.content_hash:
                    reasons.append("content-hash-mismatch")
                validation = self.validator.validate(
                    candidate,
                    RawHttpResponse(
                        content=content,
                        content_type=record.content_type,
                        http_status=record.http_status,
                    ),
                )
                reasons.extend(validation.reasons)
            if reasons:
                validation_failures.append(
                    {
                        "source_id": record.source_id,
                        "reasons": list(dict.fromkeys(reasons)),
                    }
                )
                continue

            discovered = discovered_by_id.get(record.source_id)
            entry = record.model_dump(mode="json")
            entry.update(
                {
                    "title": candidate.title,
                    "publisher": candidate.publisher,
                    "author": candidate.author,
                    "file_type": candidate.file_type.value,
                    "source_type": candidate.source_type.value,
                    "source_tier": candidate.source_tier,
                    "artist": candidate.artist,
                    "album": candidate.album,
                    "topics": candidate.topics,
                    "replacement_of": (
                        discovered.replaces_source_id if discovered else None
                    ),
                }
            )
            entries.append(entry)

        report = self._build_report(
            entries=entries,
            existing_candidates=existing_candidates,
            discovered_candidates=discovered_candidates,
            attempts=attempts,
            validation_failures=validation_failures,
        )
        self.raw_directory.mkdir(parents=True, exist_ok=True)
        raw_manifest = "".join(
            f"{json.dumps(entry, ensure_ascii=False, separators=(',', ':'))}\n"
            for entry in entries
        )
        self._atomic_write(
            self.raw_directory / "raw_corpus_manifest.jsonl", raw_manifest
        )
        self._atomic_write(
            self.raw_directory / "source_corpus_report.json",
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        )
        self._atomic_write(
            self.raw_directory / "source_corpus_report.md",
            self._render_markdown(report),
        )
        return report

    def _build_report(
        self,
        *,
        entries: list[dict[str, object]],
        existing_candidates: list[CandidateSource],
        discovered_candidates: list[DiscoveredCandidate],
        attempts: list[SourceAttemptRecord],
        validation_failures: list[dict[str, object]],
    ) -> dict[str, object]:
        format_counts = Counter(str(entry["file_type"]) for entry in entries)
        file_type_counts = {
            file_type: format_counts.get(file_type, 0)
            for file_type in ("html", "pdf", "json", "txt", "markdown")
        }
        publishers = Counter(str(entry["publisher"]) for entry in entries)
        tier_counts = Counter(str(entry["source_tier"]) for entry in entries)
        topic_counts: Counter[str] = Counter()
        html_publishers: set[str] = set()
        for entry in entries:
            if entry["file_type"] == "html":
                html_publishers.add(str(entry["publisher"]))
            for topic in entry["topics"]:
                canonical = _TOPIC_ALIASES.get(str(topic), str(topic))
                topic_counts[canonical] += 1

        attempt_counts = Counter(record.status.value for record in attempts)
        fetch_attempted = (
            attempt_counts[SourceAttemptStatus.FETCH_FAILED.value]
            + attempt_counts[SourceAttemptStatus.SNAPSHOT_VALID.value]
        )
        valid_source_ids = {str(entry["source_id"]) for entry in entries}
        discovered_ids = {
            candidate.source_id for candidate in discovered_candidates
        }
        new_pdf_count = sum(
            entry["file_type"] == "pdf" and entry["source_id"] in discovered_ids
            for entry in entries
        )
        replacement_records = [
            record
            for record in attempts
            if record.status is SourceAttemptStatus.SNAPSHOT_VALID
            and record.replaces_source_id is not None
            and record.source_id in valid_source_ids
        ]
        failure_types = Counter(
            self._failure_category(record.reason or "")
            for record in attempts
            if record.status is SourceAttemptStatus.FETCH_FAILED
            and failure_policy(record.reason).category == "failure"
        )
        rate_limits = Counter(
            reason_code(record.reason) for record in attempts
            if record.status is SourceAttemptStatus.FETCH_FAILED
            and failure_policy(record.reason).category == "rate_limited"
        )
        policy_denials = sum(
            record.status is SourceAttemptStatus.FETCH_FAILED
            and failure_policy(record.reason).category == "policy_denied"
            for record in attempts
        )
        covered_topics = sorted(
            self.config.required_topics.intersection(topic_counts)
        )
        missing_topics = sorted(
            self.config.required_topics.difference(topic_counts)
        )
        gates = {
            "snapshot_count_in_range": self.config.minimum_snapshots
            <= len(entries)
            <= self.config.maximum_snapshots,
            "pdf_count_in_range": self.config.minimum_pdf_snapshots
            <= file_type_counts["pdf"]
            <= self.config.maximum_pdf_snapshots,
            "new_pdf_minimum_met": new_pdf_count
            >= self.config.minimum_new_pdf_snapshots,
            "html_publisher_minimum_met": len(html_publishers)
            >= self.config.minimum_html_publishers,
            "structured_json_present": (
                not self.config.require_structured_json
                or file_type_counts["json"] > 0
            ),
            "core_topics_covered": not missing_topics,
            "snapshot_integrity_valid": not validation_failures,
        }
        original_failed_ids = sorted(
            {
                record.source_id
                for record in attempts
                if record.attempt_id.startswith("historical-")
                and record.status is SourceAttemptStatus.FETCH_FAILED
            }
        )
        replacements_by_original: dict[str, list[str]] = defaultdict(list)
        for record in replacement_records:
            assert record.replaces_source_id is not None
            replacements_by_original[record.replaces_source_id].append(record.source_id)
        original_results = [
            {
                "source_id": source_id,
                "status": (
                    "replacement_success"
                    if replacements_by_original.get(source_id)
                    else "unresolved_source_gap"
                ),
                "replacement_source_ids": sorted(
                    replacements_by_original.get(source_id, [])
                ),
            }
            for source_id in original_failed_ids
        ]
        generated_at = self.now()
        if generated_at.tzinfo is None or generated_at.utcoffset() is None:
            raise ValueError("report clock must be timezone-aware")
        return {
            "corpus_version": "jay-chou-corpus-v1",
            "generated_at": generated_at.isoformat(),
            "historical_candidate_count": len(existing_candidates),
            "total_discovered": len(discovered_candidates),
            "filtered_out": attempt_counts[SourceAttemptStatus.FILTERED_OUT.value],
            "duplicate_candidate": attempt_counts[
                SourceAttemptStatus.DUPLICATE_CANDIDATE.value
            ],
            "fetch_attempted": fetch_attempted,
            "fetch_success": attempt_counts[SourceAttemptStatus.SNAPSHOT_VALID.value],
            "fetch_failed": sum(failure_types.values()),
            "rate_limited": sum(rate_limits.values()),
            "rate_limit_distribution": dict(sorted(rate_limits.items())),
            "robots_denied": policy_denials,
            "replacement_success": len(replacement_records),
            "unresolved_source_gap": len(
                {
                    record.replacement_group
                    for record in attempts
                    if record.status is SourceAttemptStatus.UNRESOLVED_SOURCE_GAP
                }
            ),
            "total_valid_snapshots": len(entries),
            "file_type_counts": file_type_counts,
            "new_pdf_snapshots": new_pdf_count,
            "publisher_count": len(publishers),
            "html_publisher_count": len(html_publishers),
            "publisher_distribution": dict(sorted(publishers.items())),
            "source_tier_distribution": dict(sorted(tier_counts.items())),
            "topic_distribution": dict(sorted(topic_counts.items())),
            "covered_core_topics": covered_topics,
            "missing_core_topics": missing_topics,
            "fetch_failure_distribution": dict(sorted(failure_types.items())),
            "original_failed_source_results": original_results,
            "snapshot_validation_failures": validation_failures,
            "readiness_gates": gates,
            "ready_for_parse_normalize": all(gates.values()),
        }

    def _load_latest_raw_records(self) -> dict[str, RawSnapshotRecord]:
        path = self.raw_directory / "sources_manifest.jsonl"
        if not path.exists():
            return {}
        latest: dict[str, RawSnapshotRecord] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = RawSnapshotRecord.model_validate_json(line)
            current = latest.get(record.source_id)
            if current is None or record.fetched_at > current.fetched_at:
                latest[record.source_id] = record
        return latest

    @staticmethod
    def _failure_category(reason: str) -> str:
        code = reason_code(reason)
        if code != "unknown_failure":
            return code
        normalized = reason.casefold()
        if "robots" in normalized:
            return "robots_denied"
        if "404" in normalized:
            return "http_404"
        if "403" in normalized:
            return "http_403"
        if "timeout" in normalized or "timed out" in normalized:
            return "timeout"
        if "snapshot_validation" in normalized:
            return "snapshot_validation"
        if "content-type" in normalized:
            return "content_type"
        return "other"

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
                temporary_path = Path(handle.name)
            os.replace(temporary_path, path)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()

    @staticmethod
    def _render_markdown(report: dict[str, object]) -> str:
        gates = report["readiness_gates"]
        file_counts = report["file_type_counts"]
        publishers = report["publisher_distribution"]
        topics = report["topic_distribution"]
        failures = report["fetch_failure_distribution"]
        original_results = report["original_failed_source_results"]
        lines = [
            "# Music RAG Source Corpus V1 Report",
            "",
            "## Technical summary",
            "",
            f"Validated raw snapshots: **{report['total_valid_snapshots']}**. ",
            f"Newly discovered candidates: **{report['total_discovered']}**. ",
            f"Ready for Parse + Normalize: **{report['ready_for_parse_normalize']}**.",
            "",
            "## Completion gates",
            "",
            "| Gate | Passed |",
            "| --- | --- |",
            *[f"| {name} | {passed} |" for name, passed in gates.items()],
            "",
            "## Valid snapshot formats",
            "",
            "| File type | Documents |",
            "| --- | ---: |",
            *[f"| {name} | {count} |" for name, count in file_counts.items()],
            "",
            "## Publisher diversity",
            "",
            f"Distinct publishers: **{report['publisher_count']}**; HTML publishers: **{report['html_publisher_count']}**.",
            "",
            "| Publisher | Documents |",
            "| --- | ---: |",
            *[f"| {name} | {count} |" for name, count in publishers.items()],
            "",
            "## Core topic coverage",
            "",
            f"Missing core topics: {', '.join(report['missing_core_topics']) or 'none'}.",
            "",
            "| Topic | Documents |",
            "| --- | ---: |",
            *[f"| {name} | {count} |" for name, count in topics.items()],
            "",
            "## Fetch and validation outcomes",
            "",
            f"Attempted: {report['fetch_attempted']}; successful V2 snapshots: {report['fetch_success']}; failed attempts: {report['fetch_failed']}; replacement successes: {report['replacement_success']}.",
            f"Rate-limited attempts: {report['rate_limited']}; robots policy denials: {report['robots_denied']} (excluded from failed attempts).",
            "",
            "| Failure category | Attempts |",
            "| --- | ---: |",
            *[f"| {name} | {count} |" for name, count in failures.items()],
            "",
            "## Original 14 failed sources",
            "",
            "| Source | Outcome | Replacement |",
            "| --- | --- | --- |",
            *[
                f"| {row['source_id']} | {row['status']} | {', '.join(row['replacement_source_ids']) or '-'} |"
                for row in original_results
            ],
            "",
            "## Method and limitations",
            "",
            "Only responses that passed byte-level format, error-page, parseability, and SHA-256 checks are included. Candidate deduplication is identity-level only; semantic body deduplication remains out of scope. Robots, authentication, 403, and anti-bot controls were not bypassed. No Parse + Normalize or downstream stage was run.",
            "",
            "## Recommended next step",
            "",
            "Enter Parse + Normalize only when every completion gate above is true; otherwise resolve the listed source or diversity gaps first.",
            "",
        ]
        return "\n".join(lines)
