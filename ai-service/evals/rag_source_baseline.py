"""Read-only Gold V5 measurement of the real RagSource.collect pipeline.

Run from ai-service: .venv/Scripts/python.exe -m evals.rag_source_baseline
Observers delegate to production functions and never change their inputs/results.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import platform
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

import httpx
from qdrant_client import AsyncQdrantClient

from app.agent.foundation import rag_source
from app.rag.retrieval import service as retrieval_module
from app.rag.retrieval.binding import load_retrieval_index_binding
from app.rag.retrieval.config import load_retrieval_config
from app.rag.retrieval.qdrant import QdrantHybridSearchBackend
from app.rag.reranking.config import load_rerank_config


AI_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = AI_ROOT.parent
RELEASE_ROOT = AI_ROOT / "data/reruns/jay-chou-b-20261016-182958/full-20261016-213514"
INDEX_DIR = RELEASE_ROOT / "data/indexing/v1/knowledge-v2-e40ab37ffcc7bcf0/v4"
GOLD = AI_ROOT / "evals/datasets/rag_eval_gold_v5.frozen.jsonl"
PROJECTION = INDEX_DIR / "index_projection.jsonl"
MANIFEST = INDEX_DIR / "index_build_manifest.json"
OUTPUT = REPO_ROOT / "workspace/moodflow-review/rag-source-baseline-v5-20261017"
GOLD_SHA = "b708332729177a095cb0993381bc9b3ffaef7808846e61bbdb8cbd0877afbfad"
PROJECTION_SHA = "80b1de8e0ea7f72cf44bdc6266888cee85ff73ae56748bd98ea9af289d4bcf7b"
COLLECTION = "music_knowledge_v2_e40ab37ffcc7bcf0_v1_v2_v4"
LABEL_COUNTS = {"full": 18, "partial": 12, "insufficient": 13, "needs_input": 7}
METRICS = ("recall_at_40", "recall_at_12", "precision_at_12", "mrr")

# Reporting-only, fixed before retrieval. V5 has no question-type annotations;
# V4 expected_query_types is empty for all 50 cases. Never fed to the SearchPlan.
QUESTION_GROUPS = {
    "artist_overview": [1, 4],
    "style_influence": [2, 3, 7, 9],
    "song_background": [5, 6, 8, 46, 47],
    "collaboration": [23, 24, 25, 26],
    "comparison": [31, 32, 33, 34, 35],
    "timeline": [36, 37, 38, 39, 40],
    "source_request": [42, 43, 44, 45],
    "context_reference": [48, 68],
    "source_specific_fact": list(range(49, 68)),
}
GROUP_BY_ID = {f"rag-v0-{i:03}": group for group, ids in QUESTION_GROUPS.items() for i in ids}


def now():
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


class Projection:
    def __init__(self, rows):
        self.children = {}
        self.parents = set()
        self.points = {}
        for row in rows:
            payload = row["payload"]
            for key in ("chunk_id", "parent_id", "doc_id"):
                if row[key] != payload[key]:
                    raise ValueError("projection_identity_mismatch:" + key)
            child, parent, point = row["chunk_id"], row["parent_id"], str(row["point_id"])
            if child in self.children or point in self.points:
                raise ValueError("projection_duplicate_identity")
            self.children[child] = payload
            self.parents.add(parent)
            self.points[point] = child

    def item(self, *, child, parent, point, relevant, **extra):
        payload = self.children.get(child)
        if payload is None or payload["parent_id"] != parent or self.points.get(str(point)) != child:
            raise ValueError("retrieved_child_projection_mismatch:" + child)
        return {"child_id": child, "parent_id": parent, "point_id": str(point),
                "doc_id": payload["doc_id"], "source_id": payload["source_id"],
                "source_title": payload.get("source_title"), "breadcrumb": payload.get("breadcrumb"),
                "hit": parent in relevant, **extra}


def guard_request(request, log):
    """Allow only metadata reads and read-only Query API POSTs on this collection."""
    allowed = {("GET", "/"), ("GET", f"/collections/{COLLECTION}"),
               ("POST", f"/collections/{COLLECTION}/points/query/batch"),
               ("POST", f"/collections/{COLLECTION}/points/query")}
    if (request.url.host not in {"localhost", "127.0.0.1", "::1"}
            or request.url.port != 6333 or request.url.scheme != "http"
            or (request.method, request.url.path) not in allowed):
        raise ValueError("baseline_qdrant_request_not_read_only")
    entry = {"method": request.method, "path": request.url.path}
    if request.method == "POST":
        body = json.loads(request.content)
        queries = body.get("searches", [body])
        entry["queries"] = [{"using": q.get("using"), "limit": q.get("limit"),
                              "params": q.get("params"), "filter": q.get("filter"),
                              "bm25": q.get("query") if q.get("using") == "bm25" else None}
                             for q in queries]
    log.append(entry)


def score_case(case, rrf, selected, reranked):
    relevant = set(case["relevant_parent_ids"])
    stages = {"rrf_top40": rrf[:40], "selection": selected, "rerank_top12": reranked[:12]}
    hits = {key: sorted(relevant & {r["parent_id"] for r in rows}) for key, rows in stages.items()}
    missing = {key: sorted(relevant - set(ids)) for key, ids in hits.items()}
    result = {"stage_hits": hits, "stage_missing": missing, "metrics": None}
    if case["answerability"] not in {"full", "partial"}:
        return result
    if not relevant or len(case["relevant_parent_ids"]) != len(relevant):
        raise ValueError("scored_gold_parents_empty_or_duplicate")
    # Do not deduplicate before the Child top-40 cutoff. Precision denominator
    # remains 12 even if the pipeline returns fewer than twelve unique Parents.
    unique12 = list(dict.fromkeys(row["parent_id"] for row in reranked[:12]))
    first = next((i for i, row in enumerate(reranked[:12], 1) if row["parent_id"] in relevant), None)
    result["metrics"] = {"recall_at_40": len(hits["rrf_top40"]) / len(relevant),
                         "recall_at_12": len(set(unique12) & relevant) / len(relevant),
                         "precision_at_12": len(set(unique12) & relevant) / 12,
                         "mrr": 1 / first if first else 0,
                         "first_hit_child_rank": first, "unique_rerank_parent_count": len(unique12)}
    return result


def summarize(records):
    scored = [row for row in records if row["scored"]]

    def mean(rows):
        return {"case_count": len(rows), "case_ids": [r["case_id"] for r in rows],
                **{key: math.fsum(r["metrics"][key] for r in rows) / len(rows) if rows else None
                   for key in METRICS}}

    groups = sorted({r["question_group"] for r in records})
    return {"all_scored": mean(scored),
            "by_answerability": {label: mean([r for r in scored if r["answerability"] == label])
                                 for label in ("full", "partial")},
            "by_question_group": {group: mean([r for r in scored if r["question_group"] == group])
                                  for group in groups},
            "unscored_counts": dict(Counter(r["answerability"] for r in records if not r["scored"]))}


def protected_hashes():
    paths = [GOLD, AI_ROOT / "evals/datasets/rag_eval_gold_v4.frozen.jsonl", PROJECTION, MANIFEST]
    for directory in ("retrieval", "reranking", "query_understanding"):
        paths.extend(sorted((AI_ROOT / "app/rag" / directory).rglob("*.py")))
        paths.extend(sorted((AI_ROOT / "app/rag" / directory).rglob("*.yaml")))
    paths.extend([Path(rag_source.__file__), AI_ROOT / "app/rag/indexing/dense.py"])
    return {p.relative_to(REPO_ROOT).as_posix(): sha256(p) for p in paths}


def preflight():
    if sha256(GOLD) != GOLD_SHA or sha256(PROJECTION) != PROJECTION_SHA:
        raise ValueError("baseline_frozen_input_hash_mismatch")
    cases = read_jsonl(GOLD)
    if len(cases) != 50 or Counter(c["answerability"] for c in cases) != LABEL_COUNTS:
        raise ValueError("baseline_gold_population_mismatch")
    if len({c["case_id"] for c in cases}) != 50 or set(GROUP_BY_ID) != {c["case_id"] for c in cases}:
        raise ValueError("baseline_case_id_or_group_mismatch")
    projection = Projection(read_jsonl(PROJECTION))
    if len(projection.children) != 691 or len(projection.parents) != 413:
        raise ValueError("baseline_projection_population_mismatch")
    for case in cases:
        if not set(case["relevant_parent_ids"]) <= projection.parents:
            raise ValueError("baseline_gold_parent_outside_projection")
    config, rerank = load_retrieval_config(), load_rerank_config()
    binding = load_retrieval_index_binding(RELEASE_ROOT, MANIFEST, config)
    if binding.profile.collection_name != COLLECTION or binding.manifest.projected_count != 691:
        raise ValueError("baseline_binding_mismatch")
    plan = rag_source.literal_search_plan("baseline", binding.profile, 40)
    actual = (plan.candidate_top_k, plan.final_top_k, config.fusion.rrf_k,
              config.fusion.dense_weight, config.fusion.lexical_weight, config.bm25.k,
              config.bm25.b, config.qdrant.hnsw_ef, config.qdrant.timeout_seconds,
              config.selection.broad_document_cap, config.selection.source_cap, rerank.top_k)
    if actual != (40, 12, 60, 1.0, 1.0, 1.2, 0.75, 128, 5, 4, 5, 12):
        raise ValueError("baseline_current_parameters_drifted")
    return cases, projection, config, rerank, binding


def collect_case(case, projection, binding, config, request_log):
    observation = {}
    relevant = set(case["relevant_parent_ids"])
    original_rrf = retrieval_module.weighted_rrf
    original_select = retrieval_module.select_candidates

    def hit_row(hit, rank):
        return projection.item(child=hit.payload["chunk_id"], parent=hit.payload["parent_id"],
                               point=hit.point_id, relevant=relevant, rank=rank,
                               fusion_score=hit.fusion_score,
                               channels=sorted({e.channel.value for e in hit.evidence}),
                               channel_evidence=[e.model_dump(mode="json") for e in hit.evidence])

    def candidate_row(candidate, rank):
        for child in candidate.matched_child_ids:
            if projection.children[child]["parent_id"] != candidate.parent_id:
                raise ValueError("baseline_collapsed_child_parent_mismatch")
        return projection.item(child=candidate.primary_child_id, parent=candidate.parent_id,
                               point=candidate.point_id, relevant=relevant, rank=rank,
                               fusion_score=candidate.fusion_score,
                               matched_child_ids=candidate.matched_child_ids,
                               channel_evidence=[e.model_dump(mode="json") for e in candidate.channel_evidence])

    def observe_rrf(results, *, k):
        fused = original_rrf(results, k=k)
        if "rrf_top40" in observation:
            raise ValueError("baseline_unexpected_retrieval_fallback")
        observation["rrf_top40"] = [hit_row(hit, i) for i, hit in enumerate(fused[:40], 1)]
        observation["fusion_union_count"] = len(fused)
        observation["legs"] = [{"channel": r.leg.channel.value, "weight": r.leg.weight,
                                "text": r.leg.text, "hit_count": len(r.hits), "error_code": r.error_code}
                               for r in results]
        return fused

    def observe_selection(hits, plan, cfg):
        selected, warnings = original_select(hits, plan, cfg)
        observation["selection_input_rrf"] = [hit_row(hit, i) for i, hit in enumerate(hits, 1)]
        observation["selection"] = [candidate_row(c, i) for i, c in enumerate(selected, 1)]
        observation["selection_warnings"] = warnings
        observation["plan"] = plan.model_dump(mode="json")
        return selected, warnings

    class ObservedReranker(rag_source.QwenCandidateReranker):
        async def rerank(self, plan, pool):
            observation["retrieval_status"] = pool.status.value
            observation["retrieval_warnings"] = pool.warnings
            observation["retrieval_timings"] = pool.timings.model_dump(mode="json")
            observation["rerank_input"] = [candidate_row(c, i) for i, c in enumerate(pool.candidates, 1)]
            result = await super().rerank(plan, pool)
            observation["rerank_status"] = result.status.value
            observation["rerank_fallback_used"] = result.fallback_used
            observation["rerank_warnings"] = result.warnings
            observation["rerank_timings"] = result.timings.model_dump(mode="json")
            observation["rerank_top12"] = [
                {**candidate_row(r.candidate, r.rerank_rank), "rerank_score": r.rerank_score,
                 "retrieval_rank": r.retrieval_rank} for r in result.candidates]
            return result

    class ReadOnlyBackend(QdrantHybridSearchBackend):
        def __init__(self, cfg):
            async def guard(request):
                guard_request(request, request_log)
            client = AsyncQdrantClient(url=cfg.qdrant.url,
                                       api_key=os.getenv(cfg.qdrant.api_key_env) or None,
                                       timeout=cfg.qdrant.timeout_seconds,
                                       prefer_grpc=cfg.qdrant.prefer_grpc, trust_env=cfg.qdrant.trust_env,
                                       event_hooks={"request": [guard]})
            super().__init__(cfg, client=client)

    source = rag_source.RagSource(settings=SimpleNamespace(retrieval_mode="hybrid", index_manifest=None),
                                  binding=binding)
    started = now()
    with (patch.object(retrieval_module, "weighted_rrf", observe_rrf),
          patch.object(retrieval_module, "select_candidates", observe_selection),
          patch.object(retrieval_module, "QdrantHybridSearchBackend", ReadOnlyBackend),
          patch.object(rag_source, "QwenCandidateReranker", ObservedReranker)):
        items = source.collect(SimpleNamespace(query=case["question"]), limit=40)
    healthy = (observation.get("retrieval_status") == "success"
               and observation.get("rerank_status") == "success" and not items.status
               and not observation.get("rerank_fallback_used")
               and len(observation.get("rrf_top40", [])) == 40
               and len(observation.get("rerank_top12", [])) == 12)
    record = {"case_id": case["case_id"], "question": case["question"],
              "answerability": case["answerability"], "scored": case["answerability"] in {"full", "partial"},
              "question_group": GROUP_BY_ID[case["case_id"]], "started_at": started, "finished_at": now(),
              "relevant_parent_ids": case["relevant_parent_ids"], "healthy": healthy,
              "source_status": items.status, "source_audit": items.audit, **observation}
    # Validate the serialized RagSource result against the actual reranker output.
    if healthy:
        if [i.metadata["chunk_id"] for i in items] != [r["child_id"] for r in record["rerank_top12"]]:
            raise ValueError("baseline_source_output_mismatch")
        if len({r["parent_id"] for r in record["selection"]}) != len(record["selection"]):
            raise ValueError("baseline_selection_parent_not_unique")
        record.update(score_case(case, record["rrf_top40"], record["selection"], record["rerank_top12"]))
    else:
        record["metrics"] = None
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()
    cases, projection, config, rerank, binding = preflight()
    print(json.dumps({"preflight": "passed", "cases": 50, "parents": 413, "children": 691,
                      "collection": COLLECTION, "doc_cap": 4, "final_top_k": 12}), flush=True)
    if args.preflight_only:
        return 0
    # Never overwrite an earlier measurement, including an interrupted run.
    OUTPUT.mkdir(parents=True, exist_ok=False)
    before = protected_hashes()
    metadata = {"started_at": now(), "entry": "app.agent.foundation.rag_source.RagSource.collect",
                "binding_root": str(RELEASE_ROOT), "collection": COLLECTION,
                "gold_sha256": GOLD_SHA, "projection_sha256": PROJECTION_SHA,
                "label_counts": LABEL_COUNTS, "retrieval_config": config.model_dump(mode="json"),
                "rerank_config": rerank.model_dump(mode="json"),
                "approved_scope_difference": "User approved actual exploratory broad_document_cap=4; no edits.",
                "other_scope_differences": ["RRF overfetch=2: selection consumes 80 fused Children; recall@40 uses only first 40.",
                                            "Selection caps are soft: existing overflow refill is preserved.",
                                            "Planner final_top_k=10 concern: actual literal_search_plan yields 12; no planner invoked.",
                                            "SourceRegistry default candidate_limit=24 is bypassed by explicit task limit=40."],
                "metric_definitions": {"recall_at_40": "unique gold Parents in first 40 fused Children / gold Parent count",
                                       "recall_at_12": "unique gold Parents in returned 12 reranked Children / gold Parent count",
                                       "precision_at_12": "unique gold Parents in returned 12 reranked Children / 12",
                                       "mrr": "1 / first relevant Child rank in returned rerank top-12, else 0; macro mean across 30"},
                "question_groups": QUESTION_GROUPS, "question_group_provenance": "Analyst fixed case-ID taxonomy; absent in Gold V5; reporting only.",
                "python": platform.python_version(), "protected_before": before}
    write_json(OUTPUT / "run_manifest.json", metadata)
    records, request_log = [], []
    error_type = None
    try:
        # Production model implementations, pinned config, no generation or judge.
        # Loading is outside the per-query timeout; inference remains bounded at 30s.
        encoder, _, scorer, _ = rag_source._local_models()
        print("Preparing pinned embedding and reranking models", flush=True)
        encoder._encoder._ensure_loaded()
        scorer.prepare()
        import torch
        metadata["runtime"] = {"torch": torch.__version__, "cuda": torch.cuda.is_available(),
                               "device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu",
                               "embedding_device": encoder._encoder._device, "reranker_device": scorer._device}
        original_sync_send = httpx.Client.send

        def guarded_sync_send(client, request, *args, **kwargs):
            # qdrant-client's compatibility probe uses a background sync Client.
            guard_request(request, request_log)
            return original_sync_send(client, request, *args, **kwargs)

        with (patch.object(httpx.Client, "send", guarded_sync_send),
              (OUTPUT / "cases.jsonl").open("x", encoding="utf-8") as stream):
            for index, case in enumerate(cases, 1):
                request_start = len(request_log)
                row = collect_case(case, projection, binding, config, request_log)
                row["qdrant_requests"] = request_log[request_start:]
                records.append(row)
                stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
                stream.flush()
                print(json.dumps({"progress": f"{index}/50", "case_id": row["case_id"],
                                  "healthy": row["healthy"], "metrics": row["metrics"]}), flush=True)
                if not row["healthy"]:
                    raise RuntimeError("baseline_pipeline_not_healthy")
    except Exception as exc:
        error_type = type(exc).__name__
        print(json.dumps({"run_error": error_type}), flush=True)
    finally:
        after = protected_hashes()
        metadata.update(finished_at=now(), completed_case_count=len(records), error_type=error_type,
                        status="complete" if len(records) == 50 and error_type is None and before == after else "incomplete",
                        protected_after=after, protected_unchanged=before == after,
                        qdrant_request_counts=dict(Counter(f"{r['method']} {r['path']}" for r in request_log)))
        write_json(OUTPUT / "run_manifest.json", metadata)
        write_json(OUTPUT / "qdrant_requests.json", request_log)
        if metadata["status"] == "complete":
            write_json(OUTPUT / "metrics.json", summarize(records))
        print(json.dumps({"status": metadata["status"], "output": str(OUTPUT)}), flush=True)
    return 0 if metadata["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
