"""Offline H1 analysis; preserve the raw runner's advisory-probe audit flag.

No retrieval retries, model loading or parameters are changed by this module.
All metrics are reconstructed from already-recorded healthy production results.
"""

import argparse
from collections import Counter
import math
from pathlib import Path
import statistics
import sys
from unittest.mock import patch

from evals import rag_h1_retrieval_v5 as retrieval
from evals.ragas_gate0 import Gate0Guard, configure_offline


def scoring_eligibility(state, rows, requests, diagnostic, expected_ids):
    """Require complete production data, not just a special-case status string."""
    if (len(expected_ids) != 25 or [r["case_id"] for r in rows] != list(expected_ids)
            or state["completed_case_count"] != 25 or state["error_code"] is not None
            or not state["protected_unchanged"]
            or any(state.get(k) != 0 for k in ("new_deepseek_calls", "judge_calls", "secret_reads"))
            or state.get("baseline_rerun") is not False
            or any(not r["healthy"] or r["search_calls"] != 1 or r["rrf_calls"] != 1
                   or len(r["leg_results"]) != 7 or any(leg["error_code"] is not None for leg in r["leg_results"])
                   for r in rows)):
        raise retrieval.original.H1Error("h1_analysis_missing_or_unhealthy_production_data")
    root = "/collections/" + retrieval.baseline.COLLECTION
    counts = Counter((r["method"], r["path"]) for r in requests)
    if counts[("POST", root + "/points/query/batch")] != 25:
        raise retrieval.original.H1Error("h1_analysis_query_count_mismatch")
    allowed = {("GET", "/"), ("GET", root), ("POST", root + "/points/query/batch")}
    if any(operation not in allowed for operation in counts):
        raise retrieval.original.H1Error("h1_analysis_unapproved_http_operation")
    if counts[("GET", root)] != 25 or counts[("GET", "/")] < 25:
        raise retrieval.original.H1Error("h1_analysis_missing_binding_metadata")
    if not state["guard_denied"] and state["status"] == "complete":
        return {"valid_for_scoring": True, "runner_status": "complete", "audit_issue": None}
    # The collector deliberately flags any attempted forbidden socket as an
    # incomplete run. Keep that flag. Separately establish whether the scored
    # data itself is complete and the exception is the advisory client probe.
    if (state["status"] != "incomplete" or state["guard_denied"] != ["socket.getaddrinfo"] * 25
            or counts != Counter({("GET", "/"): 50, ("GET", root): 25, ("POST", root + "/points/query/batch"): 25})
            or diagnostic.get("guard_denied") != ["socket.getaddrinfo"]
            or diagnostic.get("http_attempts") != [{"method": "GET", "path": "/"}]
            or diagnostic.get("query_api_calls") != 0
            or diagnostic.get("error_type") != "Gate0ForbiddenOperation"
            or len(diagnostic.get("dns_attempts", [])) != 1
            or diagnostic["dns_attempts"][0]["host"] != "127.0.0.1"
            or diagnostic["dns_attempts"][0]["port"] != 7897
            or not any("http_proxy.py" in f["file"] for f in diagnostic["dns_attempts"][0]["stack"])):
        raise retrieval.original.H1Error("h1_analysis_unclassified_runtime_audit_issue")
    return {"valid_for_scoring": True, "runner_status": "incomplete", "runner_status_preserved": True,
            "audit_issue": "Advisory qdrant-client version probe attempted the environment proxy 127.0.0.1:7897 and was blocked before DNS resolution/connection. Mandatory production binding validation and all seven-leg searches succeeded.",
            "classification_basis": "Inference from installed version-probe source, exactly one extra root request per client, 25 denied DNS events, and isolated reproduction. Original live guard saved event names, not per-event destinations.",
            "additional_diagnostic_metadata_attempts": 1, "additional_diagnostic_query_calls": 0,
            "unresolved_runtime_audit_flag_for_West_review": True}


def latency_stats(values):
    values = sorted(values)
    return {"n": len(values), "mean_ms": math.fsum(values) / len(values),
            "median_ms": statistics.median(values), "min_ms": values[0], "max_ms": values[-1],
            "p95_nearest_rank_ms": values[math.ceil(0.95 * len(values)) - 1]}


