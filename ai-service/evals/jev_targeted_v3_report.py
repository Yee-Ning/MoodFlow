"""Frozen-test quality, latency and failure report for Jev targeted v3 work."""

from __future__ import annotations

from collections import Counter
from math import ceil
from pathlib import Path
from statistics import mean
from typing import Any

from app.agent.foundation.routing import RouteKey
from evals.jev_context_audit_v3 import AUDIT as CONTEXT_AUDIT, RESULTS as CONTEXT_RESULTS
from evals.jev_memory_diagnostic_v3 import DIAGNOSTIC
from evals.jev_memory_filter_v3 import (
    BOUNDARY_AUDIT, CALIBRATION as FILTER_CALIBRATION, TEST_DECISIONS,
)
from evals.jev_memory_retrieval_v3 import CALIBRATION as RETRIEVAL_CALIBRATION, TEST_RESULTS
from evals.jev_shadow_v1 import REPORTS, read_jsonl
from evals.jev_shadow_v2 import ROUTER_RESULTS as ROUTER_V2_RESULTS
from evals.jev_synthetic_memory_v2 import synthetic_memories
from evals.jev_targeted_v3 import ROUTER_CALIBRATION, ROUTER_RESULTS, ROUTES, select_routes

REPORT = REPORTS / "jev_targeted_optimization_v3.md"


def latency(values: list[float | None]) -> dict[str, float | None]:
    ordered = sorted(float(value) for value in values if value is not None)
    if not ordered:
        return {key: None for key in ("avg", "p50", "p95", "p99")}
    return {"avg": mean(ordered),
            "p50": ordered[ceil(.5 * len(ordered)) - 1],
            "p95": ordered[ceil(.95 * len(ordered)) - 1],
            "p99": ordered[ceil(.99 * len(ordered)) - 1]}


def _lat(value: dict[str, float | None]) -> str:
    return " / ".join("n/a" if value[key] is None else f"{value[key]:.1f}"
                      for key in ("avg", "p50", "p95", "p99"))


