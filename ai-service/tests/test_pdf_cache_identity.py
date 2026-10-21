import json
import shutil

import pytest

from app.rag.parse_normalize.pdf.models import PDFBackend
from app.rag.parse_normalize.service import OutcomeStatus, create_default_service
from tests.test_pdf_parse_e2e import FIXTURE_ROOT, fixture_cascade


@pytest.mark.parametrize("change", ["backend_version", "runner_version", "runner_config", "runtime_config", "title", "author"])
def test_same_raw_bytes_miss_normalized_cache_when_identity_changes(tmp_path, change):
    shutil.copytree(FIXTURE_ROOT, tmp_path, dirs_exist_ok=True)
    parser, runner, runtime = fixture_cascade(tmp_path / "cache")
    manifest = tmp_path / "sources_manifest.jsonl"
    service = create_default_service(tmp_path, raw_manifest_path=manifest, pdf_parser=parser)
    source = "pdf-complex-layout"
    adapter = parser.adapters[PDFBackend.MINERU]
    assert service.parse_source(source, parser_mode="mineru").status is OutcomeStatus.PARSED
    assert service.parse_source(source, parser_mode="mineru").status is OutcomeStatus.SKIPPED
    before_calls = len(runner.calls)
    old_cache = adapter._cache_directory(service_input(tmp_path, manifest, source))
    if change == "backend_version":
        adapter.adapter_version = "9.0.0"
    elif change == "runner_version":
        runner.version_label = "fixture-v2"
    elif change == "runner_config":
        runner.backend = "changed-pipeline"
    elif change == "runtime_config":
        runtime.timeout_seconds = 99
    else:
        rows = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines()]
        for row in rows:
            if row["source_id"] == source:
                row[change] = "Updated source metadata"
        manifest.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    assert service.parse_source(source, parser_mode="mineru").status is OutcomeStatus.PARSED
    assert service.parse_source(source, parser_mode="mineru").status is OutcomeStatus.SKIPPED
    if change in {"backend_version", "runner_version", "runner_config"}:
        assert len(runner.calls) == before_calls + 1
        assert adapter._cache_directory(service_input(tmp_path, manifest, source)) != old_cache
    else:
        assert len(runner.calls) == before_calls  # normalized re-renders; byte extraction remains reusable
    if change in {"title", "author"}:
        document = json.loads((tmp_path / "data/normalized" / source / "document.json").read_text(encoding="utf-8"))
        assert document[change] == "Updated source metadata"


def service_input(root, manifest, source):
    from app.rag.parse_normalize.manifest import RawSnapshotLoader
    from app.rag.parse_normalize.models import ParseInput
    loaded = RawSnapshotLoader(root).load_latest(manifest, source)
    return ParseInput(snapshot=loaded.record, raw_bytes=loaded.raw_bytes)


def test_command_builder_identity_is_stable_but_distinguishes_settings():
    from app.rag.parse_normalize.cache_identity import component_identity
    from app.rag.parse_normalize.pdf.adapters import SubprocessMinerURunner
    def builder(mode):
        return lambda source, output, ocr: ["mineru", mode, str(source), str(output)]
    assert component_identity(SubprocessMinerURunner(command_builder=builder("first"))) == component_identity(SubprocessMinerURunner(command_builder=builder("first")))
    assert component_identity(SubprocessMinerURunner(command_builder=builder("first"))) != component_identity(SubprocessMinerURunner(command_builder=builder("second")))
