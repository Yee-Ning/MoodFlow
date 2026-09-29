import json
import shutil
import sys
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from deploy.artifacts import LOCK_PATH, digest, export, verify, within
from deploy.audit_inventory import category, inventory
from deploy.runtime import configure, load_secrets, validate_contract, validate_http, validate_schema, warm_models
from deploy.model_cache import export_model_cache, verify_model_cache

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ai-service"))


def test_ai_image_requires_verified_local_cpu_torch():
    compose = yaml.safe_load((ROOT / "docker-compose.production.yml").read_text(encoding="utf-8"))
    dockerfile = (ROOT / "deploy/ai.Dockerfile").read_text(encoding="utf-8")
    requirements = (ROOT / "deploy/requirements.production.txt").read_text(encoding="utf-8")
    assert compose["x-ai"]["build"]["additional_contexts"]["wheelhouse"] == "./deployment-artifacts/wheelhouse"
    assert "torch==2.14.0+cpu" in requirements
    assert "download.pytorch.org" not in requirements
    assert "COPY --from=wheelhouse /torch-2.14.0+cpu-cp312-cp312-manylinux_2_28_x86_64.whl" in dockerfile
    assert "a09987c95ec4cffdb6df798d3d641558110a334cfccce22f2f046d83142bc260" in dockerfile
    assert "sha256sum -c -" in dockerfile
    assert "--no-index --no-deps --find-links=/tmp/wheelhouse --require-hashes" in dockerfile
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert "!ai-service/data/intent/taxonomy.json" in dockerignore
    assert "COPY ai-service/data/intent/taxonomy.json data/intent/taxonomy.json" in dockerfile
    assert "a60027b108e744d2baa457b6221572b9720d6f1ba377ef0350dbe014002bc63a" in dockerfile


def fixture_lock(root):
    (root / "manifest.json").write_text(json.dumps({"collection_name": "approved",
        "source_release_id": "release", "projection_fingerprint": "projection",
        "ready_for_retrieval": True, "projected_count": 41}), encoding="utf-8")
    return {"manifest_path": "manifest.json", "collection_name": "approved",
        "source_release_id": "release", "projection_fingerprint": "projection",
        "files": {"manifest.json": digest(root / "manifest.json")}}


def test_current_frozen_artifacts_pass_without_mutation():
    lock = verify(ROOT / "ai-service")
    assert lock["source_release_id"] == "knowledge-v2-daa965c10984eefa"
    assert lock["files"][lock["manifest_path"]] == "sha256:445b7263221ae5864015752a6689e5d8303c21409407375a6e0b33ac831f50f3"
    # Exercise frozen schema/release validators, with no online query or Eval.
    from app.rag.retrieval import load_retrieval_config, load_retrieval_index_binding
    from app.agent.foundation.subgraphs.worldline.runtime_entity_binding import VerifiedEndpointMaterializer
    binding = load_retrieval_index_binding(ROOT / "ai-service", Path(lock["manifest_path"]), load_retrieval_config())
    materializer = VerifiedEndpointMaterializer.from_binding(ROOT / "ai-service", binding)
    assert len(materializer.payloads) == 41


@pytest.mark.parametrize("failure", ["tamper", "missing", "identity", "not_ready"])
def test_artifacts_fail_closed(tmp_path, failure):
    lock = fixture_lock(tmp_path)
    target = tmp_path / "manifest.json"
    if failure == "missing":
        target.unlink()  # Test-owned temporary fixture only.
    elif failure == "tamper":
        target.write_text("changed", encoding="utf-8")
    else:
        payload = json.loads(target.read_text())
        payload["collection_name" if failure == "identity" else "ready_for_retrieval"] = "other" if failure == "identity" else False
        target.write_text(json.dumps(payload), encoding="utf-8")
        lock["files"]["manifest.json"] = digest(target)
    with pytest.raises(ValueError):
        verify(tmp_path, lock)


def test_artifact_path_escape_rejected(tmp_path):
    with pytest.raises(ValueError, match="outside_root"):
        within(tmp_path, "../outside")


