"""Replay the saved baseline without models/network and produce West's review pack."""

from __future__ import annotations

from collections import Counter
import json
import math

from evals.rag_source_baseline import (
    GOLD, GROUP_BY_ID, OUTPUT, PROJECTION, Projection, protected_hashes,
    read_jsonl, score_case, summarize, write_json,
)


def validate_records(cases, records, projection):
    if len(records) != len(cases) or [r["case_id"] for r in records] != [c["case_id"] for c in cases]:
        raise ValueError("baseline_replay_population_mismatch")
    counts = Counter()
    for case, record in zip(cases, records, strict=True):
        if not record["healthy"]:
            raise ValueError("baseline_replay_unhealthy_case")
        for field in ("question", "answerability", "relevant_parent_ids"):
            if record[field] != case[field]:
                raise ValueError("baseline_replay_gold_mismatch:" + field)
        if record["scored"] != (case["answerability"] in {"full", "partial"}):
            raise ValueError("baseline_replay_scoring_population_mismatch")
        if record["question_group"] != GROUP_BY_ID[case["case_id"]]:
            raise ValueError("baseline_replay_group_mismatch")
        plan = record["plan"]
        query = " ".join(case["question"].split())[:1000]
        if (plan["standalone_query"] != query or len(plan["queries"]) != 1
                or plan["queries"][0]["text"] != query or plan["queries"][0]["channel"] != "both"
                or plan["candidate_top_k"] != 40 or plan["final_top_k"] != 12
                or plan["query_types"] != ["exploratory"]):
            raise ValueError("baseline_replay_literal_plan_mismatch")
        relevant = set(case["relevant_parent_ids"])
        for stage in ("rrf_top40", "selection_input_rrf", "selection", "rerank_input", "rerank_top12"):
            for rank, row in enumerate(record[stage], 1):
                mapped = projection.item(child=row["child_id"], parent=row["parent_id"],
                                         point=row["point_id"], relevant=relevant)
                if row["rank"] != rank or any(row[k] != mapped[k] for k in ("hit", "doc_id", "source_id")):
                    raise ValueError("baseline_replay_mapping_or_rank_mismatch")
                counts["mapped_stage_entries"] += 1
        if record["rrf_top40"] != record["selection_input_rrf"][:40]:
            raise ValueError("baseline_replay_rrf_cutoff_mismatch")
        if len(record["rrf_top40"]) != 40 or len(record["selection_input_rrf"]) != 80:
            raise ValueError("baseline_replay_stage_size_mismatch")
        fused = record["selection_input_rrf"]
        if fused != sorted(fused, key=lambda r: (-r["fusion_score"], r["point_id"])):
            raise ValueError("baseline_replay_fusion_order_mismatch")
        for row in fused:
            expected = math.fsum(e["weight"] / (60 + e["rank"]) for e in row["channel_evidence"])
            if not math.isclose(row["fusion_score"], expected, rel_tol=1e-14):
                raise ValueError("baseline_replay_fusion_score_mismatch")
        if len({r["parent_id"] for r in record["selection"]}) != len(record["selection"]):
            raise ValueError("baseline_replay_parent_collapse_mismatch")
        for row in record["selection"]:
            siblings = [f for f in fused if f["parent_id"] == row["parent_id"]]
            if row["child_id"] != max(siblings, key=lambda f: f["fusion_score"])["child_id"]:
                raise ValueError("baseline_replay_primary_child_not_highest_fusion")
        if record["selection"] != record["rerank_input"]:
            raise ValueError("baseline_replay_rerank_input_mismatch")
        if len(record["rerank_top12"]) != 12 or len({r["parent_id"] for r in record["rerank_top12"]}) != 12:
            raise ValueError("baseline_replay_rerank_cutoff_mismatch")
        for row in record["rerank_top12"]:
            if not math.isfinite(row["rerank_score"]) or not 0 <= row["rerank_score"] <= 1:
                raise ValueError("baseline_replay_rerank_score_invalid")
            original = record["rerank_input"][row["retrieval_rank"] - 1]
            if original["child_id"] != row["child_id"]:
                raise ValueError("baseline_replay_rerank_provenance_mismatch")
        if record["rerank_top12"] != sorted(record["rerank_top12"],
                                             key=lambda r: (-r["rerank_score"], -r["fusion_score"], r["point_id"])):
            raise ValueError("baseline_replay_rerank_order_mismatch")
        expected = score_case(case, record["rrf_top40"], record["selection"], record["rerank_top12"])
        if any(record[key] != expected[key] for key in ("metrics", "stage_hits", "stage_missing")):
            raise ValueError("baseline_replay_metric_mismatch")
        counts["validated_cases"] += 1
    return dict(counts)


