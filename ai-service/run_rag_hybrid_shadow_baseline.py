"""Run the frozen Gold V4 hybrid baseline in two offline model phases.

The retrieval phase records the real Top40 and separate Top100 diagnostics.
The rerank phase starts in a fresh process, so both Qwen models need not share
GPU memory. Neither phase changes serving configuration or retrieval settings.
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import hashlib
import json
from collections import Counter
from pathlib import Path

from langchain_core.messages import HumanMessage

from app.agent.foundation.subgraphs.worldline.retrieval import WorldLineContextAdapter
from app.rag.evaluation import load_evaluation_cases, write_jsonl
from app.rag.evaluation.group_aware import score_stage
from app.rag.evaluation.models import (
    RAGEvaluationTrace,
    TraceStageStatuses,
)
from app.rag.evaluation.trace_builder import build_evaluation_trace
from app.rag.evaluation.v4_answerability import (
    evaluate_v4_answerability,
    supported_evidence_units,
)
from app.rag.query_understanding import (
    QueryUnderstandingService,
    create_query_understanding_graph,
    create_query_understanding_state,
    load_query_understanding_config,
)
from app.rag.query_understanding.models import QueryUnderstandingStatus, SearchChannel, SearchPlan
from app.rag.reranking import QwenCandidateReranker, build_rerank_inputs, load_rerank_config
from app.rag.reranking.runtime import (
    ASSISTANT_SUFFIX,
    DOCUMENT_PREFIX,
    QUERY_PREFIX,
    SYSTEM_PREFIX,
    QwenRerankScorer,
)
from app.rag.retrieval import (
    HybridRetrievalService,
    RetrievalResult,
    load_retrieval_config,
    load_retrieval_index_binding,
)
from app.rag.retrieval.fusion import LegSearchResult, weighted_rrf
from app.rag.retrieval.qdrant import QdrantHybridSearchBackend
from app.rag.retrieval.selection import select_candidates
from app.schemas.player import PlayerContext


ROOT = Path(__file__).resolve().parent
GOLD = ROOT / "evals/datasets/rag_eval_gold_v4.frozen.jsonl"
MANIFEST = ROOT / "data/indexing/v1/knowledge-v2-8608ffc2fa72e441/v3/index_build_manifest.json"
RETRIEVAL = ROOT / "evals/artifacts/gold_v4_hybrid_shadow_retrieval_50_20260917.jsonl"
TRACES = ROOT / "evals/artifacts/gold_v4_hybrid_shadow_trace_50_20260917.jsonl"
REPORT = ROOT / "evals/reports/gold_v4_hybrid_shadow_baseline_20260917.json"
REPORT_MD = ROOT / "evals/reports/gold_v4_hybrid_shadow_baseline_20260917.md"
RUN_ID = "gold-v4-hybrid-shadow-50-20260917"
DIAGNOSTIC_CHILD_LIMIT = 600


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class DiagnosticBackend:
    """Record Top100 without changing the result returned to RetrievalService."""

    def __init__(self, delegate: QdrantHybridSearchBackend) -> None:
        self.delegate = delegate
        self.results: list[LegSearchResult] | None = None
        self.diagnostic: list[LegSearchResult] | None = None
        self.calls = 0

    async def validate_binding(self, binding) -> None:
        await self.delegate.validate_binding(binding)

    async def search(self, binding, legs, query_filter, *, limit: int) -> list[LegSearchResult]:
        actual = await self.delegate.search(binding, legs, query_filter, limit=limit)
        if self.calls == 0:
            self.results = actual
            self.diagnostic = await self.delegate.search(
                binding, legs, query_filter, limit=max(DIAGNOSTIC_CHILD_LIMIT, limit)
            )
        self.calls += 1
        return actual

    def reset(self) -> None:
        self.results = None
        self.diagnostic = None
        self.calls = 0


def _rankings(
    plan: SearchPlan,
    results: list[LegSearchResult],
    config,
    gold_parent_ids: set[str],
) -> dict[str, list[dict]]:
    diagnostic_plan = plan.model_copy(update={"candidate_top_k": 100})
    rankings: dict[str, list[dict]] = {}
    for name, channel in (("dense", SearchChannel.DENSE), ("bm25", SearchChannel.LEXICAL), ("hybrid", None)):
        selected_legs = [item for item in results if channel is None or item.leg.channel is channel]
        fused = weighted_rrf(selected_legs, k=config.fusion.rrf_k)
        candidates, _ = select_candidates(fused, diagnostic_plan, config)
        ranking = []
        for rank, candidate in enumerate(candidates, start=1):
            evidence = [item.model_dump(mode="json") for item in candidate.channel_evidence]
            primary_evidence = [item for item in candidate.channel_evidence
                                if item.chunk_id == candidate.primary_child_id]
            contribution = {
                "dense": sum(item.weight / (config.fusion.rrf_k + item.rank)
                             for item in primary_evidence if item.channel is SearchChannel.DENSE),
                "bm25": sum(item.weight / (config.fusion.rrf_k + item.rank)
                            for item in primary_evidence if item.channel is SearchChannel.LEXICAL),
            }
            ranking.append({
                "parent_id": candidate.parent_id,
                "rank": rank,
                "primary_child_id": candidate.primary_child_id,
                "raw_score": primary_evidence[0].raw_score,
                "source_id": candidate.source_id,
                "metadata": candidate.metadata,
                "gold_or_acceptable_parent": candidate.parent_id in gold_parent_ids,
                "dense_rank": min((item.rank for item in primary_evidence
                                   if item.channel is SearchChannel.DENSE), default=None),
                "bm25_rank": min((item.rank for item in primary_evidence
                                  if item.channel is SearchChannel.LEXICAL), default=None),
                "dense_contribution": contribution["dense"],
                "bm25_contribution": contribution["bm25"],
                "rrf_fused_score": candidate.fusion_score,
                "leg_evidence": evidence,
            })
        rankings[name] = ranking
    return rankings


async def collect() -> None:
    cases = load_evaluation_cases(GOLD)
    config = load_retrieval_config()
    binding = load_retrieval_index_binding(ROOT, MANIFEST, config)
    graph = create_query_understanding_graph(service=QueryUnderstandingService(
        config=load_query_understanding_config(), index_profile=binding.profile
    ))
    backend = DiagnosticBackend(QdrantHybridSearchBackend(config))
    retriever = HybridRetrievalService(binding, config=config, backend=backend)
    rows = []
    for index, case in enumerate(cases, start=1):
        backend.reset()
        row: dict = {"case_id": case.case_id, "query": case.query}
        try:
            output = await graph.ainvoke(create_query_understanding_state(
                case.query,
                messages=[HumanMessage(content=case.query)],
                routing_context=PlayerContext(
                    has_current_song=case.current_song_id is not None,
                    current_song_id=case.current_song_id,
                ),
            ))
            understanding = output["result"]
            row["understanding_status"] = understanding.status.value
            if understanding.status is QueryUnderstandingStatus.READY:
                plan = understanding.search_plan
                assert plan is not None
                result = await retriever.retrieve(plan)
                row["plan"] = plan.model_dump(mode="json")
                row["retrieval"] = result.model_dump(mode="json")
                positive = supported_evidence_units(case)
                gold_ids = {pid for unit in positive for pid in (
                    unit.group.acceptable_parent_ids if unit.group else (unit.anchor_parent_id,)
                )}
                row["diagnostic_rankings"] = (
                    _rankings(plan, backend.diagnostic, config, gold_ids)
                    if backend.diagnostic is not None else {}
                )
                row["actual_leg_limits"] = [len(item.hits) for item in backend.results or []]
                row["diagnostic_leg_limits"] = [len(item.hits) for item in backend.diagnostic or []]
                row["retrieval_status"] = result.status.value
                row["diagnostic_errors"] = [item.error_code for item in backend.diagnostic or [] if item.error_code]
        except Exception as exc:  # Keep every case in the auditable artifact.
            row["error"] = f"{type(exc).__name__}:{str(exc)[:300]}"
        rows.append(row)
        print(f"retrieval {index}/{len(cases)} {case.case_id} {row.get('retrieval_status', row.get('understanding_status', row.get('error')))}", flush=True)
    _write_dict_jsonl(RETRIEVAL, rows)
    print(f"wrote {RETRIEVAL}")


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_dict_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) for row in rows) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


class CapturingRerankScorer:
    """Observe every existing model score before the service selects Top12."""

    def __init__(self, config) -> None:
        self.delegate = QwenRerankScorer(config)
        self.scores: list[float] | None = None

    def prepare(self) -> None:
        self.delegate.prepare()

    def score(self, inputs) -> list[float]:
        scores = self.delegate.score(inputs)
        self.scores = list(scores)
        return scores


async def rerank() -> None:
    cases = load_evaluation_cases(GOLD)
    rows = _read_jsonl(RETRIEVAL)
    if [row["case_id"] for row in rows] != [case.case_id for case in cases]:
        raise ValueError("retrieval artifact does not cover frozen Gold in order")
    config = load_retrieval_config()
    rerank_config = load_rerank_config()
    capture = CapturingRerankScorer(rerank_config.model)
    reranker = QwenCandidateReranker(rerank_config, scorer=capture)
    await reranker.prepare()
    adapter = WorldLineContextAdapter(config)
    traces = []
    diagnostics = []
    for index, (case, row) in enumerate(zip(cases, rows, strict=True), start=1):
        if "plan" not in row:
            trace = RAGEvaluationTrace(
                run_id=RUN_ID, case_id=case.case_id, query=case.query,
                statuses=TraceStageStatuses(query_understanding=row.get("understanding_status", "failed")),
                warnings=[row["error"]] if "error" in row else [],
            )
            diagnostics.append({"case_id": case.case_id, "rerank_status": "not_run"})
        else:
            plan = SearchPlan.model_validate(row["plan"])
            retrieval = RetrievalResult.model_validate(row["retrieval"])
            inputs = build_rerank_inputs(plan, retrieval, reranker.config)
            capture.scores = None
            ranked = await reranker.rerank(plan, retrieval)
            all_scores = capture.scores if ranked.status.value == "success" else None
            if all_scores is not None and len(all_scores) != len(inputs):
                raise ValueError(f"reranker score count mismatch:{case.case_id}")
            full_rank_by_index = {}
            if all_scores is not None:
                ranked_indices = sorted(
                    range(len(inputs)),
                    key=lambda i: (-all_scores[i], -retrieval.candidates[i].fusion_score,
                                   retrieval.candidates[i].point_id),
                )
                full_rank_by_index = {i: rank for rank, i in enumerate(ranked_indices, start=1)}
            documents = adapter.adapt(ranked, plan)
            by_parent = {item.parent_id: item for item in retrieval.candidates}
            from app.rag.evaluation.models import EvaluationContext
            final = [EvaluationContext(
                parent_id=str(doc.metadata.get("parent_id") or doc.id),
                rank=rank, title=doc.title, content=doc.content,
                source_id=by_parent[str(doc.metadata.get("parent_id") or doc.id)].source_id,
                source_name=doc.source, source_url=doc.url,
                matched_child_id=by_parent[str(doc.metadata.get("parent_id") or doc.id)].primary_child_id,
                token_count=doc.metadata.get("context_token_count") or by_parent[str(doc.metadata.get("parent_id") or doc.id)].parent_context.token_count,
            ) for rank, doc in enumerate(documents, start=1)]
            trace = build_evaluation_trace(
                run_id=RUN_ID, case_id=case.case_id, plan=plan, retrieval=retrieval,
                rerank=ranked, final_context=final,
                context_token_count=sum(item.token_count for item in final),
                context_token_budget=config.selection.worldline_token_budget,
                retrieval_version=config.retrieval_version,
                context_version="context-pack-v1",
                context_status="success" if final else "empty",
            )
            diagnostics.append({
                "case_id": case.case_id,
                "rerank_status": ranked.status.value,
                "fallback_used": ranked.fallback_used,
                "rerank_warnings": ranked.warnings,
                "inputs": [{"parent_id": item.parent_id, "retriever_rank": i + 1,
                            "input_length_chars": len(item.query) + len(item.document),
                            "document_length_chars": len(item.document),
                            "reranker_score": all_scores[i] if all_scores is not None else None,
                            "reranker_rank_full": full_rank_by_index.get(i),
                            "selected_top12": item.parent_id in {
                                candidate.candidate.parent_id for candidate in ranked.candidates
                            }}
                           for i, item in enumerate(inputs)],
                "outputs": [{"parent_id": item.candidate.parent_id, "retriever_rank": item.retrieval_rank,
                             "reranker_rank": item.rerank_rank, "reranker_score": item.rerank_score}
                            for item in ranked.candidates],
            })
        traces.append(trace)
        print(f"rerank {index}/{len(cases)} {case.case_id} {trace.statuses.rerank}", flush=True)
    write_jsonl(TRACES, traces)
    _write_dict_jsonl(TRACES.with_suffix(".rerank_diagnostics.jsonl"), diagnostics)
    print(f"wrote {TRACES}")


def _ids(ranking: list[dict], limit: int) -> list[str]:
    return [item["parent_id"] for item in ranking[:limit]]


def _unit_hit(case, unit, parent_ids: list[str]) -> bool:
    projected = case.model_copy(update={
        "relevant_parent_ids": [unit.anchor_parent_id],
        "evidence_groups": [unit.group] if unit.group else [],
    })
    return bool(score_stage(projected, parent_ids, metric="recall", mode="group_aware").hits)


def _failure_category(*, dense100: bool, bm25100: bool, top40: bool,
                      top12: bool, final: bool) -> str:
    if not top40 and not dense100 and not bm25100:
        return "DENSE_AND_BM25_MISS"
    if not top40:
        return "FUSION_RANKING_MISS"
    if not top12:
        return "RERANK_MISS"
    if not final:
        return "CONTEXT_DROP"
    return "SUCCESS"


def _rerank_input_lengths(rows: list[dict]) -> list[dict]:
    """Reproduce the existing tokenizer budget calculation without model inference."""
    from transformers import AutoTokenizer

    config = load_rerank_config()
    model = config.model
    tokenizer = AutoTokenizer.from_pretrained(
        model.name, revision=model.revision, local_files_only=model.local_files_only
    )
    prefix = len(tokenizer.encode(SYSTEM_PREFIX + model.instruction + QUERY_PREFIX,
                                  add_special_tokens=False))
    document_prefix = len(tokenizer.encode(DOCUMENT_PREFIX, add_special_tokens=False))
    suffix = len(tokenizer.encode(ASSISTANT_SUFFIX, add_special_tokens=False))
    reserved = prefix + document_prefix + suffix
    details = []
    for row in rows:
        if "plan" not in row:
            continue
        plan = SearchPlan.model_validate(row["plan"])
        retrieval = RetrievalResult.model_validate(row["retrieval"])
        for item in build_rerank_inputs(plan, retrieval, config):
            query_tokens = len(tokenizer.encode(QwenRerankScorer._safe_text(item.query),
                                                add_special_tokens=False))
            document_tokens = len(tokenizer.encode(QwenRerankScorer._safe_text(item.document),
                                                   add_special_tokens=False))
            query_budget = min(model.query_token_budget, model.input_token_limit - reserved - 1)
            kept_query = min(query_tokens, query_budget)
            document_budget = model.input_token_limit - reserved - kept_query
            kept_document = min(document_tokens, document_budget)
            details.append({
                "case_id": row["case_id"], "parent_id": item.parent_id,
                "query_tokens_original": query_tokens,
                "document_tokens_original": document_tokens,
                "input_tokens_after_truncation": reserved + kept_query + kept_document,
                "query_truncated": query_tokens > query_budget,
                "document_truncated": document_tokens > document_budget,
            })
    return details


def report() -> None:
    cases = load_evaluation_cases(GOLD)
    rows = _read_jsonl(RETRIEVAL)
    traces = [RAGEvaluationTrace.model_validate(row) for row in _read_jsonl(TRACES)]
    rerank_rows = _read_jsonl(TRACES.with_suffix(".rerank_diagnostics.jsonl"))
    if len(rows) != 50 or len(traces) != 50:
        raise ValueError("full 50-case baseline is required")
    if [row["case_id"] for row in rows] != [case.case_id for case in cases]:
        raise ValueError("retrieval rows mismatch frozen Gold")
    if [trace.case_id for trace in traces] != [case.case_id for case in cases]:
        raise ValueError("trace rows mismatch frozen Gold")
    if [row["case_id"] for row in rerank_rows] != [case.case_id for case in cases]:
        raise ValueError("rerank diagnostics mismatch frozen Gold")
    rerank_top_k = load_rerank_config().top_k
    for retrieval_row, trace, rerank_row in zip(rows, traces, rerank_rows, strict=True):
        if rerank_row["rerank_status"] != "success":
            continue
        inputs = rerank_row["inputs"]
        candidates = retrieval_row["retrieval"]["candidates"]
        if len(inputs) != len(candidates):
            raise ValueError(f"reranker did not score every input:{trace.case_id}")
        if sorted(item["reranker_rank_full"] for item in inputs) != list(range(1, len(inputs) + 1)):
            raise ValueError(f"reranker ranks incomplete:{trace.case_id}")
        selected = {item["parent_id"] for item in inputs if item["selected_top12"]}
        if selected != {item.parent_id for item in trace.reranked_context}:
            raise ValueError(f"reranker diagnostics and Trace differ:{trace.case_id}")
        expected_selected = {item["parent_id"] for item in inputs
                             if item["reranker_rank_full"] <= rerank_top_k}
        if selected != expected_selected:
            raise ValueError(f"captured reranker order differs from service:{trace.case_id}")
        if any(item["reranker_score"] is None for item in inputs):
            raise ValueError(f"reranker score missing:{trace.case_id}")
    aware = evaluate_v4_answerability(cases, traces)
    by_trace = {trace.case_id: trace for trace in traces}
    comparisons = []
    metric_totals = {channel: {k: 0.0 for k in (10, 20, 40, 100)}
                     for channel in ("dense", "bm25", "hybrid")}
    funnel = Counter()
    failures = Counter()
    for case, row in zip(cases, rows, strict=True):
        actual = [item["parent_id"] for item in row.get("retrieval", {}).get("candidates", [])]
        if actual != [item.parent_id for item in by_trace[case.case_id].candidate_pool]:
            raise ValueError(f"retrieval artifact and final trace differ:{case.case_id}")
        units = supported_evidence_units(case)
        if not units:
            continue
        rankings = row.get("diagnostic_rankings", {})
        if set(rankings) != set(metric_totals):
            raise ValueError(f"missing diagnostic ranking:{case.case_id}")
        trace = by_trace[case.case_id]
        for channel in metric_totals:
            for k in metric_totals[channel]:
                metric_totals[channel][k] += sum(
                    _unit_hit(case, unit, _ids(rankings[channel], k)) for unit in units
                ) / len(units)
        for unit in units:
            hit = {channel: _unit_hit(case, unit, _ids(rankings[channel], 100))
                   for channel in metric_totals}
            top40 = _unit_hit(case, unit, [x.parent_id for x in trace.candidate_pool])
            top12 = _unit_hit(case, unit, [x.parent_id for x in trace.reranked_context])
            final = _unit_hit(case, unit, [x.parent_id for x in trace.final_context])
            for key, value in (("dense_top100", hit["dense"]), ("bm25_top100", hit["bm25"]),
                               ("hybrid_top100", hit["hybrid"]), ("hybrid_top40", top40),
                               ("rerank_top12", top12), ("final_context", final)):
                funnel[key] += value
            category = _failure_category(
                dense100=hit["dense"], bm25100=hit["bm25"],
                top40=top40, top12=top12, final=final,
            )
            failures[category] += 1
            channel_pattern = ("DENSE_ONLY_RECOVERY" if hit["dense"] and not hit["bm25"]
                               else "BM25_ONLY_RECOVERY" if hit["bm25"] and not hit["dense"]
                               else "BOTH" if hit["dense"] and hit["bm25"] else "NEITHER")
            failures[channel_pattern] += 1
            dense40 = _unit_hit(case, unit, _ids(rankings["dense"], 40))
            bm2540 = _unit_hit(case, unit, _ids(rankings["bm25"], 40))
            promoted = top40 and not dense40 and not bm2540
            demoted = not top40 and (dense40 or bm2540)
            failures["HYBRID_PROMOTED_PAST_40"] += promoted
            failures["HYBRID_DEMOTED_PAST_40"] += demoted
            acceptable = (unit.group.acceptable_parent_ids if unit.group
                          else [unit.anchor_parent_id])
            accepted_set = set(acceptable)
            comparisons.append({
                "case_id": case.case_id, "unit_id": unit.unit_id, "claim": unit.claim,
                "evidence_group_id": unit.group.evidence_group_id if unit.group else None,
                "acceptable_parent_ids": acceptable,
                "dense_top100_hit": hit["dense"], "bm25_top100_hit": hit["bm25"],
                "hybrid_top100_hit": hit["hybrid"], "hybrid_actual_top40_hit": top40,
                "rerank_top12_hit": top12, "final_context_hit": final,
                "channel_pattern": channel_pattern, "failure_category": category,
                "dense_top40_hit": dense40, "bm25_top40_hit": bm2540,
                "hybrid_promoted_past_40": promoted,
                "hybrid_demoted_past_40": demoted,
                "matched_parent_ids": {
                    "dense_top100": [pid for pid in _ids(rankings["dense"], 100) if pid in accepted_set],
                    "bm25_top100": [pid for pid in _ids(rankings["bm25"], 100) if pid in accepted_set],
                    "hybrid_top100": [pid for pid in _ids(rankings["hybrid"], 100) if pid in accepted_set],
                    "actual_top40": [item.parent_id for item in trace.candidate_pool if item.parent_id in accepted_set],
                    "rerank_top12": [item.parent_id for item in trace.reranked_context if item.parent_id in accepted_set],
                    "final_context": [item.parent_id for item in trace.final_context if item.parent_id in accepted_set],
                },
                "dense_rank": next((i for i in range(1, len(rankings["dense"]) + 1)
                                    if _unit_hit(case, unit, _ids(rankings["dense"], i))), None),
                "bm25_rank": next((i for i in range(1, len(rankings["bm25"]) + 1)
                                   if _unit_hit(case, unit, _ids(rankings["bm25"], i))), None),
                "hybrid_rank": next((i for i in range(1, len(rankings["hybrid"]) + 1)
                                     if _unit_hit(case, unit, _ids(rankings["hybrid"], i))), None),
            })
    positive_cases = aware.eligible_full_cases + aware.eligible_partial_cases
    percentages = {channel: {f"recall_at_{k}": metric_totals[channel][k] / positive_cases
                             for k in metric_totals[channel]} for channel in metric_totals}
    diagnostic_shortfall = [
        {"case_id": row["case_id"],
         "parent_counts": {channel: len(row["diagnostic_rankings"][channel])
                           for channel in metric_totals},
         "legs_exhausted": all(count < DIAGNOSTIC_CHILD_LIMIT
                               for count in row["diagnostic_leg_limits"])}
        for row in rows
        if row.get("retrieval_status") == "success"
        and any(len(row["diagnostic_rankings"][channel]) < 100
                for channel in metric_totals)
    ]
    if any(not item["legs_exhausted"] for item in diagnostic_shortfall):
        raise ValueError("diagnostic Child limit insufficient for Parent Top100")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    corpus_file = ROOT / manifest["source_path"]
    projection_file = ROOT / manifest["projection_path"]
    release_file = ROOT / manifest["source_release_manifest_path"]
    if "sha256:" + sha256(corpus_file) != manifest["source_fingerprint"]:
        raise ValueError("index manifest corpus fingerprint mismatch")
    if "sha256:" + sha256(projection_file) != manifest["projection_fingerprint"]:
        raise ValueError("index manifest projection fingerprint mismatch")
    if "sha256:" + sha256(release_file) != manifest["source_release_manifest_fingerprint"]:
        raise ValueError("index manifest release fingerprint mismatch")
    archives = {}
    for label, source in (
        ("retrieval", RETRIEVAL),
        ("trace", TRACES),
        ("rerank_diagnostics", TRACES.with_suffix(".rerank_diagnostics.jsonl")),
    ):
        archive = ROOT / "evals/reports" / (source.name + ".gz")
        archive.write_bytes(gzip.compress(source.read_bytes(), compresslevel=9, mtime=0))
        if gzip.decompress(archive.read_bytes()) != source.read_bytes():
            raise ValueError(f"artifact archive verification failed:{label}")
        archives[label] = {"path": str(archive), "sha256": sha256(archive),
                           "uncompressed_sha256": sha256(source)}
    retrieval_config = load_retrieval_config()
    query_config = load_query_understanding_config()
    rerank_config = load_rerank_config()
    output = {
        "run_id": RUN_ID, "gold_path": str(GOLD), "gold_sha256": sha256(GOLD),
        "corpus_path": manifest["source_path"], "corpus_sha256": manifest["source_fingerprint"],
        "projection_path": manifest["projection_path"],
        "projection_sha256": manifest["projection_fingerprint"],
        "release_manifest_path": manifest["source_release_manifest_path"],
        "release_manifest_sha256": manifest["source_release_manifest_fingerprint"],
        "index_manifest_path": str(MANIFEST), "index_manifest_sha256": sha256(MANIFEST),
        "collection_name": manifest["collection_name"],
        "index_projection_version": manifest["projection_version"],
        "index_point_count_manifest": manifest["collection_point_count"],
        "dense_model": manifest["dense_model"], "dense_revision": manifest["dense_model_revision"],
        "bm25_model": manifest["bm25_model"], "bm25_options": manifest["bm25_options"],
        "reranker_model": rerank_config.model.name,
        "reranker_revision": rerank_config.model.revision,
        "parameters": {
            "rrf_k": retrieval_config.fusion.rrf_k,
            "dense_weight": retrieval_config.fusion.dense_weight,
            "bm25_weight": retrieval_config.fusion.lexical_weight,
            "per_leg_top_k_actual": retrieval_config.fusion.per_leg_top_k,
            "per_leg_child_limit_diagnostic": DIAGNOSTIC_CHILD_LIMIT,
            "candidate_top_k_actual": query_config.candidate_top_k,
            "candidate_top_k_diagnostic": 100,
            "rerank_top_k": rerank_config.top_k,
            "rerank_batch_size": rerank_config.model.batch_size,
            "rerank_input_token_limit": rerank_config.model.input_token_limit,
        },
        "retrieval_config_sha256": sha256(ROOT / "app/rag/retrieval/config/v1.yaml"),
        "rerank_config_sha256": sha256(ROOT / "app/rag/reranking/config/v1.yaml"),
        "query_config_sha256": sha256(ROOT / "app/rag/query_understanding/config/v1.yaml"),
        "trace_path": str(TRACES), "trace_sha256": sha256(TRACES),
        "rerank_diagnostics_path": str(TRACES.with_suffix(".rerank_diagnostics.jsonl")),
        "rerank_diagnostics_sha256": sha256(TRACES.with_suffix(".rerank_diagnostics.jsonl")),
        "portable_archives": archives,
        "retrieval_artifact_path": str(RETRIEVAL), "retrieval_artifact_sha256": sha256(RETRIEVAL),
        "case_count": len(cases), "trace_count": len(traces),
        "stage_statuses": {stage: dict(Counter(getattr(trace.statuses, stage) for trace in traces))
                           for stage in ("query_understanding", "retrieval", "rerank", "context")},
        "full_cases": aware.eligible_full_cases, "partial_cases": aware.eligible_partial_cases,
        "insufficient_cases": aware.insufficient_cases, "needs_input_cases": aware.needs_input_cases,
        "supported_evidence_units": aware.evidence_unit_count,
        "channel_recall_macro": percentages,
        "hybrid_evidence_recall_at_40": aware.evidence_recall_at_40,
        "hybrid_rerank_recall_at_12": aware.evidence_rerank_recall_at_12,
        "final_context_precision": aware.final_context_precision,
        "funnel_unit_hits": dict(funnel), "failure_counts": dict(failures),
        "per_claim": comparisons,
        "diagnostic_non_nested_units": [
            {"case_id": item["case_id"], "unit_id": item["unit_id"],
             "claim": item["claim"], "actual_top40_parent_ids": item["matched_parent_ids"]["actual_top40"]}
            for item in comparisons
            if item["hybrid_actual_top40_hit"] and not item["hybrid_top100_hit"]
        ],
        "diagnostic_parent_counts_by_case": [{"case_id": row["case_id"],
                                              **{channel: len(row.get("diagnostic_rankings", {}).get(channel, []))
                                                 for channel in metric_totals}} for row in rows],
        "diagnostic_top100_shortfall_exhausted_cases": diagnostic_shortfall,
        "rerank_input_lengths": _rerank_input_lengths(rows),
        "diagnostic_top100_note": "Top100 is a separate Qdrant query for up to 600 Child hits per leg to obtain 100 distinct Parents after collapse; actual retrieval uses configured per-leg limit and Top40. Selection target 100 also changes cap relaxation. Therefore diagnostic Top100 is not guaranteed to contain actual Top40; non-nested units are explicitly reported.",
    }
    REPORT.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# Frozen Gold V4 Hybrid Shadow Baseline", "",
             f"Gold {len(cases)} cases（full {aware.eligible_full_cases}、partial {aware.eligible_partial_cases}、"
             f"insufficient {aware.insufficient_cases}、needs_input {aware.needs_input_cases}）；"
             f"supported evidence units {aware.evidence_unit_count}。", "",
             f"Gold SHA-256 `{sha256(GOLD)}`；corpus `{manifest['source_path']}` SHA-256 `{sha256(corpus_file)}`。", "",
             f"Index manifest: `{MANIFEST}` (SHA-256 `{sha256(MANIFEST)}`)；"
             f"collection `{manifest['collection_name']}`，{manifest['collection_point_count']} points。", "",
             f"Dense `{manifest['dense_model']}` revision `{manifest['dense_model_revision']}`；"
             f"BM25 `{manifest['bm25_model']}`；reranker `{rerank_config.model.name}` revision `{rerank_config.model.revision}`。", "",
             f"实际配置：RRF k={retrieval_config.fusion.rrf_k}，dense/BM25 权重 "
             f"{retrieval_config.fusion.dense_weight}/{retrieval_config.fusion.lexical_weight}，"
             f"per-leg Top{retrieval_config.fusion.per_leg_top_k}，候选 Top{query_config.candidate_top_k}，"
             f"rerank Top{rerank_config.top_k}；诊断另取每 leg 最多 {DIAGNOSTIC_CHILD_LIMIT} 个 Child，"
             "折叠后记录 Top100 Parent，未送入 reranker。", "",
             "阶段状态：" + json.dumps(output["stage_statuses"], ensure_ascii=False) + "。", "",
             "全部 50-case 检索诊断、Trace 与 rerank 明细以 `.jsonl.gz` 保存在本报告目录，"
             "各自压缩及原始 SHA-256 见 JSON `portable_archives`；本地未压缩工作副本位于 `evals/artifacts`。", "",
             f"Recall@K：每个 full/partial case 的命中 evidence unit 数除以该 case 支持单位数，再对 {positive_cases} 个正样本 case 等权平均。"
             "同一组 equivalent Parent 只算一个 unit；complementary 必须同时命中。"
             "Final Context Precision：每 case 的已满足证据 Parent 数除以 final context 唯一 Parent 数，再按 case 宏平均。", "",
             f"有 {len(diagnostic_shortfall)} 个检索成功 case 的至少一通道不足 100 个不同 Parent；"
             "这些 case 的各检索 leg 均已返回少于诊断上限的 Child，表中按实际可用数量计算。", "",
             "| Metric | Dense | BM25 | Hybrid diagnostic |", "| --- | ---: | ---: | ---: |"]
    for k in (10, 20, 40, 100):
        lines.append(f"| Evidence Recall@{k} | {percentages['dense'][f'recall_at_{k}']:.4f} | {percentages['bm25'][f'recall_at_{k}']:.4f} | {percentages['hybrid'][f'recall_at_{k}']:.4f} |")
    lines += ["", f"Actual Hybrid Recall@40: {aware.evidence_recall_at_40:.4f}",
              f"Rerank Recall@12: {aware.evidence_rerank_recall_at_12:.4f}",
              f"Final Context Precision: {aware.final_context_precision:.4f}", "",
              "Top100 是独立诊断查询，不保证与正式 Top40 构成严格前缀。"
              f"有 {len(output['diagnostic_non_nested_units'])} 个正式 Top40 evidence unit 不在诊断 Hybrid Top100，详见 JSON。"
              "上表 @10/20/40/100 均取诊断排名；正式 Hybrid @40 单列。", "",
              "## Funnel", "", f"{dict(funnel)}", "", "## Failure counts", "",
              f"{dict(failures)}", "",
              "A/B/C/D 是诊断 Top100 的通道覆盖；E/F/G 以正式 Top40→Top12→final context 计。"
              "分类互斥计数为 DENSE_AND_BM25_MISS、FUSION_RANKING_MISS、RERANK_MISS、CONTEXT_DROP、SUCCESS；"
              "Dense-only、BM25-only、BOTH、NEITHER 是另一个可交叉维度。"
              "FUSION_RANKING_MISS 表示诊断通道能找到但正式 Top40 未收录，不能仅凭此归因于 RRF；"
              "过滤、Parent 折叠与选择也可能影响排序。", "",
              f"从当前计数看，下一轮应先核查 Retriever 入口和融合/选择：{failures['DENSE_AND_BM25_MISS']} 个 evidence unit "
              "在两通道诊断 Top100 均未命中且正式 Top40 未命中，"
              f"另有 {failures['FUSION_RANKING_MISS']} 个至少一通道命中但正式 Top40 未命中。"
              "此处只指出检查顺序，不推断词法或语义根因。", "",
              "## Per-claim differences", "",
              "| Case | Claim / unit | Dense rank | BM25 rank | Hybrid rank | Actual Top40 | Top12 | Final | Pattern / failure |",
              "| --- | --- | ---: | ---: | ---: | :---: | :---: | :---: | --- |"]
    for item in comparisons:
        label = (item["claim"] or item["unit_id"]).replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| {item['case_id']} | {label} | {item['dense_rank'] or '—'} | "
            f"{item['bm25_rank'] or '—'} | {item['hybrid_rank'] or '—'} | "
            f"{'Y' if item['hybrid_actual_top40_hit'] else 'N'} | "
            f"{'Y' if item['rerank_top12_hit'] else 'N'} | "
            f"{'Y' if item['final_context_hit'] else 'N'} | "
            f"{item['channel_pattern']} / {item['failure_category']} |"
        )
    REPORT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(REPORT), "metrics": percentages,
                      "funnel": dict(funnel), "failures": dict(failures)}, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("collect", "rerank", "report"))
    phase = parser.parse_args().phase
    if phase == "collect":
        asyncio.run(collect())
    elif phase == "rerank":
        asyncio.run(rerank())
    else:
        report()


if __name__ == "__main__":
    main()
