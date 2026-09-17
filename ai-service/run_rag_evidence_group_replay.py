"""Compare legacy and evidence-group scoring on immutable historical traces."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.rag.evaluation.group_aware import evaluate_evidence_metrics
from app.rag.evaluation.io import load_evaluation_cases, load_evaluation_traces
from app.rag.evaluation.service import evaluate_traces


ROOT = Path(__file__).resolve().parent
V3 = ROOT / "evals/datasets/rag_eval_gold_v3.ai_assisted_source_resolution.jsonl"
V4 = ROOT / "evals/datasets/rag_eval_gold_v4.candidate.jsonl"
TRACES = {
    "fallback": (
        ROOT / "evals/artifacts/gold_v2_trace_v3_fallback_20260916.jsonl",
        "7e85baf64ff79163093e1712ba889cbb221c41023609c5c6b3d5fed657bd0c8e",
    ),
    "nav_context6": (
        ROOT / "evals/artifacts/gold_v2_trace_v3_nav_context6_20260916.jsonl",
        "9beacd474a7257253cec4cfa0655a8348e71943200456abc662d84928c40f967",
    ),
}
OUT_JSON = ROOT / "evals/reports/gold_v4_evidence_groups_same_trace_20260917.json"
OUT_MD = ROOT / "evals/reports/gold_v4_evidence_groups_same_trace_20260917.md"
STAGES = ("recall_at_40", "rerank_recall_at_12", "final_context_precision")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def metrics(result) -> dict[str, float]:
    return {name: getattr(result, name) for name in STAGES}


def differences(legacy, aware, trace_by_case: dict) -> list[dict]:
    rows = []
    for old, new in zip(legacy.cases, aware.cases, strict=True):
        assert old.case_id == new.case_id
        trace = trace_by_case[old.case_id]
        for name, contexts in (
            ("recall_at_40", trace.candidate_pool),
            ("rerank_recall_at_12", trace.reranked_context),
            ("final_context_precision", trace.final_context),
        ):
            before = getattr(old, name)
            after = getattr(new, name)
            if before.value == after.value:
                continue
            affected_groups = [group for group in after.matched_groups if group["retrieved_parent_ids"]]
            if not affected_groups:
                raise ValueError(f"unexplained group scoring difference:{old.case_id}:{name}")
            rows.append({
                "case_id": old.case_id,
                "metric_stage": name,
                "legacy_result": before.value,
                "group_aware_result": after.value,
                "retrieved_parent_ids": list(dict.fromkeys(x.parent_id for x in contexts)),
                "legacy_hits": list(before.hits),
                "group_aware_hits": list(after.hits),
                "matched_evidence_groups": affected_groups,
                "direction": "MISS_TO_HIT" if after.value > before.value else "HIT_TO_MISS",
                "reason": (
                    "An acceptable duplicate/equivalent Parent satisfied a Gold evidence group."
                    if after.value > before.value else
                    "A complementary group requires all listed Parents; a partial set is insufficient."
                ),
            })
    return rows


def check_legacy(legacy_report, compatible, trace_by_case: dict, source_cases) -> list[dict]:
    mismatches = []
    expected = {
        "recall_at_40": legacy_report.retrieval.recall_at_40,
        "rerank_recall_at_12": legacy_report.rerank.rerank_recall_at_12,
        "final_context_precision": legacy_report.final_context_precision,
    }
    for name, value in expected.items():
        if getattr(compatible, name) != value:
            mismatches.append({"metric_stage": name, "legacy": value, "compatibility": getattr(compatible, name)})
    retrieved = {x.case_id: x for x in legacy_report.retrieval.cases}
    reranked = {x.case_id: x for x in legacy_report.rerank.cases}
    relevant_by_case = {case.case_id: set(case.relevant_parent_ids) for case in source_cases}
    for row in compatible.cases:
        trace = trace_by_case[row.case_id]
        final_ids = {x.parent_id for x in trace.final_context}
        relevant = relevant_by_case[row.case_id]
        precision = len(final_ids & relevant) / len(final_ids) if final_ids else 0.0
        baseline = {
            "recall_at_40": retrieved[row.case_id].recall_at_40,
            "rerank_recall_at_12": reranked[row.case_id].rerank_recall_at_12,
            "final_context_precision": precision,
        }
        for name, value in baseline.items():
            if getattr(row, name).value != value:
                mismatches.append({"case_id": row.case_id, "metric_stage": name, "legacy": value,
                                   "compatibility": getattr(row, name).value})
    return mismatches


def main() -> None:
    if sha256(V3) != "5d2888542042af16bf38d3ae63f45c956c8e37f658397e2f5d3a78c3759236e9":
        raise ValueError("frozen Gold V3 hash changed")
    v3_cases = load_evaluation_cases(V3)
    v4_cases = load_evaluation_cases(V4)
    output = {"gold_v3_sha256": sha256(V3), "gold_v4_sha256": sha256(V4), "traces": {}}
    loaded_traces = {}
    for label, (path, expected_hash) in TRACES.items():
        actual_hash = sha256(path)
        if actual_hash != expected_hash:
            raise ValueError(f"historical Trace hash changed:{label}")
        traces = load_evaluation_traces(path)
        loaded_traces[label] = traces
        trace_by_case = {x.case_id: x for x in traces}
        old_v3 = evaluate_traces(v3_cases, traces)
        # The new compatibility path must match both aggregate and per-case legacy scores.
        compatible_v3 = evaluate_evidence_metrics(v3_cases, traces, mode="compatibility")
        mismatches = check_legacy(old_v3, compatible_v3, trace_by_case, v3_cases)
        output["traces"][label] = {
            "trace_path": str(path.relative_to(ROOT)).replace("\\", "/"),
            "trace_sha256": actual_hash,
            "v3": {"legacy": {
                "recall_at_40": old_v3.retrieval.recall_at_40,
                "rerank_recall_at_12": old_v3.rerank.rerank_recall_at_12,
                "final_context_precision": old_v3.final_context_precision,
            }, "compatibility": metrics(compatible_v3), "case_count": compatible_v3.case_count,
                "case_differences": mismatches},
        }
        if mismatches:
            OUT_JSON.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            raise ValueError(f"V3 compatibility failed:{label}; stopped before V4 scoring")
    # No V4 scoring starts until every historical Trace has passed V3 compatibility.
    for label, traces in loaded_traces.items():
        trace_by_case = {x.case_id: x for x in traces}
        old_v4 = evaluate_traces(v4_cases, traces)
        compatible_v4 = evaluate_evidence_metrics(v4_cases, traces, mode="compatibility")
        v4_mismatches = check_legacy(old_v4, compatible_v4, trace_by_case, v4_cases)
        if v4_mismatches:
            raise ValueError(f"V4 legacy projection differs:{label}:{v4_mismatches}")
        aware_v4 = evaluate_evidence_metrics(v4_cases, traces, mode="group_aware")
        group_audit = []
        for case in aware_v4.cases:
            for stage in STAGES:
                for group in getattr(case, stage).matched_groups:
                    group_audit.append({"case_id": case.case_id, "metric_stage": stage, **group})
        output["traces"][label]["v4"] = {
            "case_count": compatible_v4.case_count,
            "legacy_id_only": metrics(compatible_v4),
            "group_aware": metrics(aware_v4),
            "case_differences": differences(compatible_v4, aware_v4, trace_by_case),
            "group_stage_audit": group_audit,
        }
    OUT_JSON.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Gold V4 evidence_groups：同 Trace 兼容性与差异",
        "",
        f"- Gold V3 SHA-256：`{output['gold_v3_sha256']}`。",
        f"- Gold V4 candidate SHA-256：`{output['gold_v4_sha256']}`。",
        "- 两份历史 Trace 均经 SHA-256 核对；本次仅离线读取，没有运行 Retrieval / Rerank。",
        "- 仅比较 Recall@40、Rerank Recall@12、Final Context Precision；case eligibility 仍是 approved 且 expected_outcome=answerable。部分可回答 case 当前不进入这三项指标，answerability_level 不参与筛选。",
        "- Recall 分母仍是每条 case 的 relevant Parent 数，再对 eligible case 宏平均；Final Context Precision 分母仍是 final context 的唯一 Parent 数，再宏平均。",
        "- 原 evaluator 在 Candidate Top40、Rerank Top12、Final Context 都仅将 `relevant_parent_ids` 与 retrieved Parent ID 取交集；不检查 evidence_text 是否出现在 Trace 内容中。Final Context Precision 以唯一 Parent ID 命中数除以唯一 context Parent 数。",
        "- `expected_outcome` 还在旧 evaluator 中决定 outcome accuracy 的对照逻辑；`answerability_level` 目前只存于 Gold，不用于上述评分。",
        "- 无 group 的 exact Parent 必须按 ID 命中；duplicate/equivalent 组任意 acceptable Parent 命中；complementary 组须全部 acceptable Parent 命中才算完整证据。",
        "",
    ]
    for label, item in output["traces"].items():
        v3 = item["v3"]
        v4 = item["v4"]
        lines += [
            f"## {label}", "",
            f"历史 Trace：`{item['trace_path']}`；SHA-256 `{item['trace_sha256']}`。",
            f"V3 legacy 与 compatibility：{v3['case_count']} 条 eligible case；逐 case 差异 {len(v3['case_differences'])} 条；三项指标逐值相同。",
            "", "| Metric | V3 Legacy | V3 Compatibility |", "| --- | ---: | ---: |",
        ]
        for name, title in (("recall_at_40", "Recall@40"), ("rerank_recall_at_12", "Rerank Recall@12"),
                            ("final_context_precision", "Final Context Precision")):
            lines.append(f"| {title} | {v3['legacy'][name]:.10f} | {v3['compatibility'][name]:.10f} |")
        lines += ["", "| Metric | V4 Legacy ID-only | V4 Group-aware |", "| --- | ---: | ---: |"]
        for name, title in (("recall_at_40", "Recall@40"), ("rerank_recall_at_12", "Rerank Recall@12"),
                            ("final_context_precision", "Final Context Precision")):
            lines.append(f"| {title} | {v4['legacy_id_only'][name]:.10f} | {v4['group_aware'][name]:.10f} |")
        lines += ["", f"V4 eligible case：{v4['case_count']}；指标差异 {len(v4['case_differences'])} 条。", ""]
        if v4["case_differences"]:
            for diff in v4["case_differences"]:
                lines.append(f"- {diff['case_id']} / {diff['metric_stage']}：{diff['legacy_result']} → {diff['group_aware_result']} ({diff['direction']})；retrieved `{diff['retrieved_parent_ids']}`；matched group `{diff['matched_evidence_groups']}`；原因：{diff['reason']}")
        else:
            lines.append("- 无 MISS→HIT，也无异常 HIT→MISS。")
        lines += ["", "### Group 逐 stage 核对", ""]
        if v4["group_stage_audit"]:
            for match in v4["group_stage_audit"]:
                lines.append(f"- {match['case_id']} / {match['metric_stage']} / `{match['evidence_group_id']}`：relation=`{match['relation']}`，retrieved=`{list(match['retrieved_parent_ids'])}`，acceptable=`{list(match['acceptable_parent_ids'])}`，complete=`{match['complete']}`。")
        else:
            lines.append("- eligible case 没有 evidence_group。")
        lines.append("")
    lines += [
        "## 解释与边界", "",
        "- V4 当前仅 026 有两组 `duplicate`；没有 `equivalent` 或 `complementary` 的真实 case，后两种关系由单元测试验证。",
        "- 026 在候选集和 Top12 中出现替代 Parent `parent-d0c34fdc20c0a8d545deea06`，但同组锚点 `parent-cfb9f049a0cfdf86866257fd` 也在，因此这两份 Trace 上没有 Parent-ID false negative 被修复。Final context 未出现该替代 Parent。",
        "- 当前 legacy 的 expected_outcome 决定 eligible case 和 outcome accuracy；answerability_level 不参与评分。V4 的 partially_answerable 投影到 insufficient_evidence，所以这些 case 暂不进入本次三项指标；本轮未改变该规则。",
        "- 本报告不代表 Gold V4 冻结或 Retrieval 质量变化。",
        "",
    ]
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"status": "compatible", "report": str(OUT_MD), "json": str(OUT_JSON)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
