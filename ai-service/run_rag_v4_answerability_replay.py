"""Compare three V4 evidence metrics on the same frozen historical traces."""

from __future__ import annotations

import json
from collections import Counter

from app.rag.evaluation.group_aware import evaluate_evidence_metrics
from app.rag.evaluation.io import load_evaluation_cases, load_evaluation_traces
from app.rag.evaluation.service import evaluate_traces
from app.rag.evaluation.v4_answerability import evaluate_v4_answerability
from run_rag_evidence_group_replay import ROOT, TRACES, V3, V4, sha256


OUT_JSON = ROOT / "evals/reports/gold_v4_answerability_same_trace_20260917.json"
OUT_MD = ROOT / "evals/reports/gold_v4_answerability_same_trace_20260917.md"
PREVIOUS = ROOT / "evals/reports/gold_v4_evidence_groups_same_trace_20260917.json"

# These are verbatim Gold V4 limitations. They are displayed only; the scoring
# denominator is built exclusively from supported spans and evidence groups.
UNSUPPORTED_BY_CASE = {
    "rag-v0-003": [
        "不得将来源评价写成已独立证实的行业共识、影响规模或因果贡献",
        "不得用歌曲主题举例直接证明对华语音乐的影响",
    ],
    "rag-v0-005": ["缺少《晴天》专属创作缘起证据"],
    "rag-v0-008": ["冻结材料未提供其具体创作动机、灵感来源或过程"],
    "rag-v0-031": ["缺少林俊杰独立风格证据，不能可靠比较"],
    "rag-v0-034": ["现有证据只分别说明特征，不能确认两曲的直接差异或程度"],
    "rag-v0-036": [
        "不得以2005年专辑发行日期推断2005年的音乐风格变化",
        "不得把《东风破》单曲特征推广为整张专辑或2000至2005年整体风格",
    ],
    "rag-v0-039": ["局部事件不能证明2008至2012年完整变化或事业转型"],
    "rag-v0-045": ["缺少针对《晴天》音乐风格/审美/编曲的乐评文本"],
}


def _metric_values(report) -> dict[str, float]:
    return {
        "evidence_recall_at_40": report.recall_at_40,
        "evidence_rerank_recall_at_12": report.rerank_recall_at_12,
        "final_context_precision": report.final_context_precision,
    }


def _partial_detail(case, score) -> dict:
    limitations = UNSUPPORTED_BY_CASE[case.case_id]
    gold_statements = set(case.required_facts) | set(case.forbidden_claims)
    if not set(limitations).issubset(gold_statements):
        raise ValueError(f"unsupported-claim note is not in Gold:{case.case_id}")
    units = []
    for item in score.units:
        unit = item.unit
        units.append({
            "unit_id": unit.unit_id,
            "kind": unit.kind,
            "supported_claim": unit.claim,
            "anchor_parent_id": unit.anchor_parent_id,
            "evidence_group_id": unit.group.evidence_group_id if unit.group else None,
            "relation": unit.group.relation if unit.group else None,
            "acceptable_parent_ids": list(unit.group.acceptable_parent_ids) if unit.group else [unit.anchor_parent_id],
            "supporting_spans": [
                {"parent_id": span.parent_id, "claim": span.claim, "support_level": span.support_level,
                 "evidence_text": span.exact_text}
                for span in unit.spans
            ],
            "top40_hit": item.top40_hit,
            "top12_hit": item.top12_hit,
            "final_context_hit": item.final_hit,
            "final_accepted_parent_ids": list(item.final_accepted_parent_ids),
            "included_because": (
                "Gold evidence_group defines this supported claim and its Parent relationship."
                if unit.group else
                "Gold supporting span has support_level full/partial; only this local evidence unit is scored."
            ),
        })
    if any(span.support_level == "insufficient" for unit in score.units for span in unit.unit.spans):
        raise ValueError(f"insufficient supporting span entered metric:{case.case_id}")
    return {
        "case_id": case.case_id,
        "question": case.query,
        "answerability_level": case.answerability_level,
        "expected_outcome_legacy": case.expected_outcome.value,
        "supported_claims": [
            unit["supported_claim"] if unit["supported_claim"] is not None
            else f"[claim 未标注] {unit['supporting_spans'][0]['evidence_text']}"
            for unit in units
        ],
        "unsupported_claims_from_gold": limitations,
        "participating_evidence_groups": [unit["evidence_group_id"] for unit in units if unit["evidence_group_id"]],
        "evidence_unit_count": score.evidence_unit_count,
        "top40_hit_count": sum(unit["top40_hit"] for unit in units),
        "top12_hit_count": sum(unit["top12_hit"] for unit in units),
        "final_context_hit_count": sum(unit["final_context_hit"] for unit in units),
        "evidence_recall_at_40": score.evidence_recall_at_40,
        "evidence_rerank_recall_at_12": score.evidence_rerank_recall_at_12,
        "final_context_precision": score.final_context_precision,
        "scored_units": units,
        "excluded_because": "Gold limitations/forbidden claims have no supported evidence unit; insufficient spans are excluded.",
    }


