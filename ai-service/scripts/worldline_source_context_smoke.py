"""Real corpus transport smoke, without generation, eval, or PostgreSQL restart."""

from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

from app.agent.foundation.subgraphs.worldline.retrieval import (
    RetrievalRuntimeSettings, WorldLineContextAdapter, create_worldline_retrieval_runtime,
)
from app.rag.chunk.config import load_chunk_config
from app.rag.chunk.engine import ChunkEngine
from app.rag.clean.models import CleanedDocument
from app.rag.parse_normalize.models import NormalizedDocument
from app.rag.query_understanding.models import (
    FilterCondition, FilterGroup, FilterOperator, MetadataField, MetadataFilter,
    QueryType, QueryVariant, QueryVariantKind, RetrievalStrategy, SearchChannel, SearchPlan,
)
from app.rag.retrieval.fusion import FusedSearchHit
from app.rag.retrieval.selection import _candidate_from_hit
from app.rag.source_context import source_context
from scripts.worldline_source_context_preparation import confined, html_text, verified_raw


def replay_chunk_metadata(root: Path, preparation: Path) -> dict:
    metadata = json.loads((preparation / "document_metadata.json").read_text(encoding="utf-8"))
    summary = json.loads((preparation / "preparation.json").read_text(encoding="utf-8"))
    original = {}
    for line in confined(root, summary["input_knowledge_path"]).open(encoding="utf-8"):
        row = json.loads(line)
        original[row["chunk_id"]] = {k: v for k, v in row.items() if k not in {"metadata", "enrichment", "knowledge"}}
    engine = ChunkEngine(load_chunk_config())
    count = 0
    for doc_id, patched in metadata.items():
        sid = patched["source_metadata"]["source_id"]
        cleaned_payload = json.loads((root / "data/cleaned/v0.2" / sid / "document.json").read_text(encoding="utf-8"))
        cleaned_payload["metadata"] = patched
        cleaned = CleanedDocument.model_validate(cleaned_payload)
        normalized = NormalizedDocument.model_validate(json.loads(confined(root, cleaned.normalized_path).read_text(encoding="utf-8")))
        result = engine.chunk(cleaned, normalized)
        if result.document_id != doc_id or not result.validation.valid:
            raise ValueError("chunk_metadata_replay_invalid")
        for chunk in [*result.parents, *result.children]:
            if chunk.model_dump(mode="json", exclude={"metadata"}) != original[chunk.chunk_id]:
                raise ValueError("chunk_metadata_replay_changed_ids_or_source_text")
            if chunk.metadata["document_metadata"] != patched:
                raise ValueError("chunk_engine_document_metadata_lost")
            count += 1
    if count != len(original):
        raise ValueError("chunk_metadata_replay_count_mismatch")
    return {"passed": True, "document_count": len(metadata), "chunk_count": count,
            "normal_chunk_engine_metadata_inheritance_verified": True,
            "all_original_ids_text_table_data_and_structural_offsets_unchanged": True}


def verify_raw_origin(root: Path, packet: dict, document_metadata: dict) -> None:
    sm = document_metadata["source_metadata"]
    _, raw = verified_raw(root, sm)
    if packet["response_sha256"] != sha256(raw).hexdigest():
        raise ValueError("packet_upstream_response_sha_mismatch")
    matches = [c for c in document_metadata["source_context_captures"] if c["content_sha256"] == packet["content_sha256"]]
    origins = [p for p in document_metadata["source_context_capture_provenance"] if p["content_sha256"] == packet["content_sha256"]]
    if len(matches) != 1 or len(origins) != 1:
        raise ValueError("packet_original_capture_missing_or_ambiguous")
    if any(packet[k] != matches[0][k] for k in matches[0]):
        raise ValueError("packet_changed_from_original_capture")
    _, text = html_text(raw)
    origin = origins[0]
    if (sha256(text.encode()).hexdigest() != origin["source_text_sha256"]
            or text[origin["source_text_start"]:origin["source_text_end"]] != packet["content"]):
        raise ValueError("packet_absolute_source_offsets_mismatch")


