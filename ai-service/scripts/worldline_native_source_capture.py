"""Attach audited verbatim HTML context to freshly parsed production documents.

Selections identify paragraphs in immutable raw responses, never claims or
predicates. Normalized content and IDs are preserved; claim extraction remains
the responsibility of KnowledgePipeline.
"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from pathlib import Path

from app.rag.parse_normalize.models import NormalizedDocument, ParseReport
from app.rag.parse_normalize.writer import AtomicNormalizedWriter
from scripts.worldline_source_context_preparation import (
    html_text,
    make_capture,
    unique_offset,
    verified_raw,
    write_json,
    write_once,
)


def attach_captures(
    root: Path, normalized_root: Path, selections: list[dict], evidence_root: Path
) -> list[dict]:
    root = root.resolve()
    normalized_root = normalized_root.resolve()
    evidence_root = evidence_root.resolve()
    if not normalized_root.is_relative_to(root) or not evidence_root.is_relative_to(root):
        raise ValueError("preparation_path_outside_project")
    writer = AtomicNormalizedWriter(root)
    writer.normalized_root = normalized_root
    results = []
    for selection in selections:
        directory = (normalized_root / selection["source_id"]).resolve()
        if not directory.is_relative_to(normalized_root):
            raise ValueError("source_path_outside_preparation")
        document = NormalizedDocument.model_validate_json(
            (directory / "document.json").read_text(encoding="utf-8")
        )
        metadata = document.metadata["source_metadata"]
        if (document.source_id != selection["source_id"]
                or metadata["source_id"] != document.source_id):
            raise ValueError("native_source_id_mismatch")
        _, raw = verified_raw(root, metadata)
        if document.raw_content_hash != "sha256:" + sha256(raw).hexdigest():
            raise ValueError("document_raw_sha_mismatch")
        soup, text = html_text(raw)
        paragraphs = soup.select(selection["paragraph_scope"])
        matches = [p.get_text() for p in paragraphs if selection["contains"] in p.get_text()]
        if len(matches) != 1:
            raise ValueError("selected_original_paragraph_missing_or_ambiguous")
        paragraph = matches[0]
        start = unique_offset(text, paragraph)
        end = start + len(paragraph)
        locator = f'{selection["paragraph_scope"]}; decoded DOM text [{start}:{end}]'
        capture = make_capture(
            metadata, metadata["original_url"], text, start, end,
            [{"role": "article_sentence", "exact_span": paragraph,
              "start": 0, "end": len(paragraph)}], locator,
        )
        provenance = {
            "raw_path": metadata["raw_path"],
            "response_sha256": sha256(raw).hexdigest(),
            "content_sha256": capture["content_sha256"],
            "source_text_start": start, "source_text_end": end,
            "source_text_sha256": sha256(text.encode()).hexdigest(),
            "locator": locator,
            "offset_unit": "unicode_code_point_in_verbatim_decoded_DOM_text",
            "extraction_method": "BeautifulSoup; remove script/style; separator='' strip=False",
        }
        updated_metadata = deepcopy(document.metadata)
        existing = updated_metadata.get("source_context_captures")
        if existing and existing != [capture]:
            raise ValueError("existing_source_context_would_be_replaced")
        updated_metadata["source_context_captures"] = [capture]
        updated_metadata["source_context_capture_provenance"] = [provenance]
        updated_metadata["source_context_preparation_version"] = "worldline-native-v2-20260927"
        updated = document.model_copy(update={"metadata": updated_metadata})
        assert updated.markdown == document.markdown and updated.blocks == document.blocks
        assert updated.document_id == document.document_id
        report = ParseReport.model_validate_json(
            (directory / "parse_report.json").read_text(encoding="utf-8")
        )
        write_once(evidence_root / (document.source_id + ".dom.txt"), text.encode())
        write_json(evidence_root / (document.source_id + ".capture.json"),
                   {"document_id": document.document_id, "source_id": document.source_id,
                    "capture": capture, "provenance": provenance,
                    "interview_question": None, "interview_answer": None,
                    "speaker_binding": None})
        writer.write(updated, report)
        results.append({"source_id": document.source_id, "document_id": document.document_id,
                        "response_sha256": provenance["response_sha256"],
                        "content_sha256": capture["content_sha256"],
                        "source_text_start": start, "source_text_end": end,
                        "content_and_ids_unchanged": True})
    write_json(evidence_root / "capture_summary.json", results)
    return results