def _assert_prior_metrics(label: str, old, compatible, grouped, prior: dict) -> None:
    expected_old = {
        "evidence_recall_at_40": old.retrieval.recall_at_40,
        "evidence_rerank_recall_at_12": old.rerank.rerank_recall_at_12,
        "final_context_precision": old.final_context_precision,
    }
    if _metric_values(compatible) != expected_old:
        raise ValueError(f"legacy compatibility changed:{label}")
    previous = prior["traces"][label]["v4"]
    prior_legacy = {"evidence_recall_at_40": previous["legacy_id_only"]["recall_at_40"],
                    "evidence_rerank_recall_at_12": previous["legacy_id_only"]["rerank_recall_at_12"],
                    "final_context_precision": previous["legacy_id_only"]["final_context_precision"]}
    prior_grouped = {"evidence_recall_at_40": previous["group_aware"]["recall_at_40"],
                     "evidence_rerank_recall_at_12": previous["group_aware"]["rerank_recall_at_12"],
                     "final_context_precision": previous["group_aware"]["final_context_precision"]}
    if _metric_values(compatible) != prior_legacy or _metric_values(grouped) != prior_grouped:
        raise ValueError(f"prior V4 evaluator result changed:{label}")


def main() -> None:
    previous = json.loads(PREVIOUS.read_text(encoding="utf-8"))
    if sha256(V3) != previous["gold_v3_sha256"] or sha256(V4) != previous["gold_v4_sha256"]:
        raise ValueError("Gold V3/V4 hash differs from prior same-Trace comparison")
    cases = load_evaluation_cases(V4)
    by_id = {case.case_id: case for case in cases}
    partial_ids = {case.case_id for case in cases if case.answerability_level == "partially_answerable"}
    if partial_ids != set(UNSUPPORTED_BY_CASE):
        raise ValueError("partial-case limitations need review")
    output = {
        "gold_v3_sha256": sha256(V3), "gold_v4_sha256": sha256(V4),
        "schema_limit": "31 supporting spans lack claim labels; each is one evidence unit, not an inferred claim.",
        "traces": {},
    }
    for label, (trace_path, expected_hash) in TRACES.items():
        if sha256(trace_path) != expected_hash:
            raise ValueError(f"historical Trace hash changed:{label}")
        traces = load_evaluation_traces(trace_path)
        legacy = evaluate_traces(cases, traces)
        compatible = evaluate_evidence_metrics(cases, traces, mode="compatibility")
        grouped = evaluate_evidence_metrics(cases, traces, mode="group_aware")
        _assert_prior_metrics(label, legacy, compatible, grouped, previous)
        aware = evaluate_v4_answerability(cases, traces)
        kind_counts = Counter(result.unit.kind for case in aware.cases for result in case.units)
        partial = [
            _partial_detail(by_id[case.case_id], case)
            for case in aware.cases if case.answerability_level == "partially_answerable"
        ]
        output["traces"][label] = {
            "trace_path": str(trace_path.relative_to(ROOT)).replace("\\", "/"),
            "trace_sha256": expected_hash,
            "eligible_full_cases": aware.eligible_full_cases,
            "eligible_partial_cases": aware.eligible_partial_cases,
            "insufficient_cases": aware.insufficient_cases,
            "needs_input_cases": aware.needs_input_cases,
            "evidence_unit_count": aware.evidence_unit_count,
            "full_evidence_unit_count": aware.full_evidence_unit_count,
            "partial_evidence_unit_count": aware.partial_evidence_unit_count,
            "evidence_unit_kinds": dict(kind_counts),
            "metrics": {
                "legacy_v4": _metric_values(compatible),
                "group_aware_v4": _metric_values(grouped),
                "answerability_aware_group_aware_v4": {
                    "evidence_recall_at_40": aware.evidence_recall_at_40,
                    "evidence_rerank_recall_at_12": aware.evidence_rerank_recall_at_12,
                    "final_context_precision": aware.final_context_precision,
                },
            },
            "partial_cases": partial,
            "anomalies": [],
            "schema_warnings": [
                "Unlabeled spans are counted separately; Gold V4 cannot prove whether they share a claim.",
                "A supported span with support_level=partial is a local evidence unit, not a fully supported answer.",
            ],
        }
    OUT_JSON.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Gold V4 answerability-aware：同 Trace 三口径对照", "",
        f"- Gold V3 SHA-256：`{output['gold_v3_sha256']}`；Gold V4 candidate SHA-256：`{output['gold_v4_sha256']}`。",
        "- 两份历史 Trace 经 SHA-256 核对，只读取既有候选集、Top12 与 final context；没有重新运行 Retrieval。",
        "- 旧 legacy 与 group-aware 路径及其既有报告逐值一致；V3 兼容性由既有同 Trace 报告和相关测试保持。",
        "", "## 评分语义", "",
        "- 仅 approved 且 `answerability_level` 为 fully 或 partially 的 case 进入正样本。insufficient 和 needs_input 不进入三项 evidence 指标。`expected_outcome` 仍用于旧口径，不参与本口径 eligibility。",
        "- 每个显式 evidence_group 是一个单位；未分组且有相同 claim 标签的同 Parent spans 合成一个单位；没有 claim 标签的 supporting span 各算一个证据单位。`support_level=insufficient` 不计。无证据映射的 required_facts / forbidden_claims 不计。",
        "- 每 case 的 Evidence Recall@40 = Top40 命中单位数 / 该 case 支持单位数；Evidence Rerank Recall@12 同理。Final Context Precision = final context 中属于已满足单位的唯一 Parent 数 / final context 唯一 Parent 数。三指标再对 eligible case 等权宏平均。",
        "- 单位命中调用既有 group-aware `score_stage`：exact 按 Parent ID，duplicate/equivalent 任一可接受 Parent，complementary 必须全部 Parent。",
        "- Schema 限制：31 个 span 没有 claim 标签，无法证明它们之间的 claim 同一性；按独立证据单位回退。此数是评分代理单位数，不等于人工核定的真实 claim 数。",
        "- 逐 case 的 unsupported_claims 原文取自 Gold V4 的 required_facts / forbidden_claims，仅用于审计展示，从不参加分母。",
        "",
    ]
    for label, item in output["traces"].items():
        lines += [
            f"## {label}", "",
            f"Trace：`{item['trace_path']}`，SHA-256 `{item['trace_sha256']}`。",
            f"full={item['eligible_full_cases']}，partial={item['eligible_partial_cases']}，insufficient={item['insufficient_cases']}，needs_input={item['needs_input_cases']}；评分证据单位 {item['evidence_unit_count']}（full {item['full_evidence_unit_count']}、partial {item['partial_evidence_unit_count']}；按类型 {item['evidence_unit_kinds']}）。",
            "", "| Metric | Legacy V4 | Group-aware V4 | Answerability-aware + Group-aware V4 |",
            "| --- | ---: | ---: | ---: |",
        ]
        for key, title in (
            ("evidence_recall_at_40", "Evidence Recall@40"),
            ("evidence_rerank_recall_at_12", "Evidence Rerank Recall@12"),
            ("final_context_precision", "Final Context Precision"),
        ):
            m = item["metrics"]
            lines.append(f"| {title} | {m['legacy_v4'][key]:.10f} | {m['group_aware_v4'][key]:.10f} | {m['answerability_aware_group_aware_v4'][key]:.10f} |")
        lines += ["", "### 逐条 partial case", ""]
        for case in item["partial_cases"]:
            lines += [
                f"#### {case['case_id']}", "",
                f"- 问题：{case['question']}",
                f"- 支持单位 {case['evidence_unit_count']}；Top40 命中 {case['top40_hit_count']}，Top12 命中 {case['top12_hit_count']}，final context 命中 {case['final_context_hit_count']}。Case 分数：{case['evidence_recall_at_40']:.4f} / {case['evidence_rerank_recall_at_12']:.4f} / {case['final_context_precision']:.4f}。",
                f"- 参与的 evidence_groups：{case['participating_evidence_groups'] or '无'}。",
                "- 不支持的结论（Gold 原文，均未进入分母）：" + "；".join(case["unsupported_claims_from_gold"]) + "。",
            ]
            for unit in case["scored_units"]:
                claim = unit["supported_claim"] or "[Gold 未标注 claim；见下列 span]"
                levels = sorted({span["support_level"] for span in unit["supporting_spans"]})
                lines.append(
                    f"- `{unit['unit_id']}` {claim}；Parent `{unit['anchor_parent_id']}`；Gold support_level={levels}；Top40/Top12/Final={unit['top40_hit']}/{unit['top12_hit']}/{unit['final_context_hit']}；计入原因：{unit['included_because']}"
                )
                for span in unit["supporting_spans"]:
                    lines.append(f"  - 原文证据 `{span['parent_id']}`：{span['evidence_text']}")
            lines.append("")
    lines += [
        "## 风险与异常", "",
        "- 自动校验没有发现无证据的正样本、无支持 span 的 group、跨 Parent 同 claim 却缺少 group 关系等结构异常；这些情况会直接报错，而非猜测。",
        "- 031、005、008、045 等 case 含 claim 未标注的 span；对应评分单位是可定位证据，并非自动推断的完整 claim。",
        "- 新口径与旧口径的数值不能直接解释为 Retrieval 改善：eligibility 从 13 条变为 21 条，且 Recall 由 Parent 单位变为证据单位。",
        "- Gold V4 保持 candidate；本轮没有更改 Gold、旧 evaluator、历史 Trace 或检索链路。",
        "",
    ]
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"status": "valid", "report": str(OUT_MD), "json": str(OUT_JSON)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
