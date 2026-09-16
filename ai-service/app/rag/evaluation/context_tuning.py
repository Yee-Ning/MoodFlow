"""Single-variable Context Adapter V1 experiments over fixed rerank outputs."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import fmean
from time import perf_counter
from pydantic import BaseModel, ConfigDict, Field

from app.rag.context_packing import ContextPackBuilder, ContextPackingConfig
from app.rag.context_packing.models import EvidenceSufficiencyStatus
from app.rag.evaluation.latency import (
    LatencyPercentiles,
    PipelineLatencyReport,
    summarize_latencies,
)
from app.rag.evaluation.models import RAGEvaluationCase
from app.rag.query_understanding.models import SearchPlan
from app.rag.reranking.models import RerankResult


_DUPLICATE_WARNING = re.compile(r"^near_duplicate_evidence_removed:(\d+)$")


@dataclass(frozen=True, slots=True)
class ContextTuningInput:
    case: RAGEvaluationCase
    plan: SearchPlan
    rerank: RerankResult


class ContextTuningParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    varied_parameter: str | None = None
    varied_value: int | float | None = None
    context_top_k: int = Field(ge=1, le=10)
    neighbor_window: int = Field(ge=0, le=3)
    source_cap: int = Field(ge=1, le=10)
    token_budget: int = Field(ge=500, le=30_000)
    max_item_tokens: int = Field(ge=100, le=8_000)
    near_duplicate_threshold: float = Field(ge=0.8, le=1)


class ContextExperimentMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_count: int = Field(ge=1)
    sufficient_case_rate: float = Field(ge=0, le=1)
    final_context_precision: float = Field(ge=0, le=1)
    final_context_recall: float = Field(ge=0, le=1)
    supporting_span_coverage: float = Field(ge=0, le=1)
    subquery_coverage: float = Field(ge=0, le=1)
    required_subquery_count: int = Field(ge=0)
    entity_coverage: float = Field(ge=0, le=1)
    required_entity_count: int = Field(ge=0)
    duplicate_rate: float = Field(ge=0, le=1)
    duplicate_removed_count: int = Field(ge=0)
    candidate_count_before_dedup: int = Field(ge=0)
    average_context_items: float = Field(ge=0)
    average_token_usage: float = Field(ge=0)
    token_usage_p50: float = Field(ge=0)
    token_usage_p95: float = Field(ge=0)
    token_usage_p99: float = Field(ge=0)
    maximum_token_usage: int = Field(ge=0)
    adapter_latency: LatencyPercentiles
    quality_score: float = Field(ge=0, le=1)


class ContextExperimentResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    parameters: ContextTuningParameters
    metrics: ContextExperimentMetrics


class MetricDelta(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    final_context_precision: float
    final_context_recall: float
    supporting_span_coverage: float
    subquery_coverage: float
    entity_coverage: float
    duplicate_rate: float
    average_token_usage: float
    adapter_latency_p95_ms: float
    adapter_latency_p99_ms: float


class EvidenceSpanAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    total_span_count: int = Field(ge=1)
    covered_span_count: int = Field(ge=0)
    missing_span_count: int = Field(ge=0)
    extractor_window_miss_count: int = Field(ge=0)
    parent_not_selected_count: int = Field(ge=0)
    span_not_in_index_parent_count: int = Field(ge=0)
    recommendation: str


class ContextTuningReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "v1"
    dataset_case_count: int = Field(ge=1)
    tuning_case_count: int = Field(ge=1)
    experiment_policy: str = "sequential_single_variable_scan"
    upstream_provenance: str = "live_fixed_run"
    warnings: list[str] = Field(default_factory=list, max_length=20)
    baseline_experiment: str
    control_experiment: str
    recommended_experiment: str
    experiments: list[ContextExperimentResult] = Field(min_length=2)
    baseline_vs_best: MetricDelta
    pipeline_latency: PipelineLatencyReport | None = None
    evidence_span_extractor_analysis_required: bool
    evidence_span_analysis: EvidenceSpanAnalysis
    evidence_span_analysis_threshold: float = Field(default=0.85, ge=0, le=1)


def fixed_control_config(baseline: ContextPackingConfig) -> ContextPackingConfig:
    return baseline.model_copy(
        update={
            "compression": baseline.compression.model_copy(
                update={"max_item_tokens": 900}
            ),
            "selection": baseline.selection.model_copy(
                update={"near_duplicate_threshold": 0.90}
            ),
        }
    )


def config_from_parameters(
    baseline: ContextPackingConfig,
    parameters: ContextTuningParameters,
) -> ContextPackingConfig:
    return baseline.model_copy(
        update={
            "compression": baseline.compression.model_copy(
                update={
                    "neighbor_window": parameters.neighbor_window,
                    "max_item_tokens": parameters.max_item_tokens,
                }
            ),
            "selection": baseline.selection.model_copy(
                update={
                    "max_items": parameters.context_top_k,
                    "source_cap": parameters.source_cap,
                    "token_budget": parameters.token_budget,
                    "near_duplicate_threshold": parameters.near_duplicate_threshold,
                }
            ),
        }
    )


def run_context_experiments(
    inputs: Sequence[ContextTuningInput],
    *,
    baseline: ContextPackingConfig,
    dataset_case_count: int,
) -> ContextTuningReport:
    if not inputs:
        raise ValueError("context tuning requires at least one answerable input")
    case_ids = [item.case.case_id for item in inputs]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("context tuning case ids must be unique")
    baseline_parameters = _parameters("baseline-current", baseline)
    baseline_result = evaluate_context_parameters(
        inputs,
        baseline_parameters,
        config=baseline,
    )
    control = fixed_control_config(baseline)
    experiments = [baseline_result]
    current = control
    current_result: ContextExperimentResult | None = None
    scans: tuple[tuple[str, Sequence[int]], ...] = (
        ("context_top_k", (4, 6, 8)),
        ("neighbor_window", (0, 1, 2)),
        ("source_cap", (1, 2, 3)),
        ("token_budget", (3_000, 4_500, 6_000)),
    )
    for field_name, values in scans:
        stage_results = []
        for value in values:
            config = _replace_parameter(current, field_name, value)
            name = (
                "control-fixed"
                if field_name == "context_top_k" and value == 8
                else f"{field_name}-{value}"
            )
            stage_results.append(
                evaluate_context_parameters(
                    inputs,
                    _parameters(
                        name,
                        config,
                        varied_parameter=field_name,
                        varied_value=value,
                    ),
                    config=config,
                )
            )
        experiments.extend(stage_results)
        reference = current_result or next(
            item for item in stage_results if item.parameters.name == "control-fixed"
        )
        current_result = _select_stage_winner(stage_results, reference)
        current = config_from_parameters(baseline, current_result.parameters)
    assert current_result is not None
    recommended = current_result
    return ContextTuningReport(
        dataset_case_count=dataset_case_count,
        tuning_case_count=len(inputs),
        baseline_experiment=baseline_result.parameters.name,
        control_experiment="control-fixed",
        recommended_experiment=recommended.parameters.name,
        experiments=experiments,
        baseline_vs_best=_delta(baseline_result.metrics, recommended.metrics),
        evidence_span_extractor_analysis_required=(
            recommended.metrics.supporting_span_coverage < 0.85
        ),
        evidence_span_analysis=_analyze_evidence_spans(
            inputs,
            config=config_from_parameters(baseline, recommended.parameters),
        ),
    )


def evaluate_context_parameters(
    inputs: Sequence[ContextTuningInput],
    parameters: ContextTuningParameters,
    *,
    config: ContextPackingConfig,
) -> ContextExperimentResult:
    builder = ContextPackBuilder(config)
    precisions: list[float] = []
    recalls: list[float] = []
    span_coverages: list[float] = []
    token_usage: list[int] = []
    item_counts: list[int] = []
    latencies: list[float] = []
    sufficient_count = 0
    required_subqueries = covered_subqueries = 0
    required_entities = covered_entities = 0
    duplicate_removed = candidate_count = 0
    for item in inputs:
        started_at = perf_counter()
        pack = builder.build(item.plan, item.rerank)
        latencies.append((perf_counter() - started_at) * 1_000)
        selected = (
            pack.items
            if pack.sufficiency.status is EvidenceSufficiencyStatus.SUFFICIENT
            else []
        )
        if selected:
            sufficient_count += 1
        selected_parents = {evidence.parent_id for evidence in selected}
        relevant = set(item.case.relevant_parent_ids)
        precisions.append(
            len(selected_parents.intersection(relevant)) / len(selected_parents)
            if selected_parents
            else 0.0
        )
        recalls.append(len(selected_parents.intersection(relevant)) / len(relevant))
        content_by_parent = {evidence.parent_id: evidence.context for evidence in selected}
        span_coverages.append(
            sum(
                span.exact_text in content_by_parent.get(span.parent_id, "")
                for span in item.case.supporting_spans
            )
            / len(item.case.supporting_spans)
        )
        selected_subqueries = {
            value for evidence in selected for value in evidence.subquery_ids
        }
        selected_entities = {
            value for evidence in selected for value in evidence.entity_ids
        }
        required_subquery_ids = set(pack.sufficiency.required_subquery_ids)
        required_entity_ids = set(pack.sufficiency.required_entity_ids)
        required_subqueries += len(required_subquery_ids)
        covered_subqueries += len(required_subquery_ids.intersection(selected_subqueries))
        required_entities += len(required_entity_ids)
        covered_entities += len(required_entity_ids.intersection(selected_entities))
        duplicate_removed += _duplicate_count(pack.warnings)
        candidate_count += len(item.rerank.candidates)
        token_usage.append(sum(evidence.token_count for evidence in selected))
        item_counts.append(len(selected))
    token_percentiles = summarize_latencies(token_usage)
    subquery_coverage = (
        covered_subqueries / required_subqueries if required_subqueries else 1.0
    )
    entity_coverage = covered_entities / required_entities if required_entities else 1.0
    precision = fmean(precisions)
    recall = fmean(recalls)
    span_coverage = fmean(span_coverages)
    quality_score = (
        0.35 * precision
        + 0.35 * span_coverage
        + 0.15 * subquery_coverage
        + 0.15 * entity_coverage
    )
    return ContextExperimentResult(
        parameters=parameters,
        metrics=ContextExperimentMetrics(
            case_count=len(inputs),
            sufficient_case_rate=sufficient_count / len(inputs),
            final_context_precision=precision,
            final_context_recall=recall,
            supporting_span_coverage=span_coverage,
            subquery_coverage=subquery_coverage,
            required_subquery_count=required_subqueries,
            entity_coverage=entity_coverage,
            required_entity_count=required_entities,
            duplicate_rate=(duplicate_removed / candidate_count if candidate_count else 0),
            duplicate_removed_count=duplicate_removed,
            candidate_count_before_dedup=candidate_count,
            average_context_items=fmean(item_counts),
            average_token_usage=fmean(token_usage),
            token_usage_p50=token_percentiles.p50_ms or 0,
            token_usage_p95=token_percentiles.p95_ms or 0,
            token_usage_p99=token_percentiles.p99_ms or 0,
            maximum_token_usage=max(token_usage, default=0),
            adapter_latency=summarize_latencies(latencies),
            quality_score=quality_score,
        ),
    )


def render_context_tuning_markdown(report: ContextTuningReport) -> str:
    lines = [
        "# Context Adapter V1 参数实验报告",
        "",
        f"固定评测集：{report.dataset_case_count} 条；可回答调优样本：{report.tuning_case_count} 条。",
        f"上游数据：`{report.upstream_provenance}`。",
        "",
    ]
    if report.warnings:
        lines.extend([f"> 注意：{warning}" for warning in report.warnings])
        lines.append("")
    lines.extend(
        [
            "| 实验 | 变更 | Precision | Span Coverage | Context Recall | Subquery | Entity | Duplicate | Avg Tokens | Context P95/P99 ms |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for experiment in report.experiments:
        parameters = experiment.parameters
        metrics = experiment.metrics
        change = (
            "current defaults"
            if parameters.name == report.baseline_experiment
            else (
                "fixed 900 / 0.90"
                if parameters.name == report.control_experiment
                else f"{parameters.varied_parameter}={parameters.varied_value}"
            )
        )
        lines.append(
            "| "
            + " | ".join(
                [
                    parameters.name,
                    change,
                    f"{metrics.final_context_precision:.4f}",
                    f"{metrics.supporting_span_coverage:.4f}",
                    f"{metrics.final_context_recall:.4f}",
                    f"{metrics.subquery_coverage:.4f}",
                    f"{metrics.entity_coverage:.4f}",
                    f"{metrics.duplicate_rate:.4f}",
                    f"{metrics.average_token_usage:.1f}",
            f"{metrics.adapter_latency.p95_ms or 0:.3f}/{metrics.adapter_latency.p99_ms or 0:.3f}",
                ]
            )
            + " |"
        )
    best = next(
        item
        for item in report.experiments
        if item.parameters.name == report.recommended_experiment
    )
    lines.extend(
        [
            "",
            "## 推荐配置",
            "",
            f"推荐实验：`{best.parameters.name}`。",
            "",
            "```yaml",
            f"context_top_k: {best.parameters.context_top_k}",
            f"neighbor_window: {best.parameters.neighbor_window}",
            f"source_cap: {best.parameters.source_cap}",
            f"token_budget: {best.parameters.token_budget}",
            f"max_item_tokens: {best.parameters.max_item_tokens}",
            f"near_duplicate_threshold: {best.parameters.near_duplicate_threshold:.2f}",
            "```",
            "",
            "## Baseline vs Best",
            "",
            f"- Final Context Precision: {report.baseline_vs_best.final_context_precision:+.4f}",
            f"- Supporting Span Coverage: {report.baseline_vs_best.supporting_span_coverage:+.4f}",
            f"- Final Context Recall: {report.baseline_vs_best.final_context_recall:+.4f}",
            f"- Average Tokens: {report.baseline_vs_best.average_token_usage:+.1f}",
            f"- Context P95: {report.baseline_vs_best.adapter_latency_p95_ms:+.3f} ms",
            "",
            "Evidence Span Extractor 后续分析："
            + (
                "需要；最佳配置的 Supporting Span Coverage 仍低于 0.85。"
                if report.evidence_span_extractor_analysis_required
                else "暂不需要；最佳配置已达到 0.85。"
            ),
            "",
            f"- Supporting spans: {report.evidence_span_analysis.covered_span_count}/{report.evidence_span_analysis.total_span_count}",
            f"- 已选 Parent 但窗口未覆盖标注 span: {report.evidence_span_analysis.extractor_window_miss_count}",
            f"- Parent 未进入最终 Context: {report.evidence_span_analysis.parent_not_selected_count}",
            f"- 标注 span 不在索引 Parent: {report.evidence_span_analysis.span_not_in_index_parent_count}",
            f"- 结论：{report.evidence_span_analysis.recommendation}",
            "",
        ]
    )
    if report.pipeline_latency is not None:
        generation_note = (
            "Generation 尚未运行；参数扫描阶段不重复调用 LLM。"
            if report.pipeline_latency.generation.sample_count == 0
            else "Generation 仅对推荐配置调用一次答案模型；未调用 DeepEval Judge。"
        )
        lines.extend(
            [
                "## 全链路延迟",
                "",
                "| 阶段 | 样本数 | P50 ms | P95 ms | P99 ms |",
                "|---|---:|---:|---:|---:|",
                *[
                    _latency_row(stage, latency)
                    for stage, latency in (
                        ("Retrieval", report.pipeline_latency.retrieval),
                        ("Rerank", report.pipeline_latency.rerank),
                        ("Context Adapter", report.pipeline_latency.context_adapter),
                        ("Generation", report.pipeline_latency.generation),
                    )
                ],
                "",
                f"> {generation_note}",
                "",
            ]
        )
    return "\n".join(lines)


def _parameters(
    name: str,
    config: ContextPackingConfig,
    *,
    varied_parameter: str | None = None,
    varied_value: int | float | None = None,
) -> ContextTuningParameters:
    return ContextTuningParameters(
        name=name,
        varied_parameter=varied_parameter,
        varied_value=varied_value,
        context_top_k=config.selection.max_items,
        neighbor_window=config.compression.neighbor_window,
        source_cap=config.selection.source_cap,
        token_budget=config.selection.token_budget,
        max_item_tokens=config.compression.max_item_tokens,
        near_duplicate_threshold=config.selection.near_duplicate_threshold,
    )


def _replace_parameter(
    config: ContextPackingConfig,
    field_name: str,
    value: int,
) -> ContextPackingConfig:
    if field_name == "neighbor_window":
        return config.model_copy(
            update={
                "compression": config.compression.model_copy(update={field_name: value})
            }
        )
    selection_field = "max_items" if field_name == "context_top_k" else field_name
    return config.model_copy(
        update={
            "selection": config.selection.model_copy(
                update={selection_field: value}
            )
        }
    )


def _duplicate_count(warnings: Sequence[str]) -> int:
    return sum(
        int(match.group(1))
        for warning in warnings
        if (match := _DUPLICATE_WARNING.match(warning)) is not None
    )


def _select_stage_winner(
    candidates: Sequence[ContextExperimentResult],
    reference: ContextExperimentResult,
) -> ContextExperimentResult:
    non_regressing = [
        item
        for item in candidates
        if (
            item.metrics.final_context_recall
            >= reference.metrics.final_context_recall - 0.01
            and item.metrics.supporting_span_coverage
            >= reference.metrics.supporting_span_coverage - 0.01
        )
    ]
    return max(
        non_regressing or candidates,
        key=lambda item: (
            item.metrics.quality_score,
            -item.metrics.average_token_usage,
            -item.parameters.token_budget,
            -(item.metrics.adapter_latency.p95_ms or 0),
        ),
    )


def _analyze_evidence_spans(
    inputs: Sequence[ContextTuningInput],
    *,
    config: ContextPackingConfig,
) -> EvidenceSpanAnalysis:
    builder = ContextPackBuilder(config)
    total = covered = extractor_miss = parent_not_selected = missing_from_parent = 0
    for item in inputs:
        pack = builder.build(item.plan, item.rerank)
        selected = (
            pack.items
            if pack.sufficiency.status is EvidenceSufficiencyStatus.SUFFICIENT
            else []
        )
        selected_content = {evidence.parent_id: evidence.context for evidence in selected}
        parent_content = {
            candidate.candidate.parent_id: candidate.candidate.parent_context.content
            for candidate in item.rerank.candidates
        }
        for span in item.case.supporting_spans:
            total += 1
            if span.exact_text in selected_content.get(span.parent_id, ""):
                covered += 1
            elif span.parent_id not in selected_content:
                parent_not_selected += 1
            elif span.exact_text in parent_content.get(span.parent_id, ""):
                extractor_miss += 1
            else:
                missing_from_parent += 1
    missing = total - covered
    recommendation = (
        "Supporting Span Coverage 仍低，且主要缺口来自已选 Parent 内的窗口抽取；V1 验收后应单独设计 Evidence Span Extractor V2，不继续放宽 token/top_k。"
        if extractor_miss > parent_not_selected
        else "主要缺口来自 Parent 选择，暂不应优先升级 Evidence Span Extractor。"
    )
    return EvidenceSpanAnalysis(
        total_span_count=total,
        covered_span_count=covered,
        missing_span_count=missing,
        extractor_window_miss_count=extractor_miss,
        parent_not_selected_count=parent_not_selected,
        span_not_in_index_parent_count=missing_from_parent,
        recommendation=recommendation,
    )


def _delta(
    baseline: ContextExperimentMetrics,
    best: ContextExperimentMetrics,
) -> MetricDelta:
    return MetricDelta(
        final_context_precision=(
            best.final_context_precision - baseline.final_context_precision
        ),
        final_context_recall=best.final_context_recall - baseline.final_context_recall,
        supporting_span_coverage=(
            best.supporting_span_coverage - baseline.supporting_span_coverage
        ),
        subquery_coverage=best.subquery_coverage - baseline.subquery_coverage,
        entity_coverage=best.entity_coverage - baseline.entity_coverage,
        duplicate_rate=best.duplicate_rate - baseline.duplicate_rate,
        average_token_usage=best.average_token_usage - baseline.average_token_usage,
        adapter_latency_p95_ms=(
            (best.adapter_latency.p95_ms or 0)
            - (baseline.adapter_latency.p95_ms or 0)
        ),
        adapter_latency_p99_ms=(
            (best.adapter_latency.p99_ms or 0)
            - (baseline.adapter_latency.p99_ms or 0)
        ),
    )


def _latency_row(stage: str, latency: LatencyPercentiles) -> str:
    def display(value: float | None) -> str:
        return "-" if value is None else f"{value:.3f}"

    return " | ".join(
        [
            f"| {stage}",
            str(latency.sample_count),
            display(latency.p50_ms),
            display(latency.p95_ms),
            f"{display(latency.p99_ms)} |",
        ]
    )