def test_export_new_bundle_only(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    lock = fixture_lock(source)
    destination = tmp_path / "bundle"
    with patch("deploy.artifacts.LOCK_PATH", tmp_path / "lock.json"):
        (tmp_path / "lock.json").write_text(json.dumps(lock))
        export(source, destination)
        assert digest(destination / "manifest.json") == digest(source / "manifest.json")
        with pytest.raises(FileExistsError):
            export(source, destination)


def test_model_cache_portable_copy_and_hash_failure(tmp_path):
    source = tmp_path / "cache"
    source.mkdir()
    (source / "weights").write_bytes(b"fixture-weights")
    lock = {"files": {"weights": digest(source / "weights")}}
    path = tmp_path / "model-lock.json"
    path.write_text(json.dumps(lock))
    with patch("deploy.model_cache.LOCK_PATH", path):
        destination = tmp_path / "portable-cache"
        export_model_cache(source, destination)
        assert verify_model_cache(destination) == lock
        assert not (destination / "weights").is_symlink()
        (destination / "weights").write_bytes(b"changed")
        with pytest.raises(ValueError, match="model_cache_missing_or_changed"):
            verify_model_cache(destination)
        with pytest.raises(FileExistsError):
            export_model_cache(source, destination)


def test_configure_changes_only_transport_and_local_model_loading(tmp_path, monkeypatch):
    relative = Path("app/rag/retrieval/config/v1.yaml")
    target = tmp_path / relative
    target.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / "ai-service" / relative, target)
    before = yaml.safe_load(target.read_text())
    monkeypatch.setenv("QDRANT_URL", "http://qdrant:6333")
    configure(tmp_path)
    after = yaml.safe_load(target.read_text())
    after["qdrant"]["url"] = before["qdrant"]["url"]
    after["dense"]["device"] = before["dense"]["device"]
    after["dense"]["local_files_only"] = before["dense"]["local_files_only"]
    assert after == before


@pytest.mark.parametrize("url", ["http://user:password@qdrant", "file:///data", "garbage"])
def test_configure_invalid_url(tmp_path, monkeypatch, url):
    target = tmp_path / "app/rag/retrieval/config/v1.yaml"
    target.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / "ai-service/app/rag/retrieval/config/v1.yaml", target)
    before = target.read_bytes()
    monkeypatch.setenv("QDRANT_URL", url)
    with pytest.raises(ValueError):
        configure(tmp_path)
    assert target.read_bytes() == before


def production_environment(monkeypatch):
    compose = yaml.safe_load((ROOT / "docker-compose.production.yml").read_text())
    for key, value in compose["x-ai"]["environment"].items():
        if not str(value).startswith("${"):
            monkeypatch.setenv(key, str(value))
    monkeypatch.setenv("DATABASE_URL", "postgresql://moodflow@postgres:5432/moodflow_agent")
    monkeypatch.setenv("AI_RAG_INDEX_MANIFEST", json.loads(LOCK_PATH.read_text())["manifest_path"])
    for name, value in {"POSTGRES_USER": "moodflow", "POSTGRES_DB": "moodflow_agent",
            "SPRING_BOOT_BASE_URL": "http://java:8080", "NETEASE_API_BASE_URL": "http://netease:3000",
            "QDRANT_URL": "http://qdrant:6333", "LLM_BASE_URL": "https://provider.invalid"}.items():
        monkeypatch.setenv(name, value)


def test_production_contract_and_failure_paths(monkeypatch):
    production_environment(monkeypatch)
    validate_contract()
    for key, value in [("CHECKPOINT_BACKEND", "memory"), ("JEV_ENABLED", "true"),
                       ("AI_RAG_INDEX_MANIFEST", "data/staging/manifest.json"),
                       ("DATABASE_URL", "postgresql://user:password@postgres/db"),
                       ("DATABASE_URL", "postgresql://other@postgres/other"),
                       ("LLM_BASE_URL", "https://embedded-key@provider.invalid")]:
        old = __import__("os").environ[key]
        monkeypatch.setenv(key, value)
        with pytest.raises(ValueError):
            validate_contract()
        monkeypatch.setenv(key, old)


def test_http_readiness_rejects_dependency_failure(monkeypatch):
    monkeypatch.setenv("SPRING_BOOT_BASE_URL", "http://java:8080")
    monkeypatch.setenv("LLM_BASE_URL", "https://provider.invalid")
    monkeypatch.setenv("LLM_API_KEY", "test-fixture-only")
    monkeypatch.setenv("NETEASE_API_BASE_URL", "http://netease:3000")
    with patch("httpx.Client") as factory:
        client = factory.return_value.__enter__.return_value
        client.get.return_value.json.return_value = {"code": 200, "data": []}
        validate_http(provider=True)
        assert client.get.call_count == 3
        assert all("/models" in call.args[0] or "/api/auth/status" in call.args[0]
                   or "/inner/version" in call.args[0] for call in client.get.call_args_list)
        client.get.return_value.json.return_value = {"code": 502}
        with pytest.raises(ValueError, match="java_not_ready"):
            validate_http(provider=False)
        client.get.return_value.raise_for_status.side_effect = RuntimeError("dependency down")
        with pytest.raises(RuntimeError):
            validate_http(provider=False)


