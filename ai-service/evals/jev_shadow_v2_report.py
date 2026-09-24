"""Render the Jev v2 component comparison and complete case-level audit."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from evals.jev_shadow_v1 import DATASETS, REPORTS, read_jsonl, write_jsonl
from evals.jev_shadow_v2_metrics import context_v2_metrics, memory_v2_metrics, router_v2_metrics

ROUTER_RESULTS = REPORTS / "router_eval_v2_results.jsonl"
MEMORY_RESULTS = REPORTS / "memory_eval_v2_results.jsonl"
CONTEXT_RESULTS = REPORTS / "context_eval_v2_results.jsonl"
AUDIT = REPORTS / "jev_shadow_failure_audit_v2.jsonl"
REPORT = REPORTS / "jev_shadow_evaluation_v2.md"


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _ms(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f} ms"


def _latency(value: dict[str, float | None]) -> str:
    return f"{_ms(value['avg_ms'])} / {_ms(value['p95_ms'])}"


def _compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _audit_table(rows: list[dict[str, Any]], kinds: set[str]) -> list[str]:
    relevant = [row for row in rows if row["kind"] in kinds]
    if not relevant:
        return ["无。"]
    lines = ["| Case | 类别 | 输入 | Gold | Baseline | Jev | Confidence / Probability | 原因 |",
             "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for row in relevant:
        confidence = row.get("jev_confidence")
        probability = row.get("jev_probability", row.get("jev_probabilities"))
        score = confidence if confidence is not None else probability
        cells = (row["case_id"], row["kind"], row["input"], _compact(row["gold"]),
                 _compact(row["baseline_result"]), _compact(row["jev_result"]),
                 _compact(score), row["reason"])
        lines.append("| " + " | ".join(str(cell).replace("|", "\\|").replace("\n", " ")
                                       for cell in cells) + " |")
    return lines


def _model_table(groups: dict[str, list[dict[str, Any]]]) -> list[str]:
    lines = ["| 组件 | requested_model | response_model | evaluation_timestamp (UTC) | Calls / cases |",
             "| --- | --- | --- | --- | ---: |"]
    for name, rows in groups.items():
        requests = sorted({str(row.get("requested_model")) for row in rows})
        responses = sorted({str(row.get("response_model")) for row in rows})
        timestamps = sorted({str(row.get("evaluation_timestamp")) for row in rows})
        lines.append(f"| {name} | {', '.join(requests)} | {', '.join(responses)} | "
                     f"{', '.join(timestamps)} | {len(rows)} |")
    return lines


def write_report_v2() -> Path:
    router_rows = read_jsonl(ROUTER_RESULTS)
    memory_rows = read_jsonl(MEMORY_RESULTS)
    context_rows = read_jsonl(CONTEXT_RESULTS)
    if (len(router_rows), len(memory_rows), len(context_rows)) != (47, 130, 30):
        raise ValueError("v2 report requires all 47 router, 130 memory and 30 context replays")
    if not all(row.get("requested_model") == "jev-latest"
               for row in router_rows + memory_rows + context_rows):
        raise ValueError("mixed requested model in v2 result set")
    router, router_failures = router_v2_metrics(router_rows)
    memory, memory_failures = memory_v2_metrics(memory_rows)
    context, context_failures = context_v2_metrics(context_rows)
    failures = router_failures + memory_failures + context_failures
    write_jsonl(AUDIT, failures)

    memory_corpus = read_jsonl(DATASETS / "synthetic_memories_v2.jsonl")
    categories = Counter(row["metadata"]["category"] for row in memory_corpus)
    source_counts = Counter(item["source_type"] for row in read_jsonl(DATASETS / "context_eval_v2.jsonl")
                            for item in row["items"])
    failure_counts = Counter(row["kind"] for row in failures)
    default = memory["default"]
    probe = memory["episodic_only_probe"]

    lines = [
        "# Jev Shadow Evaluation v2",
        "",
        "## 1. Evaluation Design 与资产盘点",
        "",
        "- 复用资产：Gold V4 frozen 50 条、Router v1 47 条（31 单意图、14 多意图、2 依赖上下文的追问）、已有 Retrieval/RAG trace、Foundation E2E acceptance、v1 failure audit 与 `run_jev_eval.py`。Router 中的历史失败、边界和容易混淆表述继续保留。",
        "- 旧标签缺口：Router 的多 route gold 曾与生产 single Choice 混算；Memory v1 只有 9 个不同 demo memory ID，缺真实用户历史；Context v1 的 useful 主要由 Gold V4 `relevant_parent_ids` 推导，只表示 RAG evidence 相关性，未覆盖任务所需的 Memory、History、Tool、Handoff。Gold V4 是已审批的 AI 辅助标签，不等于本次各组件的独立人工盲审。",
        "- 本次新增：Router 按单意图 / 多意图 / 追问分析分组；Memory 使用 100 条符合 `MemoryRecord` 的合成记录与 130 次会话问题回放；Context 重新构造 30 个任务场景并逐项给出 required/useful/irrelevant 与理由。新增标签是显式规则设计，尚未经双人盲审。",
        f"- Memory 分类：{_compact(dict(categories))}；默认 Top-8 查询 100 条，另对 30 条 episodic 目标做 `memory_type=episodic` 诊断探针。合成记录仅临时写入本地 pgvector 表，采样后按专用 user_id 和 ID 前缀清理；结果 JSONL 固定候选集，Jev 回放读取同一候选集。",
        f"- Context 来源分布：{_compact(dict(source_counts))}；Gold 标签数 { _compact(context['label_counts']) }。任务相关性独立于 RAG relevance 标注。",
        "- 所有 Jev 调用使用官方 TypeSafe SDK 与 `jev-latest`；Baseline/Jev 使用同一 query 和候选，Context 两路使用同一 `ContextCollector` pool、`ContextSelector` policy 和 token counter。`JEV_SHADOW_MODE=true`，输出仅评测，未修改生产开关。Router Baseline 的本地模型及必要时的 LLM fallback 与 Jev 是不同模型，延迟是组件决策耗时，不能解释为只替换模型的因果差。",
        "",
        "### 模型血缘",
        "",
        *_model_table({"Router": router_rows, "Memory": memory_rows, "Context": context_rows}),
        "",
        "`jev-latest` 是 mutable alias。每个 case 的 requested_model、API response_model 和 evaluation_timestamp 也保存在结果 JSONL；跨时间复评必须按实际 response_model 分组。",
        "",
        "## 2. Router Results",
        "",
        f"单意图 31 条：Existing { _pct(router['existing_accuracy']) } (20/31)，Jev { _pct(router['jev_accuracy']) } (25/31)，agreement { _pct(router['agreement']) }；old_wrong_jev_right={router['old_wrong_jev_right']}、old_right_jev_wrong={router['old_right_jev_wrong']}、both_wrong={router['both_wrong']}。Jev 平均 confidence={router['mean_jev_confidence']:.3f}。Baseline 平均/P95 {_latency(router['existing_latency'])}；Jev 决策平均/P95 {_latency(router['jev_latency'])}。有效 Jev 响应 {router['single_jev_valid']}/{router['single_cases']}。",
        f"多意图 14 条单独分析：现有 Router 识别多个意图 {router['existing_multi_detected']}/14；评测用四个独立 Noul 问题（阈值 0.5）识别多个意图 {router['jev_multi_detected']}/14，但完整 route 集仅 {router['jev_multi_exact']}/14，漏掉 {router['jev_multi_missing_intents']} 个意图、额外添加 {router['jev_multi_extra_intents']} 个意图；Jev 平均/P95 {_latency(router['multi_latency'])}。这不是生产 Router 的 single Choice 能力，也未计入单意图 accuracy。另有 {router['followup_analysis_cases']} 条追问保存 recent_context；现有两条路由路径均未消费，故不计入准确率。",
        "",
        "## 3. Memory Results",
        "",
        "| 指标 | Baseline pgvector Top-8 | Jev Filter（同一 Top-8） |",
        "| --- | ---: | ---: |",
        f"| 默认 100 条 Key Memory Recall（完整链路） | {_pct(default['retrieval_key_recall'])} ({default['retrieved_gold']}/{default['gold_targets']}) | {_pct(default['end_to_end_key_recall'])} ({default['selected_gold']}/{default['gold_targets']}) |",
        f"| 已检索到目标时的 Filter Recall | 100% | {_pct(default['filter_key_recall'])} |",
        f"| Precision（candidate occurrence） | {_pct(default['baseline_precision'])} | {_pct(default['jev_precision'])} |",
        f"| 无关项保留 / False Keep | 100% | {_pct(default['false_keep_rate'])} |",
        f"| Candidate reduction | 0% | {_pct(default['candidate_reduction'])} |",
        f"| 平均 / P95 latency | {_latency(default['baseline_retrieval_latency'])} | Filter 额外 {_latency(default['jev_filter_latency'])} |",
        "",
        f"默认模式包含 {default['candidate_occurrences']} 次候选出现，其中 episodic {default['episodic_candidate_occurrences']} 次。Jev False Drop Rate（仅已检索到 gold）{_pct(default['false_drop_rate'])}；fallback {_pct(default['fallback_rate'])}。完整链路召回将检索遗漏也计入，Filter Recall 仅衡量 Jev 是否删掉已命中的关键 Memory，两者不能混淆。",
        f"Episodic-only 探针 {probe['cases']} 条：Baseline 检索目标 {probe['retrieved_gold']}/{probe['gold_targets']} ({_pct(probe['retrieval_key_recall'])})，Jev 之后 {probe['selected_gold']}/{probe['gold_targets']} ({_pct(probe['end_to_end_key_recall'])})；条件 Filter Recall {_pct(probe['filter_key_recall'])}，Jev Precision {_pct(probe['jev_precision'])}，Candidate reduction {_pct(probe['candidate_reduction'])}，额外 Filter latency {_latency(probe['jev_filter_latency'])}。此探针显式指定 `memory_type=episodic`，不能合并进默认 100 条主指标。",
        "",
        "## 4. Context Results",
        "",
        "| 指标 | Existing Select | Jev Select（同 pool 后接 Existing Selector） |",
        "| --- | ---: | ---: |",
        f"| Required Preservation | {_pct(context['baseline_required_preservation'])} | {_pct(context['jev_required_preservation'])} |",
        f"| Useful Context Recall | {_pct(context['baseline_useful_recall'])} | {_pct(context['jev_useful_recall'])} |",
        f"| Irrelevant Context Retention | {_pct(context['baseline_irrelevant_retention'])} | {_pct(context['jev_irrelevant_retention'])} |",
        f"| Context item reduction | 0% | {_pct(context['context_reduction'])} |",
        f"| Token reduction | 0% | {_pct(context['token_reduction'])} |",
        f"| 平均 / P95 latency | {_latency(context['baseline_latency'])} | Filter 额外 {_latency(context['jev_filter_latency'])} |",
        "",
        f"30 条任务场景共 {sum(context['label_counts'].values())} 个 item；required false drop={context['required_false_drops']}，useful false drop={context['useful_false_drops']}，fallback={_pct(context['fallback_rate'])}。Context 的 token reduction 来自生产 token counter 在相同 policy 下的最终包计数。",
        "",
        "## 5. Failure Audit",
        "",
        f"完整逐项清单：`jev_shadow_failure_audit_v2.jsonl`，共 {len(failures)} 项；分类计数 {_compact(dict(failure_counts))}。每项包含 case_id、input、gold、baseline result、Jev result、confidence/probability 和原因。",
        "",
        "### Router 单意图差异及多意图错误",
        "",
        *_audit_table(router_failures, {"old_wrong_jev_right", "old_right_jev_wrong", "both_wrong", "multi_intent_mismatch"}),
        "",
        "### Memory 关键误删",
        "",
        *_audit_table(memory_failures, {"jev_false_drop"}),
        "",
        "Memory false keep 逐候选记录在 audit JSONL。合成查询刻意窄化为单个目标事实；其他候选按本轮规则标为 false，此标签不能推断其在真实对话中永远无用。",
        "",
        "### Context required / useful 误删与大量无关保留",
        "",
        *_audit_table(context_failures, {"required_false_drop", "useful_false_drop", "many_irrelevant_kept"}),
        "",
        "Context 的所有 irrelevant false keep 逐项记录在 audit JSONL。",
        "",
        "## 6. Baseline vs Jev Summary",
        "",
        "| 组件 | Baseline | Jev | 判读 |",
        "| --- | --- | --- | --- |",
        f"| Router 单意图 | accuracy {_pct(router['existing_accuracy'])}；latency {_latency(router['existing_latency'])} | accuracy {_pct(router['jev_accuracy'])}；agreement {_pct(router['agreement'])}；latency {_latency(router['jev_latency'])} | 5 个 old-right/Jev-wrong；多意图另评，精确集 {router['jev_multi_exact']}/14 |",
        f"| Memory 默认 | key recall {_pct(default['retrieval_key_recall'])}；precision {_pct(default['baseline_precision'])}；无关保留 100% | key recall {_pct(default['end_to_end_key_recall'])}；precision {_pct(default['jev_precision'])}；无关保留 {_pct(default['false_keep_rate'])}；候选减少 {_pct(default['candidate_reduction'])}；额外 latency {_latency(default['jev_filter_latency'])} | 关键目标误删见 audit；检索召回和过滤召回分别判断 |",
        f"| Context | required {_pct(context['baseline_required_preservation'])}；useful {_pct(context['baseline_useful_recall'])}；无关保留 {_pct(context['baseline_irrelevant_retention'])} | required {_pct(context['jev_required_preservation'])}；useful {_pct(context['jev_useful_recall'])}；无关保留 {_pct(context['jev_irrelevant_retention'])}；token 减少 {_pct(context['token_reduction'])}；额外 latency {_latency(context['jev_filter_latency'])} | required 满足硬约束，但 useful 丢失 9 项 |",
        "",
        "三个组件没有合成总分。",
        "",
        "## 7. E2E Diagnostic 与 Live Decision Recommendation",
        "",
        "未执行完整 E2E A/B：现有 Foundation E2E acceptance 依赖固定路由与测试替身，Gold V4 主要标注 RAG 证据，缺少针对相同主 LLM、RAG、tools、输入下最终 task success、groundedness、personalization 的可靠成对 gold/judge。Router/Memory/Context 的组件差异与 token/latency 已按各自同输入单独记录，不伪造端到端质量分。",
        "- Router：继续 Shadow。单意图 Jev accuracy 高于 Baseline，但 5 个原本正确的 case 退化；多意图 Noul 分析仅 2/14 精确集，尚未成为生产路由合同。先复核退化 case 和 multi gold，再考虑仅单意图门控 Live。",
        "- Memory：继续 Shadow。默认 100 条已检索目标未被 Jev 删除，但 episodic-only 探针有 1 项关键误删；加上默认检索仅命中 44/100，尚不足以满足完整链路 Key Memory Recall 门槛。",
        "- Context：继续 Shadow。Required Preservation=100%，但 Useful Recall 从 100% 降到 87.5%，存在 9 项 useful false drop；须解决或接受这些具体损失后再评估 Live。",
        "- `JEV_SHADOW_MODE` 保持 true，未修改生产路径。",
        "",
        "## 8. 已知限制",
        "",
        "- Memory 使用合成用户与规则标签，短会话回放不是长期真实用户历史；默认检索的 profile/episodic 比例受现有优先策略影响。重复候选按 occurrence 统计 Precision/False Keep。",
        "- Memory 数据集保存 recent_context 以说明合成事实的会话来源；现有 `MemoryRetriever` / Jev Filter 接口只消费当前 query 与已检索候选，故本轮不能测量多轮上下文推理。",
        "- Context pool 是经生产 Collector 构造的诊断输入，不是线上完整轨迹；required/useful/irrelevant 来源于任务 rubric，未经双人独立复核。Baseline 的全保留是本诊断 pool 和预算条件下的结果。",
        "- Router 的 2 条 follow-up recent_context 两路都未消费，不能据此判断多轮路由质量；Noul 阈值 0.5 未调优，不等于已实现的生产多意图 Router。",
        "- `jev-latest` 可变；本报告的 API 实际版本和时间见模型血缘表，不能与别的版本或时间直接混合。",
        "",
    ]
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    return REPORT
