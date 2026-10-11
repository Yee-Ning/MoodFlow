"""Absent legacy audit fields and collected RAG audit retain their own shape."""

from copy import deepcopy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.agent.foundation.context_contracts import PackManifest, SourceManifest
from tests.test_context_rag_source import collected, pack, source


@pytest.fixture
def legacy_pack():
    report = Path(__file__).resolve().parents[2] / (
        "docs/reports/context-engineering-phase2-20261005/manifest-example.json"
    )
    manifest = json.loads(report.read_text(encoding="utf-8"))["manifest"]
    return {key: manifest[key] for key in ("tokens", "budget", "sources")}


@pytest.mark.parametrize("mode", ["python", "json"])
def test_legacy_pack_and_sources_roundtrip_exactly(legacy_pack, mode):
    original = deepcopy(legacy_pack)
    projection = PackManifest.model_validate_json(json.dumps(original))
    assert projection.model_dump(mode=mode) == original
    assert json.loads(projection.model_dump_json()) == original
    for row in original["sources"].values():
        assert SourceManifest.model_validate(row).model_dump(mode=mode) == row
    assert legacy_pack == original


@pytest.mark.parametrize("audit", [
    {"chunks": []},
    {"model_usage": []},
    {"collection": None},
    {"chunks": [], "model_usage": [], "collection": None},
])
def test_explicit_empty_audit_fields_are_preserved(legacy_pack, audit):
    row = {**next(iter(legacy_pack["sources"].values())), **audit}
    projection = SourceManifest.model_validate(row)
    assert projection.model_dump() == row
    assert json.loads(projection.model_dump_json()) == row


def test_collected_rag_audit_roundtrip_retains_chunks_usage_and_collection():
    items, _ = collected(source(contents=["相同证据", "相同证据", "另一段证据"]))
    result = pack(items)
    original = deepcopy(result.manifest)
    audit = original["sources"]["rag"]
    assert len(audit["chunks"]) == 3
    assert audit["chunks"][1]["included"] is False
    assert audit["chunks"][1]["reasons"]
    assert [row["stage"] for row in audit["model_usage"]] == ["embedding", "rerank"]
    assert audit["collection"]["rerank_status"] == "success"
    # Existing nested DTOs normalize absent optional references to null. Keep
    # that contract, while comparing every aggregate and RAG audit value.
    expected = deepcopy(original)
    for row in expected["sources"].values():
        for event in row["events"]:
            for key in ("task_id", "session_id", "request_id"):
                event.setdefault(key, None)
        for usage in row.get("model_usage", []):
            usage.setdefault("usage_ref", None)
    projection = PackManifest.model_validate_json(json.dumps(original))
    assert projection.model_dump(mode="json") == expected
    assert json.loads(projection.model_dump_json()) == expected
    assert result.manifest == original


def test_audit_content_added_to_an_absent_field_is_not_discarded(legacy_pack):
    items, _ = collected(source(count=1))
    audit = PackManifest.model_validate(pack(items).manifest).sources["rag"]
    projection = SourceManifest.model_validate(next(iter(legacy_pack["sources"].values())))
    assert "chunks" not in projection.model_fields_set
    projection.chunks.extend(audit.chunks)
    dumped = projection.model_dump(mode="json")
    assert dumped["chunks"] == audit.model_dump(mode="json")["chunks"]
    assert "model_usage" not in dumped and "collection" not in dumped


def test_required_aggregate_fields_and_extra_forbid_remain_strict(legacy_pack):
    row = next(iter(legacy_pack["sources"].values()))
    for field in ("candidate_tokens", "final_tokens", "events", "dedup_dropped"):
        with pytest.raises(ValidationError):
            SourceManifest.model_validate({key: value for key, value in row.items() if key != field})
    with pytest.raises(ValidationError):
        SourceManifest.model_validate({**row, "unknown_audit": []})