def stage_diagnosis(record):
    gold = set(record["relevant_parent_ids"])
    stages = {stage: {r["parent_id"] for r in record[stage]}
              for stage in ("rrf_top40", "selection_input_rrf", "selection", "rerank_top12")}
    return {"rrf_top40_missed": sorted(gold - stages["rrf_top40"]),
            "outside_rrf_selection_top80": sorted(gold - stages["selection_input_rrf"]),
            "overfetch_recovered_beyond40": sorted(gold & stages["selection_input_rrf"] - stages["rrf_top40"]),
            "selection_dropped": sorted(gold & stages["selection_input_rrf"] - stages["selection"]),
            "rerank_dropped": sorted(gold & stages["selection"] - stages["rerank_top12"]),
            "final_missed": sorted(gold - stages["rerank_top12"])}


def metric_table(groups):
    lines = ["| 分组 | n | recall@40 | recall@12 | precision@12 | MRR |",
             "|---|---:|---:|---:|---:|---:|"]
    for name, group in groups.items():
        values = ["—" if group[key] is None else f"{group[key]:.6f}"
                  for key in ("recall_at_40", "recall_at_12", "precision_at_12", "mrr")]
        lines.append(f"| {name} | {group['case_count']} | " + " | ".join(values) + " |")
    return lines


