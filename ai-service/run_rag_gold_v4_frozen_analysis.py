"""Read-only Gold V4 failure analysis on the two immutable 30-case traces.

Never collects new retrieval traces. Missing traces are coverage gaps, not misses.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from app.rag.evaluation.io import load_evaluation_cases, load_evaluation_traces
from app.rag.evaluation.v4_answerability import (
    evaluate_v4_answerability,
    supported_evidence_units,
)
from run_rag_evidence_group_replay import ROOT, TRACES

GOLD = ROOT / "evals/datasets/rag_eval_gold_v4.frozen.jsonl"
BASELINE = ROOT / "evals/reports/gold_v4_frozen_baseline.md"
ANALYSIS = ROOT / "evals/reports/gold_v4_retrieval_failure_analysis.md"
DETAIL = ROOT / "evals/reports/gold_v4_retrieval_failure_analysis.jsonl"
GOLD_SHA256 = "1ca311c731b83c0fd0f3ab95dcf31d06a95565e54e631e174e09dd890b81ad4e"
FOCUS_CASES = ("rag-v0-003", "rag-v0-034", "rag-v0-036", "rag-v0-039")
# Query-form hypotheses, not attributed root causes. They are never used by
# the evaluator or the quantitative failure-stage classification.
SUSPECTED_BY_CASE = {
    "rag-v0-001": ("query_too_broad", "A whole music career is requested without a period or evidence type."),
    "rag-v0-002": ("query_too_broad", "Music-style characteristics span many albums and sources."),
    "rag-v0-003": ("entity_mismatch", "The query says Jay Chou in English while most Gold evidence is Chinese; alias handling needs inspection."),
    "rag-v0-004": ("query_too_broad", "Background story does not specify which biographical aspect."),
    "rag-v0-024": ("query_too_vague", "The question asks for lyricists without naming any songs."),
    "rag-v0-036": ("query_too_broad", "The requested 2000–2005 evolution spans multiple years and albums."),
    "rag-v0-037": ("query_too_vague", "Important experiences in 2003 has no event type."),
    "rag-v0-039": ("query_too_broad", "Changes across 2008–2012 span multiple event types and years."),
    "rag-v0-040": ("query_too_broad", "A full career timeline spans many independent milestones."),
    "rag-v0-042": ("query_too_vague", "English-language material does not specify subject or document type."),
    "rag-v0-043": ("query_too_vague", "Interview material does not specify year or topic."),
    "rag-v0-044": ("query_too_vague", "News material excludes live recordings but names no event or date."),
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def failure_stage(top40: bool, top12: bool, final: bool) -> str:
    if final and not top12 or top12 and not top40:
        raise ValueError("trace/evaluator stage hit is non-monotonic")
    if not top40:
        return "RETRIEVER_MISS"
    if not top12:
        return "RERANK_MISS"
    if not final:
        return "CONTEXT_DROP"
    return "SUCCESS"


def _grams(text: str) -> set[str]:
    normalized = "".join(re.findall(r"[\u3400-\u9fffA-Za-z0-9]+", text.lower()))
    return {normalized[i:i + 2] for i in range(max(0, len(normalized) - 1))}


def nearest_top40(unit, candidate_pool) -> dict | None:
    """Lexical diagnostic only; does not assert relevance or causality."""
    subject = unit.claim or " ".join(span.exact_text for span in unit.spans)
    target = _grams(subject)
    if not target or not candidate_pool:
        return None
    best = max(candidate_pool, key=lambda item: len(target & _grams(item.content)))
    return {
        "parent_id": best.parent_id, "rank": best.rank, "title": best.title,
        "source_id": best.source_id,
        "claim_bigram_overlap": round(len(target & _grams(best.content)) / len(target), 3),
        "selection_method": "maximum claim-character-bigram overlap; diagnostic, not a relevance judgment",
    }


def _rank(contexts, acceptable: list[str]) -> int | None:
    ranks = [item.rank for item in contexts if item.parent_id in acceptable]
    return min(ranks) if ranks else None


def _unit_row(case, unit, label: str, trace, scored_unit=None) -> dict:
    gold_ids = list(unit.group.acceptable_parent_ids) if unit.group else [unit.anchor_parent_id]
    row = {
        "trace_label": label, "case_id": case.case_id,
        "claim_id": f"{case.case_id}/{unit.unit_id}", "question": case.query,
        "claim": unit.claim, "answerability_level": case.answerability_level,
        "unit_kind": unit.kind, "group_relation": unit.group.relation if unit.group else None,
        "gold_parent_ids": gold_ids,
        "gold_evidence": [
            {"parent_id": span.parent_id, "source_id": span.source_id,
             "exact_text": span.exact_text, "support_level": span.support_level}
            for span in unit.spans
        ],
        "trace_available": trace is not None,
        "standalone_query": trace.standalone_query if trace else None,
        "trace_warnings": list(trace.warnings) if trace else [],
        "top40_hit": None, "top12_hit": None, "final_context_hit": None,
        "retriever_rank": None, "rerank_rank": None, "reranker_score": None,
        "failure_stage": "NO_HISTORICAL_TRACE", "failure_reason": "trace_not_collected",
        "retriever_ranking_diagnosis": None, "nearest_top40": None,
        "suspected_causes": [], "suspected_causes_basis": None,
        "top12_non_gold": [], "context_drop_rule": None,
        "notes": "The fixed historical traces predate this new case; no retrieval miss is inferred.",
    }
    if trace is None:
        return row
    if scored_unit is None:
        raise ValueError(f"missing scored unit for traced case:{case.case_id}:{unit.unit_id}")
    row.update({
        "top40_hit": scored_unit.top40_hit,
        "top12_hit": scored_unit.top12_hit,
        "final_context_hit": scored_unit.final_hit,
        "retriever_rank": _rank(trace.candidate_pool, gold_ids),
        "rerank_rank": _rank(trace.reranked_context, gold_ids),
        "failure_stage": failure_stage(
            scored_unit.top40_hit, scored_unit.top12_hit, scored_unit.final_hit,
        ),
    })
    stage = row["failure_stage"]
    if stage == "RETRIEVER_MISS":
        row["nearest_top40"] = nearest_top40(unit, trace.candidate_pool)
        row["retriever_ranking_diagnosis"] = "insufficient_trace_for_ranking_diagnosis"
        row["failure_reason"] = "unknown"
        if case.case_id in SUSPECTED_BY_CASE:
            cause, basis = SUSPECTED_BY_CASE[case.case_id]
            row["suspected_causes"] = [cause]
            row["suspected_causes_basis"] = basis
        row["notes"] = (
            "Gold acceptable Parent absent from captured Top40. No deeper candidate list or "
            "filter decision is recorded; rank below Top40 and root cause cannot be established."
        )
    elif stage == "RERANK_MISS":
        row["failure_reason"] = "reranked_out_cause_unrecorded"
        case_gold_ids = set(case.relevant_parent_ids)
        case_gold_ids.update(pid for group in case.evidence_groups
                             for pid in group.acceptable_parent_ids)
        row["top12_non_gold"] = [
            {"parent_id": item.parent_id, "rank": item.rank, "title": item.title}
            for item in trace.reranked_context if item.parent_id not in case_gold_ids
        ]
        row["notes"] = (
            "Gold unit hit Top40 but not Top12. Listed non-Gold Top12 items co-occur; the "
            "trace has no reranker scores or discarded-item ranks, so displacement is unproven."
        )
    elif stage == "CONTEXT_DROP":
        row["failure_reason"] = "context_rule_unrecorded"
        row["context_drop_rule"] = "not_recorded_in_historical_trace"
        row["notes"] = (
            "Gold unit hit Top12 but not final context. Trace has no per-item removal "
            "decision; dedup, token budget, cap and expansion cannot be distinguished. "
            f"Context token count/budget={trace.context_token_count}/{trace.context_token_budget}; "
            f"warnings={trace.warnings}."
        )
    else:
        row["failure_reason"] = "none"
        row["notes"] = "Gold evidence unit survived Top40, Top12 and final context."
    return row


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _funnel(rows: list[dict]) -> dict:
    traced = [row for row in rows if row["trace_available"]]
    stages = Counter(row["failure_stage"] for row in traced)
    n = len(traced)
    top40 = sum(row["top40_hit"] for row in traced)
    top12 = sum(row["top12_hit"] for row in traced)
    final = sum(row["final_context_hit"] for row in traced)
    if stages != Counter({
        "RETRIEVER_MISS": n - top40, "RERANK_MISS": top40 - top12,
        "CONTEXT_DROP": top12 - final, "SUCCESS": final,
    }):
        raise ValueError("failure stages do not partition traced supported evidence units")
    return {
        "units": n, "top40_hit": top40, "top40_miss": n - top40,
        "top12_hit": top12, "lost_after_rerank": top40 - top12,
        "final_hit": final, "context_drop": top12 - final,
        "retriever_survival_rate": _ratio(top40, n),
        "reranker_survival_rate": _ratio(top12, top40),
        "context_survival_rate": _ratio(final, top12),
        "failure_categories": dict(stages),
        "retriever_miss_subtypes": dict(Counter(
            row["failure_reason"] for row in traced
            if row["failure_stage"] == "RETRIEVER_MISS"
        )),
    }


def _percent(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.2%}"


def main() -> None:
    if sha256(GOLD) != GOLD_SHA256:
        raise ValueError("frozen Gold V4 hash changed")
    cases = load_evaluation_cases(GOLD)
    if len(cases) != 50 or any(case.annotation_status.value != "approved" for case in cases):
        raise ValueError("frozen Gold V4 count/approval changed")
    counts = Counter(case.answerability_level or "needs_input" for case in cases)
    if counts != Counter({"fully_answerable": 29, "partially_answerable": 10,
                          "insufficient_evidence": 7, "needs_input": 4}):
        raise ValueError("frozen Gold V4 answerability counts changed")
    all_supported_units = sum(len(supported_evidence_units(case)) for case in cases)
    all_rows: list[dict] = []
    outputs = {}
    expected_trace_ids = None
    for label, (path, expected_hash) in TRACES.items():
        if sha256(path) != expected_hash:
            raise ValueError(f"historical Trace hash changed:{label}")
        traces = load_evaluation_traces(path)
        trace_by_id = {trace.case_id: trace for trace in traces}
        if len(trace_by_id) != len(traces) or len(traces) != 30:
            raise ValueError(f"unexpected historical Trace coverage:{label}")
        if not set(trace_by_id).issubset({case.case_id for case in cases}):
            raise ValueError(f"Trace contains unknown Gold cases:{label}")
        if expected_trace_ids is not None and set(trace_by_id) != expected_trace_ids:
            raise ValueError("fallback/nav historical Trace case coverage differs")
        expected_trace_ids = set(trace_by_id)
        traced_cases = [case for case in cases if case.case_id in trace_by_id]
        metrics = evaluate_v4_answerability(traced_cases, traces)
        scored_cases = {case.case_id: case for case in metrics.cases}
        rows = []
        for case in cases:
            units = supported_evidence_units(case)
            trace = trace_by_id.get(case.case_id)
            scored = scored_cases.get(case.case_id)
            if scored is not None and len(units) != len(scored.units):
                raise ValueError(f"unit count changed:{case.case_id}")
            for index, unit in enumerate(units):
                if scored is not None and scored.units[index].unit.unit_id != unit.unit_id:
                    raise ValueError(f"unit alignment changed:{case.case_id}")
                rows.append(_unit_row(
                    case, unit, label, trace,
                    scored.units[index] if scored is not None else None,
                ))
        if len(rows) != all_supported_units:
            raise ValueError("not every supported unit has an audit row")
        traced_rows = [row for row in rows if row["trace_available"]]
        if len(traced_rows) != metrics.evidence_unit_count:
            raise ValueError("unit detail disagrees with V4 evaluator")
        outputs[label] = {
            "trace_path": path.relative_to(ROOT).as_posix(), "trace_sha256": expected_hash,
            "trace_case_count": len(traces), "missing_trace_case_ids": [
                case.case_id for case in cases if case.case_id not in trace_by_id
            ],
            "eligible_full_cases": metrics.eligible_full_cases,
            "eligible_partial_cases": metrics.eligible_partial_cases,
            "insufficient_cases": metrics.insufficient_cases,
            "needs_input_cases": metrics.needs_input_cases,
            "traced_supported_units": metrics.evidence_unit_count,
            "untraced_supported_units": len(rows) - len(traced_rows),
            "metrics": {
                "evidence_recall_at_40": metrics.evidence_recall_at_40,
                "evidence_rerank_recall_at_12": metrics.evidence_rerank_recall_at_12,
                "final_context_precision": metrics.final_context_precision,
            },
            "funnel": _funnel(traced_rows),
            "full_funnel": _funnel([row for row in traced_rows if row["answerability_level"] == "fully_answerable"]),
            "partial_funnel": _funnel([row for row in traced_rows if row["answerability_level"] == "partially_answerable"]),
        }
        all_rows.extend(rows)
    DETAIL.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in all_rows), encoding="utf-8")
    _write_reports(cases, all_supported_units, all_rows, outputs)
    print(json.dumps({"gold_cases": len(cases), "all_supported_units": all_supported_units,
                      "traces": outputs}, ensure_ascii=False, default=dict))


def _write_reports(cases, all_supported_units: int, rows: list[dict], outputs: dict) -> None:
    baseline = [
        "# Frozen Gold V4：固定历史 Trace baseline", "",
        f"- 正式 Gold：`{GOLD.relative_to(ROOT).as_posix()}`；SHA-256 `{GOLD_SHA256}`；50 条，full 29 / partial 10 / insufficient 7 / needs_input 4。",
        f"- 全量 supported evidence units：{all_supported_units}。历史 Trace 仅覆盖其中原 30 条；新增 049–068 共 20 条没有 Trace，不能计算 50 条全量 baseline。",
        "- 下列是**有历史 Trace 的 30-case 子集基线**。V4 evaluator 仅对 approved full/partial case 计分；Recall 与 Final Context Precision 均按 eligible case 宏平均。Final Context Precision 是唯一 Parent 的 precision，不是 evidence-unit 生存率。",
        "- 只读 replay：逐份核对 Trace SHA-256，未重新运行 Retrieval。", "",
        "| Trace | eligible full | eligible partial | supported units | insufficient | needs_input | Evidence Recall@40 | Evidence Rerank Recall@12 | Final Context Precision |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for label, result in outputs.items():
        m = result["metrics"]
        baseline.append(
            f"| {label} | {result['eligible_full_cases']} | {result['eligible_partial_cases']} | "
            f"{result['traced_supported_units']} | {result['insufficient_cases']} | {result['needs_input_cases']} | "
            f"{m['evidence_recall_at_40']:.6f} | {m['evidence_rerank_recall_at_12']:.6f} | "
            f"{m['final_context_precision']:.6f} |"
        )
    baseline += ["", "## Trace 覆盖与口径", ""]
    for label, result in outputs.items():
        baseline += [
            f"- `{label}`：`{result['trace_path']}`；SHA-256 `{result['trace_sha256']}`；case {result['trace_case_count']}；未覆盖 `{', '.join(result['missing_trace_case_ids'])}`；未覆盖 supported units {result['untraced_supported_units']}。"
        ]
    baseline += [
        "- Top40/Top12 命中按 claim-scoped duplicate/equivalent/complementary 关系计；未标 claim 的旧 span 仍按既有 evaluator 的独立 evidence-unit 回退规则计，不推断为同一 claim。",
        "- historical Trace 未记录 Top40 以外排名、reranker score 或逐 Parent 的 context 淘汰规则；本报告不推断这些不存在的字段。", "",
    ]
    BASELINE.write_text("\n".join(baseline), encoding="utf-8")

    analysis = [
        "# Gold V4：固定 historical Trace 的 Retrieval Failure Analysis", "",
        "- 只分析已有 fallback 与 nav_context6 Trace，不重新检索或改动检索链路。评分单位是 V4 evaluator 的 supported evidence unit；旧 case 未标 claim 的 span 保持原有回退语义。",
        "- 50 条 Gold 中新增 049–068 没有 historical Trace，作为 `NO_HISTORICAL_TRACE` 单列，**不计为任何检索失败**。本轮无法判定这些新增 case 的真实失败类别，也无法给出 50-case 全量指标。",
        "- `RETRIEVER_RANKING_MISS` 需要 Top40 以外的候选与名次；现有 Trace 不具备，因此此类一律标记 `insufficient_trace_for_ranking_diagnosis`，不猜测。",
        "- Top12 非 Gold 文档仅列出共现项，不能从 Trace 证明其“挤出”因果；context 逐项删除规则与 reranker score 均未记录。", "",
    ]
    for label, result in outputs.items():
        label_rows = [row for row in rows if row["trace_label"] == label]
        traced = [row for row in label_rows if row["trace_available"]]
        funnel = result["funnel"]
        analysis += [
            f"## {label}", "",
            f"支持证据单位：全 Gold {all_supported_units}，本 Trace 可评 {funnel['units']}，未覆盖 {result['untraced_supported_units']}。",
            "", "| 漏斗阶段 | 数量 |", "| --- | ---: |",
            f"| Top40 hit / miss | {funnel['top40_hit']} / {funnel['top40_miss']} |",
            f"| Top12 hit / 从 Top40 丢失 | {funnel['top12_hit']} / {funnel['lost_after_rerank']} |",
            f"| Final hit / Context drop | {funnel['final_hit']} / {funnel['context_drop']} |",
            "",
            f"Retriever / Reranker / Context 生存率：{_percent(funnel['retriever_survival_rate'])} / {_percent(funnel['reranker_survival_rate'])} / {_percent(funnel['context_survival_rate'])}。",
            "", "| Failure category | Count |", "| --- | ---: |",
        ]
        for stage in ("RETRIEVER_MISS", "RERANK_MISS", "CONTEXT_DROP", "SUCCESS"):
            analysis.append(f"| {stage} | {funnel['failure_categories'].get(stage, 0)} |")
        suspected = Counter(cause for row in traced if row["failure_stage"] == "RETRIEVER_MISS"
                            for cause in row["suspected_causes"])
        analysis += ["", f"Retriever miss 根因子类型（可证实）：{funnel['retriever_miss_subtypes'] or '无'}。`unknown` 表示该 Trace 无法证明语义、实体、时间、词法、chunk 或 filter 中的哪一项是根因。",
                     f"查询表述层面的**非排他性、未验证假设**：{dict(suspected) or '无'}；其余为无足够线索。按证据单位计数，同一 query 的多个 claim 会重复计入；这不是根因发生率。每条依据见 JSONL。", ""]
        for level, key in (("fully_answerable", "full_funnel"),
                           ("partially_answerable", "partial_funnel")):
            sub = result[key]
            analysis.append(
                f"- {level}：units {sub['units']}；Top40 {sub['top40_hit']}；Top12 {sub['top12_hit']}；Final {sub['final_hit']}；"
                f"miss/rerank drop/context drop = {sub['top40_miss']}/{sub['lost_after_rerank']}/{sub['context_drop']}。"
            )
        analysis += ["", "### 重点 case", ""]
        focus = [*FOCUS_CASES, *sorted({row["case_id"] for row in traced
                                     if row["case_id"] >= "rag-v0-049"
                                     and row["failure_stage"] != "SUCCESS"})]
        for cid in focus:
            case_rows = [row for row in traced if row["case_id"] == cid]
            if not case_rows:
                analysis.append(f"- `{cid}`：本 Trace 无可评分 supported unit；新增 case 因无 Trace 不归因。")
                continue
            stages = Counter(row["failure_stage"] for row in case_rows)
            analysis.append(f"- `{cid}`：{len(case_rows)} units；{dict(stages)}。")
            for row in case_rows:
                claim = row["claim"] or f"[未标 claim] {row['gold_evidence'][0]['exact_text'][:48]}"
                nearest = row["nearest_top40"]
                analysis.append(
                    f"  - `{row['claim_id']}` {claim}；Gold `{', '.join(row['gold_parent_ids'])}`；"
                    f"{row['failure_stage']}；Retriever rank {row['retriever_rank']} / Rerank rank {row['rerank_rank']}；"
                    f"最近 Top40（词面启发式）{nearest['parent_id'] + ' (#' + str(nearest['rank']) + ')' if nearest else 'N/A'}。"
                )
        failures = Counter(row["case_id"] for row in traced if row["failure_stage"] != "SUCCESS")
        analysis += ["", "### 按可观察失败数排列的后续排查优先级", ""]
        for cid, count in sorted(failures.items(), key=lambda item: (-item[1], item[0]))[:12]:
            stages = Counter(row["failure_stage"] for row in traced
                             if row["case_id"] == cid and row["failure_stage"] != "SUCCESS")
            analysis.append(f"- `{cid}`：{count} units，{dict(stages)}。这只是调查优先级，不是优化方案。")
        if not failures:
            analysis.append("- 本 Trace 无可观察证据漏失。")
        analysis.append("")
    analysis += [
        "## 解释边界", "",
        "- 最接近 Top40 是 claim 字符 bigram 重合度启发式，供人工定位，不等同于真实相关性、检索分数或缺失 Gold 的深层 rank。",
        "- `failure_reason=unknown` 不是诊断结论；需要更深候选、索引过滤日志、reranker 分数和 Context Builder 决策日志才能做因果细分。本轮不新增这些 Trace，也不修改 Retrieval。",
        f"- 全部逐 unit 明细：`{DETAIL.relative_to(ROOT).as_posix()}`，含 Gold span/source、三个阶段命中、rank、近似项及不可判定原因。", "",
    ]
    ANALYSIS.write_text("\n".join(analysis), encoding="utf-8")


if __name__ == "__main__":
    main()