def query_plan(runtime, query: str, entity_ids: list[str]) -> SearchPlan:
    return SearchPlan(
        plan_id="query-plan:" + sha256(query.encode()).hexdigest()[:24],
        original_query=query, standalone_query=query, query_types=[QueryType.EXPLORATORY],
        queries=[QueryVariant(text=query, kind=QueryVariantKind.ORIGINAL, channel=SearchChannel.BOTH, weight=1)],
        metadata_filter=MetadataFilter(root=FilterGroup(operator="all", children=[
            FilterCondition(field=MetadataField.VERIFICATION_STATUSES, operator=FilterOperator.MATCH_VALUE, value="SUPPORTED"),
            *[
            FilterCondition(field=MetadataField.ENTITY_IDS, operator=FilterOperator.MATCH_VALUE, value=e)
            for e in entity_ids
            ],
        ])), strategy=RetrievalStrategy.HYBRID,
        candidate_top_k=40, final_top_k=6, needs_rerank=False,
        analysis_version="v1", prompt_version="v1", index_profile=runtime.index_profile,
    )


async def smoke(root: Path, manifest_path: Path, preparation: Path) -> dict:
    runtime = create_worldline_retrieval_runtime(settings=RetrievalRuntimeSettings(
        retrieval_mode="hybrid", index_manifest=manifest_path.as_posix(), rerank_mode="off",
    ), project_root=root)
    service = runtime.retriever.candidate_provider
    cases = [
        ("article", "方文山特别提到了与周杰伦的默契合作，两个美学概念不同、频率不同的人碰撞出新的东西", ["person:vincent_fang", "artist:jay_chou"], "claim:603dddf836fa9457f64461ee"),
        ("interview", "方文山都是先有曲再填词，双截棍原始的哼哼哈嘿，在词和曲的配合上", ["person:vincent_fang", "song:nunchucks"], "claim:33051d48b804d35fe8b36525"),
    ]
    report = {"chunk_metadata_replay": replay_chunk_metadata(root, preparation), "cases": []}
    try:
        origin_count = 0
        missing_count = 0
        projection = root / service.binding.manifest.projection_path
        with projection.open(encoding="utf-8") as handle:
            for line in handle:
                payload = json.loads(line)["payload"]
                packets = payload.get("relation_source_contexts", [])
                missing_count += not packets
                for packet in packets:
                    verify_raw_origin(root, packet, payload["document_metadata"])
                    if (packet["source_id"] != payload["source_id"]
                            or packet["chunk_id"] != payload["chunk_id"]
                            or packet["document_id"] != payload["parent_id"]
                            or packet["source_content_sha256"] != payload["source_content_sha256"]):
                        raise ValueError("native_packet_source_chunk_parent_hash_mismatch")
                    origin_count += 1
        report["all_projected_packet_raw_origins"] = {"passed": True, "packet_count": origin_count,
                                                       "children_without_bound_context": missing_count}
        for kind, query, entities, claim_id in cases:
            plan = query_plan(runtime, query, entities)
            result = await service.retrieve(plan)
            if result.status.value != "success" or result.failed_channels:
                raise ValueError("real_hybrid_query_failed")
            adapter = WorldLineContextAdapter(service.config)
            pack, documents = adapter.build_pack(plan, result), adapter.adapt(result, plan)
            matched = [(item, doc, c) for item, doc in zip(pack.items, documents, strict=True)
                       for c in item.relation_source_contexts if c.claim_id == claim_id]
            if len(matched) != 1:
                raise ValueError("real_source_packet_not_in_context_pack:" + kind)
            item, document, context = matched[0]
            packet = context.model_dump(mode="json")
            if source_context(document, claim_id, context.anchor.exact_span) != context:
                raise ValueError("worldline_runtime_source_binding_rejected")
            candidate = next(c for c in result.candidates if c.primary_child_id == item.child_id)
            live_packets = candidate.metadata["relation_source_contexts"]
            if packet not in live_packets or packet not in document.metadata["relation_source_contexts"]:
                raise ValueError("context_pack_packet_changed")
            verify_raw_origin(root, packet, item.document_metadata)
            if item.source_provenance != {k: document.metadata[k] for k in item.source_provenance}:
                raise ValueError("source_provenance_lost")
            if kind == "interview" and {s.role for s in context.segments} != {"interview_question", "interview_answer", "speaker_binding"}:
                raise ValueError("interview_question_or_speaker_omitted")
            if kind == "article" and {s.role for s in context.segments} != {"article_sentence"}:
                raise ValueError("article_context_role_missing")
            tampering = {}
            for name, mutate in [
                ("publisher", lambda p: p.update(publisher="unbound publisher")),
                ("content_hash", lambda p: p.update(content_sha256="0" * 64)),
                ("source_url", lambda p: p.update(source_url="https://example.org/unbound")),
                ("claim_id", lambda p: p.update(claim_id="claim:unbound")),
            ]:
                changed = deepcopy(packet)
                mutate(changed)
                untrusted = document.model_copy(update={"metadata": {**document.metadata, "relation_source_contexts": [changed]}})
                tampering[name] = source_context(untrusted, claim_id, context.anchor.exact_span) is None
            changed = {**packet, "response_sha256": "0" * 64}
            try:
                verify_raw_origin(root, changed, item.document_metadata)
                tampering["raw_response_hash"] = False
            except ValueError:
                tampering["raw_response_hash"] = True
            missing = document.model_copy(update={"metadata": {k: v for k, v in document.metadata.items() if k != "relation_source_contexts"}})
            missing_remains_missing = source_context(missing, claim_id, context.anchor.exact_span) is None
            original_missing_count = 0
            for other_item, other_doc in zip(pack.items, documents, strict=True):
                other_candidate = next(c for c in result.candidates if c.primary_child_id == other_item.child_id)
                if not other_candidate.metadata.get("relation_source_contexts"):
                    original_missing_count += 1
                    if (other_item.relation_source_contexts or other_doc.metadata.get("relation_source_contexts")
                            or any(source_context(other_doc, c["claim_id"], c["evidence_span"]) is not None
                                   for c in other_doc.metadata.get("supported_claims", []))):
                        raise ValueError("missing_real_source_context_was_invented")
            if not all(tampering.values()) or not missing_remains_missing:
                raise ValueError("source_packet_fail_closed_smoke_failed")
            report["cases"].append({
                "kind": kind, "query": query, "metadata_filter_entity_ids": entities,
                "metadata_filter_verification_status": "SUPPORTED",
                "status": result.status.value, "failed_channels": [], "candidate_count": len(result.candidates),
                "context_pack_count": len(pack.items), "warnings": result.warnings + pack.warnings,
                "claim_id": claim_id, "source_id": item.source_id, "doc_id": item.doc_id,
                "parent_id": item.parent_id, "chunk_id": item.child_id,
                "source_provenance": item.source_provenance, "packet": packet,
                "entire_packet_identical_in_candidate_context_pack_and_worldline": True,
                "original_raw_response_and_absolute_offsets_verified": True,
                "worldline_runtime_binding_accepted": True, "tampering_rejected": tampering,
                "missing_context_remains_missing": missing_remains_missing,
                "actual_retrieved_items_without_context_verified": original_missing_count,
            })
        # Exercise the existing retrieval content-hash guard on a real point.
        cid = report["cases"][0]["chunk_id"]
        payload = next(json.loads(line)["payload"] for line in projection.open(encoding="utf-8") if json.loads(line)["chunk_id"] == cid)
        payload["content"] += " changed"
        try:
            _candidate_from_hit(FusedSearchHit(payload["point_id"], 0.5, payload, []))
            raise AssertionError("changed raw Child unexpectedly accepted")
        except ValueError as exc:
            if str(exc) != "retrieval_source_content_hash_mismatch":
                raise
        report["retrieval_child_hash_tampering_rejected"] = True
        report.update(passed=True, final_online_e2e_performed=False, postgres_restart_acceptance_performed=False,
                      user_request_generation_performed=False, semantic_evaluation_performed=False)
        return report
    finally:
        await service.backend.client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--preparation", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    result = asyncio.run(smoke(root, args.manifest, confined(root, args.preparation)))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": result["passed"], "cases": [c["kind"] for c in result["cases"]]}))


if __name__ == "__main__":
    main()
