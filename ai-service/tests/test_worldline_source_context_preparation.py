"""Source preparation uses only verbatim raw evidence; no evaluator or Gold."""

from hashlib import sha256

import pytest

from app.rag.indexing import IndexDocumentProjector
from app.rag.source_context import DocumentContextCapture
from scripts.worldline_source_context_preparation import (
    INTERVIEW_CLAIM,
    capture_html,
    confined,
    identity_proof,
    verified_raw,
    write_once,
)
from scripts.worldline_source_context_smoke import query_plan, verify_raw_origin
from tests.test_rag_indexing import _child, _config, _parent


def source(raw, source_id="independent-article"):
    return {
        "source_id": source_id, "title": "Independent source", "publisher": "Engineering publisher",
        "fetched_at": "2026-09-26T00:00:00Z", "raw_path": "data/raw/engineering.html",
        "content_hash": "sha256:" + sha256(raw).hexdigest(),
    }


def test_verbatim_interview_keeps_question_full_answer_speaker_and_absolute_offsets():
    raw = "<html><p>　　主持人：这些作品如何合作？</p>\n<p>　　方文山：先有曲，再填词。第二句也必须保留。</p></html>".encode()
    metadata = source(raw, "jc-v1-018")
    text, captures, provenance, gaps = capture_html(metadata, "https://example.org/interview", raw, [
        {"claim_id": INTERVIEW_CLAIM, "evidence_span": "方文山：先有曲，再填词。"},
    ])
    assert not gaps and len(captures) == 1
    capture = DocumentContextCapture.model_validate(captures[0])
    assert capture.response_sha256 == sha256(raw).hexdigest()
    roles = {s.role: s for s in capture.segments}
    assert set(roles) == {"interview_question", "interview_answer", "speaker_binding"}
    assert roles["interview_question"].exact_span == "　　主持人：这些作品如何合作？"
    assert roles["interview_answer"].exact_span.endswith("第二句也必须保留。")
    assert roles["speaker_binding"].exact_span == "方文山："
    origin = provenance[0]
    assert text[origin["source_text_start"]:origin["source_text_end"]] == capture.content
    assert capture.content_sha256 == sha256(capture.content.encode()).hexdigest()


@pytest.mark.parametrize("question,answer", [
    ("没有问句。", "方文山：先有曲，再填词。"),
    ("主持人：如何合作？", "不明人士：先有曲，再填词。"),
])
def test_interview_does_not_guess_question_or_speaker(question, answer):
    raw = f"<p>{question}</p><p>{answer}</p>".encode()
    with pytest.raises(ValueError, match="explicit_speaker_or_adjacent_question_missing"):
        capture_html(source(raw, "jc-v1-018"), "https://example.org/interview", raw, [
            {"claim_id": INTERVIEW_CLAIM, "evidence_span": "先有曲，再填词。"},
        ])


def test_article_preserves_subject_sentence_whitespace_entities_and_no_invented_interview():
    raw = '<p>  Original Artist &amp; Collaborator released an album.  </p>'.encode()
    text, captures, origins, gaps = capture_html(source(raw), "https://example.org/article", raw, [
        {"claim_id": "claim:engineering", "evidence_span": "released an album."},
    ])
    assert not gaps
    packet = captures[0]
    assert packet["content"] == "  Original Artist & Collaborator released an album.  "
    assert [s["role"] for s in packet["segments"]] == ["article_sentence"]
    assert packet["content"] == text[origins[0]["source_text_start"]:origins[0]["source_text_end"]]


def test_missing_or_ambiguous_raw_context_stays_missing():
    raw = b"<p>Original fact.</p><p>Original fact.</p>"
    _, captures, _, gaps = capture_html(source(raw), "https://example.org/article", raw, [
        {"claim_id": "claim:ambiguous", "evidence_span": "Original fact."},
        {"claim_id": "claim:absent", "evidence_span": "Not in the source."},
    ])
    assert captures == [] and len(gaps) == 2


def test_raw_body_tampering_and_gold_paths_are_rejected(tmp_path):
    raw = tmp_path / "data/raw/engineering.html"
    raw.parent.mkdir(parents=True)
    raw.write_bytes(b"original")
    metadata = source(b"original")
    assert verified_raw(tmp_path, metadata)[1] == b"original"
    raw.write_bytes(b"changed")
    with pytest.raises(ValueError, match="raw_response_sha_mismatch"):
        verified_raw(tmp_path, metadata)
    with pytest.raises(ValueError, match="source_must_be_raw_response"):
        verified_raw(tmp_path, {**metadata, "raw_path": "evals/gold/engineering.json"})
    with pytest.raises(ValueError, match="outside_project"):
        confined(tmp_path, "../outside.html")


def test_identity_requires_actual_literal_evidence_and_keeps_original_locator():
    text = "Publication name — Actual Publisher"
    proof = identity_proof({}, text, "Actual Publisher", "HTML title / owner")
    assert text[proof["start"]:proof["end"]] == proof["value"]
    assert proof["extracted_text_sha256"] == sha256(text.encode()).hexdigest()
    with pytest.raises(ValueError, match="source_span_missing_or_ambiguous"):
        identity_proof({}, text, "Assumed publisher", "HTML title")
    with pytest.raises(ValueError, match="not_verbatim"):
        identity_proof({}, text, "Actual Publisher", "HTML title", value="Other publisher")


def test_immutable_artifact_cannot_replace_old_release(tmp_path):
    path = tmp_path / "release.json"
    write_once(path, b"approved")
    write_once(path, b"approved")
    with pytest.raises(ValueError, match="immutable_preparation_artifact_changed"):
        write_once(path, b"changed")
    assert path.read_bytes() == b"approved"


