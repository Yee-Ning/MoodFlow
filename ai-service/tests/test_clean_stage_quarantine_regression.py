"""Regression tests verifying A2-02 quarantine behavior is fully preserved in Clean 6-stage architecture."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from app.rag.clean.config import DEFAULT_CLEAN_CONFIG_PATH, load_clean_config
from app.rag.clean.service import CleanPipelineService
from tests.clean_helpers import normalized_document, write_normalized_corpus


def prepare_corpus(tmp_path: Path, *, mark_review: bool = False):
    doc1 = normalized_document(
        "doc_ok",
        "# 正常文档\n\n这是一篇合法的周杰伦新闻资讯文章。\n\n| 序号 | 歌名 |\n|---|---|\n| 1 | 晴天 |",
        title="正常文档",
        publisher="杰威爾音樂",
        document_type="news",
    )
    doc2 = normalized_document(
        "doc_pending",
        "# 待审文档\n\n这是一篇需要人工审核的文章内容。",
        title="待审文档",
        publisher="杰威爾音樂",
        document_type="news",
    )
    if mark_review:
        doc2 = doc2.model_copy(
            update={"metadata": {**doc2.metadata, "parse_status": "manual_review"}}
        )

    manifest_path = write_normalized_corpus(tmp_path, [doc1, doc2])
    config, checksum = load_clean_config(DEFAULT_CLEAN_CONFIG_PATH)
    service = CleanPipelineService(
        tmp_path,
        config,
        registry_checksum=checksum,
        normalized_root=manifest_path.parent,
        normalized_manifest_path=manifest_path,
    )
    return service, manifest_path


def test_upstream_pending_review_is_quarantined_by_default(tmp_path):
    service, _ = prepare_corpus(tmp_path, mark_review=True)

    # 1. Default run: doc_pending must be quarantined
    outcome_default = service.run(dry_run=False, run_id="run-quarantine-default")
    assert outcome_default.summary["quarantined_document_count"] == 1
    assert outcome_default.summary["output_document_count"] == 1
    assert len(outcome_default.results) == 1
    assert outcome_default.results[0].document.source_id == "doc_ok"

    # Verify manifest only has doc_ok
    manifest_lines = outcome_default.cleaned_manifest_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(manifest_lines) == 1
    assert json.loads(manifest_lines[0])["source_id"] == "doc_ok"

    # 2. Explicit flag: doc_pending is included, keeping manual_review metadata
    outcome_override = service.run(
        dry_run=False, run_id="run-quarantine-override", include_manual_review=True
    )
    assert outcome_override.summary["output_document_count"] == 2
    assert outcome_override.summary["quarantined_document_count"] == 0

    pending_res = next(r for r in outcome_override.results if r.document.source_id == "doc_pending")
    assert pending_res.document.metadata["parse_status"] == "manual_review"


def test_clean_needs_review_is_quarantined_without_explicit_flag(tmp_path):
    service, _ = prepare_corpus(tmp_path, mark_review=False)

    # Force doc_ok to trigger needs_review via guard warning
    with patch.object(service.engine.guard, "evaluate", return_value=["synthetic_clean_guard_warning"]):
        blocked = service.run(dry_run=False, run_id="run-clean-guard-blocked")
        assert blocked.summary["quarantined_document_count"] == 2
        assert blocked.summary["output_document_count"] == 0
        assert blocked.cleaned_manifest_path.read_text(encoding="utf-8") == ""

        # With explicit override flag, both documents are included
        included = service.run(
            dry_run=False, run_id="run-clean-guard-included", include_manual_review=True
        )
        assert included.summary["output_document_count"] == 2
        assert included.summary["quarantined_document_count"] == 0
