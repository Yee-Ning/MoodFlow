"""Readiness probes only: no user journey, restart, evaluator, or database repair.

Run from ai-service with its existing .env. Exceptions are reported by type,
never by message, because driver/provider messages may contain credentials.
Index approval is scoped to build integrity, not semantic corpus completeness.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path

import httpx
from psycopg import connect
from langgraph.checkpoint.postgres import PostgresSaver
from qdrant_client import QdrantClient

from app.config.settings import get_settings, load_runtime_environment
from app.agent.checkpointing import checkpoint_backend
from app.agent.foundation.subgraphs.worldline.retrieval import (
    RetrievalRuntimeSettings,
    WorldLineContextAdapter,
    create_worldline_retrieval_runtime,
)
from app.rag.indexing.corpus import IndexCorpusReader
from app.rag.indexing.models import PreparedIndexDocument
from app.rag.indexing.release import KnowledgeReleaseValidator
from app.rag.knowledge.qa import sha256_file
from app.rag.query_understanding.models import (
    MetadataFilter, QueryType, QueryVariant, QueryVariantKind,
    RetrievalStrategy, SearchChannel, SearchPlan,
)


def audit_corpus(root: Path, knowledge_path: Path) -> dict:
    corpus = IndexCorpusReader(root, knowledge_path=knowledge_path).load()
    docs = {c.doc_id: c for c in corpus.chunks}
    per_document = []
    for doc_id, chunk in sorted(docs.items()):
        metadata = chunk.metadata.get("document_metadata", {})
        source = metadata.get("source_metadata", {})
        raw_path = (root / str(source.get("raw_path", ""))).resolve()
        expected = source.get("content_hash")
        raw_hash_verified = (
            raw_path.is_relative_to(root) and raw_path.is_file()
            and sha256_file(raw_path) == expected
        )
        contexts = [
            context for c in corpus.chunks if c.doc_id == doc_id
            for context in [
                *c.metadata.get("relation_source_contexts", []),
                *c.metadata.get("document_metadata", {}).get("source_context_captures", []),
                *c.metadata.get("document_metadata", {}).get("relation_source_contexts", []),
            ]
        ]
        roles = sorted({s.get("role") for c in contexts for s in c.get("segments", [])})
        per_document.append({
            "doc_id": doc_id, "source_id": chunk.enrichment.source.source_id,
            "url_present": bool(chunk.enrichment.source.source_url),
            "publisher_present": bool(source.get("publisher")),
            "publisher_label_present": bool(chunk.enrichment.source.source_name),
            "title_present": bool(source.get("title") or chunk.metadata.get("title")),
            "response_body_sha_verified": raw_hash_verified,
            "source_context_count": len(contexts), "roles": roles,
        })
    projected_contexts = sum(
        bool(len(c.metadata.get("relation_source_contexts", []))
        + len(c.metadata.get("document_metadata", {}).get("source_context_captures", []))
        + len(c.metadata.get("document_metadata", {}).get("relation_source_contexts", [])))
        for c in corpus.children
    )
    predicates = Counter(c.claim.predicate.value for chunk in corpus.children for c in chunk.knowledge.claims)
    gaps = []
    roles = {r for d in per_document for r in d["roles"]}
    for role in ["article_sentence", "interview_question", "interview_answer", "speaker_binding"]:
        if role not in roles:
            gaps.append("missing_source_context_role:" + role)
    if not all(d["response_body_sha_verified"] for d in per_document):
        gaps.append("raw_response_body_hash_not_verified_for_all_documents")
    if not all(all(d[k] for k in ["url_present", "publisher_present", "title_present"]) for d in per_document):
        gaps.append("source_identity_metadata_incomplete")
    return {
        "ready": not gaps, "document_count": len(docs), "parent_count": len(corpus.parents),
        "child_count": len(corpus.children), "claim_predicates": dict(predicates),
        "children_with_capture_count": projected_contexts, "gaps": gaps,
        "documents": per_document,
        "note": "Roles are counted from existing captures only; no subject or speaker is inferred.",
    }


def probe_postgres() -> dict:
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        return {"configured": False, "reachable": False, "error_code": "DATABASE_URL_missing"}
    try:
        with connect(url, connect_timeout=5) as connection:
            tables = {r[0] for r in connection.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename LIKE 'checkpoint%'"
            ).fetchall()}
            required = {"checkpoint_migrations", "checkpoints", "checkpoint_blobs", "checkpoint_writes"}
            version = None
            if "checkpoint_migrations" in tables:
                version = connection.execute("SELECT MAX(v) FROM checkpoint_migrations").fetchone()[0]
        expected_version = len(PostgresSaver.MIGRATIONS) - 1
        return {"configured": True, "reachable": True, "tables": sorted(tables),
                "schema_ready": required.issubset(tables) and version == expected_version,
                "migration_version": version, "required_migration_version": expected_version}
    except Exception as exc:
        return {"configured": True, "reachable": False, "schema_ready": False,
                "error_type": type(exc).__name__}


async def checkpoint_smoke(root: Path, trace_path: Path) -> dict:
    """Use a labeled engineering result to exercise only real PG persistence."""
    if checkpoint_backend() != "postgres":
        raise ValueError("checkpoint_probe_requires_postgres_backend")
    from app.agent.checkpointing import (
        initialize_runtime_checkpointer, create_runtime_checkpointer, close_runtime_checkpointer,
    )
    from app.agent.foundation.subgraphs.worldline.models import WorldLineSessionResult
    from app.agent.foundation.subgraphs.worldline.session import WorldLineSessionService

    raw = json.loads(trace_path.read_text(encoding="utf-8"))
    if raw.get("fixture_kind") != "independent_engineering_prose_external_io_doubles":
        raise ValueError("checkpoint_probe_requires_independent_engineering_fixture")
    result = WorldLineSessionResult.model_validate({
        k: raw["api_create"][k] for k in WorldLineSessionResult.model_fields
    })

    class PersistenceProbeSession(WorldLineSessionService):
        async def _run_turn(self, *args, **kwargs):
            return result

    try:
        await initialize_runtime_checkpointer()
        saver = create_runtime_checkpointer()
        if not isinstance(saver, PostgresSaver):
            raise ValueError("checkpoint_probe_requires_real_postgres_saver")
        service = PersistenceProbeSession(saver, turn_graph=object(), distributed_lock=True)
        created = await service.create("Worldline environment persistence probe")
        snapshot = await service.load(created.session_id)
        fresh_service = WorldLineSessionService(saver, turn_graph=object(), distributed_lock=True)
        recovered = await fresh_service.recover(created.session_id)
        if recovered != created or snapshot.last_result.graph != recovered.graph:
            raise ValueError("checkpoint_roundtrip_mismatch")
        return {"passed": True, "session_id": created.session_id,
                "fixture_kind": raw["fixture_kind"], "same_process": True,
                "restart_acceptance_performed": False,
                "relation_counts": {k: len(getattr(recovered, k)) for k in
                                    ["era_relations", "scene_relations", "influence_relations"]}}
    finally:
        await close_runtime_checkpointer()


def verify_index(root: Path, runtime, *, approve: bool) -> dict:
    binding = runtime.retriever.candidate_provider.binding
    manifest = binding.manifest
    for path, expected in [
        (root / manifest.source_path, manifest.source_fingerprint),
        (root / manifest.source_release_manifest_path, manifest.source_release_manifest_fingerprint),
        (root / manifest.projection_path, manifest.projection_fingerprint),
    ]:
        if not path.resolve().is_relative_to(root) or sha256_file(path) != expected:
            raise ValueError("index_artifact_fingerprint_mismatch")
    KnowledgeReleaseValidator(root, knowledge_path=root / manifest.source_path,
                              manifest_path=root / manifest.source_release_manifest_path).validate()
    documents = [PreparedIndexDocument.model_validate(json.loads(line)) for line in
                 (root / manifest.projection_path).read_text(encoding="utf-8").splitlines() if line.strip()]
    config = runtime.retriever.candidate_provider.config
    client = QdrantClient(url=config.qdrant.url, api_key=os.getenv(config.qdrant.api_key_env) or None,
                          trust_env=config.qdrant.trust_env, timeout=30)
    try:
        verified = 0
        for offset in range(0, len(documents), 64):
            batch = documents[offset:offset + 64]
            points = {str(p.id): p for p in client.retrieve(
                manifest.collection_name, ids=[d.point_id for d in batch],
                with_payload=True, with_vectors=True,
            )}
            for document in batch:
                point = points.get(document.point_id)
                if point is None or point.payload != document.payload:
                    raise ValueError("index_live_payload_mismatch")
                vectors = point.vector or {}
                dense = vectors.get(manifest.dense_vector_name, [])
                if len(dense) != manifest.dense_dimension or manifest.bm25_vector_name not in vectors:
                    raise ValueError("index_live_vector_missing")
                verified += 1
        if client.count(manifest.collection_name, exact=True).count != verified or verified != manifest.projected_count:
            raise ValueError("index_full_build_count_mismatch")
    finally:
        client.close()
    approval_path = binding.manifest_path.parent / "ready_index_approval.json"
    approval = {
        "status": "approved", "scope": "retrieval_build_integrity_only",
        "semantic_source_context_readiness_approved": False,
        "reviewer": "Codex environment preparation",
        "manifest_sha256": sha256_file(binding.manifest_path),
        "source_release_id": manifest.source_release_id, "collection_name": manifest.collection_name,
        "projection_fingerprint": manifest.projection_fingerprint,
        "live_payloads_and_vectors_verified": verified,
        "eval_gold_or_review_pack_used_as_index_data": False,
    }
    if approve:
        approval_path.write_text(json.dumps(approval, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    approved = approval_path.is_file() and json.loads(approval_path.read_text(encoding="utf-8")) == approval
    return {"ready_for_retrieval": manifest.ready_for_retrieval, "approved": approved,
            "approval_scope": approval["scope"], "collection_name": manifest.collection_name,
            "verified_point_count": verified, "manifest_sha256": approval["manifest_sha256"]}


async def retrieval_smoke(runtime) -> dict:
    service = runtime.retriever.candidate_provider
    query = "周杰伦专辑发行与音乐风格"
    plan = SearchPlan(
        plan_id="query-plan:" + sha256(query.encode()).hexdigest()[:24],
        original_query=query, standalone_query=query, query_types=[QueryType.EXPLORATORY],
        queries=[QueryVariant(text=query, kind=QueryVariantKind.ORIGINAL, channel=SearchChannel.BOTH, weight=1)],
        metadata_filter=MetadataFilter(), strategy=RetrievalStrategy.HYBRID,
        candidate_top_k=40, final_top_k=6, needs_rerank=False,
        analysis_version="v1", prompt_version="v1", index_profile=runtime.index_profile,
    )
    try:
        result = await service.retrieve(plan)
        adapter = WorldLineContextAdapter(service.config)
        pack = adapter.build_pack(plan, result)
        documents = adapter.adapt(result, plan)
        for item, doc in zip(pack.items, documents, strict=True):
            if not item.document_metadata or doc.metadata["document_metadata"] != item.document_metadata:
                raise ValueError("context_document_metadata_lost")
        return {"passed": result.status.value == "success" and bool(pack.items) and not result.failed_channels,
                "status": result.status.value, "candidate_count": len(result.candidates),
                "context_count": len(pack.items), "failed_channels": [c.value for c in result.failed_channels],
                "warnings": result.warnings, "context_pack": pack.model_dump(mode="json")}
    finally:
        await service.backend.client.close()


def http_probe(url: str, *, expected: int = 200) -> dict:
    try:
        response = httpx.get(url, timeout=20, trust_env=False)
        return {"reachable": True, "http_status": response.status_code,
                "passed": response.status_code == expected}
    except Exception as exc:
        return {"reachable": False, "passed": False, "error_type": type(exc).__name__}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--approve-index", action="store_true")
    parser.add_argument("--checkpoint-trace", type=Path)
    args = parser.parse_args()
    load_runtime_environment()
    root = Path(__file__).resolve().parents[1]
    try:
        settings = get_settings()
    except Exception as exc:
        failure = {"environment_ready": False, "can_run_online_e2e_postgres_restart": False,
                   "blockers": ["runtime_settings_invalid"], "error_type": type(exc).__name__}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(failure, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(failure))
        return 2
    retrieval_settings = RetrievalRuntimeSettings()
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "runtime_config": {
        "checkpoint_backend": checkpoint_backend(), "retrieval_mode": retrieval_settings.retrieval_mode,
        "index_manifest": retrieval_settings.index_manifest, "rerank_mode": retrieval_settings.rerank_mode,
        "llm_model": settings.model, "generation_model": settings.generation_model,
    }, "env_present": {k: bool(os.getenv(k)) for k in [
        "DATABASE_URL", "PGPASSFILE", "LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL",
        "SPRING_BOOT_BASE_URL", "AI_RAG_RETRIEVAL_MODE", "AI_RAG_INDEX_MANIFEST", "QDRANT_API_KEY",
    ]}}
    report["postgres"] = probe_postgres()
    report["checkpoint_smoke"] = {"passed": False, "error_code": "postgres_not_ready"}
    if report["postgres"].get("schema_ready") and args.checkpoint_trace:
        try:
            report["checkpoint_smoke"] = asyncio.run(checkpoint_smoke(root, args.checkpoint_trace))
        except Exception as exc:
            report["checkpoint_smoke"] = {"passed": False, "error_type": type(exc).__name__}
    for name, url in {
        "qdrant": "http://127.0.0.1:6333/",
        "java": str(settings.spring_boot_base_url).rstrip("/") + "/api/music/entities/search?keyword=Jay%20Chou&type=artist&limit=1",
        "fastapi": "http://127.0.0.1:8001/health",
        "frontend": "http://127.0.0.1:5173/",
        "frontend_api_proxy": "http://127.0.0.1:5173/api/ai/worldline/sessions/environment-readiness-missing",
    }.items():
        report[name] = http_probe(url, expected=404 if name == "frontend_api_proxy" else 200)
    try:
        response = httpx.get(str(settings.base_url).rstrip("/") + "/models", timeout=20,
                             headers={"Authorization": "Bearer " + settings.api_key.get_secret_value()})
        ids = [m.get("id") for m in response.json().get("data", [])] if response.is_success else []
        report["model"] = {"configured": bool(settings.api_key.get_secret_value()),
                           "reachable": response.status_code == 200, "http_status": response.status_code,
                           "configured_models_available": all(m in ids for m in [settings.model, settings.generation_model])}
    except Exception as exc:
        report["model"] = {"configured": True, "reachable": False, "error_type": type(exc).__name__}
    try:
        runtime = create_worldline_retrieval_runtime(project_root=root)
        manifest = runtime.retriever.candidate_provider.binding.manifest
        report["corpus"] = audit_corpus(root, root / manifest.source_path)
        report["index"] = verify_index(root, runtime, approve=args.approve_index)
        report["retrieval_smoke"] = asyncio.run(retrieval_smoke(runtime))
    except Exception as exc:
        report["index_error"] = {"error_type": type(exc).__name__}
    blockers = []
    checks = {
        "postgres_checkpoint": report["checkpoint_smoke"].get("passed"),
        "approved_index": report.get("index", {}).get("approved"),
        "corpus_source_context": report.get("corpus", {}).get("ready"),
        "hybrid_retrieval": report.get("retrieval_smoke", {}).get("passed"),
        "model": report["model"].get("reachable") and report["model"].get("configured_models_available"),
        **{n: report[n].get("passed") for n in ["java", "qdrant", "fastapi", "frontend", "frontend_api_proxy"]},
    }
    blockers.extend(k for k, passed in checks.items() if not passed)
    report.update(blockers=blockers, environment_ready=not blockers,
                  can_run_online_e2e_postgres_restart=not blockers,
                  final_online_e2e_performed=False, postgres_restart_acceptance_performed=False)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ["blockers", "environment_ready", "can_run_online_e2e_postgres_restart"]}))
    return 0 if not blockers else 2


if __name__ == "__main__":
    raise SystemExit(main())