def test_explicit_raw_title_reaches_payload_without_breadcrumb_or_qa_title_rewriting():
    child = _child().model_copy(update={"metadata": {
        "title": None, "document_metadata": {"source_metadata": {"title": "Actual source headline"}},
    }})
    payload = IndexDocumentProjector(_config()).project(child, _parent(), source_release_id="engineering-source-release").payload
    assert payload["source_title"] == "Actual source headline"
    assert child.metadata["title"] is None


def test_upstream_response_hash_and_absolute_offsets_cannot_be_changed(tmp_path):
    raw = b"<p>Original Artist released an album.</p>"
    path = tmp_path / "data/raw/engineering.html"
    path.parent.mkdir(parents=True)
    path.write_bytes(raw)
    metadata = source(raw)
    _, captures, origins, _ = capture_html(metadata, "https://example.org/article", raw, [
        {"claim_id": "claim:engineering", "evidence_span": "released an album."},
    ])
    document_metadata = {"source_metadata": metadata, "source_context_captures": captures,
                         "source_context_capture_provenance": origins}
    verify_raw_origin(tmp_path, captures[0], document_metadata)
    with pytest.raises(ValueError, match="upstream_response_sha_mismatch"):
        verify_raw_origin(tmp_path, {**captures[0], "response_sha256": "0" * 64}, document_metadata)
    with pytest.raises(ValueError, match="changed_from_original_capture"):
        verify_raw_origin(tmp_path, {**captures[0], "content": "Rewritten text."}, document_metadata)
    origins[0]["source_text_end"] -= 1
    with pytest.raises(ValueError, match="absolute_source_offsets_mismatch"):
        verify_raw_origin(tmp_path, captures[0], document_metadata)


def test_real_packet_smoke_uses_existing_verified_evidence_filter_without_source_id_override():
    from types import SimpleNamespace

    plan = query_plan(SimpleNamespace(index_profile=None), "Independent source query", ["artist:engineering"])
    conditions = {c.field.value: c.value for c in plan.metadata_filter.root.children}
    assert conditions == {"verification_statuses": "SUPPORTED", "entity_ids": "artist:engineering"}
    assert plan.strategy.value == "hybrid"
    assert plan.queries[0].channel.value == "both"


def pack_engineering_source(child, parent, query):
    from types import SimpleNamespace

    from app.rag.context_packing import ContextPackBuilder
    from app.rag.retrieval.fusion import FusedSearchHit
    from app.rag.retrieval.models import ChannelEvidence, RetrievalResult
    from app.rag.retrieval.selection import _candidate_from_hit
    from tests.test_worldline_runtime_integration import profile

    projected = IndexDocumentProjector(_config()).project(child, parent, source_release_id="engineering-source-context")
    evidence = ChannelEvidence(
        chunk_id=child.chunk_id, subquery_id="q0:original", retrieval_leg="q0:original:dense",
        variant_index=0, variant_kind="original", channel="dense", rank=1, raw_score=1, weight=1,
    )
    candidate = _candidate_from_hit(FusedSearchHit(projected.point_id, 0.5, projected.payload, [evidence]))
    plan = query_plan(SimpleNamespace(index_profile=profile()), query, ["artist:a"])
    result = RetrievalResult(status="success", plan_id=plan.plan_id, index_profile=profile(), candidates=[candidate])
    return ContextPackBuilder().build(plan, result), projected.payload["relation_source_contexts"]


def test_child_spanning_parent_paragraphs_does_not_duplicate_article_anchor():
    from tests.test_worldline_runtime_integration import native_inputs

    child, parent = native_inputs()[0]
    span = child.knowledge.claims[0].claim.evidence_span
    child = child.model_copy(update={"content": "Earlier source paragraph.\n\n" + child.content + "\n\nFinal heading"})
    parent = parent.model_copy(update={"content": "Other background.\n\n" + span + "\n\nOther ending."})
    pack, contexts = pack_engineering_source(child, parent, span)
    assert len(pack.items) == 1
    assert pack.items[0].context.count(span) == 1
    assert [c.model_dump(mode="json") for c in pack.items[0].relation_source_contexts] == contexts


def test_long_parent_view_yields_space_to_entire_interview_packet_within_same_budget():
    from tests.test_worldline_runtime_integration import native_inputs

    child, parent = native_inputs()[1]
    parent = parent.model_copy(update={"content": child.content + " " + "detail " * 1200})
    pack, contexts = pack_engineering_source(child, parent, child.content)
    assert len(pack.items) == 1
    assert [c.model_dump(mode="json") for c in pack.items[0].relation_source_contexts] == contexts
    assert pack.items[0].token_count <= 900
    assert pack.token_budget == 3000
    assert pack.items[0].source_evidence_content == child.content


def test_oversize_interview_is_omitted_whole_without_changing_limits_or_fabricating_answer_only():
    from copy import deepcopy
    from tests.test_worldline_runtime_integration import native_inputs

    child, parent = native_inputs()[1]
    metadata = deepcopy(child.metadata)
    capture = metadata["document_metadata"]["source_context_captures"][0]
    capture["content"] += " z" * 1000
    capture["content_sha256"] = sha256(capture["content"].encode()).hexdigest()
    child = child.model_copy(update={"metadata": metadata})
    pack, _ = pack_engineering_source(child, parent, child.content)
    assert pack.items[0].relation_source_contexts == []
    assert any(w.startswith("source_context_budget_omitted:") for w in pack.warnings)
    assert pack.items[0].token_count <= 900
