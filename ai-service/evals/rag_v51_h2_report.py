"""Offline replay and reporting for the clean 27-case Gold V5.1 baseline."""

from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import sys

from evals import rag_v51_h2_baseline as run
from evals.ragas_gate0 import Gate0Guard


def recompute(record, case):
    gold = set(case["relevant_parent_ids"])
    hits40 = gold & {hit["parent_id"] for hit in record["rrf_top40"][:40]}
    hits12 = gold & {hit["parent_id"] for hit in record["rerank_top12"][:12]}
    first = next((i for i, hit in enumerate(record["rerank_top12"][:12], 1) if hit["parent_id"] in gold), None)
    if not gold or case["answerability"] not in {"full", "partial"}:
        raise run.BaselineError("v51_unscorable_row_in_baseline")
    return {"recall_at_40": len(hits40) / len(gold), "recall_at_12": len(hits12) / len(gold),
            "precision_at_12": len(hits12) / 12, "mrr": 1 / first if first else 0}


def same_legs(old, new):
    return [[(hit["point_id"], hit["raw_score"]) for hit in leg["hits"]] for leg in old["leg_results"]] == [
           [(hit["point_id"], hit["raw_score"]) for hit in leg["hits"]] for leg in new["leg_results"]]


def verify():
    from app.rag.query_understanding.models import QueryVariantKind, SearchChannel, SearchPlan
    from app.rag.retrieval.fusion import LegSearchResult, RawSearchHit, weighted_rrf
    from app.rag.retrieval.legs import RetrievalLeg
    from app.rag.retrieval.selection import select_candidates

    cases, _, controls, projection, config, _, _ = run.load_frozen()
    _, baseline = run.h2.stack()
    directory = run.OUTPUT / "retrieval"
    state, records = run.read_json(directory / "run_manifest.json"), run.read_jsonl(directory / "cases.jsonl")
    queries, plans, filters = run.read_json(run.OUTPUT / "frozen_queries.json"), run.read_json(run.OUTPUT / "plans.json"), run.read_json(run.OUTPUT / "filters.json")
    if (state["status"] != "complete" or state["guard_denied"] or state["safety_events"] or len(records) != 27
            or [row["case_id"] for row in records] != [row["case_id"] for row in cases]):
        raise run.BaselineError("v51_incomplete_retrieval_baseline")
    for record, case, query, plan, condition in zip(records, cases, queries, plans, filters, strict=True):
        if (not record["healthy"] or record["answerability"] != case["answerability"]
                or record["relevant_parent_ids"] != case["relevant_parent_ids"] or record["question"] != case["question"]
                or record["plan"] != plan or len(record["leg_results"]) != 7
                or record["search_calls"] != record["rrf_calls"] or record["search_calls"] != 1):
            raise run.BaselineError("v51_retrieval_identity_or_pipeline_mismatch")
        if [(leg["text"], leg["channel"]) for leg in record["leg_results"]] != [(leg["text"], leg["channel"]) for leg in run.expected_legs(query)]:
            raise run.BaselineError("v51_seven_leg_text_or_channel_mismatch")
        raw = []
        points_to_child, independent_scores = {}, {}
        for leg in record["leg_results"]:
            if leg["weight"] != 1 or leg["error_code"] is not None or len(leg["hits"]) > 80:
                raise run.BaselineError("v51_seven_leg_weight_health_limit_mismatch")
            reconstructed = RetrievalLeg(variant_index=leg["variant_index"], variant_kind=QueryVariantKind(leg["variant_kind"]),
                channel=SearchChannel(leg["channel"]), text=leg["text"], weight=leg["weight"])
            raw.append(LegSearchResult(leg=reconstructed, hits=[RawSearchHit(hit["point_id"], hit["raw_score"], projection.children[hit["child_id"]]) for hit in leg["hits"]]))
            seen = set()
            for rank, hit in enumerate(leg["hits"], 1):
                if not run.h2.matches(projection.children[hit["child_id"]], condition["effective_qdrant_filter"]):
                    raise run.BaselineError("v51_filter_violation")
                projection.item(child=hit["child_id"], parent=hit["parent_id"], point=hit["point_id"], relevant=set(case["relevant_parent_ids"]))
                if hit["point_id"] not in seen:
                    point = hit["point_id"]
                    independent_scores[point] = independent_scores.get(point, 0) + 1 / (60 + rank)
                    points_to_child[point] = hit["child_id"]
                    seen.add(point)
        independent_order = sorted(independent_scores, key=lambda point: (-independent_scores[point], point))
        fused = weighted_rrf(raw, k=60)
        for stage, limit in (("rrf_top40", 40), ("selection_input_rrf", 80)):
            if ([hit.payload["chunk_id"] for hit in fused[:limit]] != [hit["child_id"] for hit in record[stage]]
                    or [points_to_child[point] for point in independent_order[:limit]] != [hit["child_id"] for hit in record[stage]]):
                raise run.BaselineError("v51_rrf_order_replay_mismatch")
            if any(not math.isclose(item.fusion_score, saved["fusion_score"], rel_tol=0, abs_tol=1e-15) for item, saved in zip(fused[:limit], record[stage], strict=True)):
                raise run.BaselineError("v51_rrf_score_replay_mismatch")
        selected, warnings = select_candidates(fused[:80], SearchPlan.model_validate(plan), config)
        if ([candidate.primary_child_id for candidate in selected] != [hit["child_id"] for hit in record["selection"]]
                or record["selection_warnings"] != warnings or len(record["rerank_top12"]) != min(12, len(selected))):
            raise run.BaselineError("v51_selection_replay_mismatch")
        scores = [hit["rerank_score"] for hit in record["rerank_top12"]]
        if (any(score is None or not math.isfinite(score) for score in scores) or scores != sorted(scores, reverse=True)
                or not {hit["child_id"] for hit in record["rerank_top12"]} <= {hit["child_id"] for hit in record["selection"]}):
            raise run.BaselineError("v51_rerank_scores_or_provenance_mismatch")
        expected = baseline.score_case(case, record["rrf_top40"], record["selection"], record["rerank_top12"])
        if any(record[key] != value for key, value in expected.items()) or any(record["metrics"][key] != value for key, value in recompute(record, case).items()):
            raise run.BaselineError("v51_step2_metric_replay_mismatch")
    requests = run.read_json(directory / "qdrant_requests.json")
    batches = [request for request in requests if request["path"].endswith("/query/batch")]
    if len(batches) != 27 or any(len(request["queries"]) != 7 or request["status_code"] != 200 for request in batches):
        raise run.BaselineError("v51_wire_query_count_or_status_mismatch")
    for request, query, condition in zip(batches, queries, filters, strict=True):
        if request["case_id"] != query["case_id"] or any(item["filter"] != condition["effective_qdrant_filter"] or item["limit"] != 80 for item in request["queries"]):
            raise run.BaselineError("v51_wire_case_or_filter_mismatch")
        texts = [run.h2.query_document(item["bm25"])["text"] for item in request["queries"] if item["using"] == "bm25"]
        if texts != query["queries"]:
            raise run.BaselineError("v51_wire_bm25_text_mismatch")
    for name in ("collection_before.json", "collection_after.json"):
        if not run.read_json(directory / name)["full_payload_projection_match"]:
            raise run.BaselineError("v51_collection_projection_changed")
    calls = [run.read_json(path) for path in sorted((run.OUTPUT / "expansion/calls").glob("*.json"))]
    by_id = {row["case_id"]: row for row in queries}
    prep = run.read_json(run.OUTPUT / "preparation.json")
    clarify = prep["generation_protocol"].get("constraint_clarification", False)
    for call in calls:
        if call["case_id"] not in run.NEW_IDS or call["request"] != run.generation_body(by_id[call["case_id"]], call["stage"], clarify=clarify) or call["status_code"] != 200:
            raise run.BaselineError("v51_expansion_provider_provenance_mismatch")
    for row in run.read_json(run.OUTPUT / "expansion/new_queries.json"):
        for stage in ("expansion", "hyde"):
            accepted = [entry for entry in row["attempt_history"] if entry["stage"] == stage and entry["accepted"]]
            if len(accepted) != 1:
                raise run.BaselineError("v51_accepted_response_count_mismatch")
            entry = accepted[0]
            if entry.get("seed_record_path"):
                call = run.read_json(run.ROOT / Path(entry["seed_record_path"]).parent / "calls" / f"{entry['call_number']:03}.json")
            else:
                call = calls[entry["call_number"] - 1]
            if call["response"]["choices"][0]["message"]["content"] != row[stage + "_raw"]:
                raise run.BaselineError("v51_accepted_response_text_mismatch")
            original_body = run.h1.request_body(by_id[row["case_id"]], stage)
            clarified_body = run.generation_body(by_id[row["case_id"]], stage, clarify=True)
            if call["request"] not in (original_body, clarified_body):
                raise run.BaselineError("v51_seed_provider_request_mismatch")
    result = {"verified": True, "cases": 27, "legs": 189, "gold_sha256": run.GOLD_SHA,
              "rrf_selection_step2_metrics_replayed": True, "independent_rrf_and_metrics_recomputed": True,
              "expansion_provenance_verified": True, "qdrant_payload_projection_unchanged": True,
              "protected_hashes_unchanged": True, "verification_network_calls": 0, "verification_model_inference_calls": 0}
    run.write_json(run.OUTPUT / "verification.json", result)
    print(json.dumps(result), flush=True)
    return records, cases, controls, result


