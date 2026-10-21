import io
import json
from contextlib import redirect_stdout
from unittest.mock import patch

import pytest

from app.rag.clean.errors import NormalizedCorpusError
from app.rag.clean.config import load_clean_config
from app.rag.clean.service import CleanPipelineService
from app.rag.chunk.config import ChunkConfig
from app.rag.chunk.service import ChunkPipelineService
from tests.clean_helpers import normalized_document, write_normalized_corpus
import run_clean
import run_chunk


def prepare(root, *, metadata_review=False, report_review=False):
    document = normalized_document(
        "pending",
        "Artist: Example\n\nAlbum release year: 2001. Evidence remains pending.\n",
        title="Pending",
    )
    if metadata_review:
        document = document.model_copy(
            update={"metadata": {**document.metadata, "parse_status": "manual_review"}}
        )
    write_normalized_corpus(root, [document])
    if report_review:
        (root / "data/normalized/pending/parse_report.json").write_text(
            json.dumps({"status": "manual_review"}), encoding="utf-8"
        )
    config, checksum = load_clean_config()
    return CleanPipelineService(root, config, registry_checksum=checksum), document


@pytest.mark.parametrize("legacy_sidecar", [False, True])
def test_parse_manual_review_is_quarantined_and_flag_does_not_erase_state(
    tmp_path, legacy_sidecar
):
    service, _ = prepare(
        tmp_path, metadata_review=not legacy_sidecar, report_review=legacy_sidecar
    )
    with (
        patch.object(service.engine.guard, "evaluate", return_value=[]),
        patch.object(service.engine, "clean", wraps=service.engine.clean) as clean,
    ):
        skipped = service.run(dry_run=False, run_id="isolated")
        clean.assert_not_called()
        assert skipped.results == []
        assert skipped.cleaned_manifest_path.read_text(encoding="utf-8") == ""
        assert skipped.summary["quarantined_document_count"] == 1
        assert skipped.summary["manual_review_documents"][0]["reasons"] == [
            "parse_manual_review"
        ]
        assert (
            json.loads(
                (
                    skipped.report_paths.directory / "manual_review_documents.jsonl"
                ).read_text(encoding="utf-8")
            )["source_id"]
            == "pending"
        )
        included = service.run(
            dry_run=False, run_id="override", include_manual_review=True
        )
        assert included.summary["output_document_count"] == 1
        assert included.results[0].document.metadata["parse_status"] == "manual_review"
        assert (
            included.summary["manual_review_documents"][0]["disposition"]
            == "included_by_flag"
        )
        chunk = ChunkPipelineService(
            tmp_path, ChunkConfig(), cleaned_root=included.cleaned_manifest_path.parent
        )
        assert chunk.run().documents == []
        assert chunk.run().summary["quarantined_document_count"] == 1
        assert len(chunk.run(include_manual_review=True).documents) == 1
        assert chunk.run().documents == []  # override is not sticky
        assert (
            json.loads(
                (chunk.output_root / "chunk_summary.json").read_text(encoding="utf-8")
            )["quarantined_document_count"]
            == 1
        )


def test_clean_needs_review_is_not_published_without_explicit_override(tmp_path):
    service, _ = prepare(tmp_path)
    with patch.object(
        service.engine.guard, "evaluate", return_value=["synthetic_guard_warning"]
    ):
        blocked = service.run(dry_run=False, run_id="clean-blocked")
        assert blocked.results[0].document.needs_review
        assert blocked.cleaned_manifest_path.read_text(encoding="utf-8") == ""
        assert blocked.summary["manual_review_documents"][0]["reasons"] == [
            "clean_needs_review"
        ]
        included = service.run(
            dry_run=False, run_id="clean-override", include_manual_review=True
        )
        chunk = ChunkPipelineService(
            tmp_path, ChunkConfig(), cleaned_root=included.cleaned_manifest_path.parent
        )
        assert chunk.run().documents == []
        assert chunk.run().summary["manual_review_documents"][0]["reasons"] == [
            "clean_needs_review"
        ]
        assert len(chunk.run(include_manual_review=True).documents) == 1


def test_cli_requires_explicit_override_at_both_clean_and_chunk(tmp_path):
    prepare(tmp_path, metadata_review=True)

    def invoke(module, args):
        output = io.StringIO()
        with redirect_stdout(output):
            assert module.main(args, project_root=tmp_path) == 0
        return json.loads(output.getvalue())

    assert (
        invoke(run_clean, ["clean", "--run-id", "default"])[
            "quarantined_document_count"
        ]
        == 1
    )
    assert (
        invoke(run_clean, ["clean", "--run-id", "allowed", "--include-manual-review"])[
            "output_document_count"
        ]
        == 1
    )
    assert invoke(run_chunk, [])["document_count"] == 0
    assert invoke(run_chunk, ["--include-manual-review"])["document_count"] == 1


def test_corrupt_review_report_is_rejected_without_silent_admission(tmp_path):
    service, _ = prepare(tmp_path)
    (tmp_path / "data/normalized/pending/parse_report.json").write_text(
        "[", encoding="utf-8"
    )
    with pytest.raises(NormalizedCorpusError, match="normalized_document_invalid"):
        service.run(dry_run=False)
    assert not (tmp_path / "data/cleaned").exists()
