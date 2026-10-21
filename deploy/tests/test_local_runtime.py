from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml

from deploy import local_runtime as runtime


ROOT = Path(__file__).resolve().parents[2]


def test_container_configuration_preserves_index_query_compatibility(tmp_path, monkeypatch):
    monkeypatch.setenv("QDRANT_URL", "http://test-qdrant:6333")
    originals = {}
    for name in ("indexing", "retrieval"):
        relative = f"app/rag/{name}/config/v1.yaml"
        text = (ROOT / "ai-service" / relative).read_text(encoding="utf-8")
        path = tmp_path / relative
        path.parent.mkdir(parents=True)
        path.write_text(text, encoding="utf-8")
        originals[name] = yaml.safe_load(text)
    runtime.configure_transport(tmp_path)
    for name, original in originals.items():
        actual = yaml.safe_load((tmp_path / f"app/rag/{name}/config/v1.yaml").read_text())
        assert actual["qdrant"]["url"] == "http://test-qdrant:6333"
        assert actual["bm25"] == original["bm25"]
        for key in ("model_name", "model_revision", "dimension", "max_length", "normalize"):
            assert actual["dense"][key] == original["dense"][key]


@pytest.fixture
def indexing(monkeypatch):
    import app.rag.indexing as offline
    import app.rag.retrieval as online
    import qdrant_client

    config = SimpleNamespace(qdrant=SimpleNamespace(url="http://qdrant:6333"))
    binding = SimpleNamespace(manifest=SimpleNamespace(
        ready_for_retrieval=True, collection_name="local-fixture", projected_count=41,
    ))
    service = Mock()
    service.return_value.run.return_value = SimpleNamespace(failures=[])
    monkeypatch.setattr(offline, "OfflineIndexingService", service)
    monkeypatch.setattr(offline, "load_indexing_config", lambda _: config)
    monkeypatch.setattr(online, "load_retrieval_config", lambda _: config)
    monkeypatch.setattr(online, "load_retrieval_index_binding", lambda *args: binding)
    client = Mock()
    client.collection_exists.return_value = True
    client.count.return_value = SimpleNamespace(count=41)
    monkeypatch.setattr(qdrant_client, "QdrantClient", Mock(return_value=client))
    monkeypatch.setattr(runtime, "verify_prepared_index", Mock())
    return service, client, binding


def test_first_index_build_uses_supplied_release_and_reports_failure(tmp_path, indexing):
    service, _, binding = indexing
    runtime.prepare_index(tmp_path)
    assert service.call_args.kwargs["output_root"] == tmp_path / runtime.LOCAL_INDEX
    assert service.call_args.kwargs["release_manifest_path"] == tmp_path / runtime.SEED / "knowledge_release_manifest.json"
    service.return_value.run.return_value.failures = ["failed document"]
    with pytest.raises(RuntimeError, match="indexing failed"):
        runtime.prepare_index(tmp_path)
    service.return_value.run.return_value.failures = []
    binding.manifest.ready_for_retrieval = False
    with pytest.raises(RuntimeError, match="not ready"):
        runtime.prepare_index(tmp_path)


def test_restart_reuses_valid_index_and_rebuilds_missing_collection(tmp_path, indexing):
    service, client, _ = indexing
    manifest = tmp_path / runtime.LOCAL_INDEX / "index_build_manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{}")
    runtime.prepare_index(tmp_path)
    service.assert_not_called()
    runtime.verify_prepared_index.assert_called_once()
    client.close.assert_called_once()
    client.collection_exists.return_value = False
    runtime.prepare_index(tmp_path)
    service.assert_called_once()


def test_index_integrity_failure_blocks_startup(tmp_path, indexing):
    runtime.verify_prepared_index.side_effect = ValueError("index_live_payload_mismatch")
    with pytest.raises(ValueError, match="index_live_payload_mismatch"):
        runtime.prepare_index(tmp_path)


def test_approval_uses_existing_full_index_verification(tmp_path, monkeypatch):
    from scripts import worldline_environment_readiness as readiness

    verify = Mock()
    monkeypatch.setattr(readiness, "verify_index", verify)
    binding, config = object(), object()
    runtime.verify_prepared_index(tmp_path, binding, config)
    args = verify.call_args
    assert args.args[0] == tmp_path and args.kwargs == {"approve": True}
    provider = args.args[1].retriever.candidate_provider
    assert provider.binding is binding and provider.config is config


def test_qdrant_readiness_and_timeout(monkeypatch):
    response = Mock()
    response.__enter__ = Mock(return_value=SimpleNamespace(status=200))
    response.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(runtime, "urlopen", Mock(return_value=response))
    runtime.wait_for_qdrant(0)
    monkeypatch.setattr(runtime, "urlopen", Mock(side_effect=OSError("offline")))
    with pytest.raises(RuntimeError, match="did not become ready"):
        runtime.wait_for_qdrant(0)


def test_database_configuration_quotes_password_and_keeps_explicit_test_url(monkeypatch):
    from psycopg.conninfo import conninfo_to_dict
    import os

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("POSTGRES_PASSWORD", "test-only with 'spaces' @:#")
    runtime.configure_database()
    parsed = conninfo_to_dict(os.environ["DATABASE_URL"])
    assert parsed["password"] == "test-only with 'spaces' @:#"
    assert parsed["host"] == "postgres"
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres@127.0.0.1/test")
    runtime.configure_database()
    assert os.environ["DATABASE_URL"] == "postgresql://postgres@127.0.0.1/test"
