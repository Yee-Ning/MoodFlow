"""Preparation preserves raw identity and fails closed on ambiguous context."""

import asyncio
from hashlib import sha256
import json
from types import SimpleNamespace

import pytest

from app.rag.parse_normalize.models import NormalizedDocument
from app.rag.parse_normalize.writer import AtomicNormalizedWriter
from scripts.worldline_native_claim_preparation import ProviderTrace
from scripts.worldline_native_source_capture import attach_captures
from tests.clean_helpers import normalized_document
from tests.test_pdf_parse_contracts import _report


def parsed_source(root, raw):
    document = normalized_document("native-source", "Original fact.", title="Actual title")
    path = root / document.raw_path
    path.parent.mkdir(parents=True)
    path.write_bytes(raw)
    metadata = dict(document.metadata)
    metadata["source_metadata"] = {
        **metadata["source_metadata"], "source_id": document.source_id,
        "raw_path": document.raw_path, "title": document.title,
        "fetched_at": "2026-09-27T00:00:00Z",
        "content_hash": "sha256:" + sha256(raw).hexdigest(),
    }
    document = document.model_copy(update={"metadata": metadata,
                                          "raw_content_hash": metadata["source_metadata"]["content_hash"]})
    writer = AtomicNormalizedWriter(root)
    writer.write(document, _report().model_copy(update={"source_id": document.source_id}))
    return document, writer.normalized_root


def selection():
    return [{"source_id": "native-source", "paragraph_scope": "article p",
             "contains": "Original fact."}]


def test_capture_keeps_offsets_content_ids_and_missing_dialogue(tmp_path):
    original, normalized = parsed_source(tmp_path, b"<article><p>  Original fact.  </p></article>")
    result = attach_captures(tmp_path, normalized, selection(), tmp_path / "evidence")
    updated = NormalizedDocument.model_validate_json(
        (normalized / "native-source/document.json").read_text(encoding="utf-8"))
    assert updated.blocks == original.blocks and updated.markdown == original.markdown
    assert updated.document_id == original.document_id
    proof = json.loads((tmp_path / "evidence/native-source.capture.json").read_text())
    text = (tmp_path / "evidence/native-source.dom.txt").read_text()
    origin = proof["provenance"]
    capture = proof["capture"]
    assert text[origin["source_text_start"]:origin["source_text_end"]] == "  Original fact.  "
    assert capture["content_sha256"] == sha256(capture["content"].encode()).hexdigest()
    assert proof["interview_question"] is None and proof["speaker_binding"] is None
    assert result[0]["content_and_ids_unchanged"]


@pytest.mark.parametrize("raw", [b"<article><p>Other fact.</p></article>",
                                  b"<article><p>Original fact.</p><p>Original fact.</p></article>"])
def test_absent_or_ambiguous_paragraph_does_not_modify_metadata(tmp_path, raw):
    original, normalized = parsed_source(tmp_path, raw)
    with pytest.raises(ValueError, match="missing_or_ambiguous"):
        attach_captures(tmp_path, normalized, selection(), tmp_path / "evidence")
    updated = NormalizedDocument.model_validate_json(
        (normalized / "native-source/document.json").read_text(encoding="utf-8"))
    assert updated == original


def test_changed_raw_snapshot_rejected_before_context_write(tmp_path):
    original, normalized = parsed_source(tmp_path, b"<article><p>Original fact.</p></article>")
    (tmp_path / original.raw_path).write_bytes(b"Changed")
    with pytest.raises(ValueError, match="raw_response_sha_mismatch"):
        attach_captures(tmp_path, normalized, selection(), tmp_path / "evidence")
    assert not (tmp_path / "evidence").exists()


@pytest.mark.parametrize("fails", [False, True])
def test_provider_trace_does_not_modify_output_or_error(tmp_path, fails, monkeypatch):
    monkeypatch.setattr("scripts.worldline_native_claim_preparation.get_settings",
                        lambda: SimpleNamespace(model="unit-test-model"))
    class Delegate:
        async def generate(self, payload, **kwargs):
            assert payload == {"chunk_content": "Original fact."}
            if fails:
                raise RuntimeError("test failure")
            return '{"claims": []}'
    path = tmp_path / "trace.jsonl"
    runtime = ProviderTrace(Delegate(), "extraction", path)
    coroutine = runtime.generate({"chunk_content": "Original fact."}, schema_name="schema")
    if fails:
        with pytest.raises(RuntimeError, match="test failure"):
            asyncio.run(coroutine)
    else:
        assert asyncio.run(coroutine) == '{"claims": []}'
    event = json.loads(path.read_text())
    assert event["request_status"] == ("failed" if fails else "completed")
    assert event["latency_ms"] >= 0
    assert "api_key" not in event
