"""Run fixed-set, single-variable Context Adapter V1 tuning."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

from langchain_core.messages import HumanMessage

from app.rag.context_packing import (
    ContextPackBuilder,
    EvidenceSufficiencyStatus,
    load_context_packing_config,
)
from app.rag.evaluation import (
    EvaluationContext,
    ExpectedOutcome,
    RAGEvaluationCase,
    RAGEvaluationTrace,
    TraceStageStatuses,
    build_pipeline_latency_report,
    load_evaluation_cases,
    load_evaluation_traces,
    write_jsonl,
)
from app.rag.evaluation.collector import EvaluationQueryGraph, EvaluationRetriever
from app.rag.evaluation.context_tuning import (
    ContextTuningInput,
    config_from_parameters,
    render_context_tuning_markdown,
    run_context_experiments,
)
from app.rag.evaluation.trace_builder import build_evaluation_trace
from app.rag.query_understanding import (
    QueryUnderstandingResult,
    QueryUnderstandingService,
    QueryUnderstandingStatus,
    create_query_understanding_graph,
    create_query_understanding_state,
    load_query_understanding_config,
)
from app.rag.query_understanding.models import QueryVariantKind, SearchChannel, SearchPlan
from app.rag.reranking import (
    CandidateReranker,
    QwenCandidateReranker,
    RerankConfig,
    RerankResult,
    RerankStatus,
    RerankTimings,
    RerankedCandidate,
    load_rerank_config,
)
from app.rag.retrieval import (
    ChannelEvidence,
    DeferredParentContext,
    HybridRetrievalService,
    RetrievalCandidate,
    RetrievalConfig,
    RetrievalIndexBinding,
    RetrievalResult,
    RetrievalStatus,
    RetrievalTimings,
    load_retrieval_config,
    load_retrieval_index_binding,
)
from app.schemas.player import PlayerContext


PROJECT_ROOT = Path(__file__).resolve().parent

RetrievalFactory = Callable[
    [RetrievalIndexBinding, RetrievalConfig],
    EvaluationRetriever,
]
RerankFactory = Callable[[RerankConfig], CandidateReranker]


@dataclass(frozen=True, slots=True)
class _CompletedRun:
    case: RAGEvaluationCase
    plan: SearchPlan
    retrieval: RetrievalResult
    rerank: RerankResult


def _default_retrieval_factory(
    binding: RetrievalIndexBinding,
    config: RetrievalConfig,
) -> HybridRetrievalService:
    return HybridRetrievalService(binding, config=config)


def _default_rerank_factory(config: RerankConfig) -> CandidateReranker:
    return QwenCandidateReranker(config)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Tune Context Adapter V1 with fixed-set single-variable scans"
    )
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--index-manifest", type=Path, required=True)
    parser.add_argument("--baseline-config", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--markdown-report", type=Path, required=True)
    parser.add_argument("--best-traces", type=Path, required=True)
    parser.add_argument("--upstream-traces", type=Path)
    parser.add_argument("--index-projection", type=Path)
    parser.add_argument(
        "--generated-traces",
        type=Path,
        help="Optional best-config traces with answers, used to include generation latency",
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--no-llm", action="store_true")
    return parser


async def _replay_upstream(
    cases: Sequence[RAGEvaluationCase],
    *,
    run_id: str,
    graph: EvaluationQueryGraph,
    traces_path: Path,
    projection_path: Path,
) -> tuple[list[_CompletedRun], dict[str, RAGEvaluationTrace]]:
    traces = {item.case_id: item for item in load_evaluation_traces(traces_path)}
    projections = _load_projection(projection_path)
    completed: list[_CompletedRun] = []
    terminal: dict[str, RAGEvaluationTrace] = {}
    for case in cases:
        trace = traces.get(case.case_id)
        if trace is None:
            raise ValueError(f"upstream trace missing case: {case.case_id}")
        if trace.statuses.query_understanding == "needs_input":
            terminal[case.case_id] = RAGEvaluationTrace(
                run_id=run_id,
                case_id=case.case_id,
                query=case.query,
                statuses=TraceStageStatuses(query_understanding="needs_input"),
            )
            continue
        output = await graph.ainvoke(
            create_query_understanding_state(
                case.query,
                messages=[HumanMessage(content=case.query)],
                routing_context=PlayerContext(
                    has_current_song=case.current_song_id is not None,
                    current_song_id=case.current_song_id,
                ),
            )
        )
        understanding = output.get("result")
        if not isinstance(understanding, QueryUnderstandingResult):
            raise ValueError("query understanding graph returned an invalid result")
        plan = understanding.search_plan
        if plan is None:
            raise ValueError(f"replayed ready case omitted search plan: {case.case_id}")
        retrieval, rerank = _rebuild_ranked_results(trace, plan, projections)
        completed.append(
            _CompletedRun(
                case=case,
                plan=plan,
                retrieval=retrieval,
                rerank=rerank,
            )
        )
    return completed, terminal


def _load_projection(path: Path) -> dict[str, dict[str, object]]:
    by_child_id: dict[str, dict[str, object]] = {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, raw in enumerate(handle, start=1):
                if not raw.strip():
                    continue
                item = json.loads(raw)
                payload = item.get("payload")
                child_id = item.get("chunk_id")
                if not isinstance(payload, dict) or not isinstance(child_id, str):
                    raise ValueError(f"invalid index projection line {line_number}")
                by_child_id[child_id] = item
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"index projection invalid: {path}") from exc
    if not by_child_id:
        raise ValueError(f"index projection is empty: {path}")
    return by_child_id


def _rebuild_ranked_results(
    trace: RAGEvaluationTrace,
    plan: SearchPlan,
    projections: dict[str, dict[str, object]],
) -> tuple[RetrievalResult, RerankResult]:
    inferred_subqueries = _inferred_subquery_owners(trace, plan)
    retrieval_candidates = [
        _rebuild_candidate(
            item,
            plan=plan,
            projection=projections.get(item.matched_child_id or ""),
            retrieval_rank=item.rank,
            inferred_subqueries=inferred_subqueries,
        )
        for item in trace.candidate_pool
    ]
    candidates_by_parent = {item.parent_id: item for item in retrieval_candidates}
    reranked = [
        RerankedCandidate(
            candidate=candidates_by_parent[item.parent_id],
            retrieval_rank=candidates_by_parent[item.parent_id].metadata[
                "replay_retrieval_rank"
            ],
            rerank_rank=item.rank,
            rerank_score=max(0.0, 1 - item.rank / 100),
        )
        for item in trace.reranked_context
    ]
    retrieval_status = (
        RetrievalStatus.SUCCESS if retrieval_candidates else RetrievalStatus.EMPTY
    )
    retrieval = RetrievalResult(
        status=retrieval_status,
        plan_id=plan.plan_id,
        index_profile=plan.index_profile,
        candidates=retrieval_candidates,
        warnings=["fixed_upstream_trace_replay"],
        timings=RetrievalTimings(
            embedding_ms=trace.timings_ms.get("retrieval_embedding", 0),
            qdrant_ms=trace.timings_ms.get("retrieval_qdrant", 0),
            processing_ms=trace.timings_ms.get("retrieval_processing", 0),
            total_ms=trace.timings_ms.get("retrieval_total", 0),
        ),
    )
    rerank = RerankResult(
        status=RerankStatus.SUCCESS if reranked else RerankStatus.EMPTY,
        plan_id=plan.plan_id,
        index_profile=plan.index_profile,
        model_name="fixed-upstream-trace-replay",
        model_revision="0" * 40,
        rerank_version=trace.versions.rerank or "v1",
        prompt_version=trace.versions.rerank_prompt or "v1",
        candidate_pool_status=retrieval_status,
        input_count=len(retrieval_candidates),
        output_top_k=12,
        candidates=reranked,
        warnings=["subquery_channel_evidence_reconstructed"],
        timings=RerankTimings(
            inference_ms=trace.timings_ms.get("rerank_inference", 0),
            total_ms=trace.timings_ms.get("rerank_total", 0),
        ),
    )
    return retrieval, rerank


def _rebuild_candidate(
    context: EvaluationContext,
    *,
    plan: SearchPlan,
    projection: dict[str, object] | None,
    retrieval_rank: int,
    inferred_subqueries: dict[str, list[int]],
) -> RetrievalCandidate:
    if projection is None:
        raise ValueError(f"projection missing child: {context.matched_child_id}")
    payload = projection["payload"]
    if not isinstance(payload, dict):
        raise ValueError(f"projection payload invalid: {context.matched_child_id}")
    parent = payload.get("parent_context")
    if not isinstance(parent, dict):
        raise ValueError(f"projection parent missing: {context.matched_child_id}")
    variant_indexes = list(dict.fromkeys([0, *inferred_subqueries.get(context.parent_id, [])]))
    evidence = []
    for variant_index in variant_indexes:
        variant = plan.queries[variant_index]
        evidence.append(
            ChannelEvidence(
                chunk_id=context.matched_child_id or str(payload["chunk_id"]),
                subquery_id=f"q{variant_index}:{variant.kind.value}",
                retrieval_leg=f"q{variant_index}:{variant.kind.value}:replay",
                variant_index=variant_index,
                variant_kind=variant.kind,
                channel=(
                    SearchChannel.DENSE
                    if variant.channel is SearchChannel.BOTH
                    else variant.channel
                ),
                rank=retrieval_rank,
                raw_score=1 / retrieval_rank,
                weight=variant.weight,
            )
        )
    return RetrievalCandidate(
        point_id=str(projection["point_id"]),
        parent_id=context.parent_id,
        doc_id=str(payload["doc_id"]),
        title=context.title,
        primary_child_id=context.matched_child_id or str(payload["chunk_id"]),
        primary_child_content=context.content,
        primary_child_breadcrumb=str(payload.get("breadcrumb") or ""),
        primary_child_token_count=context.token_count,
        parent_context=DeferredParentContext(
            chunk_id=context.parent_id,
            content=str(parent["content"]),
            token_count=int(parent.get("token_count") or 0),
            breadcrumb=str(parent.get("breadcrumb") or ""),
        ),
        source_id=context.source_id,
        source_name=context.source_name,
        source_url=context.source_url,
        entity_ids=[str(item) for item in payload.get("entity_ids", [])],
        supported_claims=payload.get("supported_claims", []),
        fusion_score=1 / (60 + retrieval_rank),
        channel_evidence=evidence,
        matched_child_ids=[context.matched_child_id or str(payload["chunk_id"])],
        metadata={"replay_retrieval_rank": retrieval_rank},
    )


def _inferred_subquery_owners(
    trace: RAGEvaluationTrace,
    plan: SearchPlan,
) -> dict[str, list[int]]:
    decomposed = [
        index
        for index, item in enumerate(plan.queries)
        if item.kind is QueryVariantKind.DECOMPOSED
    ]
    owners: dict[str, list[int]] = {}
    fallback_parent = (
        trace.final_context[0].parent_id
        if trace.final_context
        else (trace.reranked_context[0].parent_id if trace.reranked_context else None)
    )
    for position, variant_index in enumerate(decomposed):
        parent_id = (
            trace.final_context[position].parent_id
            if position < len(trace.final_context)
            else fallback_parent
        )
        if parent_id is not None:
            owners.setdefault(parent_id, []).append(variant_index)
    return owners


async def _collect_upstream(
    cases: Sequence[RAGEvaluationCase],
    *,
    run_id: str,
    graph: EvaluationQueryGraph,
    retriever: EvaluationRetriever,
    reranker: CandidateReranker,
) -> tuple[list[_CompletedRun], dict[str, RAGEvaluationTrace]]:
    completed: list[_CompletedRun] = []
    terminal_traces: dict[str, RAGEvaluationTrace] = {}
    for case in cases:
        stage = "query_understanding"
        try:
            output = await graph.ainvoke(
                create_query_understanding_state(
                    case.query,
                    messages=[HumanMessage(content=case.query)],
                    routing_context=PlayerContext(
                        has_current_song=case.current_song_id is not None,
                        current_song_id=case.current_song_id,
                    ),
                )
            )
            understanding = output.get("result")
            if not isinstance(understanding, QueryUnderstandingResult):
                raise ValueError("query understanding graph returned an invalid result")
            if understanding.status is QueryUnderstandingStatus.NEEDS_INPUT:
                terminal_traces[case.case_id] = RAGEvaluationTrace(
                    run_id=run_id,
                    case_id=case.case_id,
                    query=case.query,
                    statuses=TraceStageStatuses(query_understanding="needs_input"),
                )
                continue
            plan = understanding.search_plan
            if plan is None:
                raise ValueError("ready query understanding omitted its search plan")
            stage = "retrieval"
            retrieval = await retriever.retrieve(plan)
            stage = "rerank"
            rerank = await reranker.rerank(plan, retrieval)
            completed.append(
                _CompletedRun(
                    case=case,
                    plan=plan,
                    retrieval=retrieval,
                    rerank=rerank,
                )
            )
        except Exception as exc:  # noqa: BLE001 - retain each fixed-set case
            statuses = TraceStageStatuses(
                query_understanding=("failed" if stage == "query_understanding" else "ready"),
                retrieval=("failed" if stage == "retrieval" else "not_run"),
                rerank=("failed" if stage == "rerank" else "not_run"),
            )
            terminal_traces[case.case_id] = RAGEvaluationTrace(
                run_id=run_id,
                case_id=case.case_id,
                query=case.query,
                statuses=statuses,
                warnings=[f"context_tuning_failed:{stage}:{type(exc).__name__}"],
            )
    return completed, terminal_traces


def _build_best_trace(
    run: _CompletedRun,
    *,
    run_id: str,
    builder: ContextPackBuilder,
    retrieval_version: str,
) -> RAGEvaluationTrace:
    started_at = perf_counter()
    pack = builder.build(run.plan, run.rerank)
    context_latency_ms = (perf_counter() - started_at) * 1_000
    selected = (
        pack.items
        if pack.sufficiency.status is EvidenceSufficiencyStatus.SUFFICIENT
        else []
    )
    final_context = [
        EvaluationContext(
            parent_id=item.parent_id,
            rank=rank,
            title=item.title,
            content=item.context,
            source_id=item.source_id,
            source_name=item.source_name,
            source_url=item.source_url,
            matched_child_id=item.child_id,
            token_count=item.token_count,
        )
        for rank, item in enumerate(selected, start=1)
    ]
    trace = build_evaluation_trace(
        run_id=run_id,
        case_id=run.case.case_id,
        plan=run.plan,
        retrieval=run.retrieval,
        rerank=run.rerank,
        final_context=final_context,
        context_token_count=sum(item.token_count for item in final_context),
        context_token_budget=pack.token_budget,
        retrieval_version=retrieval_version,
        context_version="context-pack-v1-tuned",
        context_status="success" if final_context else "empty",
    )
    return RAGEvaluationTrace.model_validate(
        {
            **trace.model_dump(mode="json"),
            "timings_ms": {
                **trace.timings_ms,
                "context_adapter_total": context_latency_ms,
            },
            "warnings": list(
                dict.fromkeys(
                    [
                        *trace.warnings,
                        *pack.warnings,
                        *(f"context_insufficient:{reason}" for reason in pack.sufficiency.reasons),
                    ]
                )
            ),
        }
    )


async def _run(
    args: argparse.Namespace,
    root: Path,
    retrieval_factory: RetrievalFactory,
    rerank_factory: RerankFactory,
) -> dict[str, object]:
    dataset_path = _resolve(args.dataset, root)
    report_path = _resolve(args.report, root)
    markdown_path = _resolve(args.markdown_report, root)
    traces_path = _resolve(args.best_traces, root)
    cases = load_evaluation_cases(dataset_path)
    baseline = (
        load_context_packing_config(_resolve(args.baseline_config, root))
        if args.baseline_config is not None
        else load_context_packing_config()
    )
    retrieval_config = load_retrieval_config()
    binding = load_retrieval_index_binding(root, args.index_manifest, retrieval_config)
    understanding_config = load_query_understanding_config()
    if args.no_llm:
        understanding_config = understanding_config.model_copy(
            update={"llm_fallback_enabled": False}
        )
    graph = create_query_understanding_graph(
        service=QueryUnderstandingService(
            config=understanding_config,
            index_profile=binding.profile,
        )
    )
    replay_mode = args.upstream_traces is not None or args.index_projection is not None
    if replay_mode:
        if args.upstream_traces is None or args.index_projection is None:
            raise ValueError(
                "--upstream-traces and --index-projection must be provided together"
            )
        completed, terminal_traces = await _replay_upstream(
            cases,
            run_id=args.run_id,
            graph=graph,
            traces_path=_resolve(args.upstream_traces, root),
            projection_path=_resolve(args.index_projection, root),
        )
    else:
        retriever = retrieval_factory(binding, retrieval_config)
        reranker = rerank_factory(load_rerank_config())
        if isinstance(reranker, QwenCandidateReranker):
            await reranker.prepare()
        completed, terminal_traces = await _collect_upstream(
            cases,
            run_id=args.run_id,
            graph=graph,
            retriever=retriever,
            reranker=reranker,
        )
    tuning_inputs = [
        ContextTuningInput(case=item.case, plan=item.plan, rerank=item.rerank)
        for item in completed
        if item.case.expected_outcome is ExpectedOutcome.ANSWERABLE
    ]
    report = run_context_experiments(
        tuning_inputs,
        baseline=baseline,
        dataset_case_count=len(cases),
    )
    if replay_mode:
        report = report.model_copy(
            update={
                "upstream_provenance": str(_resolve(args.upstream_traces, root)),
                "warnings": [
                    "Top40/Top12、Parent 内容和阶段延迟来自固定真实 trace；原 trace 未持久化 subquery retrieval leg，5 个分解查询样本的 leg 归属按原 Context Pack 顺序恢复。"
                ],
            }
        )
    recommended = next(
        item.parameters
        for item in report.experiments
        if item.parameters.name == report.recommended_experiment
    )
    builder = ContextPackBuilder(config_from_parameters(baseline, recommended))
    completed_by_id = {item.case.case_id: item for item in completed}
    best_traces = [
        (
            _build_best_trace(
                completed_by_id[case.case_id],
                run_id=args.run_id,
                builder=builder,
                retrieval_version=retrieval_config.retrieval_version,
            )
            if case.case_id in completed_by_id
            else terminal_traces[case.case_id]
        )
        for case in cases
    ]
    latency_traces = (
        load_evaluation_traces(_resolve(args.generated_traces, root))
        if args.generated_traces is not None
        else best_traces
    )
    report = report.model_copy(
        update={"pipeline_latency": build_pipeline_latency_report(latency_traces)}
    )
    _write_text(report_path, report.model_dump_json(indent=2))
    _write_text(markdown_path, render_context_tuning_markdown(report))
    write_jsonl(traces_path, best_traces)
    failed_count = sum(
        any(
            getattr(trace.statuses, stage) == "failed"
            for stage in ("query_understanding", "retrieval", "rerank", "context")
        )
        for trace in best_traces
    )
    return {
        "status": "partial" if failed_count else "success",
        "dataset_case_count": len(cases),
        "tuning_case_count": len(tuning_inputs),
        "experiment_count": len(report.experiments),
        "recommended_experiment": report.recommended_experiment,
        "recommended_parameters": recommended.model_dump(),
        "failed_count": failed_count,
        "report": str(report_path),
        "markdown_report": str(markdown_path),
        "best_traces": str(traces_path),
    }


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(content + "\n", encoding="utf-8")
    temporary.replace(path)


def _resolve(path: Path, root: Path) -> Path:
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def main(
    argv: Sequence[str] | None = None,
    *,
    project_root: Path = PROJECT_ROOT,
    retrieval_factory: RetrievalFactory = _default_retrieval_factory,
    rerank_factory: RerankFactory = _default_rerank_factory,
) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = asyncio.run(
            _run(args, project_root.resolve(), retrieval_factory, rerank_factory)
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 2 if payload["status"] == "partial" else 0


if __name__ == "__main__":
    raise SystemExit(main())