def test_schema_is_read_only_and_missing_table_rejected(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user@postgres/db")
    with patch("psycopg.connect") as connect:
        conn = connect.return_value.__enter__.return_value
        conn.execute.return_value.fetchone.return_value = (None,)
        with pytest.raises(ValueError, match="schema_table_missing"):
            validate_schema()
        assert "default_transaction_read_only=on" in connect.call_args.kwargs["options"]
        assert all(call.args[0].startswith("SELECT") for call in conn.execute.call_args_list)


@pytest.mark.parametrize("failure", [None, "business", "checkpoint", "vector"])
def test_schema_success_and_incomplete_migrations_rejected(monkeypatch, failure):
    from langgraph.checkpoint.postgres import PostgresSaver
    from app.agent.postgres_migrations import MIGRATION_ROOT
    monkeypatch.setenv("DATABASE_URL", "postgresql://user@postgres/db")

    class Cursor:
        def __init__(self, rows):
            self.rows = rows

        def __iter__(self):
            return iter(self.rows)

        def fetchone(self):
            return self.rows[0] if self.rows else None

    def execute(query, *_args):
        assert query.startswith("SELECT")
        if "agent_schema_migration" in query:
            return Cursor([] if failure == "business" else [(p.name,) for p in MIGRATION_ROOT.glob("*.sql")])
        if "checkpoint_migrations" in query:
            return Cursor([] if failure == "checkpoint" else [(v,) for v in range(len(PostgresSaver.MIGRATIONS))])
        if "pg_extension" in query:
            return Cursor([] if failure == "vector" else [("0.8.6",)])
        return Cursor([("existing_table",)])

    with patch("psycopg.connect") as connect:
        connect.return_value.__enter__.return_value.execute.side_effect = execute
        if failure:
            with pytest.raises(ValueError):
                validate_schema()
        else:
            validate_schema()


@pytest.mark.parametrize("value,valid", [("fixture-only", True), ("", False), ("a\nb", False), ('a"b', False)])
def test_secret_files_fail_closed_without_disclosing_values(tmp_path, monkeypatch, value, valid):
    for filename in ("postgres_password", "llm_api_key", "qdrant_api_key"):
        (tmp_path / filename).write_text(value, encoding="utf-8")
    for name in ("PGPASSWORD", "LLM_API_KEY", "QDRANT_API_KEY"):
        monkeypatch.setenv(name, "")
    if valid:
        load_secrets(tmp_path)
        assert __import__("os").environ["PGPASSWORD"] == value
    else:
        with pytest.raises(ValueError) as error:
            load_secrets(tmp_path)
        if value:
            assert value not in str(error.value)


def test_warm_models_uses_existing_singletons_without_inference():
    with patch("app.agent.foundation.subgraphs.worldline.retrieval.get_worldline_retrieval_runtime") as retrieval, \
         patch("app.agent.memory.runtime.get_memory_runtime") as memory, \
         patch("app.agent.foundation.streaming.get_foundation_graph") as graph:
        warm_models()
        retrieval.return_value.retriever.candidate_provider.encoder._encoder._ensure_loaded.assert_called_once()
        memory.return_value.retriever.embedder._encoder._ensure_loaded.assert_called_once()
        graph.assert_called_once()


def test_compose_readiness_and_isolation():
    compose = yaml.safe_load((ROOT / "docker-compose.production.yml").read_text())
    services = compose["services"]
    assert set(services) == {"postgres", "qdrant", "migrate", "netease", "java", "ai-service", "frontend"}
    for name, service in services.items():
        if name != "frontend":
            assert "ports" not in service
        for dependency in service.get("depends_on", {}).values():
            assert dependency["condition"] in {"service_healthy", "service_completed_successfully"}
    assert services["migrate"]["restart"] == "no"
    assert services["ai-service"]["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
    for mount in services["ai-service"]["volumes"]:
        assert mount["read_only"] is True and mount["bind"]["create_host_path"] is False
        assert mount["target"] != "/app/data"
    assert set(compose["volumes"]) == {"postgres_data", "qdrant_data", "qdrant_snapshots"}


def test_frontend_preserves_api_paths_and_streaming():
    nginx = (ROOT / "deploy/nginx.conf").read_text()
    assert nginx.index("location /api/ai/ {") < nginx.index("location /api/ {")
    assert "proxy_pass http://ai-service:8001;" in nginx
    assert "proxy_pass http://java:8080;" in nginx
    assert "proxy_buffering off;" in nginx
    # Spring compares Origin with forwarded scheme/host/port for cookie requests.
    assert nginx.count("proxy_set_header X-Forwarded-Host $http_host;") == 2
    assert '"" $scheme;' in nginx
    assert "try_files $uri $uri/ /index.html" in nginx
    dockerfile = (ROOT / "deploy/frontend.Dockerfile").read_text()
    assert 'VITE_API_BASE_URL="" VITE_AI_API_BASE_URL=""' in dockerfile


def test_host_nginx_only_proxies_loopback_and_preserves_sse():
    nginx = (ROOT / "deploy/nginx-host.conf").read_text()
    assert "listen 80 default_server;" in nginx
    http_server, https_server = nginx.split("server {\n    listen 443 ssl default_server;", 1)
    assert "location ^~ /.well-known/acme-challenge/ {" in http_server
    assert "root /var/www/moodflow-acme;" in http_server
    assert "return 308 https://47.83.162.191$request_uri;" in http_server
    assert "proxy_pass" not in http_server
    assert "ssl_certificate /etc/letsencrypt/live/47.83.162.191/fullchain.pem;" in https_server
    assert "ssl_certificate_key /etc/letsencrypt/live/47.83.162.191/privkey.pem;" in https_server
    assert "ssl_protocols TLSv1.2 TLSv1.3;" in https_server
    assert nginx.count("proxy_pass http://127.0.0.1:8088;") == 3
    assert nginx.index("location /api/ai/ {") < nginx.index("location /api/ {")
    ai_location = nginx.split("location /api/ai/ {", 1)[1].split("\n    }", 1)[0]
    for directive in ("proxy_buffering off;", "proxy_request_buffering off;",
                      "proxy_cache off;", "proxy_read_timeout 300s;",
                      "proxy_set_header X-Forwarded-Proto $scheme;"):
        assert directive in ai_location
    assert "postgres:5432" not in nginx and "qdrant:6333" not in nginx
    hook = (ROOT / "deploy/certbot-reload-nginx.sh").read_text()
    assert '"${RENEWED_LINEAGE:-}" != /etc/letsencrypt/live/47.83.162.191' in hook
    assert hook.index("/usr/sbin/nginx -t") < hook.index("/usr/bin/systemctl reload nginx")


@pytest.fixture(scope="module")
def compiled_java_probe(tmp_path_factory):
    directory = tmp_path_factory.mktemp("java-probe")
    subprocess.run(["javac", "-d", str(directory), str(ROOT / "deploy/HttpHealthCheck.java")], check=True)
    return directory


@pytest.mark.parametrize("status,body,exit_code", [
    (200, '{"code":200,"data":{"loggedIn":false}}', 0),
    (200, '{"code":2000}', 1),
    (503, '{"code":200}', 1),
    (200, '{"code":500}', 1),
])
def test_java_readiness_response_contract(compiled_java_probe, status, body, exit_code):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(status)
            self.end_headers()
            self.wfile.write(body.encode())

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = subprocess.run(["java", "-cp", str(compiled_java_probe), "HttpHealthCheck",
                                 f"http://127.0.0.1:{server.server_port}/api/auth/status"], timeout=10)
        assert result.returncode == exit_code
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_size_inventory_never_reads_file_contents(tmp_path):
    target = tmp_path / "ai-service/.venv/a.bin"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"1234")
    with patch.object(Path, "read_bytes", side_effect=AssertionError("content access forbidden")):
        rows, skipped = inventory(tmp_path)
    assert sum(row["bytes"] for row in rows) == 4 and not skipped
    assert rows[0]["category"] == "B"
    assert category("ai-service/artifacts/intent_model/encoder/model.safetensors")[0] == "A"
    assert category("ai-service/data/indexing/worldline/old/manifest.json")[0] == "C"
    assert category("ai-service/data/knowledge/releases/worldline-final-native-release-20260927/v2/qa/qa_review_attestation.json")[0] == "A"