def generate_report(records, metrics, manifest, validation):
    scored = [r for r in records if r["scored"]]
    diagnoses = {r["case_id"]: stage_diagnosis(r) for r in scored}
    lines = ["# Gold V5 正式 RagSource 检索 baseline · Step 2", "",
             f"运行时间：{manifest['started_at']} 至 {manifest['finished_at']}（Asia/Shanghai）。",
             "50/50 题实跑成功；30 题计分，20 题仅观察；全部使用 dense + Qdrant 原生 BM25 和指定 reranker，未调用文本生成或 LLM judge；指标纯确定性。",
             "", "## 口径与边界", "",
             "- 入口：`RagSource.collect(..., limit=40)`；使用指定 release 的只读 binding。未经过聊天路由，未调用 WorldLine 深度管线或 query planner。",
             "- query：仅空白归一化并截 1000 字符。embedding 的既有检索 instruction 保留。",
             "- **West 待确认：原清单 doc cap=2，正式字面量入口固定 exploratory，实际取 broad_document_cap=4。用户已明确授权按实际 4 测；配置与代码未修改。**",
             "- 各路取 80，weighted RRF k=60、各路权重 1；RRF top-40 指前 40 个 Child，先截断再映射 Parent 去重。既有 overfetch=2，selection 实际处理前 80 个 Child，完整输入另存。",
             "- selection 按 Parent 保留 fusion 最高 Child；doc cap=4/source cap=5 是既有软约束，候选不足时会按既有逻辑回填 overflow，未强行加硬限制。",
             "- **West 待确认：planner final_top_k=10 与 reranker top_k=12 的清单口径差保留；本轮未调用 planner。正式 literal_search_plan 的实际 final_top_k=12，实际 rerank 返回 12。**",
             "- 正式 SourceRegistry 默认 candidate_limit=24；本任务显式要求 40，因此直接 collect(limit=40)，不改 registry 配置。",
             "- 模型按原 revision/设备/批次/精度加载，加载在计时查询之前；Qdrant timeout=5s、ef=128；reranker 既有 timeout=30s。",
             "- recall@12 按返回的 12 个 Child 映射并去重 Parent；precision@12 分母固定 12。MRR 使用实际返回 top-12 中首个标准 Parent 所属 Child 的原排名倒数，无命中为 0（观测范围等价 MRR@12）。",
             "- 四项主指标都是逐题算分再宏平均；分母严格为 V5 answerability=full/partial 的 30 题，不过滤难题或零分题。",
             "- V5 无题型字段、V4 expected_query_types 全空；题型采用运行前固定的评测侧 case-ID 分类，见 run_manifest.json。它不参与检索，也不等同于 Gold 题型标注。",
             "", "## 指标", ""]
    lines += metric_table({"全部计分": metrics["all_scored"], **metrics["by_answerability"]})
    lines += ["", "按题型（只有计分题进入均值；n=0 不报分）：", ""]
    lines += metric_table(metrics["by_question_group"])
    lines += ["", "## 环节体检（仅定位，不下调参结论）", "",
              "**召回覆盖最弱的是 timeline（recall@40=0.3155）与 artist_overview（0.3333），其次 comparison（0.5000）和 collaboration（0.6111）。** timeline 的4道计分题均在RRF top-40有标准 Parent 缺失；这部分损失发生在 reranker 之前。",
              "",
              "**直接的排序截断证据集中在 song_background 的 rag-v0-005，以及 artist_overview/collaboration 的 rag-v0-004、023、026。** 4题共5个标准 Parent 已进入 selection 却落在最终 top-12 之外。005 最终完全无命中；024 则在 selection 阶段丢失其唯一已召回的标准 Parent，不应归给 reranker。",
              "",
              "以下缺失数均为“题目×标准 Parent”对数；同一 Parent 在不同题目中分别计数。RRF top-40 与 selection 的输入范围不同，不能把两者差值直接视作 selection 损失。", "",
              "| 环节 | 缺失/变化的标准 Parent 对数 | 题数 |", "|---|---:|---:|"]
    for key, label in [("rrf_top40_missed", "RRF 前40未覆盖"),
                       ("outside_rrf_selection_top80", "RRF 前80仍未覆盖"),
                       ("overfetch_recovered_beyond40", "仅在 RRF 41–80 补入"),
                       ("selection_dropped", "前80有、selection后无"),
                       ("rerank_dropped", "selection有、rerank前12无"),
                       ("final_missed", "rerank前12最终未覆盖")]:
        lines.append(f"| {label} | {sum(len(d[key]) for d in diagnoses.values())} | "
                     f"{sum(bool(d[key]) for d in diagnoses.values())} |")
    lines += ["", "按题型的观测：", ""]
    for group, metric in metrics["by_question_group"].items():
        if not metric["case_count"]:
            continue
        rows = [r for r in scored if r["question_group"] == group]
        misses = [r["case_id"] for r in rows if r["metrics"]["recall_at_40"] < 1]
        weak = [r["case_id"] for r in rows if r["metrics"]["mrr"] < 1]
        dropped = [r["case_id"] for r in rows if diagnoses[r["case_id"]]["rerank_dropped"]]
        lines.append(f"- `{group}`（n={len(rows)}）：recall@40={metric['recall_at_40']:.4f}，"
                     f"MRR={metric['mrr']:.4f}。RRF 覆盖不全：{', '.join(misses) or '无'}；"
                     f"首个标准命中不在第1名/未命中：{', '.join(weak) or '无'}；"
                     f"selection 已有标准 Parent 被 rerank 截出：{', '.join(dropped) or '无'}。")
    lines += ["", "MRR=0 同时 selection 无标准 Parent 的题，定位为上游覆盖问题；selection 已有标准 Parent 而最终无命中，才有直接的 rerank 截断证据。MRR<1 且仍命中的题反映首命中排序位置，不能仅凭 MRR 将全部损失归给 reranker。", "",
              "逐题定位：", "", "| case | RRF40缺失 | RRF80缺失 | selection丢失 | rerank丢失 | 首命中Child排名 |", "|---|---|---|---|---|---:|"]
    for r in scored:
        d = diagnoses[r["case_id"]]
        values = [", ".join(d[k]) or "—" for k in ("rrf_top40_missed", "outside_rrf_selection_top80", "selection_dropped", "rerank_dropped")]
        lines.append(f"| {r['case_id']} | " + " | ".join(values) + f" | {r['metrics']['first_hit_child_rank'] or '未命中'} |")
    lines += ["", "## 不计分的20题", "",
              "全部照跑。下表的 hit 只表示与该题冻结 relevant_parent_ids 的 ID 交集，不是语义可回答性判断；若标准集合为空，hit=0 不代表没有捞回内容。未调用 LLM 判断假阳性。", "",
              "| case | answerability | 标准Parent数 | RRF40返回Child数/不同Parent数/标准命中 | selection返回Parent数/标准命中 | rerank返回Parent数/标准命中 | 首条Parent/source |",
              "|---|---|---:|---|---|---|---|"]
    for r in records:
        if r["scored"]:
            continue
        top = r["rerank_top12"][0]
        lines.append(f"| {r['case_id']} | {r['answerability']} | {len(r['relevant_parent_ids'])} | "
                     f"{len(r['rrf_top40'])}/{len({a['parent_id'] for a in r['rrf_top40']})}/{len(r['stage_hits']['rrf_top40'])} | "
                     f"{len(r['selection'])}/{len(r['stage_hits']['selection'])} | "
                     f"{len(r['rerank_top12'])}/{len(r['stage_hits']['rerank_top12'])} | {top['parent_id']} / {top['source_id']} |")
    lines += ["", "## 完整性与限制", "",
              f"- Gold V5 SHA256：`{manifest['gold_sha256']}`；projection SHA256：`{manifest['projection_sha256']}`。",
              f"- projection：691 Child / 413 Parent；逐阶段映射和 point ID、doc/source 一致性复核：{validation['mapped_stage_entries']} 条通过，未知/越界 Parent=0。",
              f"- protected_before/protected_after 完全一致：{manifest['protected_unchanged']}，包含 Gold V4/V5、projection、index manifest、检索/排序配置和代码。",
              f"- Qdrant 请求方法/路径计数：`{json.dumps(manifest['qdrant_request_counts'], ensure_ascii=False)}`。仅本机 metadata GET 与指定 collection 的只读 query POST，HTTP 钩子拒绝其他方法/路径；同步兼容性探测也纳入守卫。",
              f"- selection 软 cap 回填题数：{sum('selection_caps_relaxed_for_recall' in r['selection_warnings'] for r in records)} / 50；retrieval degraded/unavailable=0；reranker fallback=0；所有题均取得12个不同 Parent。",
              "- 运行控制台出现 Qdrant 后台兼容性探测告警和一次 CUDA 分配重试/OOM 诊断；正式 backend 的版本/集合校验与50题推理均成功，未发生降级或缺失分数，未因此改参数。保留告警为运行限制，不把它当作检索失败。",
              "- 小样本分组只提供定位线索；标准 Parent ID 命中不证明命中了该 Parent 内正确的具体 Child 证据，也不证明生成答案正确。未开展生成、语义评分、聊天交互或 WorldLine 验证。",
              "- 只测本地当前代码/配置绑定指定线上 collection 的链路；不声称远端部署进程环境与本机快照完全一致。未修改线上配置、未写 Qdrant、未修改冻结文件、未 push。",
              "- 本轮停在 Step 2，等待 West 审阅；不包含 Step 5 调参建议。", "",
              "## 验证命令与结果", "",
              "以下命令工作目录为 `ai-service`：", "",
              "- `.venv/Scripts/python.exe -u -m evals.rag_source_baseline`：50/50成功，所有保护文件哈希不变。",
              "- `.venv/Scripts/python.exe -m evals.rag_source_baseline_report`：50题离线复算通过；10600条阶段映射校验通过；四项指标及分组汇总与原始记录完全一致。",
              "- `.venv/Scripts/python.exe -m pytest tests/test_rag_source_baseline.py tests/test_context_rag_source.py tests/test_retrieval_service.py tests/test_retrieval_fusion_qdrant.py tests/test_retrieval_encoding_filters.py tests/test_retrieval_contracts.py tests/test_reranking_service.py tests/test_reranking_runtime.py tests/test_reranking_contracts.py tests/test_retrieval_evaluation.py tests/test_reranking_evaluation.py -q`：97 passed、1 skipped、5 subtests passed。",
              "- 跳过项：`test_real_qdrant_runs_named_dense_and_bm25_legs` 未配置 `QDRANT_INTEGRATION_URL`；它需要创建/写入/删除临时集合，本轮只读红线下未启用。正式collection的双路查询已由50题实跑覆盖。",
              "- `.venv/Scripts/python.exe -m ruff check evals/rag_source_baseline.py evals/rag_source_baseline_report.py tests/test_rag_source_baseline.py`：通过。",
              "- `.venv/Scripts/python.exe -m compileall -q evals/rag_source_baseline.py evals/rag_source_baseline_report.py tests/test_rag_source_baseline.py`：通过。未修改服务或UI，无服务构建/视觉验证范围。", "",
              "产物：`cases.jsonl` 为完整机器记录；`cases.md` 为逐题三阶段表；`metrics.json` 为分组均值；`diagnostics.json` 为环节定位；`validation.json` 为纯确定性复算结果。", ""]
    (OUTPUT / "report.md").write_text("\n".join(lines), encoding="utf-8")
    write_json(OUTPUT / "diagnostics.json", diagnoses)