def _tail(value: dict[str, float | None]) -> str:
    return f"{value['p95']:.1f} / {value['p99']:.1f}"


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _classification(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    single = [row for row in rows if row["task"] == "single_intent"]
    multi = [row for row in rows if row["task"] == "multi_intent"]
    scored = single + multi
    def exact(row: dict[str, Any]) -> bool:
        return set(row[key]) == set(row["gold_routes"])
    tp = fp = fn = 0
    macro = []
    for row in multi:
        gold, predicted = set(row["gold_routes"]), set(row[key])
        t, f_p, f_n = len(gold & predicted), len(predicted - gold), len(gold - predicted)
        tp += t
        fp += f_p
        fn += f_n
        macro.append(2 * t / (2 * t + f_p + f_n) if 2 * t + f_p + f_n else 0)
    return {"single_cases": len(single), "single_correct": sum(exact(row) for row in single),
            "single_accuracy": sum(exact(row) for row in single) / len(single),
            "multi_cases": len(multi), "multi_exact": sum(exact(row) for row in multi),
            "micro_precision": tp / (tp + fp) if tp + fp else 0,
            "micro_recall": tp / (tp + fn) if tp + fn else 0,
            "micro_f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0,
            "macro_f1": mean(macro), "false_positive_routes": fp, "missed_routes": fn,
            "decision_failure_rate": sum(not exact(row) for row in scored) / len(scored)}


def router_metrics() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    all_rows = read_jsonl(ROUTER_RESULTS)
    rows = [row for row in all_rows if row["split"] == "test"]
    if len(all_rows) != 47 or len(rows) != 24:
        raise ValueError("frozen Router test missing")
    policy = read_jsonl(ROUTER_CALIBRATION)[0]
    thresholds = {route: policy["threshold"] +
                  (policy["general_chat_delta"] if route == RouteKey.GENERAL_CHAT.value else 0)
                  for route in ROUTES}
    for row in rows:
        row["jev_routes"] = (select_routes(row["jev_scores"], thresholds, policy["max_routes"])
                             if row["jev_scores"] else [])
    existing = _classification(rows, "baseline_routes")
    jev = _classification(rows, "jev_routes")
    previous = {row["case_id"]: row for row in read_jsonl(ROUTER_V2_RESULTS)}
    previous_single = [previous[row["case_id"]] for row in rows if row["task"] == "single_intent"]
    fallback = sum(row["jev_error"] is not None or not row["jev_routes"] for row in rows)
    result = {"policy": policy, "thresholds": thresholds, "existing": existing, "jev": jev,
              "jev_fallback": fallback, "jev_fallback_rate": fallback / len(rows),
              "jev_api_errors": sum(row["jev_error"] is not None for row in rows),
              "existing_call_errors": sum(row["baseline_error"] is not None for row in rows),
              "existing_fallback": sum(row["baseline_error"] is not None or
                                       not row["baseline_complete"] or not row["baseline_routes"]
                                       for row in rows),
              "existing_total": latency([row["baseline_total_ms"] for row in rows]),
              "jev_api": latency([row["jev_api_ms"] for row in rows]),
              "jev_total": latency([row["jev_total_ms"] for row in rows]),
              "layers": {layer: latency([row["baseline_layer_ms"][layer] for row in rows])
                         for layer in ("rule", "model", "llm")},
              "models": sorted({row["response_model"] for row in rows}),
              "timestamp": rows[0]["evaluation_timestamp"],
              "request_count": Counter(row["jev_request_count"] for row in rows)}
    result["previous_choice_single"] = {
        "correct": sum(row["jev_routes"] == row["gold_routes"] for row in previous_single),
        "cases": len(previous_single),
        "latency": latency([row["jev_latency_ms"] for row in previous_single]),
        "timestamp": previous_single[0]["evaluation_timestamp"],
        "models": sorted({row["response_model"] for row in previous_single}),
    }
    failures = []
    for row in rows:
        if row["task"] == "followup_analysis":
            continue
        if set(row["jev_routes"]) != set(row["gold_routes"]):
            failures.append({"case_id": row["case_id"], "task": row["task"],
                             "query": row["query"], "gold": row["gold_routes"],
                             "existing": row["baseline_routes"], "jev": row["jev_routes"],
                             "scores": row["jev_scores"],
                             "reason": (f"missing={sorted(set(row['gold_routes']) - set(row['jev_routes']))}; "
                                        f"extra={sorted(set(row['jev_routes']) - set(row['gold_routes']))}")})
    return result, failures


def memory_metrics() -> dict[str, Any]:
    rows = read_jsonl(TEST_RESULTS)
    if len(rows) != 49 * 5 * 2:
        raise ValueError("frozen Memory retrieval test missing")
    curves = {}
    for strategy in ("legacy", "hybrid"):
        curves[strategy] = {}
        for k in (3, 5, 8, 10, 20):
            group = [row for row in rows if row["strategy"] == strategy and row["top_k"] == k]
            hits = sum(row["target_id"] in row["candidate_ids"] for row in group)
            curves[strategy][k] = {"hits": hits, "recall": hits / len(group),
                                   "mrr": mean(1 / (row["candidate_ids"].index(row["target_id"]) + 1)
                                               if row["target_id"] in row["candidate_ids"] else 0
                                               for row in group),
                                   "mean_candidates": mean(len(row["candidate_ids"]) for row in group),
                                   "latency": latency([row["retrieval_latency_ms"] for row in group])}
    exact_rows = [row for row in read_jsonl(DIAGNOSTIC) if row["split"] == "test"]
    if len(exact_rows) != 49:
        raise ValueError("exact/HNSW test missing")
    episode_ids = {row["memory_id"] for row in synthetic_memories()
                   if row["memory_type"] == "episodic"}
    # Profile targets have no vector and are excluded from this raw-vector denominator.
    episodic = [row for row in exact_rows if row["target_id"] in episode_ids]
    exact_curves = {k: {mode: sum(row["target_id"] in row[mode][:k] for row in episodic)
                        for mode in ("exact_ids", "planner_ids", "forced_hnsw_ids")}
                    for k in (3, 5, 10, 20)}
    result = {"curves": curves, "episodic_cases": len(episodic),
              "exact_curves": exact_curves,
              "raw_ranking_identical": sum(row["exact_ids"] == row["planner_ids"] ==
                                           row["forced_hnsw_ids"] for row in exact_rows),
              "embedding_dim": {row["query_vector_dim"] for row in exact_rows},
              "dev_calibration": read_jsonl(RETRIEVAL_CALIBRATION)}
    query_norms = [row["query_vector_norm"] for row in exact_rows]
    result["vector_norms"] = {
        "query": (min(query_norms), max(query_norms)),
        "episode": (exact_rows[0]["document_vector_norm_min"],
                    exact_rows[0]["document_vector_norm_max"]),
    }
    decisions = read_jsonl(TEST_DECISIONS)
    selected = [row for row in decisions if row["policy"] == "dev_calibrated"]
    configured = [row for row in decisions if row["policy"] == "configured"]
    if len(selected) != 49 or len(configured) != 49:
        raise ValueError("frozen Memory Filter test missing")
    def filter_score(group: list[dict[str, Any]]) -> dict[str, Any]:
        kept_gold = sum(row["target_kept"] for row in group)
        kept_total = sum(len(row["jev_selected_ids"]) for row in group)
        negatives = sum(len(row["candidate_ids"]) - 1 for row in group)
        kept_negative = kept_total - kept_gold
        return {"key_recall": kept_gold / len(group), "precision": kept_gold / kept_total,
                "false_drop": len(group) - kept_gold,
                "false_keep_rate": kept_negative / negatives,
                "candidate_reduction": 1 - kept_total / sum(len(row["candidate_ids"]) for row in group),
                "fallback_rate": sum(row["fallback_needed"] for row in group) / len(group),
                "api_latency": latency([row["jev_api_ms"] for row in group]),
                "total_latency": latency([row["jev_total_ms"] for row in group]),
                "models": sorted({row["response_model"] for row in group}),
                "timestamp": group[0]["evaluation_timestamp"]}
    result["filter"] = {"configured": filter_score(configured),
                        "dev_calibrated": filter_score(selected),
                        "threshold": read_jsonl(FILTER_CALIBRATION)[0]["threshold"]}
    result["filter_failures"] = [row for row in selected if not row["target_kept"]]
    return result


def context_metrics() -> dict[str, Any]:
    rows = read_jsonl(CONTEXT_RESULTS)
    audit = read_jsonl(CONTEXT_AUDIT)
    if len(rows) != 30 or len(audit) != 9:
        raise ValueError("frozen Context test or audit missing")
    by_policy = {}
    for policy in ("baseline_jev", "optimized"):
        group = [row for row in rows if row["policy"] == policy]
        labels = Counter()
        for row in group:
            selected = set(row["selected_ids"])
            for cid, label in row["gold_labels"].items():
                labels[label] += 1
                labels[f"kept_{label}"] += cid in selected
        by_policy[policy] = {
            "cases": len(group), "required_preservation": labels["kept_required"] / labels["required"],
            "useful_recall": labels["kept_useful"] / labels["useful"],
            "irrelevant_retention": labels["kept_irrelevant"] / labels["irrelevant"],
            "false_drop": labels["useful"] - labels["kept_useful"],
            "token_reduction": 1 - sum(row["selected_tokens"] for row in group) /
            sum(row["baseline_tokens"] for row in group),
            "api_latency": latency([row["jev_api_ms"] for row in group]),
            "total_latency": latency([row["jev_total_ms"] for row in group]),
            "baseline_latency": latency([row["baseline_total_ms"] for row in group]),
            "response_model": sorted({row["response_model"] for row in group}),
            "api_timestamp": group[0]["evaluation_timestamp"],
            "replay_timestamp": group[0]["replay_timestamp"],
            "threshold": group[0]["tool_result_threshold"],
        }
    return {"policies": by_policy, "audit": audit}


def write_report_v3() -> Path:
    router, router_failures = router_metrics()
    memory = memory_metrics()
    context = context_metrics()
    old_r, jev_r = router["existing"], router["jev"]
    old_m, new_m = memory["curves"]["legacy"][8], memory["curves"]["hybrid"][8]
    filter_m = memory["filter"]["dev_calibrated"]
    old_c, new_c = context["policies"]["baseline_jev"], context["policies"]["optimized"]
    boundary = read_jsonl(BOUNDARY_AUDIT)
    if len(boundary) != 4 or len({tuple(row["candidate_ids"]) for row in boundary}) != 1:
        raise ValueError("identical-candidate episodic boundary audit required")
    boundary_scores = [row["score"] for row in boundary]
    lines = [
        "# Jev Targeted Optimization v3",
        "",
        "## Evaluation contract",
        "",
        "- `jev_targeted_v3_splits.jsonl` 在参数搜索前冻结：Router 分任务分层，Memory 按 target ID 奇偶，Context 每个 family 按编号奇偶。所有阈值只在 dev 校准，下面的最终指标只来自 test。Router test 为 16 单意图、7 多意图、1 追问分析；Memory test 为 49 个独立目标；Context test 为 15 个场景。样本仍小，尤其 Router 多意图。",
        "- Jev 请求模型为 `jev-latest`；实际 response model 和 UTC 时间分别见下文及逐 case JSONL。保持 `JEV_SHADOW_MODE=true`。Router 多标签与 Context 阈值策略为 Shadow 评测，未接管生产路由或上下文；Memory Retrieval 的轻量排序已改动生产检索器，但仍走 PostgreSQL profile + pgvector episodic，没有改写入流程或数据库结构。",
        "- 延迟均是同一台本机上真实调用的 wall clock 测量，单位 ms；表中顺序为 avg / P50 / P95 / P99。Router Baseline 可能调用主 LLM fallback，Jev 只进行一次 System One API；下游主 LLM 生成延迟不在本轮。",
        "",
        "## 1. Router：统一单/多意图 Jev Shadow",
        "",
        "旧 single Choice 最多表达一个 route。v3 用一个 shared state `{query}`，在同一次 System One 请求中对四个 RouteKey 同时请求 Noul；每个分数独立过阈值，允许 0/1/2/3 个 route，没有固定 Top-2。dev 在共同阈值、general_chat 增量阈值和 max_routes=2/3/4 的网格上优化单意图 accuracy 与多意图 micro F1 的均值，不接触 test。",
        f"作为设计参照，v2 single Choice 在同一组 16 条单意图 test case 上为 {router['previous_choice_single']['correct']}/16，决策 avg/P50/P95/P99 {_lat(router['previous_choice_single']['latency'])} ms；其 API 时间 {router['previous_choice_single']['timestamp']}、实际模型 {router['previous_choice_single']['models']}。这属于不同时间的旧请求形式，不能当作本次实时 A/B 的低延迟结论；下表主比较为同轮 Existing vs Multi-label Jev。",
        f"冻结策略：threshold={router['policy']['threshold']:.2f}，general_chat 加 {router['policy']['general_chat_delta']:.2f}，max_routes={router['policy']['max_routes']}；四路阈值 {router['thresholds']}。test 每条 Jev request count 分布 {dict(router['request_count'])}。requested_model=jev-latest，response_model={router['models']}，evaluation_timestamp={router['timestamp']}。",
        "",
        "| Router test 指标 | Existing Hybrid | Jev Multi-label |",
        "| --- | ---: | ---: |",
        f"| 单意图 accuracy | {old_r['single_correct']}/{old_r['single_cases']} ({_pct(old_r['single_accuracy'])}) | {jev_r['single_correct']}/{jev_r['single_cases']} ({_pct(jev_r['single_accuracy'])}) |",
        f"| 多意图 exact set | {old_r['multi_exact']}/{old_r['multi_cases']} | {jev_r['multi_exact']}/{jev_r['multi_cases']} |",
        f"| 多意图 micro precision | {_pct(old_r['micro_precision'])} | {_pct(jev_r['micro_precision'])} |",
        f"| 多意图 micro recall | {_pct(old_r['micro_recall'])} | {_pct(jev_r['micro_recall'])} |",
        f"| 多意图 micro F1 / macro F1 | {_pct(old_r['micro_f1'])} / {_pct(old_r['macro_f1'])} | {_pct(jev_r['micro_f1'])} / {_pct(jev_r['macro_f1'])} |",
        f"| 多意图 false positive route / missed route | {old_r['false_positive_routes']} / {old_r['missed_routes']} | {jev_r['false_positive_routes']} / {jev_r['missed_routes']} |",
        f"| 全量 fallback need（含追问） | {router['existing_fallback']}/24 ({_pct(router['existing_fallback'] / 24)}) | {router['jev_fallback']}/24 ({_pct(router['jev_fallback_rate'])}) |",
        f"| 调用错误率（含追问） | {router['existing_call_errors']}/24 | {router['jev_api_errors']}/24 |",
        f"| 单+多意图 decision failure rate | {_pct(old_r['decision_failure_rate'])} | {_pct(jev_r['decision_failure_rate'])} |",
        "",
        "| Router 延迟，ms avg/P50/P95/P99 | 数值 |",
        "| --- | --- |",
        f"| Existing total | {_lat(router['existing_total'])} |",
        f"| Existing rule | {_lat(router['layers']['rule'])} |",
        f"| Existing model | {_lat(router['layers']['model'])} |",
        f"| Existing LLM | {_lat(router['layers']['llm'])} |",
        f"| Jev API | {_lat(router['jev_api'])} |",
        f"| Jev total | {_lat(router['jev_total'])} |",
        "",
        "分层计时是各层在同一请求内的累计执行时间；多 segment 与调度开销使其不能直接相加为 total。两侧在相同 Router test query 上串行执行。",
        f"Jev total P95/P99 为 {_tail(router['jev_total'])} ms，Existing 为 {_tail(router['existing_total'])} ms，分别缩短 { _pct(1 - router['jev_total']['p95'] / router['existing_total']['p95']) } 和 { _pct(1 - router['jev_total']['p99'] / router['existing_total']['p99']) }；但冻结 test 的单意图 accuracy 和多意图 F1 均低于 Existing，目前**不值得替换三层 Router**。不能为了低延迟牺牲当前路由质量。",
        f"Jev test 错误 case 共 {len(router_failures)}，逐项 gold/Existing/Jev/四路分数如下：",
        "",
        "| Case | 输入 | 类型 | Gold | Existing | Jev | 分数 | 原因 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
        *[f"| {row['case_id']} | {row['query']} | {row['task']} | {row['gold']} | {row['existing']} | "
          f"{row['jev']} | {row['scores']} | {row['reason']} |" for row in router_failures],
        "",
        "## 2. Memory Retrieval：先定位上游",
        "",
        "v2 默认 Top-8 的 44/100 不是 Jev Filter 所致：旧 `_profile_relevance` 在没有 scene/intent 时给所有 profile 0.75；profile 与 episodic 混排后，800 个默认候选出现中 episodic 为 0。合成集每个用户有约 23 个 profile；仅按 importance/recency 截取和统一分数不能区分查询主题。",
        f"同一批 test 查询的原始 episodic cosine 诊断共有 {memory['episodic_cases']} 个 episodic 目标；向量维度 {sorted(memory['embedding_dim'])}，query 向量范数范围 {memory['vector_norms']['query'][0]:.4f}–{memory['vector_norms']['query'][1]:.4f}，document {memory['vector_norms']['episode'][0]:.4f}–{memory['vector_norms']['episode'][1]:.4f}。两侧共用 Qwen 1024 维归一化编码（query 加 instruction，document 用原文）；PostgreSQL 索引为 `vector_cosine_ops`、查询排序为 `<=>` 升序。精确扫描、当前 planner 和强制 HNSW 的 Top-K 结果如下；{memory['raw_ranking_identical']}/49 条 test 原始排序完全一致。强制 HNSW 用 `hnsw.ef_search=100` 并禁用 seq/bitmap/sort 的会话设置，经 EXPLAIN 确认索引扫描；小样本未见 ANN 召回损失。",
        "",
        "| episodic raw Recall@K | Exact cosine | Planner | Forced HNSW |",
        "| --- | ---: | ---: | ---: |",
        *[f"| @{k} | {memory['exact_curves'][k]['exact_ids']}/{memory['episodic_cases']} | "
          f"{memory['exact_curves'][k]['planner_ids']}/{memory['episodic_cases']} | "
          f"{memory['exact_curves'][k]['forced_hnsw_ids']}/{memory['episodic_cases']} |"
          for k in (3, 5, 10, 20)],
        "",
        "优化：profile 用已有 structured subject/topic/scene 的精确命中与内容字符二元组重叠评分；明显询问旧经历的 query 给 episodic semantic 分数有限加成，仍保留 importance/recency、user/status/type/scene 过滤与原 Top-K。未把 Memory 搬到 Qdrant，未调 HNSW 或改 schema。冲突旧事实在 v2 合成数据中仍标为 active，这是数据真实性限制。",
        "",
        "| Test Recall@K / MRR | 旧 Retrieval | 新 Retrieval |",
        "| --- | ---: | ---: |",
        *[f"| @{k} | {memory['curves']['legacy'][k]['hits']}/49 "
          f"({_pct(memory['curves']['legacy'][k]['recall'])}); MRR {memory['curves']['legacy'][k]['mrr']:.3f} | "
          f"{memory['curves']['hybrid'][k]['hits']}/49 "
          f"({_pct(memory['curves']['hybrid'][k]['recall'])}); MRR {memory['curves']['hybrid'][k]['mrr']:.3f} |"
          for k in (3, 5, 8, 10, 20)],
        "",
        f"Top-8 平均候选数两边均为 {old_m['mean_candidates']:.1f}；旧检索 avg/P50/P95/P99 {_lat(old_m['latency'])} ms，新检索 {_lat(new_m['latency'])} ms。新策略 P95 略降、P99 从 {old_m['latency']['p99']:.1f} 升到 {new_m['latency']['p99']:.1f} ms，不能声称尾延迟全面改善。dev 上新策略 Top-5=51/51，但冻结 test Top-5=44/49：漏掉的恰是 5 条 current conflict profile，`max(top_k*4,20)` 的 SQL profile 预取在 K=5 时只取前 20，目标根本未进入应用重排。当前不据 test 调预取参数，维持生产 Top-8；test Top-8=49/49。原始 SQL 的 exact/HNSW 诊断与最终 profile+episodic merge 不能混作同一个 Recall。",
        "",
        "## 3. Jev Memory Filter：同候选集 Stage B",
        "",
        f"在新检索 Top-8 的 49 条 test 上，Baseline 全保留的 Key Memory Recall=100%、Precision=12.5%、False Keep=100%。dev 校准仍选择现有阈值 {memory['filter']['threshold']:.2f}，未修改 Jev instruction/threshold 配置。Jev test：Key Memory Recall {_pct(filter_m['key_recall'])}、Precision {_pct(filter_m['precision'])}、False Drop={filter_m['false_drop']}、False Keep {_pct(filter_m['false_keep_rate'])}、Candidate Reduction {_pct(filter_m['candidate_reduction'])}、fallback {_pct(filter_m['fallback_rate'])}。API latency avg/P50/P95/P99 {_lat(filter_m['api_latency'])} ms；Filter total {_lat(filter_m['total_latency'])} ms。requested_model=jev-latest，response_model={filter_m['models']}，evaluation_timestamp={filter_m['timestamp']}。",
        f"旧 episodic-only 探针 `memory-v2-episodic_only_probe-120` 的牙医目标确实是该查询的 gold，旧分数 {boundary_scores[0]:.2f} 被阈值 0.5 删除；它不是 retrieval miss。v3 dev 的相同 query/候选 ID 得到 0.53，另对原始候选集连续三次重复得到 {boundary_scores[1:]}，response_model 均为 jev-1.13.0。该项是 threshold 边界上的 Jev 分数漂移/误判，不是 Gold 或跨信息推理问题。dev 主集阈值 0.5 的 Precision 优于 0.45，但单条关键记忆存在重复误删；本轮不据已见 test 再调阈值，继续 Shadow。",
        f"冻结 test 的 Jev 关键误删 {len(memory['filter_failures'])} 项。",
        "",
        "## 4. Context：9 个 false drop 的定向审计",
        "",
        "v2 的 9 项全部是 `tool_result`：6 项 `source-index` 仅报告“已找到来源索引”，没有实际 ID、URL 或证据，原 useful gold 过宽，审计后改为 irrelevant；3 项 `version` 明确指出录音室/现场版，对用户任务有用，保留 useful。不是 RAG relevance 与 Context usefulness 的等价错误，也没有 required 被删除。逐项审计：",
        "",
        "| Case | 分组 | Item | 旧/新 Gold | Jev 分数 | 分类 |",
        "| --- | --- | --- | --- | ---: | --- |",
        *[f"| {row['case_id']} | {row['split']} | {row['context_id']} | "
          f"{row['original_gold']}/{row['audited_gold']} | {row['jev_probability']:.2f} | "
          f"{row['classification']} |" for row in context["audit"]],
        "",
        f"dev 仅调整 `tool_result` 阈值并冻结为 {new_c['threshold']:.2f}，其他 source 仍 0.50；required 仍直接 bypass Jev。以下 test 数字按审计后的同一 Gold、同一 v2 Jev API 分数确定性重放，未再次调用 API；API 时间为 {new_c['api_timestamp']}，策略重放时间为 {new_c['replay_timestamp']}，response_model={new_c['response_model']}。",
        "",
        "| Context test | 原 Jev threshold 0.50 | tool_result threshold 调整后 |",
        "| --- | ---: | ---: |",
        f"| Required Preservation | {_pct(old_c['required_preservation'])} | {_pct(new_c['required_preservation'])} |",
        f"| Useful Recall | {_pct(old_c['useful_recall'])} | {_pct(new_c['useful_recall'])} |",
        f"| Irrelevant Retention | {_pct(old_c['irrelevant_retention'])} | {_pct(new_c['irrelevant_retention'])} |",
        f"| False Drop | {old_c['false_drop']} | {new_c['false_drop']} |",
        f"| Token Reduction | {_pct(old_c['token_reduction'])} | {_pct(new_c['token_reduction'])} |",
        f"| Jev API latency ms avg/P50/P95/P99 | {_lat(old_c['api_latency'])} | {_lat(new_c['api_latency'])} |",
        f"| Jev total latency ms avg/P50/P95/P99 | {_lat(old_c['total_latency'])} | {_lat(new_c['total_latency'])} |",
        f"| Existing Select latency ms avg/P50/P95/P99 | {_lat(old_c['baseline_latency'])} | {_lat(new_c['baseline_latency'])} |",
        "",
        "调整后仍误删 `context-v2-mixed-06` 的 `tool_result:version`（Jev 分数 0.38）；该项说明 0.45 的 dev 最优阈值尚不能安全覆盖所有版本消歧结果。不能根据这个 test case 再降低阈值并重算同一 test。",
        "Context 调整仅在 Shadow 评测重放，生产 `Context Select` 阈值配置未改变；下一步是否写入生产配置取决于更广的人工 Gold 与稳定复评。",
        "",
        "## 5. Decision Matrix",
        "",
        "| 模块 | Quality | Performance | 判断 |",
        "| --- | --- | --- | --- |",
        f"| Router | 单意图 {_pct(jev_r['single_accuracy'])} vs {_pct(old_r['single_accuracy'])}；多意图 F1 {_pct(jev_r['micro_f1'])} vs {_pct(old_r['micro_f1'])} | P95/P99 Jev {_tail(router['jev_total'])} vs Existing {_tail(router['existing_total'])} ms | 继续 Shadow；当前不值得替换三层 Router |",
        f"| Memory Retrieval | Top-8 {new_m['hits']}/49 vs {old_m['hits']}/49，已达本合成 test 100% | P95/P99 新 {_tail(new_m['latency'])} vs 旧 {_tail(old_m['latency'])} ms | 本数据集可接受；P99 略增，真实用户长历史待验证 |",
        f"| Memory Filter | Key Recall {_pct(filter_m['key_recall'])}，false drop {filter_m['false_drop']} | 额外 P95/P99 {_tail(filter_m['total_latency'])} ms | 继续 Shadow；边界分数漂移和付费延迟仍需观察 |",
        f"| Context | Required {_pct(new_c['required_preservation'])}，Useful {_pct(new_c['useful_recall'])} | 额外 API P95/P99 {_tail(new_c['api_latency'])} ms；token 降 {_pct(new_c['token_reduction'])} | 继续 Shadow；仍有 1 项 useful 误删 |",
        "",
        "## 限制与复现",
        "",
        "- 全部 Memory 目标与部分 Context 场景为合成/规则 Gold，缺少真实长期用户历史和双人独立复核。v2 的 conflict former memory 被合成脚本标成 active；真实 superseded/inactive 过滤仍由现有存储合同保证，本轮未改写入流程。",
        "- Memory 的 exact/HNSW 实验只有每用户约 10 条 episodic、整个表 30 条合成 episodic；它排除了此次样本的 ANN 根因，不能证明大规模索引完全没有 ANN 误差。检索端 profile SQL 预截断在更大用户历史上仍有风险。Memory 按目标 ID 奇偶拆分让 conflict 的 former/episodic 都落在 dev、current/profile 都落在 test，造成分布偏移；本轮保留已冻结 split 并如实报告，不再重新划分追求更好数字。",
        "- Router test 仅 7 条多意图，阈值基于 15 单/7 多 dev 校准；2 条追问路径仍未消费 recent_context。v3 仍复用 v2 案例，Router Noul 文案受到 v2 总体失效现象启发；冻结 test 只用于本轮参数选择后的报告，不能视为从未见过的外部基准。Context 阈值重放沿用 v2 一次 API score，不代表重新请求时分数完全一致。",
        "- 完整原始结果、分组、dev 校准和审计 JSONL 均在 `evals/datasets` 与 `evals/reports`；各组件独立判断，没有合成 Jev 总分。",
        "",
    ]
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    return REPORT
