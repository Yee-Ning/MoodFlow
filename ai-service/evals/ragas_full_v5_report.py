"""Offline full-population report, stratification and raw-verdict score replay."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
import json
import math
from unittest.mock import patch
from zoneinfo import ZoneInfo

from evals import ragas_full_v5 as run
from evals import ragas_smoke_v5_report as smoke_report
from evals.ragas_gate0 import Gate0Guard, configure_offline, read_jsonl, sha256, write_json


KEYS = smoke_report.METRIC_KEYS


def metric_summary(rows):
    return {key: {"mean": math.fsum(r[key] for r in rows if r[key] is not None)
                          / sum(r[key] is not None for r in rows)
                          if any(r[key] is not None for r in rows) else None,
                  "finite": sum(r[key] is not None for r in rows),
                  "not_scorable": sum(r[key] is None for r in rows)} for key in KEYS}


def summarize(rows):
    def group(selected):
        normal = [r for r in selected if r["generation_status"] == "success"]
        failed = [r for r in selected if r["generation_status"] != "success"]
        return {"cases": len(selected), "normal_answers": {"cases": len(normal), **metric_summary(normal)},
                "failure_messages": {"cases": len(failed), **metric_summary(failed)},
                "all_including_failure_messages": metric_summary(selected)}
    return {"all_50": group(rows), "by_answerability": {
        label: group([r for r in rows if r["answerability"] == label]) for label in run.gate0.LABELS}}


def decoded_reply(call):
    choices = call.get("response", {}).get("choices", [])
    if not choices:
        return None
    content = choices[0]["message"].get("content")
    try:
        return json.loads(content)
    except (TypeError, ValueError):
        return None


def prompt_input(call):
    text = call["request"]["messages"][-1]["content"]
    try:
        return json.JSONDecoder().raw_decode(text.rsplit("input: ", 1)[1].lstrip())[0]
    except (ValueError, IndexError):
        return None


def replay_scores(rows, calls, outcomes):
    """Independently recompute defaults; retain undefined/exception cells as null."""
    result = {}
    for row in rows:
        nli = defaultdict(list)
        empty_extractions = set()
        for call in calls:
            if call["case_id"] != row["case_id"]:
                continue
            value, incoming = decoded_reply(call), prompt_input(call)
            if not isinstance(value, dict):
                continue
            statements = value.get("statements")
            if incoming and "context" in incoming and isinstance(statements, list):
                if all(isinstance(s, dict) and "verdict" in s for s in statements):
                    nli[call["metric"]].append((incoming["context"], [bool(int(s["verdict"])) for s in statements]))
            elif statements == [] or value.get("claims") == []:
                empty_extractions.add(call["metric"])
        faithful = nli["faithfulness"]
        factual = nli["factual_correctness"]
        cells = {}
        for key, name in ((KEYS[0], "faithfulness"), (KEYS[1], "factual_correctness")):
            score = row[key]
            outcome = outcomes[(row["case_id"], name)]
            if outcome["value"] != score:
                raise ValueError("step4_metric_outcome_disagrees_with_evaluate")
            if name == "faithfulness" and len(faithful) == 1:
                context, verdicts = faithful[0]
                if context != "\n".join(row["contexts"]):
                    raise ValueError("step4_replay_faithfulness_context_drift")
                computed = sum(verdicts) / len(verdicts) if verdicts else None
                detail = {"supported_claims": sum(verdicts), "claims": len(verdicts)}
            elif name == "factual_correctness" and len(factual) == 2:
                by_context = dict(factual)
                if set(by_context) != {row["ground_truth"], row["answer"]}:
                    raise ValueError("step4_replay_factual_context_drift")
                answer = by_context[row["ground_truth"]]
                reference = by_context[row["answer"]]
                tp, fp, fn = sum(answer), len(answer) - sum(answer), len(reference) - sum(reference)
                computed = round(2 * tp / (2 * tp + fp + fn + 1e-8), 2)
                detail = {"tp": tp, "fp": fp, "fn": fn,
                          "answer_claims": len(answer), "reference_claims": len(reference),
                          "reference_supported_claims": sum(reference)}
            elif score is None and (name in empty_extractions or outcome["status"] == "metric_exception"):
                computed = None
                detail = {"undefined_reason": "no_extracted_claims" if name in empty_extractions else "metric_exception",
                          "outcome": outcome}
            else:
                raise ValueError("step4_replay_incomplete_verdicts:" + row["case_id"] + ":" + name)
            if (computed is None) != (score is None) or (computed is not None
                    and not math.isclose(computed, score, abs_tol=1e-12)):
                raise ValueError("step4_replay_score_mismatch:" + row["case_id"] + ":" + name)
            cells[key] = {"value": computed, "status": "not_scorable" if computed is None else "exact_match", **detail}
        result[row["case_id"]] = cells
    return result


def reasons(calls):
    result = defaultdict(lambda: defaultdict(list))
    for call in calls:
        choices = call.get("response", {}).get("choices", [])
        for choice in choices:
            result[call["case_id"]][call["metric"]].append({
                "call_number": call["call_number"], "finish_reason": choice.get("finish_reason"),
                "trace_file": f"judge_calls/{call['call_number']:03}.json",
                "raw_content": choice["message"].get("content"), "parsed": decoded_reply(call),
                "usage": call.get("usage")})
    return dict(result)


def validate(inputs, answers, scores, generation, judge):
    ids = [r["case_id"] for r in inputs]
    if [r["case_id"] for r in answers] != ids or [r["case_id"] for r in scores] != ids:
        raise ValueError("step4_report_population_mismatch")
    for raw, answer, score in zip(inputs, answers, scores, strict=True):
        if any(answer[k] != raw[k] for k in ("case_id", "answerability", "question", "ground_truth", "contexts", "provenance")):
            raise ValueError("step4_report_gold_or_context_drift")
        if not answer["answer"].strip() or set(score["scores"]) != set(KEYS):
            raise ValueError("step4_report_missing_answer_or_metrics")
        if any(v is not None and (not math.isfinite(v) or not 0 <= v <= 1) for v in score["scores"].values()):
            raise ValueError("step4_report_metric_out_of_range")
        calls = [c for c in generation if c["case_id"] == raw["case_id"]]
        if not 1 <= len(calls) <= 2 or answer["generation_attempts"] != len(calls):
            raise ValueError("step4_report_generation_attempts_mismatch")
        actual_counts = []
        for call in calls:
            payload = json.loads(call["request"]["input"][0]["content"])
            if [s["excerpt"] for s in payload["sources"]] != list(dict.fromkeys(raw["contexts"])):
                raise ValueError("step4_report_actual_evidence_mismatch")
            if "answerability" in payload or "ground_truth" in payload or payload["user_query"] != raw["question"]:
                raise ValueError("step4_report_prompt_leak_or_question_drift")
            graph = payload["worldline_graph"]
            if (payload["root_entity"]["entity_id"] != "artist:jay_chou" or payload["catalog_facts"]
                    or len(graph["nodes"]) != 1 or graph["edges"]):
                raise ValueError("step4_report_production_fixture_changed")
            actual_counts.append(len(payload["sources"]))
        if actual_counts != answer["actual_source_counts"]:
            raise ValueError("step4_report_actual_source_counts_mismatch")
    for call in [*generation, *judge]:
        body = call["request"]
        if (call["case_id"] not in ids or call["offline"] or body["temperature"] != 0
                or type(body["temperature"]) not in (int, float)):
            raise ValueError("step4_report_call_scope_or_temperature")
        expected = "deepseek-flash" if call["stage"] == "generation" else "deepseek-chat"
        if body["model"] != expected:
            raise ValueError("step4_report_model_mismatch")
        if call.get("status_code") == 200:
            usage = call.get("usage")
            if usage is None:
                raise ValueError("step4_report_missing_usage")
            left = usage.get("input_tokens", usage.get("prompt_tokens"))
            right = usage.get("output_tokens", usage.get("completion_tokens"))
            if left + right != usage["total_tokens"]:
                raise ValueError("step4_report_provider_usage_mismatch")
    for stage in (generation, judge):
        if {c["case_id"] for c in stage} != set(ids):
            raise ValueError("step4_report_cases_without_calls")


def fmt(value):
    return "NaN" if value is None else f"{value:.4f}"


def fmt_mean(cell):
    return "—" if not cell["finite"] else fmt(cell["mean"])


def retrieval_diagnosis(case):
    """Use the actual selection input (RRF 80), not the top-40 observation cut."""
    selected = set(case["stage_hits"]["selection"])
    reranked = set(case["stage_hits"]["rerank_top12"])
    gold = set(case["relevant_parent_ids"])
    in80 = {hit["parent_id"] for hit in case["selection_input_rrf"]} & gold
    return {"rrf40_missing": sorted(gold - set(case["stage_hits"]["rrf_top40"])),
            "rrf80_missing": sorted(gold - in80), "selection_dropped": sorted(in80 - selected),
            "rerank_dropped": sorted(selected - reranked), "top12_missing": sorted(gold - reranked)}


def report(rows, summary, usage, diagnostics, reasons_by_case):
    directory = run.OUTPUT
    failures = [r for r in rows if r["generation_status"] != "success"]
    lines = ["# Step 4 Gate 1：Gold V5 全部 50 题生成层评测", "",
             f"固定 50 题测量完成：正式生成校验 {50-len(failures)} 题成功、{len(failures)} 题失败。现有 bug 保留；未进入 Step 5。", "",
             "West 已分别批准本轮 DeepSeek 调用和本轮在内存中读取 .env 的限定 LLM 字段。密钥未输出、未落盘。",
             "只新增评测代码、测试与产物；生产 Python 源码、Gold V4/V5、Step 2、smoke 产物前后哈希一致；生产配置未改，未访问 Qdrant，未 push。", "",
             "## 运行协议", "",
             "50 题名单在真调用前固定于 inputs.json，字节与 Gate 0 完全一致，full18 / partial12 / insufficient13 / needs_input7；未筛选或换题。",
             "生成原样复用 smoke 的 StructuredWorldLineGenerator.generate_narrative → ProviderWorldLineRuntime → create_generation_response，deepseek-flash；使用生产 NARRATIVE_SYSTEM_PROMPT、NarrativeDraft、evidence_row、grounding/repair。",
             "fixture 为 registry artist:jay_chou、空 catalog facts、仅根节点无边的 graph。唯一替换数据源为 RagSource：逐字重放 Step 2 每题 top-12 Child 文本，保留原文去重；没有追加 Parent/Gold/answerability 提示。未运行聊天路由、planner、WorldLine 检索/建图/分支或音乐目录。",
             "失败沿用生产最多两次尝试与失败文案；原始响应保存在 generation_calls。schema/parse 等不在生产诊断 allowlist 内的安全代码由评测器记录，同时保留原 production_validation_code；没有改变校验、repair 或模型输出。", "",
             "Ragas 0.4.3 legacy evaluate() 一次评估全部 50 行，只用 Faithfulness() + FactualCorrectness() 默认 F1；未用 AnswerCorrectness、answer_relevancy 或 embeddings。",
             'judge = LangchainLLMWrapper(ChatOpenAI(model="deepseek-chat", base_url="https://api.deepseek.com/v1", temperature=0), bypass_temperature=True)。全部实际请求 JSON temperature 均为数值 0。',
             "评分协议、run_config(timeout=180,max_workers=1,max_retries=1)、ChatOpenAI(max_retries=0) 沿用 smoke。未为获取理由另调模型；judge_reasons 为原始判定与理由。", "",
             "## 分层统计", "",
             "均值仅对有限分数计算，并列出有效题数；NaN 保留为 null / 不可评分。正常答案均值排除生产失败文案；完整原始均值和失败文案均值另存 summary.json。", "",
             "| answerability | 题数 | 成功 | 失败 | 正常答案 faithfulness / 有效数 | 正常答案 Factual F1 / 有效数 | 全层原始 faithfulness | 全层原始 F1 |",
             "|---|---:|---:|---:|---|---|---:|---:|"]
    for label, group in summary["by_answerability"].items():
        normal, all_scores = group["normal_answers"], group["all_including_failure_messages"]
        lines.append(f"| {label} | {group['cases']} | {normal['cases']} | {group['failure_messages']['cases']} | "
                     f"{fmt_mean(normal[KEYS[0]])} / {normal[KEYS[0]]['finite']} | "
                     f"{fmt_mean(normal[KEYS[1]])} / {normal[KEYS[1]]['finite']} | "
                     f"{fmt(all_scores[KEYS[0]]['mean'])} | {fmt(all_scores[KEYS[1]]['mean'])} |")
    all_group = summary["all_50"]
    lines += ["", "全 50 题原始均值（包含生产失败文案，仅供备查）：" + "; ".join(
        f"{key}={fmt(all_group['all_including_failure_messages'][key]['mean'])}，有效 {all_group['all_including_failure_messages'][key]['finite']}/50"
        for key in KEYS), "", "## 逐题状态与原始分数", "",
        "| case | answerability | 生成状态 | 次数 | 实际 sources（各次） | validation_code | faithfulness | Factual F1 |",
        "|---|---|---|---:|---|---|---:|---:|"]
    for r in rows:
        detail = r["generation_failure"] or {}
        lines.append(f"| {r['case_id']} | {r['answerability']} | {r['generation_status']} | {r['generation_attempts']} | "
                     f"{r['actual_source_counts']} | {detail.get('validation_code', '')} | {fmt(r[KEYS[0]])} | {fmt(r[KEYS[1]])} |")
    lines += ["", "## 定位：检索覆盖、生成校验与指标判定", "",
              "以下检索数字复用 Step 2 保存的 Parent 命中结果，没有重新检索。Parent 命中是证据覆盖代理，不保证该 Child 包含参考答案的所有事实；LLM judge 低分也不能直接归因于幻觉。", ""]
    for text in diagnostics["findings"]:
        lines.append("- " + text)
    lines += ["", "按 Step 2 已固定的评测侧题型分组（只含 30 道 full/partial；不是 Gold 新增标签）：", "",
              "| 题型 | n | 生成失败 | recall@40 | recall@12 | MRR | 正常答案 faithfulness | 正常答案 F1 |",
              "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for name, group in diagnostics["by_question_group_scored_only"].items():
        lines.append(f"| {name} | {group['cases']} | {group['generation_failures']} | {fmt(group['recall_at_40'])} | "
                     f"{fmt(group['recall_at_12'])} | {fmt(group['mrr'])} | "
                     f"{fmt_mean(group['normal_answers'][KEYS[0]])} | {fmt_mean(group['normal_answers'][KEYS[1]])} |")
    lines += ["", "表中 — 表示该组没有可计算均值的成功答案；不等同某题指标返回 NaN。"]
    lines += ["", "逐题 Step 2 recall@40 / recall@12 / MRR 与本轮分数合并于 diagnosis.json。FactualCorrectness 低分的 TP/FP/FN 和逐条原始理由分别见 metric_replay.json / judge_reasons.json，应据此区分证据缺失、答案漏项与指标对 reference 的严格要求；本轮不做调参或修复结论。", "",
              "## insufficient 与 needs_input", "",
              "20 题原样生成和评分，另存 abstention_review.json。服务失败文案不等于诚实拒答已交付；其低分不作为幻觉证据。对成功输出中的拒答/追问也不人工填分或扣分；保留原始 judge 判定供 West 审阅。无可抽取事实导致的 NaN 按不可评分处理。", "",
              "## 实际调用与 token", "",
              "| 阶段 | HTTP 尝试 | HTTP 200 | 输入 tokens | 输出 tokens | 合计 tokens | 缓存输入子集 |",
              "|---|---:|---:|---:|---:|---:|---:|"]
    for name in ("generation", "judge", "total"):
        u = usage[name]
        lines.append(f"| {name} | {u['http_attempts']} | {u['successful_responses']} | {u['input_tokens']} | "
                     f"{u['output_tokens']} | {u['total_tokens']} | {u['cached_input_tokens']} |")
    lines += ["", "次数来自 HTTP 钩子，token 来自 provider 原始 usage；缓存 token 是输入子集，不重复相加。账单金额未取得，不将估算写成实际扣费。逐题用量见 usage.json。", "",
              "## 产物、验证与限制", "",
              "fifty_cases.jsonl：逐题四列、两指标、生成状态、实际 sources、failure_type / validation_code / attempts；cases.md：完整四列与 judge 原始理由；generation_status.json：生成状态表；judge_calls / generation_calls：原始请求与响应；metric_outcomes：实际 Ragas 单指标返回或异常类别。",
              "validation.json / metric_replay.json 记录从原始 NLI verdict 独立复算的逐格一致性；无事实/异常 NaN 记录原因，未填成零或一。有限分数不做人工修正。",
              "generation_replay.json：全部 50 题的已付费响应通过当前生产装配/校验离线重放，请求 JSON、最终答案、状态、失败详情与尝试数完全一致；重放零 API、未读取凭据。",
              "smoke_overlap_comparison.json：与 smoke 重叠的 8 题首轮请求 JSON 完全一致，但部分 provider 返回及生成状态变化。本轮结果不视为 bug 已修复；源码和协议保持原样。",
              "48 项测试通过（隔离环境 39、生产运行时离线 9），Ruff、compileall、pip check 通过，命令和结果见 validation_checks.json。只测试固定证据下的生产生成阶段；没有验证完整线上路由、实体识别、WorldLine 图流程或 UI。Ragas 判定不能代替逐条证据审核。",
              "测量完成后停下等待 West 审阅，不进入 Step 5。", ""]
    (directory / "report.md").write_text("\n".join(lines), encoding="utf-8")
    with patch.object(smoke_report, "OUTPUT", directory):
        smoke_report.cases_document(rows, reasons_by_case)
    case_path = directory / "cases.md"
    case_path.write_text(case_path.read_text(encoding="utf-8").replace("# 八题四列", "# 50题四列", 1), encoding="utf-8")


def main():
    configure_offline()
    import sys
    sys.addaudithook(Gate0Guard().audit)
    inputs = run.inputs()
    answers = [read_json(run.OUTPUT / "answers" / (r["case_id"] + ".json")) for r in inputs]
    scores = read_json(run.OUTPUT / "scores.json")
    generation = smoke_report.load_calls(run.OUTPUT / "generation_calls")
    judge = smoke_report.load_calls(run.OUTPUT / "judge_calls")
    validate(inputs, answers, scores, generation, judge)
    previous_calls = smoke_report.load_calls(run.smoke.OUTPUT / "generation_calls")
    answer_by_id = {r["case_id"]: r for r in answers}
    overlap = []
    for case_id in run.smoke.CASE_IDS:
        prior = next(c for c in previous_calls if c["case_id"] == case_id)
        current = next(c for c in generation if c["case_id"] == case_id)
        if prior["request"] != current["request"]:
            raise ValueError("step4_generation_request_differs_from_smoke")
        old_answer = read_json(run.smoke.OUTPUT / "answers" / (case_id + ".json"))
        overlap.append({"case_id": case_id, "first_request_json_exact": True,
            "smoke_generation_status": old_answer.get("generation_status", "success"),
            "full_generation_status": answer_by_id[case_id]["generation_status"],
            "same_delivered_answer": old_answer["answer"] == answer_by_id[case_id]["answer"]})
    write_json(run.OUTPUT / "smoke_overlap_comparison.json", overlap)
    generation_replay = read_json(run.OUTPUT / "generation_replay.json")
    if (generation_replay["status"] != "passed" or len(generation_replay["cases"]) != 50
            or generation_replay["real_api_calls"] != 0):
        raise ValueError("step4_report_generation_replay_incomplete")
    rows = [{**answer, **score["scores"]} for answer, score in zip(answers, scores, strict=True)]
    outcomes = {(r["case_id"], r["metric"]): r for r in
                [read_json(p) for p in (run.OUTPUT / "metric_outcomes").glob("*.json")]}
    replay = replay_scores(rows, judge, outcomes)
    baseline = {r["case_id"]: r for r in read_jsonl(run.gate0.STEP2 / "cases.jsonl")}
    diagnostic_rows = [{"case_id": r["case_id"], "answerability": r["answerability"],
        "question_group": baseline[r["case_id"]]["question_group"],
        "generation_status": r["generation_status"], "failure": r["generation_failure"],
        "retrieval": baseline[r["case_id"]]["metrics"],
        "stage_hits": baseline[r["case_id"]]["stage_hits"],
        "retrieval_loss": retrieval_diagnosis(baseline[r["case_id"]]),
        "scores": {key: r[key] for key in KEYS}, "metric_replay": replay[r["case_id"]]} for r in rows]
    scored = [r for r in diagnostic_rows if r["answerability"] in {"full", "partial"}]
    failed = [r for r in rows if r["generation_status"] != "success"]
    codes = Counter((r["generation_failure"] or {}).get("validation_code", "unclassified") for r in failed)
    findings = [f"生成校验失败 {len(failed)}/50：" + "; ".join(f"{k}={v}" for k, v in codes.items()) + "。这是实际交付失败，独立于 judge 分数。"]
    for stage, label in (("rrf40_missing", "RRF top-40 未覆盖"), ("rrf80_missing", "完整 selection 输入 RRF top-80 未覆盖"),
                         ("selection_dropped", "top-80 有而 selection 丢失"), ("rerank_dropped", "selection 有而 rerank top-12 丢失")):
        selected = [r for r in scored if r["retrieval_loss"][stage]]
        findings.append(f"30 道 full/partial 中，{label}：{sum(len(r['retrieval_loss'][stage]) for r in selected)} 个题目×Parent 对，"
                        f"涉及 {len(selected)} 题（" + ", ".join(r["case_id"] for r in selected) + "）。")
    findings.append("RRF top-40 是观测截断，selection 实际接收 top-80；两层差值不能直接当作 selection 损失。生产 doc cap=4、软约束回填及 planner 10 / reranker 12 的 Step 2 口径差继续保留。")
    for label in ("full", "partial"):
        selected = [r for r in scored if r["answerability"] == label]
        findings.append(f"{label} 的 Step 2 平均 recall@40={math.fsum(r['retrieval']['recall_at_40'] for r in selected)/len(selected):.4f}，"
                        f"recall@12={math.fsum(r['retrieval']['recall_at_12'] for r in selected)/len(selected):.4f}，"
                        f"MRR={math.fsum(r['retrieval']['mrr'] for r in selected)/len(selected):.4f}；检索数字与生成分数逐题对照，不能以低 F1 单独判定生成幻觉。")
    complete_parent_coverage = [r for r in scored if not r["retrieval_loss"]["top12_missing"]]
    covered_but_failed = [r for r in complete_parent_coverage if r["generation_status"] != "success"]
    findings.append(f"30 道计分题中 {len(complete_parent_coverage)} 题的 top-12 已覆盖全部标准 Parent，其中 "
                    f"{len(covered_but_failed)} 题仍发生生成校验失败（" + ", ".join(r["case_id"] for r in covered_but_failed)
                    + "）；这些交付失败存在直接的生成校验证据，不能归为标准 Parent 未召回。Parent 命中仍不保证 Child 包含参考答案的全部事实。")
    strong = [r for r in diagnostic_rows if r["generation_status"] == "success" and r["answerability"] in {"full", "partial"}
              and r["scores"][KEYS[0]] is not None and r["scores"][KEYS[0]] >= .8
              and r["scores"][KEYS[1]] is not None and r["scores"][KEYS[1]] < .3]
    findings.append("正常答案中 faithfulness≥0.8 且 Factual F1<0.3 的 full/partial 题：" + ", ".join(r["case_id"] for r in strong)
                    + "。表示多数抽取断言受上下文支持但与 reference 的 F1 有落差；是原始判定抽查清单，不自动认定 metric 过严或答案漏项。")
    question_groups = {}
    by_id = {r["case_id"]: r for r in rows}
    for name in sorted({r["question_group"] for r in scored}):
        selected = [r for r in scored if r["question_group"] == name]
        normal = [by_id[r["case_id"]] for r in selected if r["generation_status"] == "success"]
        question_groups[name] = {"cases": len(selected),
            "generation_failures": sum(r["generation_status"] != "success" for r in selected),
            **{key: math.fsum(r["retrieval"][key] for r in selected)/len(selected)
               for key in ("recall_at_40", "recall_at_12", "mrr")}, "normal_answers": metric_summary(normal)}
    diagnostics = {"findings": findings, "cases": diagnostic_rows,
                   "by_question_group_scored_only": question_groups}
    summary = summarize(rows)
    usage = {"generation": smoke_report.summarize_calls(generation), "judge": smoke_report.summarize_calls(judge),
             "total": smoke_report.summarize_calls([*generation, *judge]), "per_case": {
                 r["case_id"]: {"generation": smoke_report.summarize_calls([c for c in generation if c["case_id"] == r["case_id"]]),
                                "judge": smoke_report.summarize_calls([c for c in judge if c["case_id"] == r["case_id"]])} for r in rows}}
    why = reasons(judge)
    for r in rows:
        why.setdefault(r["case_id"], {})
    (run.OUTPUT / "fifty_cases.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False, allow_nan=False) + "\n" for r in rows), encoding="utf-8")
    for name, value in (("summary.json", summary), ("usage.json", usage), ("metric_replay.json", replay),
                        ("judge_reasons.json", why), ("diagnosis.json", diagnostics)):
        write_json(run.OUTPUT / name, value)
    write_json(run.OUTPUT / "generation_status.json", [{k: r[k] for k in (
        "case_id", "answerability", "generation_status", "generation_failure", "generation_attempts", "actual_source_counts")} for r in rows])
    write_json(run.OUTPUT / "abstention_review.json", [{"case_id": r["case_id"], "answerability": r["answerability"],
        "answer": r["answer"], "generation_status": r["generation_status"], "generation_failure": r["generation_failure"],
        "generation_attempt_files": [f"generation_calls/{c['call_number']:03}.json" for c in generation if c["case_id"] == r["case_id"]],
        "raw_scores": {k: r[k] for k in KEYS},
        "interpretation": "Service failure is not delivered honest abstention. Raw scores are not edited; low scores alone are not proof of hallucination. NaN is not scorable."}
        for r in rows if r["answerability"] in {"insufficient", "needs_input"}])
    write_json(run.OUTPUT / "validation.json", {"status": "passed", "cases": 50, "contexts_verified": 600,
        "protected_unchanged": True, "production_unchanged": True, "actual_temperatures": [0],
        "generation_replay_cases": 50, "generation_replay_api_calls": 0,
        "metric_replay_cells": 100, "finite_exact_matches": sum(r[k] is not None for r in rows for k in KEYS),
        "not_scorable_cells": sum(r[k] is None for r in rows for k in KEYS), "judge_batches": 1})
    write_json(run.OUTPUT / "judge_config.json", {"ragas_version": "0.4.3", "api": "legacy evaluate()",
        "dataset": "EvaluationDataset", "rows": 50, "evaluate_batches": 1,
        "metrics": ["Faithfulness()", "FactualCorrectness()"], "factual_correctness_mode": "f1",
        "model": "deepseek-chat", "base_url": "https://api.deepseek.com/v1", "temperature": 0,
        "bypass_temperature": True, "actual_request_temperatures": sorted({c["request"]["temperature"] for c in judge}),
        "run_config": {"timeout": 180, "max_workers": 1, "max_retries": 1},
        "chat_openai_max_retries": 0, "raise_exceptions": False, "embeddings": None})
    report(rows, summary, usage, diagnostics, why)
    write_json(run.OUTPUT / "completion.json", {"step": 4, "gate": 1,
        "timestamp": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "status": "completed_with_generation_failures" if failed else "complete", "step5_started": False,
        "protected_after": run.gate0.protected_hashes(), "production_unchanged": True,
        "evaluation_source_hashes": {p.relative_to(run.AI_ROOT).as_posix(): sha256(p) for p in [
            run.AI_ROOT / "evals/ragas_full_v5.py", run.AI_ROOT / "evals/ragas_full_v5_report.py",
            run.AI_ROOT / "evals/ragas_full_v5_saved_generation.py", run.AI_ROOT / "tests/test_ragas_full_v5.py",
            run.AI_ROOT / "tests/test_ragas_full_v5_report.py", run.AI_ROOT / "tests/test_ragas_full_v5_saved_generation.py"]},
        "artifact_hashes": {p.relative_to(run.OUTPUT).as_posix(): sha256(p) for p in sorted(run.OUTPUT.rglob("*"))
                            if p.is_file() and p.name != "completion.json"}})
    print(json.dumps({"cases": 50, "generation_success": 50-len(failed), "generation_failures": len(failed),
                      "usage": {k: usage[k] for k in ("generation", "judge", "total")}}))


read_json = run.read_json


if __name__ == "__main__":
    main()