def leg_summary(rows):
    ids = [leg["leg_id"] for leg in rows[0]["leg_results"]]
    summaries = []
    for leg_id in ids:
        legs = [next(item for item in row["leg_results"] if item["leg_id"] == leg_id) for row in rows]
        counts = [leg["hit_count"] for leg in legs]
        summaries.append({"leg_id": leg_id, "queries": len(legs), "channel": legs[0]["channel"],
                          "variant_index": legs[0]["variant_index"], "hit_count_total": sum(counts),
                          "hit_count_mean": math.fsum(counts) / len(counts), "hit_count_min": min(counts), "hit_count_max": max(counts),
                          "shared_search_latency": latency_stats([item["shared_search_wall_ms"] for item in legs]),
                          "independent_leg_latency_ms": None, "latency_scope": "shared seven-leg search, do not sum across legs"})
    return summaries


def query_understanding_summary(rows):
    inputs = [r["query_understanding"] for r in rows]
    statuses = Counter(e["status"] for row in inputs for e in row["entities"])
    return {"cases": len(inputs), "completion_triggered": sum(r["completion_triggered"] for r in inputs),
            "completion_changed": sum(r["completion_changed"] for r in inputs),
            "cases_with_entity_output": sum(bool(r["entities"]) for r in inputs),
            "entity_status_counts": dict(statuses), "entities_used_as_filters_or_query_rewrites": False}


def stage_localization(row):
    relevant = set(row["relevant_parent_ids"])
    raw = {hit["parent_id"] for leg in row["leg_results"] for hit in leg["hits"]}
    stages = {key: {r["parent_id"] for r in row[key]}
              for key in ("rrf_top40", "selection_input_rrf", "selection", "rerank_top12")}
    classes = {"not_in_any_leg": [], "raw_retrieved_below_rrf80": [], "selection_dropped": [],
               "rerank_dropped": [], "rerank_top12_hit": []}
    for parent in sorted(relevant):
        key = ("not_in_any_leg" if parent not in raw else
               "raw_retrieved_below_rrf80" if parent not in stages["selection_input_rrf"] else
               "selection_dropped" if parent not in stages["selection"] else
               "rerank_dropped" if parent not in stages["rerank_top12"] else "rerank_top12_hit")
        classes[key].append(parent)
    return {"case_id": row["case_id"], "question_group": row["question_group"],
            "gold_parent_count": len(relevant), "exclusive_terminal_classes": classes,
            "raw_retrieved_below_rrf40": sorted((raw & relevant) - stages["rrf_top40"]),
            "rrf40_missed_but_rerank12_hit": sorted((stages["rerank_top12"] & relevant) - stages["rrf_top40"])}


