"""Prepare immutable source metadata from raw responses, without new claims or LLMs.

HTML capture offsets address verbatim decoded DOM text (no whitespace cleanup).
The existing projector performs native claim binding. Existing validators and
knowledge QA gates remain unchanged. Original corpora and releases are read-only.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

from bs4 import BeautifulSoup
import httpx
import pymupdf

from app.rag.chunk.models import ChildChunk, ParentChunk
from app.rag.enrichment.hashing import enrichment_input_hash
from app.rag.enrichment.inheritance import MetadataInheritanceResolver
from app.rag.enrichment.models import EnrichedChildChunk, EnrichedParentChunk
from app.rag.indexing import IndexDocumentProjector, load_indexing_config
from app.rag.indexing.release import KnowledgeReleaseValidator
from app.rag.knowledge.config import load_knowledge_config
from app.rag.knowledge.hashing import knowledge_input_hash
from app.rag.knowledge.models import KnowledgeEnrichedChildChunk, KnowledgeEnrichedParentChunk
from app.rag.knowledge.qa import sha256_file
from app.rag.knowledge.report import KnowledgeQualityReporter
from app.rag.knowledge.writer import KnowledgeDatasetWriter
from app.rag.source_context import DocumentContextCapture


IDENTITY_AUTHORITIES = {
    "publisher-journal": "https://www.wsp-publishing.com/en/home/",
    "title-journal": "https://www.wsp-publishing.com/zh/article/doi/10.12184/wspcyycx2WSP2516-415516.20220608/",
    "publisher-newspaper": "https://epaper.voc.com.cn/jtdb/",
}
INTERVIEW_SOURCE = "jc-v1-018"
INTERVIEW_CLAIM = "claim:33051d48b804d35fe8b36525"
QUESTION_LABEL = "主持人："
RESPONDENT_LABEL = "方文山："


def confined(root: Path, path: str | Path) -> Path:
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError("source_path_outside_project")
    return resolved


def write_once(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != content:
            raise ValueError("immutable_preparation_artifact_changed:" + path.name)
        return
    with path.open("xb") as handle:
        handle.write(content)


def write_json(path: Path, value) -> None:
    write_once(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())


def html_text(raw: bytes) -> tuple[BeautifulSoup, str]:
    soup = BeautifulSoup(raw, "html.parser")
    for node in soup(["script", "style"]):
        node.decompose()
    return soup, soup.get_text(separator="", strip=False)


def unique_offset(text: str, span: str) -> int:
    if not span or text.count(span) != 1:
        raise ValueError("source_span_missing_or_ambiguous")
    return text.index(span)


def verified_raw(root: Path, metadata: dict) -> tuple[Path, bytes]:
    path = confined(root, metadata["raw_path"])
    if not path.is_relative_to(root / "data" / "raw"):
        raise ValueError("source_must_be_raw_response")
    raw = path.read_bytes()
    if "sha256:" + sha256(raw).hexdigest() != metadata["content_hash"]:
        raise ValueError("raw_response_sha_mismatch")
    return path, raw


def acquire_authority(root: Path, raw_root: Path, name: str) -> dict:
    url = IDENTITY_AUTHORITIES[name]
    body_path = raw_root / (name + ".html")
    record_path = raw_root / (name + ".json")
    if record_path.is_file():
        record = json.loads(record_path.read_text(encoding="utf-8"))
        if record["request_url"] != url or record["response_sha256"] != sha256(body_path.read_bytes()).hexdigest():
            raise ValueError("identity_authority_cached_response_changed")
        return record
    response = httpx.get(url, follow_redirects=True, timeout=25, trust_env=False)
    response.raise_for_status()
    write_once(body_path, response.content)
    record = {
        "request_url": url, "response_url": str(response.url),
        "http_status": response.status_code,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "raw_path": body_path.relative_to(root).as_posix(),
        "response_sha256": sha256(response.content).hexdigest(),
    }
    write_json(record_path, record)
    return record


def identity_proof(raw_record: dict, text: str, span: str, locator: str, *, value: str | None = None) -> dict:
    start = unique_offset(text, span)
    normalized = " ".join(span.split()) if value is None else value
    if normalized not in span and normalized != " ".join(span.split()):
        raise ValueError("identity_value_not_verbatim_or_whitespace_joined")
    return {
        **raw_record, "value": normalized, "exact_span": span,
        "start": start, "end": start + len(span), "locator": locator,
        "extracted_text": text, "extracted_text_sha256": sha256(text.encode()).hexdigest(),
        "offset_unit": "unicode_code_point_in_extracted_response_text",
    }


def complete_identity(root: Path, source_id: str, metadata: dict, raw: bytes, raw_root: Path) -> tuple[dict, dict]:
    result = deepcopy(metadata)
    proofs = {}
    raw_record = {
        "raw_path": metadata["raw_path"],
        "response_url": metadata.get("url") or metadata["original_url"],
        "response_sha256": metadata["content_hash"].removeprefix("sha256:"),
        "captured_at": metadata["fetched_at"],
    }
    if not result.get("publisher") and source_id in {
        "jc-v2-016", "jc-v2-020", "jc-v2-022", "jc-v2-025", "jc-v2-026", "jc-v2-029", "jc-v2-033",
    }:
        soup, _ = html_text(raw)
        nodes = soup.select('a[href="https://wikimediafoundation.org"]')
        if len(nodes) != 1:
            raise ValueError("wikipedia_publisher_identity_not_explicit")
        span = nodes[0].get_text()
        proofs["publisher"] = identity_proof(raw_record, span, span, 'a[href="https://wikimediafoundation.org"]')
    elif not result.get("publisher") and source_id == "jc-v2-009":
        soup, _ = html_text(raw)
        nodes = soup.select('a[href="http://www.ifeng.com/corp/about/intro/"]')
        if len(nodes) != 1:
            raise ValueError("phoenix_publisher_identity_not_explicit")
        label = nodes[0].get_text()
        proofs["publisher"] = identity_proof(raw_record, label, "凤凰新媒体", 'a[href="http://www.ifeng.com/corp/about/intro/"]')
    elif source_id == "jc-v2-045":
        with pymupdf.open(stream=raw, filetype="pdf") as document:
            text = document[0].get_text()
        proofs["publisher"] = identity_proof(raw_record, text, "UNIVERSITY OF MALAYA", "PDF page 1 / cover institution")
        start = unique_offset(text, "JAY CHOU'S ZHONGGUO FENG:")
        end = unique_offset(text, "SONGS AND IDENTITY") + len("SONGS AND IDENTITY")
        proofs["title"] = identity_proof(raw_record, text, text[start:end], "PDF page 1 / cover title")
    elif source_id == "jc-v2-046":
        publisher = acquire_authority(root, raw_root, "publisher-journal")
        title = acquire_authority(root, raw_root, "title-journal")
        publisher_soup, _ = html_text(confined(root, publisher["raw_path"]).read_bytes())
        title_soup, title_text = html_text(confined(root, title["raw_path"]).read_bytes())
        if "10.12184/wspcyycx2WSP2516-415516.20220608" not in title_text:
            raise ValueError("journal_authority_doi_mismatch")
        for field, record, soup in [("publisher", publisher, publisher_soup), ("title", title, title_soup)]:
            if soup.title is None:
                raise ValueError("identity_authority_title_missing")
            span = soup.title.get_text()
            proofs[field] = identity_proof(record, span, span, "HTML title metadata")
        proofs["publisher"]["scope"] = "current_publishing_platform_identity; not a claim about historical corporate ownership"
    elif source_id == "jc-v2-048":
        publisher = acquire_authority(root, raw_root, "publisher-newspaper")
        soup, _ = html_text(confined(root, publisher["raw_path"]).read_bytes())
        if soup.title is None or "家庭导报" not in soup.title.get_text():
            raise ValueError("newspaper_authority_identity_mismatch")
        span = soup.title.get_text()
        proofs["publisher"] = identity_proof(publisher, span, "湖南日报报业集团", "HTML title metadata / publication owner")
        with pymupdf.open(stream=raw, filetype="pdf") as document:
            text = document[0].get_text()
        proofs["title"] = identity_proof(raw_record, text, "周杰伦：为妈妈和外婆而歌唱", "PDF page 1 / article headline")
    if not result.get("title") and "title" not in proofs and Path(metadata["raw_path"]).suffix.lower() == ".html":
        soup, _ = html_text(raw)
        if soup.title is not None:
            span = soup.title.get_text()
            proofs["title"] = identity_proof(raw_record, span, span, "HTML title metadata")
    for field, proof in proofs.items():
        if result.get(field) and result[field] != proof["value"]:
            raise ValueError("existing_explicit_source_identity_conflict")
        result[field] = proof["value"]
    return result, proofs


def make_capture(metadata: dict, source_url: str, text: str, start: int, end: int, segments: list[dict], locator: str) -> dict:
    content = text[start:end]
    if len(content) > 6000 or start < 0 or end <= start:
        raise ValueError("capture_size_or_offsets_invalid")
    payload = {
        "source_url": source_url, "publisher": metadata["publisher"], "title": metadata["title"],
        "locator": locator, "captured_at": metadata["fetched_at"],
        "capture_method": "verbatim_web_excerpt",
        "response_sha256": metadata["content_hash"].removeprefix("sha256:"),
        "content": content, "content_sha256": sha256(content.encode()).hexdigest(), "segments": segments,
    }
    return DocumentContextCapture.model_validate(payload).model_dump(mode="json")


def capture_html(metadata: dict, source_url: str, raw: bytes, claims: list[dict]) -> tuple[str, list[dict], list[dict], list[dict]]:
    soup, text = html_text(raw)
    paragraphs = soup.find_all("p")
    captures, provenance, gaps = [], [], []
    by_content = set()
    for claim in claims:
        span = claim["evidence_span"]
        nodes = [(i, p.get_text()) for i, p in enumerate(paragraphs) if span in p.get_text()]
        if len(nodes) != 1 or text.count(nodes[0][1]) != 1:
            gaps.append({"claim_id": claim["claim_id"], "reason": "no_unique_verbatim_HTML_paragraph"})
            continue
        index, paragraph = nodes[0]
        if metadata["source_id"] == INTERVIEW_SOURCE:
            if claim["claim_id"] != INTERVIEW_CLAIM:
                gaps.append({"claim_id": claim["claim_id"], "reason": "interview_turn_not_explicitly_selected"})
                continue
            question = paragraphs[index - 1].get_text() if index else ""
            if not question.lstrip().startswith(QUESTION_LABEL) or not paragraph.lstrip().startswith(RESPONDENT_LABEL):
                raise ValueError("interview_explicit_speaker_or_adjacent_question_missing")
            start, answer_start = unique_offset(text, question), unique_offset(text, paragraph)
            if start + len(question) > answer_start:
                raise ValueError("interview_question_not_before_answer")
            end = answer_start + len(paragraph)
            content = text[start:end]
            speaker_start = unique_offset(content, RESPONDENT_LABEL)
            segments = [
                {"role": "interview_question", "exact_span": question, "start": 0, "end": len(question)},
                {"role": "interview_answer", "exact_span": paragraph, "start": answer_start - start, "end": end - start},
                {"role": "speaker_binding", "exact_span": RESPONDENT_LABEL, "start": speaker_start, "end": speaker_start + len(RESPONDENT_LABEL)},
            ]
            locator = f"DOM p[{index}] + p[{index + 1}]; decoded text [{start}:{end}]"
        else:
            if paragraph.lstrip().startswith((QUESTION_LABEL, RESPONDENT_LABEL, "网友：")):
                gaps.append({"claim_id": claim["claim_id"], "reason": "dialogue_not_article_context"})
                continue
            start = unique_offset(text, paragraph)
            end = start + len(paragraph)
            segments = [{"role": "article_sentence", "exact_span": paragraph, "start": 0, "end": len(paragraph)}]
            locator = f"DOM p[{index + 1}]; decoded text [{start}:{end}]"
        if len(text[start:end]) > 6000:
            gaps.append({"claim_id": claim["claim_id"], "reason": "verbatim_paragraph_exceeds_capture_limit"})
            continue
        key = sha256(text[start:end].encode()).hexdigest()
        if key in by_content:
            continue
        if len(captures) >= 8:
            gaps.append({"claim_id": claim["claim_id"], "reason": "document_capture_limit"})
            continue
        capture = make_capture(metadata, source_url, text, start, end, segments, locator)
        captures.append(capture)
        provenance.append({
            "content_sha256": capture["content_sha256"], "response_sha256": capture["response_sha256"],
            "raw_path": metadata["raw_path"], "source_text_start": start, "source_text_end": end,
            "source_text_sha256": sha256(text.encode()).hexdigest(), "locator": locator,
            "extraction_method": "BeautifulSoup HTML parser; remove script/style; get_text separator='' strip=False",
            "offset_unit": "unicode_code_point_in_verbatim_decoded_DOM_text",
        })
        by_content.add(key)
    return text, captures, provenance, gaps


def prepare(root: Path, input_path: Path, output: Path) -> dict:
    input_path, output = confined(root, input_path), confined(root, output)
    if input_path.is_relative_to(output):
        raise ValueError("preparation_must_not_replace_source")
    if (output / "working-knowledge").exists():
        raise ValueError("preparation_working_dataset_already_exists")
    KnowledgeReleaseValidator(root, knowledge_path=input_path).validate()
    raw_root = root / "data/raw/worldline-source-context-v4-20260926"
    inputs = []
    doc_chunks = defaultdict(list)
    for line in input_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        model = KnowledgeEnrichedChildChunk if value.get("parent_id") else KnowledgeEnrichedParentChunk
        chunk = model.model_validate(value)
        inputs.append(chunk)
        doc_chunks[chunk.doc_id].append(chunk)
    documents, document_metadata = [], {}
    for doc_id, chunks in sorted(doc_chunks.items()):
        original = chunks[0].metadata["document_metadata"]
        metadata = original["source_metadata"]
        source = chunks[0].enrichment.source
        if metadata["source_id"] != source.source_id:
            raise ValueError("raw_metadata_native_source_id_mismatch")
        path, raw = verified_raw(root, metadata)
        fixed, proofs = complete_identity(root, source.source_id, metadata, raw, raw_root)
        source_url = str(source.source_url or "")
        if source_url not in {metadata.get("original_url"), metadata.get("url")}:
            raise ValueError("raw_metadata_native_source_url_mismatch")
        claims = [v.claim.model_dump(mode="json") for c in chunks for v in c.knowledge.claims]
        if path.suffix.lower() == ".html" and all(fixed.get(k) for k in ["title", "publisher"]):
            text, captures, origins, gaps = capture_html(fixed, source_url, raw, claims)
            text_path = output / "verbatim_source_text" / (source.source_id + ".txt")
            write_once(text_path, text.encode())
        else:
            captures, origins = [], []
            gaps = [{"claim_id": c["claim_id"], "reason": "no_verbatim_HTML_paragraph; preserve_native_formatted_claim_without_context"} for c in claims]
        for capture, origin in zip(captures, origins, strict=True):
            if capture["response_sha256"] != sha256(raw).hexdigest():
                raise ValueError("capture_upstream_response_sha_mismatch")
            text_bytes = (output / "verbatim_source_text" / (source.source_id + ".txt")).read_bytes()
            source_text = text_bytes.decode()
            if source_text[origin["source_text_start"]:origin["source_text_end"]] != capture["content"]:
                raise ValueError("capture_absolute_source_offsets_mismatch")
        patched = deepcopy(original)
        patched["source_metadata"] = fixed
        patched["source_context_captures"] = captures
        patched["source_context_capture_provenance"] = origins
        patched["source_identity_proofs"] = proofs
        patched["source_context_preparation_version"] = "worldline-verbatim-v4-20260926"
        document_metadata[doc_id] = patched
        documents.append({
            "doc_id": doc_id, "source_id": source.source_id, "source_url": source_url,
            "publisher_before": metadata.get("publisher"), "publisher": fixed.get("publisher"),
            "title_before": metadata.get("title") or chunks[0].metadata.get("title"), "title": fixed.get("title") or chunks[0].metadata.get("title"),
            "raw_path": metadata["raw_path"], "response_sha256": sha256(raw).hexdigest(),
            "response_sha_verified": True, "locator_present": all(c["locator"] for c in captures) if captures else bool(chunks[0].section_path or chunks[0].breadcrumb),
            "capture_count": len(captures), "capture_roles": sorted({s["role"] for c in captures for s in c["segments"]}),
            "identity_proofs": proofs, "claim_context_gaps": gaps,
            "claims": len(claims), "native_bound_context_count": 0,
            "interview_qa": "captured_explicit_turn" if any(s["role"] == "interview_question" for c in captures for s in c["segments"]) else "not_captured; no_question_or_respondent_inferred",
        })
    config = load_knowledge_config(root / "app/rag/knowledge/config/v13.yaml")
    resolver = MetadataInheritanceResolver()
    enriched, knowledge = [], []
    parents = {}
    original_hashes = []
    for old in sorted(inputs, key=lambda c: (c.parent_id is not None, c.doc_id, c.chunk_index, c.chunk_id)):
        payload = old.model_dump(mode="json")
        payload["metadata"]["document_metadata"] = document_metadata[old.doc_id]
        publisher = document_metadata[old.doc_id]["source_metadata"].get("publisher")
        if publisher:
            payload["metadata"]["source_name"] = publisher
            payload["enrichment"]["source"]["source_name"] = publisher
        base_type = ChildChunk if old.parent_id else ParentChunk
        base = base_type.model_validate({k: v for k, v in payload.items() if k not in {"enrichment", "knowledge"}})
        context = resolver.resolve(base, parent=parents.get(old.parent_id))
        payload["enrichment"]["provenance"]["input_hash"] = enrichment_input_hash(
            base, title=context.title, inherited_metadata=context.inherited_metadata,
            parent_primary_subject=context.parent_primary_subject,
            enrichment_version=old.enrichment.provenance.enrichment_version,
        )
        enriched_type = EnrichedChildChunk if old.parent_id else EnrichedParentChunk
        updated = enriched_type.model_validate({k: v for k, v in payload.items() if k != "knowledge"})
        payload["knowledge"]["provenance"]["knowledge_input_hash"] = knowledge_input_hash(updated, config)
        knowledge_type = KnowledgeEnrichedChildChunk if old.parent_id else KnowledgeEnrichedParentChunk
        new = knowledge_type.model_validate(payload)
        if new.content != old.content or new.knowledge.claims != old.knowledge.claims or new.knowledge.entities != old.knowledge.entities:
            raise ValueError("metadata_preparation_changed_native_evidence_or_decisions")
        if old.parent_id is None:
            parents[old.chunk_id] = updated
        enriched.append(updated)
        knowledge.append(new)
        original_hashes.append({"chunk_id": old.chunk_id, "previous_enrichment_input_hash": old.enrichment.provenance.input_hash, "previous_knowledge_input_hash": old.knowledge.provenance.knowledge_input_hash, "enrichment_input_hash": updated.enrichment.provenance.input_hash, "knowledge_input_hash": new.knowledge.provenance.knowledge_input_hash})
    working = output / "working-knowledge"
    if working.exists():
        raise ValueError("preparation_working_dataset_already_exists")
    working.mkdir(parents=True)
    enriched_path = output / "enriched_chunks.jsonl"
    write_once(enriched_path, ("\n".join(c.model_dump_json() for c in enriched) + "\n").encode())
    KnowledgeDatasetWriter(root, working).write(knowledge)
    KnowledgeQualityReporter(root, working).write(knowledge)
    projector = IndexDocumentProjector(load_indexing_config(root / "app/rag/indexing/config/worldline_v4.yaml"))
    native = []
    by_id = {c.chunk_id: c for c in knowledge}
    documents_by_id = {d["doc_id"]: d for d in documents}
    for chunk in knowledge:
        if chunk.parent_id is None:
            continue
        projected = projector.project(chunk, by_id[chunk.parent_id], source_release_id="source-context-preparation-unpublished")
        packets = projected.payload["relation_source_contexts"]
        documents_by_id[chunk.doc_id]["native_bound_context_count"] += len(packets)
        for packet in packets:
            native.append({"doc_id": chunk.doc_id, **packet})
    summary = {
        "source_context_preparation_version": "worldline-verbatim-v4-20260926",
        "input_knowledge_path": input_path.relative_to(root).as_posix(), "input_knowledge_sha256": sha256_file(input_path),
        "enriched_path": enriched_path.relative_to(root).as_posix(), "enriched_sha256": sha256_file(enriched_path),
        "working_knowledge_root": working.relative_to(root).as_posix(),
        "document_count": len(documents), "chunk_count": len(knowledge), "claim_count": sum(len(c.knowledge.claims) for c in knowledge),
        "publisher_complete": all(d["publisher"] for d in documents), "title_complete": all(d["title"] for d in documents),
        "capture_count": sum(d["capture_count"] for d in documents), "native_bound_context_count": len(native),
        "native_role_counts": dict(Counter(s["role"] for c in native for s in c["segments"])),
        "original_source_chunk_claim_ids_and_evidence_unchanged": True,
        "semantic_decisions_reused_unchanged": True, "new_claim_extraction_or_LLM_rewriting_performed": False,
        "eval_gold_or_review_pack_used_as_source_data": False,
        "documents": documents,
    }
    write_json(output / "preparation.json", summary)
    write_json(output / "document_metadata.json", document_metadata)
    write_json(output / "native_context_bindings.json", native)
    write_json(output / "input_hash_lineage.json", original_hashes)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--knowledge-source", type=Path, default=Path("data/knowledge/worldline/v4/knowledge_enriched_chunks.jsonl"))
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    summary = prepare(root, args.knowledge_source, args.output_root)
    print(json.dumps({k: v for k, v in summary.items() if k != "documents"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