def reference_comparison(records, cases, controls):
    _, baseline = run.h2.stack()
    old_by_id = {row["case_id"]: row for row in controls}
    rows = []
    for record, case in zip(records, cases, strict=True):
        old = old_by_id.get(case["case_id"])
        rows.append({"case_id": case["case_id"], "question": case["question"], "answerability_v51": case["answerability"],
            "question_group": record["question_group"], "v51_gold_parents": case["relevant_parent_ids"], "v51_metrics": record["metrics"],
            "H2_v5_reference_only": {"answerability": old["answerability"], "gold_parents": old["relevant_parent_ids"], "metrics": old["metrics"]} if old else None,
            "same_original_seven_results": same_legs(old, record) if old else None,
            "saved_H2_trace_rescored_with_v51_gold": recompute(old, case) if old else None,
            "warning": "Gold/sample/denominators changed. Old V5 scores are reference only; do not attribute differences to pipeline improvement."})
    return rows


def generate():
    records, cases, controls, verification = verify()
    _, baseline = run.h2.stack()
    summary, old_summary = baseline.summarize(records), baseline.summarize(controls)
    comparisons = reference_comparison(records, cases, controls)
    case039 = next(row for row in records if row["case_id"] == "rag-v0-039")
    special = next(row for row in comparisons if row["case_id"] == "rag-v0-039")
    special["new_parent_stages"] = [{"parent_id": parent,
        "all_legs": [{"leg_id": leg["leg_id"], **hit} for leg in case039["leg_results"] for hit in leg["hits"] if hit["parent_id"] == parent],
        **{stage: [hit for hit in case039[stage] if hit["parent_id"] == parent] for stage in run.h2.STAGES}}
        for parent in case039["relevant_parent_ids"]]
    run.write_json(run.OUTPUT / "metrics.json", summary)
    run.write_json(run.OUTPUT / "V5_H2_reference_only.json", {"warning": "25 vs 27, changed Gold labels and Parent denominators; not directly comparable", "V5_H2": old_summary, "V51": summary, "per_case": comparisons})
    run.write_json(run.OUTPUT / "039_special.json", special)
    lines = ["# V5.1 检索 baseline：H2 冻结七路管线", "",
             "**27/27 题真实重跑完成：full 17 + partial 10。** 本文是新 V5.1 的干净 baseline。", "",
             f"Gold V5.1 SHA256：`{run.GOLD_SHA}`。计分使用 V5.1 的 relevant_parent_ids，未读取 ground_truth 作为扩写、检索或 rerank 输入。", "",
             "## 固定管线与输入", "",
             "直接调用原 H2 collect_case、原 seven_legs、原三档 filter 编译、原 weighted RRF / select_candidates 和原问题 reranker。3 子查询×dense/BM25 + HyDE dense 共 7 leg；不加 H3 繁体 leg。RRF k=60、candidate=40、每路=80、selection 输入前80、doc cap=4、source cap=5、rerank→12；模型、revision、dense ef=128、BM25 k1=1.2/b=.75、timeout 与配置全等于 H2。", "",
             "共同 24 题复用并重新校验原 H1/H2 冻结 expansion + HyDE。新增题使用 deepseek-chat、temperature=0、原H1系统提示词；续跑请求在用户内容中补充重申已有约束，不新增事实。每次原始响应均保留，扩写只看问题，不接收Gold或检索结果。全部27题在任何检索前冻结。旧031不再可计分；005/045仍不跑，不按结果删换题。", "",
             "前两轮只做扩写，均未检索：第一轮008两阶段和009 expansion通过，009 HyDE三次添加年份被拒绝（6次API）；第二轮009 HyDE通过，但054三次扩写遗漏部分子查询中的索尼，被拒绝（4次API）。原始响应与incomplete状态均保留。正式目录attempt-03-source-reminder复用全部已通过阶段，将原题已有的索尼明确为每条查询必须逐字保留的限定词；054 expansion/HyDE首轮通过（2次API）。合计12次API、6个阶段通过、6个违规响应拒绝。仅重申现有约束，不新增事实、不放宽校验器、不改H2检索。", "",
             "H1 最终全局对象集合覆盖、年份、新人名/实体、作品与限定词约束原样复用；054 的具名来源索尼额外逐字校验并阅读复核，每个子查询均保留。规则失败不得进入冻结输入；每轮固定请求最多3次/阶段的新尝试在调用前登记，SDK重试为0，提供方失败即停。调用次数、原文、失败原因与usage见expansion/。未改resolver、原H1系统提示词或校验器；用户消息中的约束提醒差异已如上记录。", "",
             "## Step 2 计分与分组", "",
             "RRF Child 前40后按 Gold Parent 去重算 R@40；rerank Child 前12后按 Parent 去重算 R@12/P@12，P@12 分母固定12；MRR取前12中首个相关 Child 的排名。按题宏平均；RRF 41–80 不算 R@40。", "",
             "| 分组 | n | R@40 | R@12 | P@12 | MRR |", "|---|---:|---:|---:|---:|---:|"]
    groups = [("全部27", summary["all_scored"])] + [(key, value) for key, value in summary["by_answerability"].items()] + list(summary["by_question_group"].items())
    for name, values in groups:
        lines.append(f"| {name} | {values['case_count']} | " + " | ".join(f"{values[key]:.9f}" for key in baseline.METRICS) + " |")
    other = [row for row in records if row["question_group"] != "timeline"]
    lines.append(f"| 其他题型合计（非timeline） | {len(other)} | " + " | ".join(f"{math.fsum(row['metrics'][key] for row in other)/len(other):.9f}" for key in baseline.METRICS) + " |")
    lines += ["", "## 27 题完整指标", "", "| case | 标签 | 题型 | Gold Parent 数 | R@40 | R@12 | P@12 | MRR |", "|---|---|---|---:|---:|---:|---:|---:|"]
    for row in records:
        lines.append(f"| {row['case_id']} | {row['answerability']} | {row['question_group']} | {len(row['relevant_parent_ids'])} | " + " | ".join(f"{row['metrics'][key]:.9f}" for key in baseline.METRICS) + " |")
    lines += ["", "## 与 V5 的 H2 旧结果对照（仅供参考，不可直接比较）", "",
              "**样本从25题变为27题，full/partial 分组成员与 Gold Parent 集合、分母均有变化。下表不是优化收益。** 旧V5 H2为17 full+8 partial；新V5.1为17 full+10 partial，共同24题，移除031，新增008/009/054。", "",
              "| 口径 | n | R@40 | R@12 | P@12 | MRR |", "|---|---:|---:|---:|---:|---:|"]
    for name, values in (("V5 H2（参考）", old_summary["all_scored"]), ("V5.1 新 baseline", summary["all_scored"])):
        lines.append(f"| {name} | {values['case_count']} | " + " | ".join(f"{values[key]:.9f}" for key in baseline.METRICS) + " |")
    lines += ["", "逐题旧值与新值如下。旧分数保留旧 Gold，新增题显示无 H2 记录。完整 JSON 另含共同24题的旧 trace 按新 Gold 离线复算，供区分标注变动与真实检索变化；它不能替代本轮真实重跑。", "",
              "| case | V5 H2 R@40（参考） | V5.1 R@40 | V5 H2 R@12（参考） | V5.1 R@12 |", "|---|---:|---:|---:|---:|"]
    for row in comparisons:
        old = row["H2_v5_reference_only"]
        old40 = f"{old['metrics']['recall_at_40']:.9f}" if old else "无"
        old12 = f"{old['metrics']['recall_at_12']:.9f}" if old else "无"
        lines.append(f"| {row['case_id']} | {old40} | {row['v51_metrics']['recall_at_40']:.9f} | {old12} | {row['v51_metrics']['recall_at_12']:.9f} |")
    unchanged = sum(row["same_original_seven_results"] is True for row in comparisons)
    lines += ["", f"共同24题中，七路原始有序 point ID 与 raw score 均与旧 H2 相同：{unchanged}/24。所有共同题的计划与三档 filter 均与旧 H2 完全相同。", "",
              "## 039 专项：新绑两个音乐 Parent", "", f"问题：{case039['question']}", "",
              f"旧 V5 H2 R@40=0.000000000；新 V5.1 R@40={case039['metrics']['recall_at_40']:.9f}，R@12={case039['metrics']['recall_at_12']:.9f}。这是新 Gold 下的覆盖情况，不可直接解释为管线改进。", "",
              "| 新 Gold Parent | 七路命中 | RRF40 排名 | selection 排名 | rerank12 排名 |", "|---|---|---|---|---|"]
    for item in special["new_parent_stages"]:
        ranks = lambda stage: ", ".join(str(hit["rank"]) for hit in item[stage]) or "未进入"
        lines.append(f"| `{item['parent_id']}` | {bool(item['all_legs'])} | {ranks('rrf_top40')} | {ranks('selection')} | {ranks('rerank_top12')} |")
    lines += ["", "完整新旧 Parent、逐 leg Child 与各阶段位置见 `039_special.json`；原始27题追踪见 `retrieval/cases.jsonl`。", "",
              "## 扩写与验证边界", "",
              "扩写全文见 `query_bundle_review.md`；API原始请求/响应见 expansion/calls/*.json，仅保存 JSON 请求体，不保存鉴权头或密钥。3题规则检查后的逐条阅读由 Codex 完成，非 West 人工签字；最终语义审阅仍由 West 决定。", "",
              "离线 verify 重放全部189路 RRF顺序/分数、原selection、Step2四项指标及新Gold映射，并独立用基础公式复算RRF和指标。前后Qdrant全量691条payload均与冻结projection一致，未读取向量值。Gold V4/V5/V5.1、生产代码/配置、H1/H2/H3历史产物哈希保持。", "",
              "测试、AST检查及命令见 checks.json / tests.txt / README.md。原有需要写入临时collection的可选集成测试不启用，真实只读流程由本轮27题覆盖。无生产/UI修改，应用构建不适用。", "",
              "只观察本次27题与固定语料，不做显著性或跨语料外推。未push、未合入main。运行结束，停下等待West审阅。", ""]
    (run.OUTPUT / "report.md").write_text("\n".join(lines), encoding="utf-8", newline="\n")
    queries = run.read_json(run.OUTPUT / "frozen_queries.json")
    review_lines = ["# V5.1 冻结扩写：27题", "", "24题复用H1/H2冻结文本，008/009/054新生成。以下全量展开供West终审。未使用Gold答案或Parent辅助生成。", ""]
    for row in queries:
        review_lines += [f"## {row['case_id']}：{row['question']}", "", f"来源：{row['provenance']['kind']}；实体：" + ", ".join(row["constraints"]["entity_ids"]), ""]
        for index, query in enumerate(row["queries"], 1): review_lines.append(f"{index}. {query}")
        review_lines += ["", "HyDE：", "", row["hyde"], ""]
    (run.OUTPUT / "query_bundle_review.md").write_text("\n".join(review_lines), encoding="utf-8", newline="\n")
    print(json.dumps({"report_complete": True, "cases": 27, "all_scored": summary["all_scored"],
                      "039_metrics": case039["metrics"], "shared_raw_legs_identical": unchanged}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("verify", "report"))
    parser.add_argument("--output", type=Path, default=run.OUTPUT)
    args = parser.parse_args()
    run.OUTPUT = args.output.resolve()
    run.h2.configure()
    sys.addaudithook(Gate0Guard().audit)
    if args.command == "verify": verify()
    else: generate()


if __name__ == "__main__":
    main()