def render_report(out, rows, comparison, changes, legs, eligibility):
    original = retrieval.original
    control, experiment = comparison["baseline"], comparison["experiment"]
    conclusion = comparison["hypothesis"]["conclusion"]
    lines = ["# H1：冻结 25 题七路检索对照实验", "",
             f"**预登记假设：{conclusion}。**25/25 实际生产检索均 healthy，175 条 leg、25 次 RRF、25 次原问题 rerank；没有新增 DeepSeek 或 judge 调用。",
             "", "**审计限制必须保留：原始 run_manifest.status=incomplete。**记录了25次DNS拦截；结合请求计数、代码路径与独立复现，归因为额外兼容性版本探针尝试环境代理127.0.0.1:7897。正式后端trust_env=false，逐题绑定验证及batch查询均成功。没有修改原始状态、关闭兼容性检查或放宽guard。指标从完整25题记录离线重建，不能将其表述为‘运行审计完全无异常’。",
             "", "此归因基于已安装客户端源代码、每个 client 一个额外 GET 根请求的计数及独立复现；原始 guard 只保存事件名，没有逐事件目标地址。隔离诊断额外尝试 1 次版本元数据 GET，被拦截，query API=0。详见 metadata_probe_diagnostic.json / analysis_manifest.json，留 West 审阅。",
             "", "## 同题分组对照", "",
             "原 30 → 已批准冻结 25（full 17 / partial 8），排除 005/008/009/045/054，未换题、未按检索分数排除。baseline 只读取 Step 2 旧产物，对相同 25 题离线重算，不重跑。",
             "", "| 分组 | n | R@40 baseline→H1 | R@12 baseline→H1 | P@12 baseline→H1 | MRR baseline→H1 |",
             "|---|---:|---:|---:|---:|---:|"]
    groups = [("全部25", control["all_scored"], experiment["all_scored"])]
    groups += [(label, control["by_answerability"][label], experiment["by_answerability"][label]) for label in ("full", "partial")]
    groups += [(label, control["by_question_group"][label], experiment["by_question_group"][label]) for label in control["by_question_group"]]
    for label, before, after in groups:
        cells = [f"{before[k]:.6f}→{after[k]:.6f}" for k in retrieval.baseline.METRICS]
        lines.append(f"| {label} | {before['case_count']} | " + " | ".join(cells) + " |")
    lines += ["", "判定口径事前固定：timeline recall@40 ≥0.6 且 artist_overview 高于同题 baseline，两项都满足才成立；恰一项满足标部分成立。未因结果移动阈值。",
              "", f"timeline={experiment['by_question_group']['timeline']['recall_at_40']:.9f}，阈值 0.6；artist_overview={experiment['by_question_group']['artist_overview']['recall_at_40']:.9f}，baseline={control['by_question_group']['artist_overview']['recall_at_40']:.9f}。",
              "", "@12 可以高于 @40：selection 实际接收融合前80，而 recall@40 只统计前40。保留这项 baseline 口径，不把 overfetch 收益算成前40命中。",
              "", "## 25 题逐题对照", "", "箭头为 baseline→实验；每题原始记录见 records/ 下对应 JSON。", "",
              "| case | 题型 | R@40 | R@12 | P@12 | MRR | 新增/丢失标准Parent@40 |", "|---|---|---:|---:|---:|---:|---:|"]
    for item in changes:
        cells = [f"{item['baseline'][k]:.6f}→{item['experiment'][k]:.6f}" for k in retrieval.baseline.METRICS]
        delta = item["parent_changes"]["rrf_top40"]
        case_id = item["case_id"]
        lines.append(f"| [{case_id}](records/{case_id}.json) | {item['question_group']} | " + " | ".join(cells)
                     + f" | +{len(delta['gained_relevant_parents'])}/−{len(delta['lost_relevant_parents'])} |")
    understanding = query_understanding_summary(rows)
    gained_cases = [item["case_id"] for item in changes if item["delta"]["recall_at_40"] > 0]
    lost_cases = [item["case_id"] for item in changes if item["delta"]["recall_at_40"] < 0]
    localizations = [stage_localization(row) for row in rows]
    lines += ["", "## 涨跌归因与环节定位", "",
              f"R@40 真正上涨的题为 {gained_cases}，下降题为 {lost_cases}。artist_overview 的召回收益没有转化为 top12 recall 收益，且该组 MRR 下降；timeline 的 R@40 增量集中在040，039仍为0，未达到事前预期。",
              "以下为候选来源归因：列出贡献该标准 Parent 的子查询/HyDE、原始 leg rank、进入 RRF 前40的 Child。没有单 leg 消融，不能将打包收益拆成各组件的独立因果功劳。只定位环节，不给调参方案。",
              "", f"指代补全：25 题触发 {understanding['completion_triggered']}，实际改字 {understanding['completion_changed']}，是no-op。实体解析有实际产物：{understanding['cases_with_entity_output']}/25题非空，状态计数{understanding['entity_status_counts']}；其结果只记录，没有回写查询或添加实体过滤，不能单独分配检索增益。逐题类型和置信度见inputs.json / records/*.json。",
              "", "先区分原始leg未召回与RRF截断，再区分selection及rerank损失。下表统计标准 Parent–题对，不是跨题去重数量，也不替代上表宏平均。", "",
              "| 题型 | Gold Parent–题对 | 七路均未召回 | 七路捞回但RRF80外 | selection丢失 | rerank丢失 | top12命中 |",
              "|---|---:|---:|---:|---:|---:|---:|"]
    for group in sorted({item["question_group"] for item in localizations}):
        subset = [item for item in localizations if item["question_group"] == group]
        names = ("not_in_any_leg", "raw_retrieved_below_rrf80", "selection_dropped", "rerank_dropped", "rerank_top12_hit")
        counts = [sum(len(item["exclusive_terminal_classes"][name]) for item in subset) for name in names]
        lines.append(f"| {group} | {sum(item['gold_parent_count'] for item in subset)} | " + " | ".join(str(n) for n in counts) + " |")
    row_by_id = {r["case_id"]: r for r in rows}
    for item in changes:
        if any(item["delta"][k] != 0 for k in retrieval.baseline.METRICS) or item["case_id"] in original.FOCUS_IDS:
            row = row_by_id[item["case_id"]]
            lines += ["", f"### {item['case_id']}：{item['question']}", "",
                      f"ΔR@40={item['delta']['recall_at_40']:+.6f}；ΔR@12={item['delta']['recall_at_12']:+.6f}；ΔMRR={item['delta']['mrr']:+.6f}。"]
            for attribution in item["gained_gold_parent_attribution"]:
                parent = attribution["parent_id"]
                sample = attribution["rrf_top40_children"][0]
                lines += ["", f"新增标准 Parent `{parent}`：{sample.get('source_title') or ''} / {sample.get('breadcrumb') or ''}", ""]
                for leg in attribution["retrieving_legs"]:
                    winning = leg["rrf_top40_contributing_children"]
                    if winning:
                        ranks = ", ".join(f"{h['child_id']}@{h['rank']}" for h in winning)
                        lines.append(f"- {leg['leg_id']}：`{leg['text']}`；贡献 Child/leg排名：{ranks}。")
                selected = parent in {r["parent_id"] for r in row["selection"]}
                reranked = parent in {r["parent_id"] for r in row["rerank_top12"]}
                lines += ["", f"该 Parent 后续：进入 selection={selected}；进入 rerank top12={reranked}。"]
            localized = stage_localization(row)
            classes = localized["exclusive_terminal_classes"]
            lines += ["", "实验最终未命中标准 Parent 的环节清单：", "",
                      f"- 七路原始候选均未召回：{classes['not_in_any_leg']}",
                      f"- 七路已捞回但融合排在80以后：{classes['raw_retrieved_below_rrf80']}",
                      f"- 前80已含、selection丢失：{classes['selection_dropped']}",
                      f"- selection已含、rerank top12丢失：{classes['rerank_dropped']}",
                      f"- 位于41–80被overfetch捞回：{item['experiment_stage_diagnosis']['overfetch_recovered_beyond40']}"]
    lines += ["", "## 各 leg 返回量与延迟", "",
              "7 路沿用生产 batch API；延迟是同一个七路 search 的共享 wall time，不代表各 leg 独立耗时，不相加。逐题真实数量、原始命中和共享耗时在 cases.jsonl 的 leg_results。",
              "", "| leg | n | 返回总量 | 每题平均 | 最小–最大 | 共享search平均ms | 共享search中位ms |", "|---|---:|---:|---:|---:|---:|---:|"]
    for leg in legs:
        timing = leg["shared_search_latency"]
        lines.append(f"| {leg['leg_id']} | {leg['queries']} | {leg['hit_count_total']} | {leg['hit_count_mean']:.2f} | {leg['hit_count_min']}–{leg['hit_count_max']} | {timing['mean_ms']:.3f} | {timing['median_ms']:.3f} |")
    timing = latency_stats([r["case_wall_ms"] for r in rows])
    lines += ["", f"每题端到端（不含模型冷加载）平均 {timing['mean_ms']:.3f}ms、中位 {timing['median_ms']:.3f}ms，p95 nearest-rank {timing['p95_nearest_rank_ms']:.3f}ms。硬件与桌面负载不等同历史 baseline，不将这些时间当严格性能增益。",
              "", "## 口径与成本", "",
              "使用正式 RagSource.collect；只在评测作用域替换 plan 和已批七路 builder。3 子查询×dense/BM25 + HyDE dense，7 路权重全 1；RRF k=60；candidate40、overfetch2，前80进入原 selection；EXPLORATORY、空过滤、doc cap4/source cap5；原问题一次 Qwen rerank→12。BM25 k1=1.2/b=.75、dense ef128/5s 全未改。软 cap 的原有 overflow refill 原样保留。",
              "", "R@40=融合前40个Child命中的唯一标准Parent数/GoldParent数；R@12、P@12 在rerank Child前12后按Parent去重，P分母仍12；MRR保持原Child排名首命中倒数。所有结果宏平均，复用Step2 score_case，不引入LLM判断。",
              "", "检索阶段新增 DeepSeek=0、tokens=0、judge=0。此前查询理解全流程累计60 HTTP /13,359 tokens（含失败与排除题），不重新生成任何子查询或HyDE；该成本在冻结报告中实报。",
              "", "Qdrant 集合只读：主运行 25 个 query batch（175 leg）、25 次 collection metadata、50 次根GET尝试（含25个被DNS guard拦截的兼容性探针）。诊断另1次根GET尝试也被拦截，未增加query。未写集合、不改Gold V4/V5、projection或冻结查询；不调用planner、音乐目录、WorldLine检索/建图/分支、聊天路由或judge。",
              "", "## 环境与审计限制", "",
              "前两次启动在查询前因WinError1455退出（累计0查询），现场均保留。West明确授权后只重启本会话Explorer，提交内存从约1.7GiB增至37GiB；随后本次25题真实检索完成。未改Windows分页配置、模型revision/device/dtype/batch/thread参数。授权及资源前后对照在 ../attempt-02/explorer_restart_authorization_and_result.json。",
              "", f"scoring eligibility：{eligibility}",
              "", "原始运行incomplete审计标记不回写；本报告只证明25题检索数据完整且可离线评分。代理探针归因有独立复现，但原始25次拦截只记事件名，留作West审阅限制。模型均为既定revision、同一RTX4070 Laptop GPU，配置/生产代码/frozen hash前后未变。",
              "", "离线重放重建全部175leg的RRF、前40/前80顺序与分数、selection Child/Parent/匹配Child集合、rerank排名分数基本一致性、全部指标与归因；所有Parent均在projection的413个内。未重新跑模型或检索。",
              "", "代码/单测变更仅evals与相应tests。相关测试、Ruff及离线核验结果见validation.json / verification.json；完整机器记录cases.jsonl，逐题便览records/*.json，汇总metrics.json，逐题对照comparisons.json。",
              "", "**停止，等West审阅。不调参、不push、不合入main、不进H2。**"]
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def analyze(out):
    with patch.object(retrieval, "OUTPUT", out):
        _, cases, controls, _, _, _, _ = retrieval.load_prepared()
        rows = retrieval.baseline.read_jsonl(out / "cases.jsonl")
        state = retrieval.original.read_json(out / "run_manifest.json")
        requests = retrieval.original.read_json(out / "qdrant_requests.json")
        diagnostic = retrieval.original.read_json(out / "metadata_probe_diagnostic.json") if (out / "metadata_probe_diagnostic.json").exists() else {}
        eligibility = scoring_eligibility(state, rows, requests, diagnostic, [c["case_id"] for c in cases])
        comparison = retrieval.summarize_comparison(rows, controls)
        changes = [retrieval.compare_case(r, c) for r, c in zip(rows, controls, strict=True)]
        original = retrieval.original
        original.write_json(out / "metrics.json", comparison)
        original.write_json(out / "comparisons.json", changes)
        verification = retrieval.verify()
        original.write_json(out / "verification.json", verification)
        original.write_json(out / "analysis_manifest.json", {**eligibility, "created_at": original.now(),
                            "raw_run_manifest_sha256": original.sha256(out / "run_manifest.json"),
                            "metrics_source": "offline reconstruction of 25 healthy saved production cases, no retrieval rerun",
                            "analyzer_sha256": original.sha256(Path(__file__)), "query_calls_added": 0, "judge_calls": 0})
        legs = leg_summary(rows)
        original.write_json(out / "leg_summary.json", legs)
        original.write_json(out / "query_understanding_summary.json", query_understanding_summary(rows))
        original.write_json(out / "stage_localization.json", [stage_localization(row) for row in rows])
        (out / "records").mkdir(exist_ok=True)
        for row, change in zip(rows, changes, strict=True):
            record = {k: row[k] for k in ("case_id", "question", "answerability", "question_group", "query_understanding", "rrf_top40", "selection_input_rrf", "selection", "rerank_top12", "stage_hits", "metrics")}
            record.update(comparison=change, stage_localization=stage_localization(row), legs=[{k: v for k, v in leg.items() if k != "hits"} for leg in row["leg_results"]])
            original.write_json(out / "records" / (row["case_id"] + ".json"), record)
        render_report(out, rows, comparison, changes, legs, eligibility)
        return comparison, eligibility


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, default=retrieval.OUTPUT / "attempt-03")
    args = parser.parse_args()
    out = args.run_dir.resolve()
    if retrieval.OUTPUT.resolve() not in out.parents:
        raise retrieval.original.H1Error("h1_analysis_output_outside_experiment")
    configure_offline()
    guard = Gate0Guard()
    sys.addaudithook(guard.audit)
    comparison, eligibility = analyze(out)
    if guard.denied:
        raise retrieval.original.H1Error("h1_analysis_network_or_secret_operation")
    print({"hypothesis": comparison["hypothesis"], "valid_for_scoring": eligibility["valid_for_scoring"], "raw_runner_status": eligibility["runner_status"]})


if __name__ == "__main__":
    main()
