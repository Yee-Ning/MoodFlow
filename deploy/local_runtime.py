"""Initialize one local deployment using the same production modules as the API."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace
from urllib.error import URLError
from urllib.request import urlopen

import yaml


ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "ai-service" if (ROOT / "ai-service").is_dir() else ROOT
SEED = Path("data/knowledge/releases/worldline-final-native-release-20260927/v2")
LOCAL_INDEX = Path("data/local/index")


def configure_database() -> None:
    if not os.getenv("DATABASE_URL"):
        from psycopg.conninfo import make_conninfo
        os.environ["DATABASE_URL"] = make_conninfo(
            host="postgres", port=5432, dbname="moodflow", user="moodflow",
            password=os.environ.get("POSTGRES_PASSWORD", "moodflow-local-only"),
        )


def configure_transport(root: Path = SERVICE) -> None:
    """Set local addresses/resource limits without changing embedding semantics."""
    address = os.getenv("QDRANT_URL", "http://qdrant:6333")
    for relative in ("app/rag/indexing/config/v1.yaml", "app/rag/retrieval/config/v1.yaml"):
        path = root / relative
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
        config["qdrant"]["url"] = address
        if "indexing" in relative:
            config["qdrant"]["collection_name"] = "moodflow_local"
            config["dense"]["device"] = "cpu"
            config["embedding_batch_size"] = 1
        path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")


def wait_for_qdrant(timeout: float = 60) -> None:
    address = os.getenv("QDRANT_URL", "http://qdrant:6333").rstrip("/") + "/readyz"
    deadline = time.monotonic() + timeout
    while True:
        try:
            with urlopen(address, timeout=3) as response:
                if response.status == 200:
                    return
        except (OSError, URLError):
            pass
        if time.monotonic() >= deadline:
            raise RuntimeError("Qdrant did not become ready; inspect the qdrant service logs")
        time.sleep(1)


def verify_prepared_index(root, binding, config):
    from scripts.worldline_environment_readiness import verify_index

    provider = SimpleNamespace(binding=binding, config=config)
    runtime = SimpleNamespace(retriever=SimpleNamespace(candidate_provider=provider))
    # Existing approval verifies every live payload/vector and signed release hash.
    # It only approves build integrity; it makes no new semantic-source claims.
    verify_index(root, runtime, approve=True)


def prepare_model_cache():
    import httpx
    from huggingface_hub import snapshot_download
    from app.rag.retrieval.config import load_retrieval_config
    from app.rag.reranking.config import load_rerank_config

    dense, reranker = load_retrieval_config().dense, load_rerank_config().model
    # The entry-context RAG source uses reranking even when WorldLine's separate
    # rerank mode is off. Download both before the first user request deadline.
    for name, revision in ((dense.model_name, dense.model_revision), (reranker.name, reranker.revision)):
        for attempt in range(3):
            try:
                snapshot_download(repo_id=name, revision=revision, allow_patterns=[
                    "config.json", "generation_config.json", "tokenizer*", "special_tokens_map.json",
                    "vocab.json", "merges.txt", "model.safetensors",
                ])
                break
            except (OSError, httpx.HTTPError):
                if attempt == 2:
                    raise
                print(f"Retrying model download: {name} (attempt {attempt + 2}/3)")
                time.sleep(2 ** attempt)
        print(f"Model cache prepared: {name}@{revision}")


def prepare_index(root: Path = SERVICE) -> None:
    from app.rag.indexing import OfflineIndexingService, load_indexing_config
    from app.rag.retrieval import load_retrieval_config, load_retrieval_index_binding
    from qdrant_client import QdrantClient

    manifest = root / LOCAL_INDEX / "index_build_manifest.json"
    retrieval = load_retrieval_config(root / "app/rag/retrieval/config/v1.yaml")
    if manifest.is_file():
        binding = load_retrieval_index_binding(root, manifest, retrieval)
        client = QdrantClient(url=retrieval.qdrant.url, timeout=10, trust_env=False)
        try:
            if client.collection_exists(binding.manifest.collection_name):
                count = client.count(binding.manifest.collection_name, exact=True).count
                if count == binding.manifest.projected_count:
                    verify_prepared_index(root, binding, retrieval)
                    print(f"Knowledge index is current: {count} points")
                    return
        finally:
            client.close()
    config = load_indexing_config(root / "app/rag/indexing/config/v1.yaml")
    outcome = OfflineIndexingService(
        root, config, knowledge_path=root / SEED / "knowledge_enriched_chunks.jsonl",
        release_manifest_path=root / SEED / "knowledge_release_manifest.json",
        output_root=root / LOCAL_INDEX,
    ).run()
    if outcome.failures:
        raise RuntimeError("Knowledge indexing failed; inspect the local index failures before retrying")
    binding = load_retrieval_index_binding(root, manifest, retrieval)
    if not binding.manifest.ready_for_retrieval:
        raise RuntimeError("Knowledge index is not ready for retrieval")
    verify_prepared_index(root, binding, retrieval)
    print(f"Knowledge index prepared: {binding.manifest.projected_count} points")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("migrate", "prepare-index", "serve", "verify-models"))
    args = parser.parse_args(argv)
    if args.command == "verify-models":
        from scripts.setup import verify_models
        # Both a source checkout and the image preserve the monorepo layout.
        model_root = ROOT if (ROOT / "ai-service").is_dir() else Path("/workspace")
        print(json.dumps({"verified_model_files": verify_models(model_root)}))
        return 0
    configure_transport()
    configure_database()
    if args.command == "migrate":
        from app.agent.postgres_migrations import run_all_migrations
        print(f"Applied {len(run_all_migrations(include_memory=True))} database migrations")
    elif args.command == "prepare-index":
        prepare_model_cache()
        wait_for_qdrant()
        prepare_index()
    else:
        # Required pinned files were fetched by prepare-index. Serving does not
        # need Hub metadata requests in the middle of a user-request deadline.
        os.environ["HF_HUB_OFFLINE"] = "1"
        import uvicorn
        uvicorn.run("app.main:app", host="0.0.0.0", port=8001)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
