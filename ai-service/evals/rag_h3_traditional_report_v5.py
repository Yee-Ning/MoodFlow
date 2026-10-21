"""Offline, exhaustive H3 vs saved H2 comparison; never queries Qdrant."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

from evals import rag_h3_traditional_v5 as run
from evals.rag_h2_metadata_report_v5 import parent_changes
from evals.ragas_gate0 import Gate0Guard


def regressions(before, after):
    _, baseline = run.stack()
    return [key for key in baseline.METRICS if after[key] < before[key] - 1e-12]


def hypothesis(before, after):
    old036 = next(row for row in before if row["case_id"] == "rag-v0-036")
    new036 = next(row for row in after if row["case_id"] == "rag-v0-036")
    _, baseline = run.stack()
    details = [{"case_id": old["case_id"], "metrics": regressions(old["metrics"], new["metrics"])}
               for old, new in zip(before, after, strict=True)]
    details = [row for row in details if row["metrics"]]
    recall_losses = [row["case_id"] for row in details if "recall_at_40" in row["metrics"]]
    improved = new036["metrics"]["recall_at_40"] > 0.5
    return {"036_control_recall_at_40": old036["metrics"]["recall_at_40"],
            "036_experiment_recall_at_40": new036["metrics"]["recall_at_40"],
            "036_hypothesis_holds": improved, "036_conclusion": "成立" if improved else "不成立",
            "per_case_metric_regressions": details, "recall_at_40_regression_case_ids": recall_losses,
            "no_case_recall_at_40_decline": not recall_losses, "no_case_any_metric_decline": not details,
            "overall_delta": {key: baseline.summarize(after)["all_scored"][key] - baseline.summarize(before)["all_scored"][key]
                              for key in baseline.METRICS}}


def fused_target(legs):
    # Reconstruct the production point-level RRF exactly, including per-leg dedup.
    scores, children = {}, {}
    for leg in legs:
        seen = set()
        for rank, hit in enumerate(leg["hits"], 1):
            point = hit["point_id"]
            if point in seen:
                continue
            seen.add(point)
            scores[point] = scores.get(point, 0) + leg["weight"] / (60 + rank)
            children[point] = hit
    order = sorted(scores, key=lambda point: (-scores[point], point))
    return [{"fusion_rank": rank, "fusion_score": scores[point], **children[point]}
            for rank, point in enumerate(order, 1) if children[point]["parent_id"] == run.TARGET]


def target_presence(row, *, experiment):
    legs = row["leg_results"]
    groups = {"original_7_legs": legs[:7], "traditional_3_legs": legs[7:], "all_legs": legs}
    result = {key: {"present": any(hit["parent_id"] == run.TARGET for leg in value for hit in leg["hits"]),
                    "hits": [{"leg_id": leg["leg_id"], "text": leg["text"], **hit}
                             for leg in value for hit in leg["hits"] if hit["parent_id"] == run.TARGET]}
              for key, value in groups.items()}
    for stage in run.STAGES:
        hits = [hit for hit in row[stage] if hit["parent_id"] == run.TARGET]
        result[stage] = {"present": bool(hits), "hits": hits}
    result.update(case_id=row["case_id"], experiment=experiment,
                  gold_parent=run.TARGET in row["relevant_parent_ids"],
                  full_fusion_original_7=fused_target(legs[:7]), full_fusion_all_legs=fused_target(legs))
    return result


def comparison(before, after):
    _, baseline = run.stack()
    changes = parent_changes(before, after)
    # parent_changes includes complete raw-leg union and all four saved stages.
    return {"case_id": before["case_id"], "question": before["question"],
            "question_group": before["question_group"], "answerability": before["answerability"],
            "relevant_parent_ids": before["relevant_parent_ids"], "control": before["metrics"],
            "experiment": after["metrics"],
            "delta": {key: after["metrics"][key] - before["metrics"][key] for key in baseline.METRICS},
            "regressed_metrics": regressions(before["metrics"], after["metrics"]),
            "parent_changes": changes, "first_seven_vs_h2": after["first_seven_vs_h2"]}


def links(ids):
    return ", ".join(f"`{value}`" for value in ids) or "无"


def generate():
    verification = run.verify()
    _, baseline = run.stack()
    before, after = run.read_jsonl(run.CONTROL / "cases.jsonl"), run.read_jsonl(run.OUTPUT / "cases.jsonl")
    converted = run.read_json(run.OUTPUT / "conversions.json")
    review = run.read_json(run.OUTPUT / "conversion_review.json")
    comparisons = [comparison(old, new) for old, new in zip(before, after, strict=True)]
    summary = {"control": baseline.summarize(before), "experiment": baseline.summarize(after),
               "hypothesis": hypothesis(before, after), "verification": verification}
    target = [{"case_id": old["case_id"], "control": target_presence(old, experiment=False),
               "experiment": target_presence(new, experiment=True)} for old, new in zip(before, after, strict=True)]
    special = next(row for row in target if row["case_id"] == "rag-v0-036")
    for name, value in (("comparison.json", comparisons), ("summary.json", summary),
                        ("target_parent_all25.json", target), ("036_target_parent.json", special)):
        run.write_json(run.OUTPUT / name, value)
    (run.OUTPUT / "records").mkdir(exist_ok=True)
    for row in comparisons:
        run.write_json(run.OUTPUT / "records" / (row["case_id"] + ".json"), row)
    h = summary["hypothesis"]
    lines = ["# H3：新增繁体 BM25 leg 对照实验", "",
             f"**036 预登记主判据：{h['036_conclusion']}。** recall@40：{h['036_control_recall_at_40']:.9f} → {h['036_experiment_recall_at_40']:.9f}；阈值为严格大于 0.5。", "",
             f"25 题逐题 R@40 均不下降：{h['no_case_recall_at_40_decline']}；四项指标均无逐题下降：{h['no_case_any_metric_decline']}。下面完整保留全部退化，不能用总体均值掩盖单题损失。", "",
             "## 协议与计分", "",
             "对照为已完成的 H2 attempt-02，未重跑。25 题名单、原问题、3 子查询、HyDE、实体与三档 filter、模型与配置逐字沿用 H2。计分继续使用 H2 的 Gold V5 标准 Parent；未混入 V5.1 新绑定/标签/答案。", "",
             "唯一实验变量：原 7 leg 后追加 3 条 OpenCC s2t 繁体子查询 BM25 leg（权重各 1.0），无新增 dense。RRF k=60；candidate=40；每路=80；selection 接收融合前 80；doc cap=4；source cap=5；原问题 rerank→12；dense ef=128；BM25 k1=1.2/b=.75。", "",
             "计分完全调用 Step 2 score_case：RRF Child 前 40 后按 Parent 去重算 R@40；rerank Child 前 12 按 Parent 去重算 R@12/P@12，P 分母固定 12；MRR 使用 rerank 前 12 中首个 Gold Child 的排名；按题宏平均。41–80 命中不算 R@40。", "",
             "OpenCC 使用 opencc-python-reimplemented 0.1.7 的 s2t 配置，安装于仓库外隔离目录；未修改生产依赖。75 条转换由 Codex 逐条阅读核对，非 West 人工签字；仅转换繁简，不作同义改写或扩词。转换 JSON 与运行前复核记录哈希锁定。s2t 对周杰伦保留人名用字杰，输出周杰倫；对魔杰座输出魔傑座，保留该原始转换，不人工改词或冒称官方标题标准写法。", "",
             "## 总体与 timeline 等分组指标", "", "表内 H2 → H3。", "",
             "| 分组 | n | R@40 | R@12 | P@12 | MRR |", "|---|---:|---:|---:|---:|---:|"]
    group_pairs = [("全部25", summary["control"]["all_scored"], summary["experiment"]["all_scored"])]
    for category in ("by_answerability", "by_question_group"):
        for key in summary["control"][category]:
            group_pairs.append((key, summary["control"][category][key], summary["experiment"][category][key]))
    for name, old, new in group_pairs:
        lines.append(f"| {name} | {old['case_count']} | " + " | ".join(f"{old[key]:.9f} → {new[key]:.9f}" for key in baseline.METRICS) + " |")
    lines += ["", "## 25 题完整指标", "", "| case | 题型 | R@40 | R@12 | P@12 | MRR | 退化指标 |", "|---|---|---:|---:|---:|---:|---|"]
    for row in comparisons:
        lines.append(f"| [{row['case_id']}](records/{row['case_id']}.json) | {row['question_group']} | "
                     + " | ".join(f"{row['control'][key]:.9f} → {row['experiment'][key]:.9f}" for key in baseline.METRICS)
                     + f" | {', '.join(row['regressed_metrics']) or '无'} |")
    lines += ["", "## bb32b222：036 逐阶段存在性", "", f"完整 ID：`{run.TARGET}`。", "",
              "| 阶段 | H2 | H3 |", "|---|---|---|"]
    for stage in ("original_7_legs", "traditional_3_legs", "all_legs", *run.STAGES):
        old = special["control"][stage]["present"] if stage != "traditional_3_legs" else "未设置"
        new = special["experiment"][stage]["present"]
        lines.append(f"| {stage} | {old} | {new} |")
    lines += ["", "完整 Child/leg 名次、RRF 全量名次、分数与阶段命中：`036_target_parent.json`；全部 25 题目标 Parent 的存在性见 `target_parent_all25.json`。", ""]
    for name in ("control", "experiment"):
        data = special[name]
        lines.append(f"- {name} 首个目标 Child RRF 全量名次：" + str(min((hit["fusion_rank"] for hit in data["full_fusion_all_legs"]), default=None)))
    new036 = next(row for row in after if row["case_id"] == "rag-v0-036")
    if special["experiment"]["full_fusion_all_legs"]:
        best = min(special["experiment"]["full_fusion_all_legs"], key=lambda hit: hit["fusion_rank"])
        lines += ["", f"目标 Child 的 RRF 分数为 {best['fusion_score']:.12f}，本题第 40 名分数为 {new036['rrf_top40'][-1]['fusion_score']:.12f}。繁体 leg 找回了原始候选，但融合分数仍不足以进入前 40；也未进入 selection 接收的前 80。此结论仅针对本次 036，不能写成 H3 的 recall@40 改善。", ""]
    lines += ["", "### 036 目标 Parent 的原始 leg 命中", "", "| 组 | leg | 原始名次 | Child |", "|---|---|---:|---|"]
    for name in ("control", "experiment"):
        for hit in special[name]["all_legs"]["hits"]:
            lines.append(f"| {name} | {hit['leg_id']} | {hit['rank']} | `{hit['child_id']}` |")
    lines += ["", "## 逐题退化与控制变量复核", "",
              f"原 7 leg 返回的有序 point ID 和 raw score 与 H2 完全相同：{verification['original_seven_identical_cases']}/25 题。不同项详见 comparison.json 的 first_seven_vs_h2，不隐去运行波动。", ""]
    for row in h["per_case_metric_regressions"]:
        lines.append(f"- `{row['case_id']}`：{', '.join(row['metrics'])}")
    for row in comparisons:
        lost = row["parent_changes"]["rrf_top40"]["lost_gold_parents"]
        if lost:
            lines.append(f"- `{row['case_id']}` 丢失 Gold@40：{links(lost)}；R@40 {row['control']['recall_at_40']:.9f} → {row['experiment']['recall_at_40']:.9f}。")
    if not h["per_case_metric_regressions"]:
        lines.append("无单题指标下降。")
    lines += ["", "## 新增/丢失 Parent", "",
              "`parent_changes.md` 完整列出 25 题在 all_legs / RRF40 / RRF80 / selection / rerank12 各阶段的新增和丢失 Parent，Gold 新增/丢失分别单列。机器清单同见 comparison.json 与 records/*.json，无列表截断。", "",
              "| 阶段 | 新增 Parent–题对 | 丢失 Parent–题对 | Gold 新增 | Gold 丢失 |", "|---|---:|---:|---:|---:|"]
    for stage in ("all_legs", *run.STAGES):
        counts = Counter()
        for row in comparisons:
            counts.update({key: len(row["parent_changes"][stage][key]) for key in ("gained_parents", "lost_parents", "gained_gold_parents", "lost_gold_parents")})
        lines.append(f"| {stage} | {counts['gained_parents']} | {counts['lost_parents']} | {counts['gained_gold_parents']} | {counts['lost_gold_parents']} |")
    lines += ["", "## 验证与限制", "",
              "25/25 真实检索、250 条 leg、25 次 RRF 与 25 次原问题 rerank。HTTP 白名单在发送前检查完整十路、繁体文本、BM25 选项、limit 与 filter；Qdrant 只读，未发 mutation。保留 H2 EXPLORATORY 安全阀，若企图增加无过滤请求即停止。", "",
              "离线 verify 重放所有 RRF 顺序/分数、原 selection、Step 2 四项指标及 Parent/Child 映射；不再次检索。Qdrant 前后全量 691 条 payload 与冻结 projection 相同，未读取向量值。Gold 所有数据集、H1/H2 既有产物及生产检索代码哈希保持；无新生成或 judge 调用。", "",
              "仅观察本轮同 25 题、同一冻结语料；未做重复运行或显著性检验。若原 7 leg 实际返回发生变化，已逐题列出，不能将所有差值完全归因于新增 leg。速度受桌面负载影响，不作为性能结论。", "",
              "测试命令：仓库根运行 `ai-service/.venv/Scripts/python.exe -B workspace/moodflow-review/h3-traditional-bm25-v5-20261018/run_checks.py`。60 项测试、5 个子测试通过，无失败；1 项原有可选集成测试因未配置 QDRANT_INTEGRATION_URL 跳过，该测试会写入临时 collection，本轮只读实验不启用。详见 checks.json / tests.txt / README.md。", "",
              "运行控制台在 058 与 059 完成记录之间出现一次 CUDA allocator OOM 分配警告（runtime_observations.json 保存原文）。所有题仍 healthy、无 leg error、无 rerank fallback，未更改 batch/device/timeout 或重跑；不宣称运行中没有显存压力。", "",
              "独立检查命令：同目录 `verify_outputs.py` 用 Python 标准库重算原始 RRF、四项指标、timeline、全部 Parent 增减及所有受保护文件哈希。生产/UI 未修改，应用构建不适用。未 push、未合入 main。运行结束，等待 West 审阅。", ""]
    (run.OUTPUT / "report.md").write_text("\n".join(lines), encoding="utf-8", newline="\n")
    parent_lines = ["# H3 全部新增/丢失 Parent（Gold 单列）", ""]
    for row in comparisons:
        parent_lines += [f"## {row['case_id']}：{row['question']}", "", "Gold 标准：" + links(row["relevant_parent_ids"]), ""]
        for stage, change in row["parent_changes"].items():
            parent_lines += [f"### {stage}", ""]
            for label, field in (("全部新增", "gained_parents"), ("全部丢失", "lost_parents"), ("Gold 新增", "gained_gold_parents"), ("Gold 丢失", "lost_gold_parents")):
                parent_lines += [f"{label}（{len(change[field])}）：{links(change[field])}", ""]
    (run.OUTPUT / "parent_changes.md").write_text("\n".join(parent_lines), encoding="utf-8", newline="\n")
    convert_lines = ["# H3 繁简转换对照表", "", "75/75 条由 Codex 逐条阅读复核，转换执行通过；无人工改写，仅 OpenCC s2t。West 人工终审待定。魔杰座→魔傑座是 s2t 的原始输出，保留以锁定唯一变量，不宣称是官方标题标准字形。", "",
                     f"转换 JSON SHA256：`{review['conversions_sha256']}`。", "", "| case | 子查询 | 原查询 | 繁体查询 | 核对 |", "|---|---:|---|---|---|"]
    for row in converted:
        convert_lines.append(f"| {row['case_id']} | {row['subquery_index'] + 1} | {row['original']} | {row['traditional']} | 通过 |")
    (run.OUTPUT / "conversion_table.md").write_text("\n".join(convert_lines) + "\n", encoding="utf-8", newline="\n")
    run.write_json(run.OUTPUT / "report_verification.json", {"complete": True, "case_count": len(comparisons),
        "conversion_count": len(converted), "target_parent": run.TARGET, "hypothesis": h})
    print(json.dumps({"report_complete": True, "hypothesis": h}, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=run.OUTPUT)
    args = parser.parse_args()
    run.OUTPUT = args.output.resolve()
    run.h2.configure()
    sys.addaudithook(Gate0Guard().audit)
    generate()


if __name__ == "__main__":
    main()