def generate_cases(records):
    lines = ["# 50题逐题检索记录", "", "完整融合来源与各路 rank/raw_score/weight 见 cases.jsonl 的 channel_evidence；命中仅按冻结 Parent ID 判断。", ""]
    for r in records:
        lines += [f"## {r['case_id']} · {r['answerability']} · {r['question_group']}", "", r["question"], "",
                  f"标准 Parent：{', '.join(r['relevant_parent_ids']) or '空（不作语义可回答性判断）'}。",
                  f"计分：{json.dumps(r['metrics'], ensure_ascii=False)}。", ""]
        for stage, label in (("rrf_top40", "RRF top-40"), ("selection", "selection（Parent collapse + 既有cap）"), ("rerank_top12", "rerank top-12")):
            lines += [f"### {label}", "", "| rank | Child | Parent | source | 来源路 | fusion | rerank | 命中 |",
                      "|---:|---|---|---|---|---:|---:|---|"]
            for row in r[stage]:
                score = f"{row['rerank_score']:.9g}" if row.get("rerank_score") is not None else "—"
                channels = ", ".join(sorted({e["channel"] for e in row["channel_evidence"]}))
                lines.append(f"| {row['rank']} | {row['child_id']} | {row['parent_id']} | {row['source_id']} | "
                             f"{channels} | {row['fusion_score']:.12g} | {score} | {'是' if row['hit'] else ''} |")
            lines += [""]
    (OUTPUT / "cases.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    manifest = json.loads((OUTPUT / "run_manifest.json").read_text(encoding="utf-8"))
    if manifest["status"] != "complete" or manifest["protected_before"] != protected_hashes():
        raise ValueError("baseline_replay_run_incomplete_or_inputs_changed")
    cases, records = read_jsonl(GOLD), read_jsonl(OUTPUT / "cases.jsonl")
    validation = validate_records(cases, records, Projection(read_jsonl(PROJECTION)))
    metrics = summarize(records)
    if metrics != json.loads((OUTPUT / "metrics.json").read_text(encoding="utf-8")):
        raise ValueError("baseline_replay_summary_mismatch")
    validation.update(status="passed", scored_cases=metrics["all_scored"]["case_count"],
                      unscored_cases=sum(metrics["unscored_counts"].values()),
                      protected_unchanged=True, metric_replay="exact_match", unknown_parents=0)
    write_json(OUTPUT / "validation.json", validation)
    write_json(OUTPUT / "metrics_by_answerability.json", metrics["by_answerability"])
    write_json(OUTPUT / "metrics_by_question_group.json", metrics["by_question_group"])
    generate_report(records, metrics, manifest, validation)
    generate_cases(records)
    print(json.dumps({"validation": validation, "all_scored": metrics["all_scored"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
