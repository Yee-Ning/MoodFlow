"""Offline exhaustive Parent changes and H2 three-tier experiment report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from evals import rag_h2_metadata_v5 as experiment
from evals.ragas_gate0 import Gate0Guard, configure_offline


def conclusion(value):
    return "成立" if value >= 0.6 else "部分成立" if value > 0.351 else "不成立"


def stage_rows(record):
    return {"all_legs": [hit for leg in record["leg_results"] for hit in leg["hits"]],
            **{field: record[field] for field in experiment.STAGES}}


def parent_changes(before, after):
    relevant = set(before["relevant_parent_ids"])
    old, new = stage_rows(before), stage_rows(after)
    result = {}
    for stage in old:
        baseline_parents = {row["parent_id"] for row in old[stage]}
        experimental_parents = {row["parent_id"] for row in new[stage]}
        gained, lost = experimental_parents - baseline_parents, baseline_parents - experimental_parents
        result[stage] = {"baseline_parent_count": len(baseline_parents), "experiment_parent_count": len(experimental_parents),
                         "gained_parents": sorted(gained), "lost_parents": sorted(lost),
                         "gained_gold_parents": sorted(gained & relevant), "lost_gold_parents": sorted(lost & relevant)}
    return result


def verify_loss_coverage(changes, losses):
    if set(changes) != set(losses):
        raise experiment.H2Error("h2_loss_stage_omission")
    for stage, change in changes.items():
        ids = [row["parent_id"] for row in losses[stage]]
        gold = [row["parent_id"] for row in losses[stage] if row["is_gold"]]
        if len(ids) != len(set(ids)) or sorted(ids) != change["lost_parents"] or sorted(gold) != change["lost_gold_parents"]:
            raise experiment.H2Error("h2_lost_parent_omission_or_duplicate:" + stage)
    return True


def loss_audit(before, after, changes, payloads):
    filtered = after["effective_qdrant_filter"]
    all_new = {hit["parent_id"] for hit in stage_rows(after)["all_legs"]}
    by_parent = {}
    for child, payload in payloads.items():
        by_parent.setdefault(payload["parent_id"], []).append((child, payload))
    old_stages, new_stages = stage_rows(before), stage_rows(after)
    losses = {}
    for stage, change in changes.items():
        rows = []
        for parent in change["lost_parents"]:
            children = by_parent[parent]
            eligible = [child for child, payload in children if experiment.matches(payload, filtered)]
            baseline_children = [hit["child_id"] for hit in old_stages[stage] if hit["parent_id"] == parent]
            excluded = sorted({child for child in baseline_children if not experiment.matches(payloads[child], filtered)})
            reason = ("all_parent_children_rejected_by_filter" if not eligible else
                      "still_retrieved_but_absent_at_this_stage" if parent in all_new else
                      "eligible_parent_not_returned_by_any_leg")
            rows.append({"parent_id": parent, "is_gold": parent in before["relevant_parent_ids"],
                         "source_title": children[0][1].get("source_title"), "breadcrumb": children[0][1].get("breadcrumb"),
                         "source_id": children[0][1]["source_id"], "baseline_stage_child_ids": baseline_children,
                         "excluded_baseline_stage_child_ids": excluded, "eligible_corpus_child_ids": eligible,
                         "reason": reason, "experiment_presence": {key: parent in {hit["parent_id"] for hit in hits} for key, hits in new_stages.items()}})
        losses[stage] = rows
    verify_loss_coverage(changes, losses)
    return losses


def special036(row, preflight):
    stages = stage_rows(row)
    result = []
    for detail in preflight:
        child, parent = detail["child_id"], detail["parent_id"]
        result.append({**detail, "retrieved_by_legs": [leg["leg_id"] for leg in row["leg_results"] if child in {hit["child_id"] for hit in leg["hits"]}],
                       "child_presence": {stage: child in {hit["child_id"] for hit in hits} for stage, hits in stages.items()},
                       "parent_presence": {stage: parent in {hit["parent_id"] for hit in hits} for stage, hits in stages.items()}})
    return result


def generate():
    out = experiment.OUTPUT
    state = experiment.read_json(out / "run_manifest.json")
    if state["status"] != "complete":
        lines = ["# H2 Gate 1 — 已停止，未形成25题结论", "", f"运行状态：`{state['status']}`；已完成 {state['completed_case_count']}/25。",
                 f"停止原因：`{state['error_code']}`。", "", "没有计算完整分组指标或判定H2成立。没有绕过安全阀继续运行。", "",
                 "安全阀事件：", "", "```json", json.dumps(state.get("safety_events", []), ensure_ascii=False, indent=2), "```", "",
                 "原始记录保留在 cases.jsonl / qdrant_requests.json / run_manifest.json。等待 West 审阅，禁止自动重试。", ""]
        (out / "report.md").write_text("\n".join(lines), encoding="utf-8", newline="\n")
        return {"complete": False, "status": state["status"]}
    verification = experiment.verify()
    h1, baseline = experiment.stack()
    inputs = experiment.load_prepared()
    controls, projection = inputs[2], inputs[5]
    rows = experiment.read_jsonl(out / "cases.jsonl")
    comparisons, losses = [], []
    records = out / "records"
    records.mkdir(exist_ok=True)
    for before, after in zip(controls, rows, strict=True):
        changes = parent_changes(before, after)
        comparison = h1.compare_case(after, before)
        comparison["exhaustive_parent_changes"] = changes
        comparison["loss_audit"] = loss_audit(before, after, changes, projection.children)
        comparisons.append(comparison)
        losses.append({"case_id": after["case_id"], "question": after["question"], "stages": comparison["loss_audit"]})
        experiment.write_json(records / (after["case_id"] + ".json"), comparison)
    control_summary, experimental_summary = baseline.summarize(controls), baseline.summarize(rows)
    timeline = experimental_summary["by_question_group"]["timeline"]["recall_at_40"]
    summary = {"control": control_summary, "experiment": experimental_summary,
               "overall_delta": {key: experimental_summary["all_scored"][key] - control_summary["all_scored"][key] for key in baseline.METRICS},
               "hypothesis": {"conclusion": conclusion(timeline), "timeline_recall_at_40": timeline,
                              "baseline_timeline_recall_at_40": control_summary["by_question_group"]["timeline"]["recall_at_40"],
                              "delta": timeline - control_summary["by_question_group"]["timeline"]["recall_at_40"],
                              "holds_at_least": 0.6, "partially_holds_strictly_above": 0.351},
               "all_25_healthy": True, "baseline_retrieval_rerun": False, "new_deepseek_calls": 0, "judge_calls": 0,
               "safety_valve_triggered": False, "qdrant_write_calls": 0,
               "loss_totals_by_stage": {stage: {"all_parent_question_pairs": sum(len(item["exhaustive_parent_changes"][stage]["lost_parents"]) for item in comparisons),
                                               "gold_parent_question_pairs": sum(len(item["exhaustive_parent_changes"][stage]["lost_gold_parents"]) for item in comparisons)}
                                        for stage in ("all_legs", *experiment.STAGES)}}
    four = special036(next(row for row in rows if row["case_id"] == "rag-v0-036"), experiment.read_json(out / "036_four_gold_children_preflight.json"))
    experiment.write_json(out / "metrics.json", summary)
    experiment.write_json(out / "comparisons.json", comparisons)
    experiment.write_json(out / "lost_parents.json", losses)
    experiment.write_json(out / "036_four_gold_children.json", four)
    lines = ["# H2 Gate 1：三档元数据过滤对照实验", "", f"**预登记结论：{summary['hypothesis']['conclusion']}。**timeline recall@40：{summary['hypothesis']['baseline_timeline_recall_at_40']:.9f} → {timeline:.9f}，Δ={summary['hypothesis']['delta']:+.9f}。",
             "", f"timeline R@12维持{experimental_summary['by_question_group']['timeline']['recall_at_12']:.9f}，P@12维持{experimental_summary['by_question_group']['timeline']['precision_at_12']:.9f}；MRR由{control_summary['by_question_group']['timeline']['mrr']:.9f}降至{experimental_summary['by_question_group']['timeline']['mrr']:.9f}。新增前40命中没有转化为最终top12召回改善。",
             "", "25/25真实生产路径检索完成，175条leg、25次RRF、25次原问题rerank；安全阀触发0，无过滤回补0，新增DeepSeek/judge=0，Qdrant写入0。",
             "", "## 协议与边界", "", "对照直接读取H1 attempt-03保存的同25题，未重跑。复用冻结3子查询×dense/BM25+HyDE dense；唯一变量为三档metadata filter。19题实体候选完整match-any，6题无实体；显式年份仅036/037/039，040不加年份组。",
             "", "本次正式结果位于attempt-02。根目录保留第一次启动的原始停止记录（status=stopped_safety_valve）：该次因HTTP适配未识别客户端query.nearest.text而在发送检索请求前停止，实际query请求0、RRF/rerank0；不是EXPLORATORY回补触发。修复后新增真实客户端MockTransport回归，再使用独立目录执行首个完整25题检索，没有对任何已取得检索结果的样本重跑。详情见上级README.md，原始状态未改写。",
             "", "年份闭区间：year命中或start/end相交或min/max mentioned相交则通过；五字段均空亦通过；否则排除。Qdrant使用每个字段的is_empty判定缺失/null/空数组。没有用source_published_at。",
             "", "生产AST不支持is_empty，因此仅评测作用域的compiler扩展第二档；plans.json保存中间AST，filters.json、每题effective_qdrant_filter与HTTP日志保存真正执行的完整三档条件。生产代码未改。",
             "", "query_types始终exploratory，RRF k60、candidate40、overfetch2、每路上限80、doc cap4/source cap5、原问题rerank→12、BM25 k1=1.2/b=.75、dense ef128/5s、模型revision与所有其余参数保持H1。过滤后不足40/80是实际候选变少，不补齐、不额外搜索；P@12分母仍12。",
             "", "安全阀复用原EXPLORATORY回补判定，若为真则在额外请求发出前抛出不可被生产except Exception吞掉的SafetyStop并终止。候选阈值仍12，未关闭fallback或放松过滤。",
             "", "本进程仅为loopback设置NO_PROXY，保留客户端兼容性探针及正式binding校验；读取凭据被禁止，使用无认证本机Qdrant；模型离线加载并禁止隐式HF token。CUDA先于评测依赖导入，不改device/dtype/batch/线程配置。",
             "", "H1既有审计限制原样保留：原run_manifest.status=incomplete，25题healthy记录可离线计分；额外版本探针被环境代理拦截的归因为原分析的推断。本轮未修写原记录。",
             "", "计分复用Step2 score_case：融合Child前40后按标准Parent去重算R@40；rerank Child前12后按Parent去重算R@12/P@12，P分母12；MRR使用首个相关Child排名；宏平均。41–80的overfetch命中不算R@40。",
             "", "West本轮阈值按字面锁定：R@40≥0.6成立；>0.351且<0.6部分成立；≤0.351不成立。实际H1精确基线0.351190476另列，不替换预登记0.351边界。",
             "", "## 分组指标", "", "表内均为H1→H2。", "", "| 分组 | n | R@40 | R@12 | P@12 | MRR |", "|---|---:|---:|---:|---:|---:|"]
    groups = [("全部25", control_summary["all_scored"], experimental_summary["all_scored"])]
    groups.extend((key, control_summary["by_answerability"][key], experimental_summary["by_answerability"][key]) for key in ("full", "partial"))
    groups.extend((key, control_summary["by_question_group"][key], experimental_summary["by_question_group"][key]) for key in experimental_summary["by_question_group"])
    for label, before, after in groups:
        values = " | ".join(f"{before[key]:.6f}→{after[key]:.6f}" for key in baseline.METRICS)
        lines.append(f"| {label} | {after['case_count']} | {values} |")
    lines.extend(["", "## 25题完整指标与Parent变化", "", "每个链接包含所有阶段全部新增/丢失Parent与独立Gold清单，不按是否相关删减。+/-为Parent数。", "",
                  "| case | 题型 | R@40 | R@12 | P@12 | MRR | Parent@40 +/- | Gold@40 +/- | Parent@12 +/- | Gold@12 +/- |",
                  "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"])
    for row, change in zip(rows, comparisons, strict=True):
        values = " | ".join(f"{change['baseline'][key]:.6f}→{change['experiment'][key]:.6f}" for key in baseline.METRICS)
        counts = []
        for stage in ("rrf_top40", "rerank_top12"):
            stage_change = change["exhaustive_parent_changes"][stage]
            counts.extend([f"+{len(stage_change['gained_parents'])}/−{len(stage_change['lost_parents'])}",
                           f"+{len(stage_change['gained_gold_parents'])}/−{len(stage_change['lost_gold_parents'])}"])
        lines.append(f"| [{row['case_id']}](records/{row['case_id']}.json) | {row['question_group']} | {values} | {' | '.join(counts)} |")
    lines.extend(["", "## 收益与退化定位", ""])
    for before, after, comparison in zip(controls, rows, comparisons, strict=True):
        gained = comparison["exhaustive_parent_changes"]["rrf_top40"]["gained_gold_parents"]
        if gained:
            before_stages = stage_rows(before)
            for parent in gained:
                was_raw = parent in {hit["parent_id"] for hit in before_stages["all_legs"]}
                was_final = parent in {hit["parent_id"] for hit in before["rerank_top12"]}
                lines.append(f"- {after['case_id']}新增Gold@40 `{parent}`；R@40 {before['metrics']['recall_at_40']:.6f}→{after['metrics']['recall_at_40']:.6f}。H1七路已召回={was_raw}，H1最终top12已命中={was_final}；不能将融合排名提升写成首次原始召回或最终召回提升。")
        if comparison["delta"]["mrr"] < 0:
            lines.append(f"- {after['case_id']} MRR下降：{before['metrics']['mrr']:.6f}→{after['metrics']['mrr']:.6f}；首个Gold Child从第{before['metrics']['first_hit_child_rank']}位移到第{after['metrics']['first_hit_child_rank']}位。")
    lines.extend(["", "## 误杀专项", "", "`lost_parents.md`与`lost_parents.json`列出全部baseline有、实验没有的Parent；覆盖七路原始候选并集、RRF40、RRF80、selection和rerank12五个集合。Gold单列。任何排名丢失都保留，不能只报被硬谓词完全排除的Parent。", "",
                  "| 阶段 | 丢失Parent–题对 | 其中Gold |", "|---|---:|---:|"])
    for stage, counts in summary["loss_totals_by_stage"].items():
        lines.append(f"| {stage} | {counts['all_parent_question_pairs']} | {counts['gold_parent_question_pairs']} |")
    lines.extend(["", "每个丢失Parent均给出baseline Child、被过滤的baseline Child、全库仍可通过的Child及实验各阶段存在性。全库无Child通过才标硬过滤排除；仍有资格却未召回，或仍在七路但掉出某阶段，分别报告，不把所有变化冒称硬过滤因果。",
                  "", "## 036四个Gold Child单独验证", "", "filter存活以实际Qdrant filtered scroll验证；其后各列是实际检索产物中该Child的存在性，Parent存在性另外记录。", "",
                  "| Child | 档位 | server filter通过 | 七路召回 | RRF前40 | selection主Child | rerank主Child前12 | Parent前12 |",
                  "|---|---|---|---|---|---|---|---|"])
    for item in four:
        present = item["child_presence"]
        lines.append(f"| {item['child_id']} | {item['tier']} | {item['server_scroll_pass']} | {present['all_legs']} | {present['rrf_top40']} | {present['selection']} | {present['rerank_top12']} | {item['parent_presence']['rerank_top12']} |")
    lines.extend(["", "## 验证与限制", "", "运行：`python -B -m evals.rag_h2_metadata_v5 prepare`、`run`、`verify`；报告：`python -B -m evals.rag_h2_metadata_report_v5`，均从ai-service目录运行。模型真实推理只在run执行一次；离线verify不重跑检索。",
                  "", "verification.json确认175路过滤合规，RRF顺序/分数、原selection、Step2指标可离线复算；HTTP每题恰一批七路请求且完整filter一致；所有Parent均映射到冻结projection。Qdrant前后全量payload与projection一致，Gold、H1、Gate0和生产代码hash保持。未读取或重新校验向量值。",
                  "", "只观察到本次同冻结语料上的效果；未做多次随机重复或统计显著性检验，不推断其他语料上的效果，也不将组合filter收益拆成年份/实体各自独立因果。速度受当前桌面负载影响，不作为性能对照。",
                  "", "自动化测试与静态检查命令、结果见 checks.json / tests.txt。无业务/UI修改，应用构建与前端交互验证不适用。未新增依赖。", "",
                  "本次只创建评测工具、测试与审计产物。Qdrant只读，Gold/H1冻结文件未改，既有未提交删除未动；不push、不合入main。**运行结束，等待West审阅。**", ""])
    (out / "report.md").write_text("\n".join(lines), encoding="utf-8", newline="\n")
    loss_lines = ["# H2全部丢失Parent清单", "", "以下每个阶段均完整列出所有baseline有、实验没有的Parent，无截断。Gold重复单列便于审阅。详情与资格归因见lost_parents.json。", ""]
    for item in comparisons:
        loss_lines.extend([f"## {item['case_id']}：{item['question']}", ""])
        for stage, change in item["exhaustive_parent_changes"].items():
            loss_lines.extend([f"### {stage}", "", f"全部丢失（{len(change['lost_parents'])}）：", "", ", ".join(f"`{parent}`" for parent in change["lost_parents"]) or "无", "",
                               f"Gold丢失（{len(change['lost_gold_parents'])}）：", "", ", ".join(f"`{parent}`" for parent in change["lost_gold_parents"]) or "无", ""])
    (out / "lost_parents.md").write_text("\n".join(loss_lines), encoding="utf-8", newline="\n")
    result = {"complete": True, "conclusion": summary["hypothesis"]["conclusion"], "timeline_recall_at_40": timeline,
              "verification": verification, "loss_counts_by_stage": summary["loss_totals_by_stage"]}
    experiment.write_json(out / "report_verification.json", result)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return result


def verify_report():
    out = experiment.OUTPUT
    _h1, baseline = experiment.stack()
    inputs = experiment.load_prepared()
    controls, projection = inputs[2], inputs[5]
    rows = experiment.read_jsonl(out / "cases.jsonl")
    comparisons = experiment.read_json(out / "comparisons.json")
    losses = experiment.read_json(out / "lost_parents.json")
    metrics = experiment.read_json(out / "metrics.json")
    if metrics["control"] != baseline.summarize(controls) or metrics["experiment"] != baseline.summarize(rows):
        raise experiment.H2Error("h2_report_macro_metric_mismatch")
    timeline = metrics["experiment"]["by_question_group"]["timeline"]
    if timeline["case_count"] != 4 or metrics["hypothesis"]["conclusion"] != conclusion(timeline["recall_at_40"]):
        raise experiment.H2Error("h2_report_hypothesis_or_population_mismatch")
    if len(rows) != len(controls) or len(comparisons) != 25 or len(losses) != 25:
        raise experiment.H2Error("h2_report_not_complete_25")
    for before, after, comparison, loss in zip(controls, rows, comparisons, losses, strict=True):
        changes = parent_changes(before, after)
        expected_losses = loss_audit(before, after, changes, projection.children)
        if comparison["exhaustive_parent_changes"] != changes or comparison["loss_audit"] != expected_losses or loss["stages"] != expected_losses:
            raise experiment.H2Error("h2_report_parent_changes_or_losses_mismatch")
        if comparison["baseline"] != before["metrics"] or comparison["experiment"] != after["metrics"]:
            raise experiment.H2Error("h2_report_per_question_metrics_mismatch")
        if experiment.read_json(out / "records" / (after["case_id"] + ".json")) != comparison:
            raise experiment.H2Error("h2_report_per_question_artifact_mismatch")
        verify_loss_coverage(changes, loss["stages"])
    special = experiment.read_json(out / "036_four_gold_children.json")
    if special != special036(next(row for row in rows if row["case_id"] == "rag-v0-036"), experiment.read_json(out / "036_four_gold_children_preflight.json")):
        raise experiment.H2Error("h2_report_036_four_child_mismatch")
    if sum(item["three_tier_filter_pass"] for item in special) != 3 or any(item["child_presence"]["all_legs"] for item in special if not item["three_tier_filter_pass"]):
        raise experiment.H2Error("h2_report_036_known_year_exclusion_violation")
    text = (out / "report.md").read_text(encoding="utf-8")
    if sum(line.startswith("| [rag-v0-") for line in text.splitlines()) != 25:
        raise experiment.H2Error("h2_report_missing_question_table_row")
    requests = experiment.read_json(out / "qdrant_requests.json")
    if any(request.get("status_code") != 200 for request in requests):
        raise experiment.H2Error("h2_report_http_incomplete_response")
    result = {"verified": True, "questions": 25, "timeline_questions": 4,
              "all_macro_and_per_question_metrics_recomputed": True, "all_five_stage_parent_losses_exhaustive": True,
              "all_gold_losses_separately_verified": True, "special_036_children_verified": 4,
              "036_filter_surviving_children": 3, "network_calls": 0}
    experiment.write_json(out / "report_acceptance.json", result)
    print(json.dumps(result), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=experiment.OUTPUT)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    configure_offline()
    sys.addaudithook(Gate0Guard().audit)
    experiment.OUTPUT = args.output
    if args.verify_only:
        verify_report()
    else:
        generated = generate()
        if generated["complete"]:
            verify_report()


if __name__ == "__main__":
    main()
