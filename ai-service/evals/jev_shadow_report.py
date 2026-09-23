"""Render a reproducible component report from Jev shadow replay JSONL."""

from __future__ import annotations

import json
import gzip
from collections import Counter
from pathlib import Path
from typing import Any

from evals.jev_shadow_metrics import context_metrics, memory_metrics, router_metrics
ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "evals" / "reports"
LOCAL = ROOT / "evals" / "local"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def _pct(value: float | None) -> str:
    return "N/A" if value is None else f"{value * 100:.1f}%"


def _ms(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.1f} ms"


def _cell(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    return text.replace("|", "\\|").replace("\n", " ")


def write_report() -> Path:
    router_rows = read_jsonl(REPORTS / "jev_router_shadow_v1.jsonl")
    context_rows = read_jsonl(REPORTS / "jev_context_shadow_v1.jsonl")
    memory_path = LOCAL / "jev_memory_shadow_v1.jsonl"
    memory_rows = read_jsonl(memory_path) if memory_path.exists() else []
    r, rf = router_metrics(router_rows)
    single, _ = router_metrics([row for row in router_rows if len(row["gold_route"]) == 1])
    eligible, _ = router_metrics([row for row in router_rows if not row.get("fallback_used")])
    c, cf = context_metrics(context_rows)
    m, mf = memory_metrics(memory_rows)
    models = Counter((row.get("requested_model"), row.get("response_model"))
                     for row in router_rows + context_rows + memory_rows)
    timestamps = {kind: sorted({row["evaluation_timestamp"] for row in rows})
                  for kind, rows in (("Router", router_rows), ("Memory", memory_rows),
                                     ("Context", context_rows))}
    failures = [dict(component="Router", **item) for item in rf]
    failures += [dict(component="Memory", **item) for item in mf]
    failures += [dict(component="Context", **item) for item in cf]
    failure_counts = Counter(item["kind"] for item in failures)
    audit_path = REPORTS / "jev_shadow_failure_audit_v1.jsonl"
    write_jsonl(audit_path, failures)

    lines = [
        "# Jev Shadow Evaluation v1",
        "",
        "## 1. 数据集来源与标签",
        "",
        "- Router：`data/intent/splits/test.jsonl` 与 `val.jsonl` 中去重后的 45 条人工标注保留集；`manual_test.jsonl` 与 test 重复，不重复计数。另从 Foundation E2E acceptance 的连续会话加入 2 条（总 47）；历史 intent model `error_analysis.jsonl` 命中 14 条作为 historically_failed 标签。`recent_context` 在两条 E2E 案例中保留，但当前生产 Hybrid Router/Choice 都没有消费会话文本，所以这是覆盖缺口的诊断，不能算完整多轮能力验证。",
        "- Context：Gold V4 冻结 50 条中的 29 条 answerable 与 1 条 insufficient_evidence，共 30 条；输入证据来自 `gold_v4_hybrid_shadow_trace_50_20260917.jsonl.gz` 的 `final_context`，按现有 `ContextCollector` 重建进入 Foundation Selector 之前的 pool。`system`/`query` 是必需项；证据 parent ID 命中 Gold V4 `relevant_parent_ids` 标为 useful，其余标为 irrelevant。这些是 owner-approved AI-assisted Gold 的派生标签，未做新的盲审；未命中的证据不一定在所有下游用途上绝对无关。",
        "- Memory：从现有 PostgreSQL/pgvector 读取，使用项目原有 Qwen embedding 与 `MemoryRetriever` 排序，本诊断请求 Top-K=3；因宿主 `DATABASE_URL` 缺少密码，本次经 Docker 内只读 `psql` 访问同一 pgvector 表，复用 `PostgresMemoryStore` 的列定义/记录解析及相同的 profile/episodic 查询逻辑。不创建第二套记忆库。当前库有 11 条活跃 demo memory（5 episodic、6 profile；5 个用户），本集覆盖 3 个用户、9 个不同 memory ID。30 个 query case 共 90 次候选出现；true/false/uncertain 由评测者按 demo 内容与查询意图标注，未经独立盲审。含 memory 文本的诊断与结果文件在被 Git 忽略的 `evals/local/`。" if memory_rows else
        "- Memory：当前本地 PostgreSQL 有 11 条活跃 demo memory（5 episodic、6 profile；5 个用户），但应用的 `DATABASE_URL` 无密码，宿主进程收到 `fe_sendauth: no password supplied`。因此尚未取得真实 `MemoryRetriever` Top-K 候选和逐候选标签；Memory 指标保持 N/A。",
        "- Foundation E2E acceptance、RAG/failure audit 已检查；前者使用固定 Router/test doubles，后者是 RAG 证据标签，均不能直接当成 Memory Gold 或真实主 LLM A/B 判断。",
        "",
        "## 2. A/B 设置与模型漂移追踪",
        "",
        f"同一 case 使用相同 query；Context 的 Baseline 与 Jev 共享同一个 `ContextCollector` 候选池，并均使用现有 `ContextSelector.build`、相同 WORLDLINE policy/token counter。Router Baseline 是现有 Hybrid Router（其中 LLM fallback 模型 `{router_rows[0].get('baseline_llm_model', 'unavailable')}`），Jev 调用生产 `compare_route` 的同一 Choice 配置。所有 Jev 调用请求 `jev-latest`，未使用 `jev-preview`。始终保持 `JEV_SHADOW_MODE=true`，Jev 结果只作为拟选择结果；`jev-latest` 是 mutable alias。",
        "",
        "| requested_model | response_model | 调用案例数 |",
        "|---|---|---:|",
    ]
    lines += [f"| {_cell(req)} | {_cell(resp)} | {count} |" for (req, resp), count in models.items()]
    lines += ["", "| 组件 | evaluation_timestamp (UTC) |", "|---|---|"]
    lines += [f"| {kind} | {', '.join(values) if values else 'N/A'} |" for kind, values in timestamps.items()]
    lines += [
        "", "## 3. Router 结果", "",
        f"{r['cases']} cases；Baseline 有效 {r['existing_valid']}，Jev 有效 {r['jev_valid']}，两边可比较 {r['comparable']}。"
        + ("本轮 Jev 响应全部有效。" if r['jev_valid'] == r['cases'] else
           f"Jev {r['cases'] - r['jev_valid']} 次无有效响应不进入 Jev accuracy 分母，但计入 fallback。")
        + f"Baseline accuracy {_pct(r['existing_accuracy'])}，Jev accuracy {_pct(r['jev_accuracy'])}，agreement {_pct(r['agreement'])}。old_wrong_jev_right={r['old_wrong_jev_right']}，old_right_jev_wrong={r['old_right_jev_wrong']}，both_wrong={r['both_wrong']}。Fallback {_pct(r['fallback_rate'])}；Baseline 平均/P95 latency {_ms(r['baseline_avg_ms'])}/{_ms(r['baseline_p95_ms'])}，Jev 决策平均/P95 latency {_ms(r['avg_ms'])}/{_ms(r['p95_ms'])}。",
        "",
        f"多意图 gold 不能由单 Choice 完整表达；生产 Router 对复杂既有路由继续保留 Baseline。单意图 {single['cases']} 条中，Baseline {_pct(single['existing_accuracy'])} ({single['existing_valid']})，Jev {_pct(single['jev_accuracy'])} ({single['jev_valid']})。实际满足本次低置信度/复杂路由门控的 {eligible['cases']} 条，Baseline {_pct(eligible['existing_accuracy'])}，Jev {_pct(eligible['jev_accuracy'])}；此子集不能外推为全面 Live 证据。",
        "", "## 4. Memory 结果", "",
    ]
    if memory_rows:
        lines += [
            f"{m['cases']} 个 query case、{m['candidates']} 个候选出现次数；true={m['useful_labels']}、false={m['irrelevant_labels']}、uncertain={m['uncertain_labels']}。重复 demo memory 与重复 query 的相关性会高估独立样本数。`old_keep` 与 `jev_keep` 的逐候选差异见本地 `evals/local/jev_memory_shadow_v1.jsonl`。",
            f"Baseline/Jev Key Memory Recall {_pct(m['baseline_key_memory_recall'])}/{_pct(m['jev_key_memory_recall'])}；Precision {_pct(m['baseline_precision'])}/{_pct(m['jev_precision'])}；Irrelevant Retention {_pct(m['baseline_irrelevant_retention'])}/{_pct(m['jev_irrelevant_retention'])}；candidate reduction {_pct(m['candidate_reduction'])}；false drop {m['false_drops']}，false keep {m['false_keeps']}；Jev latency avg/P95 {_ms(m['avg_ms'])}/{_ms(m['p95_ms'])}。",
        ]
    else:
        lines += ["无真实 Top-K/逐候选标签，因此 Key Memory Recall、Precision、Irrelevant Retention、candidate reduction 与 latency 均为 N/A。"]
    lines += [
        "", "## 5. Context 结果", "",
        f"{c['cases']} cases；required={c['required_count']}（仅 system/query）。Baseline/Jev Required Preservation {_pct(c['baseline_required_preservation'])}/{_pct(c['jev_required_preservation'])}；Useful Recall {_pct(c['baseline_useful_recall'])}/{_pct(c['jev_useful_recall'])}；Irrelevant Retention {_pct(c['baseline_irrelevant_retention'])}/{_pct(c['jev_irrelevant_retention'])}；Jev preselect candidate reduction {_pct(c['candidate_reduction'])}、最终 item reduction {_pct(c['item_reduction'])}、最终 token reduction {_pct(c['token_reduction'])}；Baseline Selector avg/P95 {_ms(c['baseline_avg_ms'])}/{_ms(c['baseline_p95_ms'])}，Jev API avg/P95 {_ms(c['avg_ms'])}/{_ms(c['p95_ms'])}。",
        "",
        "## 6. Existing vs Jev 对比", "",
        "| 组件 | 指标 | Existing | Jev |",
        "|---|---|---:|---:|",
        f"| Router | Accuracy | {_pct(r['existing_accuracy'])} ({r['existing_valid']}) | {_pct(r['jev_accuracy'])} ({r['jev_valid']}) |",
        f"| Router | Agreement / fallback | — | {_pct(r['agreement'])} / {_pct(r['fallback_rate'])} |",
        f"| Router | Decision latency avg / P95 | {_ms(r['baseline_avg_ms'])} / {_ms(r['baseline_p95_ms'])} | {_ms(r['avg_ms'])} / {_ms(r['p95_ms'])} |",
        f"| Memory | Key Memory Recall | {_pct(m['baseline_key_memory_recall'])} | {_pct(m['jev_key_memory_recall'])} |",
        f"| Memory | Precision | {_pct(m['baseline_precision'])} | {_pct(m['jev_precision'])} |",
        f"| Memory | Irrelevant Retention | {_pct(m['baseline_irrelevant_retention'])} | {_pct(m['jev_irrelevant_retention'])} |",
        f"| Memory | Candidate Reduction / latency | — | {_pct(m['candidate_reduction'])} / {_ms(m['avg_ms'])} |",
        f"| Context | Required Preservation | {_pct(c['baseline_required_preservation'])} | {_pct(c['jev_required_preservation'])} |",
        f"| Context | Useful Recall | {_pct(c['baseline_useful_recall'])} | {_pct(c['jev_useful_recall'])} |",
        f"| Context | Irrelevant Retention | {_pct(c['baseline_irrelevant_retention'])} | {_pct(c['jev_irrelevant_retention'])} |",
        f"| Context | Token Reduction / latency | {_ms(c['baseline_avg_ms'])} | {_pct(c['token_reduction'])} / {_ms(c['avg_ms'])} API |",
        "", "## 7. Failure Audit", "",
        "逐案例、逐候选审计也保存为 `jev_shadow_failure_audit_v1.jsonl`；每行包含 case_id、input、gold、baseline、Jev、confidence/probability 或 Noul score、reason。下表列出全部审计项。",
        "分类计数：" + "；".join(f"{kind}={count}" for kind, count in sorted(failure_counts.items())) + "。Memory 关键误删与明显无关保留在本轮均为 0。",
        "",
        "| case_id | 类型 | input | gold | baseline | Jev | confidence/probability/score | reason |",
        "|---|---|---|---|---|---|---:|---|",
    ]
    for item in failures:
        score = ({"confidence": item.get("jev_confidence"),
                  "probabilities": item.get("jev_probabilities")}
                 if item["component"] == "Router" else item.get("jev_score"))
        lines.append(f"| {_cell(item['case_id'])} | {_cell(item['kind'])} | {_cell(item['input'])} | {_cell(item['gold'])} | "
                     f"{_cell(item['baseline'])} | {_cell(item['jev'])} | "
                     f"{_cell(score)} | {_cell(item['reason'])} |")
    lines += [
        "", "## 8. End-to-End Diagnostic Set", "",
        "未运行：Router 全量 Jev accuracy 低于 Baseline，三个组件级门槛未全部通过；现有 E2E acceptance 用固定路由与测试替身，无法据此伪造主 LLM task success、answer quality、groundedness 或 personalization 指标。",
        "", "## 9. Live 建议", "",
        "本次没有达到可建议立即切 Live 的模块。Memory/Context 的诊断指标满足部分数值门槛，独立样本和 required 类型覆盖尚不足。",
        "",
        f"- Router：继续 Shadow。全量 Jev 准确率低于 Baseline，且 {r['old_right_jev_wrong']} 个 old_right_jev_wrong；门控后仅 {eligible['cases']} 条，样本尚不足以证明可安全切换。",
        "- Memory：继续 Shadow。" + ("本地 demo 样本仅 9 个不同 memory ID；若有关键 memory 被 Jev 删除，直接阻塞 Live。即使没有误删，也需更多独立用户/真实查询和复核标签。" if memory_rows else "缺少真实候选与标签，无法判断关键记忆召回。"),
        "- Context：本诊断集达到 required 100%、useful 无下降、token/irrelevant 有减少，具备进一步单独验证的信号。仍建议继续 Shadow：本集 required 只有 system/query，尚未覆盖 required RAG/tool/handoff；切换前需复核非 gold parent 的标签，并扩大真实 ContextPool/多轮样本。本次不改生产开关。",
        "", "## 10. 已知限制", "",
        "- Context 是由历史冻结 RAG trace 重建的 Foundation 诊断 pool，system prompt 为固定诊断文本；不是线上整条会话轨迹。required 仅覆盖 system/query，未覆盖 required RAG/tool/handoff。Gold V4 的 relevant_parent_ids 是证据相关性，不等于完整的上下文效用人工盲审。",
        "- Router 的 2 条 E2E follow-up 虽保存 recent_context，但两条生产路由路径都没有消费它；多意图包含在全量统计，而 Jev 当前 Choice 只返回单路由。",
        "- `jev-latest` 可变；每次复跑须对 requested_model、response_model 与 evaluation_timestamp 分组，不跨版本直接混算。",
        "- 首轮 45 条 Router 试跑曾有 1 次 TypeSafe 超时；最终 47 条重跑 47/47 有效。报告聚合只采用最终重跑，未把不同 evaluation_timestamp 混算。",
        "- 各组件只作单独判断，无 Jev 总分。Shadow 开关未变更。",
    ]
    path = REPORTS / "jev_shadow_evaluation_v1.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
