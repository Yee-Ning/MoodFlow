"""Production startup/readiness adapter; frozen application code is unchanged."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

from deploy.artifacts import LOCK_PATH, verify

ROOT = Path("/app")


def configure_shadow_telemetry_logging(stream=None) -> None:
    """Emit only the privacy-filtered V5 Shadow events at INFO in production."""
    shadow_logger = logging.getLogger("app.agent.decision.jev.shadow_v5")
    if not any(handler.name == "moodflow-v5-shadow" for handler in shadow_logger.handlers):
        handler = logging.StreamHandler(stream if stream is not None else sys.stdout)
        handler.name = "moodflow-v5-shadow"
        handler.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s"))
        shadow_logger.addHandler(handler)
    shadow_logger.setLevel(logging.INFO)
    shadow_logger.propagate = False


def load_secrets(secret_root: Path = Path("/run/secrets")) -> None:
    secrets = [("PGPASSWORD", "postgres_password"),
               ("LLM_API_KEY", "llm_api_key"),
               ("QDRANT_API_KEY", "qdrant_api_key")]
    if os.environ.get("JEV_ENABLED") == "true":
        secrets.append(("TYPESAFE_API_KEY", "typesafe_api_key"))
    for name, filename in secrets:
        value = (secret_root / filename).read_text(encoding="utf-8").strip()
        # Secret diagnostics never include the value or underlying exception.
        if not value or any(c in value for c in '\r\n"\\'):
            raise ValueError(f"invalid_secret_file:{filename}")
        os.environ[name] = value


def configure(root: Path = ROOT) -> None:
    import yaml
    path = root / "app/rag/retrieval/config/v1.yaml"
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    endpoint = os.environ.get("QDRANT_URL", "http://qdrant:6333")
    parsed = urlparse(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
        raise ValueError("invalid_qdrant_url")
    payload["qdrant"]["url"] = endpoint
    payload["dense"]["device"] = "cpu"
    payload["dense"]["local_files_only"] = True
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def validate_contract() -> None:
    expected = {"CHECKPOINT_BACKEND": "postgres", "PERSISTENCE_BACKEND": "postgres",
                "MEMORY_BACKEND": "postgres", "AI_RAG_RETRIEVAL_MODE": "hybrid",
                "AI_RAG_RERANK_MODE": "off", "AI_AGENT_GRAPH_MODE": "foundation",
                "JEV_SHADOW_MODE": "true", "JEV_ROUTER_ENABLED": "false",
                "JEV_ROUTER_V4_SHADOW_ENABLED": "false",
                "JEV_MEMORY_FILTER_ENABLED": "false", "JEV_CONTEXT_SELECT_ENABLED": "false"}
    for name, value in expected.items():
        if os.environ.get(name) != value:
            raise ValueError(f"production_contract_mismatch:{name}")
    jev_enabled = os.environ.get("JEV_ENABLED")
    v5_shadow_enabled = os.environ.get("JEV_ROUTER_V5_SHADOW_ENABLED")
    if (jev_enabled, v5_shadow_enabled) not in {("false", "false"), ("true", "true")}:
        raise ValueError("production_contract_mismatch:JEV_V5_SHADOW_PAIR")
    if jev_enabled == "true" and (
        os.environ.get("JEV_PROVIDER") != "typesafe" or not os.environ.get("TYPESAFE_API_KEY")
    ):
        raise ValueError("production_jev_provider_unavailable")
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    if os.environ.get("AI_RAG_INDEX_MANIFEST") != lock["manifest_path"]:
        raise ValueError("production_manifest_not_frozen")
    database = urlparse(os.environ.get("DATABASE_URL", ""))
    if database.scheme not in {"postgres", "postgresql"} or not database.hostname:
        raise ValueError("invalid_database_url")
    if database.password:
        raise ValueError("database_password_must_use_secret_file")
    if (database.username != os.environ.get("POSTGRES_USER")
            or database.path != "/" + os.environ.get("POSTGRES_DB", "")):
        raise ValueError("database_identity_does_not_match_postgres")
    for name in ("SPRING_BOOT_BASE_URL", "NETEASE_API_BASE_URL", "QDRANT_URL", "LLM_BASE_URL"):
        endpoint = urlparse(os.environ.get(name, ""))
        if endpoint.scheme not in {"http", "https"} or not endpoint.hostname or endpoint.username:
            raise ValueError(f"invalid_service_url:{name}")


def validate_schema() -> None:
    import psycopg
    from langgraph.checkpoint.postgres import PostgresSaver
    from app.agent.postgres_migrations import MIGRATION_ROOT
    required_tables = ("agent_task", "agent_session", "agent_history_event", "agent_artifact",
                       "agent_handoff", "agent_long_term_memory", "checkpoints",
                       "checkpoint_blobs", "checkpoint_writes", "checkpoint_migrations")
    with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=5,
                         options="-c default_transaction_read_only=on -c statement_timeout=5000") as conn:
        for table in required_tables:
            if conn.execute("SELECT to_regclass(%s)", (f"public.{table}",)).fetchone()[0] is None:
                raise ValueError(f"schema_table_missing:{table}")
        versions = {r[0] for r in conn.execute("SELECT version FROM agent_schema_migration")}
        if not {p.name for p in MIGRATION_ROOT.glob("*.sql")}.issubset(versions):
            raise ValueError("business_schema_migrations_missing")
        checkpoint = {r[0] for r in conn.execute("SELECT v FROM checkpoint_migrations")}
        if not set(range(len(PostgresSaver.MIGRATIONS))).issubset(checkpoint):
            raise ValueError("checkpoint_schema_migrations_missing")
        if conn.execute("SELECT extversion FROM pg_extension WHERE extname='vector'").fetchone() is None:
            raise ValueError("pgvector_missing")


async def validate_index(root: Path = ROOT) -> None:
    from app.rag.retrieval import load_retrieval_config, load_retrieval_index_binding
    from app.rag.retrieval.qdrant import QdrantHybridSearchBackend
    from app.agent.foundation.subgraphs.worldline.runtime_entity_binding import VerifiedEndpointMaterializer
    verify(root)
    config = load_retrieval_config()
    binding = load_retrieval_index_binding(root, Path(os.environ["AI_RAG_INDEX_MANIFEST"]), config)
    VerifiedEndpointMaterializer.from_binding(root, binding)
    backend = QdrantHybridSearchBackend(config)
    try:
        await backend.validate_binding(binding)
        info = await backend.client.get_collection(binding.profile.collection_name)
        dense = info.config.params.vectors[config.dense.vector_name]
        if dense.distance.value.lower() != "cosine" or info.status.value != "green":
            raise ValueError("qdrant_collection_not_ready")
        sparse = info.config.params.sparse_vectors[config.bm25.vector_name]
        if sparse.modifier is None or sparse.modifier.value.lower() != "idf":
            raise ValueError("qdrant_bm25_modifier_mismatch")
    finally:
        await backend.client.close()


def validate_http(*, provider: bool) -> None:
    import httpx
    with httpx.Client(timeout=5, trust_env=False) as client:
        music = client.get(os.environ["SPRING_BOOT_BASE_URL"] + "/api/auth/status")
        music.raise_for_status()
        if music.json().get("code") != 200:
            raise ValueError("java_not_ready")
        upstream = client.get(os.environ["NETEASE_API_BASE_URL"] + "/inner/version")
        upstream.raise_for_status()
        if upstream.json().get("code") != 200:
            raise ValueError("netease_not_ready")
        if provider:
            # Connectivity/auth check only; never treated as generation acceptance.
            response = client.get(os.environ["LLM_BASE_URL"].rstrip("/") + "/models",
                                  headers={"Authorization": "Bearer " + os.environ["LLM_API_KEY"]})
            response.raise_for_status()
            if not isinstance(response.json().get("data"), list):
                raise ValueError("provider_model_list_invalid")


def warm_models() -> None:
    # Load the exact cached model without retrieval, inference, Eval or data writes.
    from app.agent.foundation.subgraphs.worldline.retrieval import get_worldline_retrieval_runtime
    from app.agent.memory.runtime import get_memory_runtime
    from app.agent.foundation.streaming import get_foundation_graph
    retriever = get_worldline_retrieval_runtime().retriever
    retriever.candidate_provider.encoder._encoder._ensure_loaded()
    get_memory_runtime().retriever.embedder._encoder._ensure_loaded()
    get_foundation_graph()  # Restores the intent classifier via the existing factory.


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("configure", "verify", "migrate", "serve", "health"))
    args = parser.parse_args()
    try:
        if args.action == "configure":
            configure()
            return
        if args.action == "verify":
            verify(ROOT)
            print("Frozen artifacts verified")
            return
        load_secrets()
        validate_contract()
        if args.action in {"migrate", "serve"}:
            configure()
        if args.action == "migrate":
            # Future deployment only; the audit never invokes this action.
            verify(ROOT)
            from scripts.migrate_agent_postgres import main as migrate
            migrate()
            validate_schema()
            asyncio.run(validate_index())
        elif args.action == "serve":
            from deploy.model_cache import verify_model_cache
            verify_model_cache(Path("/models"))
            from app.config.settings import get_settings
            get_settings()  # Fail before declaring the application ready.
            validate_schema()
            asyncio.run(validate_index())
            validate_http(provider=True)
            from contextlib import asynccontextmanager
            from app.main import app
            original_lifespan = app.router.lifespan_context

            @asynccontextmanager
            async def production_lifespan(application):
                async with original_lifespan(application):
                    configure_shadow_telemetry_logging()
                    await asyncio.to_thread(warm_models)
                    yield

            app.router.lifespan_context = production_lifespan
            import uvicorn
            uvicorn.run(app, host="0.0.0.0", port=8001, workers=1)
        else:
            import httpx
            with httpx.Client(timeout=3, trust_env=False) as client:
                response = client.get("http://127.0.0.1:8001/health")
                response.raise_for_status()
                if response.json().get("status") != "ok":
                    raise ValueError("ai_liveness_failed")
            validate_schema()
            asyncio.run(validate_index())
            validate_http(provider=False)
    except Exception:
        # Never print raw exceptions that might contain a connection URL/credential.
        print(f"Production {args.action} failed; inspect contract, artifacts and dependency readiness", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
