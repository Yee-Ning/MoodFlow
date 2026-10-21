"""Validate and report the saved eight-case Gate 1 run, without network/models."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
import math
from zoneinfo import ZoneInfo

from evals.ragas_gate0 import GOLD, PROJECTION, STEP2, prepare_sample, protected_hashes, read_jsonl, write_json
from evals.ragas_smoke_v5 import CASE_IDS, LABELS, OUTPUT


METRIC_KEYS = ("faithfulness", "factual_correctness(mode=f1)")


def summarize_calls(calls):
    summary = {"http_attempts": len(calls), "successful_responses": 0, "failed_or_pending": 0,
               "reported_usage_responses": 0, "input_tokens": 0, "output_tokens": 0,
               "total_tokens": 0, "cached_input_tokens": 0}
    for call in calls:
        success = call.get("status_code") == 200
        summary["successful_responses" if success else "failed_or_pending"] += 1
        usage = call.get("usage")
        if usage is None:
            continue
        summary["reported_usage_responses"] += 1
        summary["input_tokens"] += usage.get("input_tokens", usage.get("prompt_tokens", 0))
        summary["output_tokens"] += usage.get("output_tokens", usage.get("completion_tokens", 0))
        summary["total_tokens"] += usage["total_tokens"]
        summary["cached_input_tokens"] += usage.get("prompt_cache_hit_tokens", usage.get("input_tokens_details", {}).get("cached_tokens", 0))
    return summary


def load_calls(directory):
    return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(directory.glob("[0-9][0-9][0-9].json"))]


def validate(inputs, answers, scores, generation, judge, before):
    if tuple(r["case_id"] for r in inputs) != CASE_IDS or Counter(r["answerability"] for r in inputs) != LABELS:
        raise ValueError("gate1_report_population_mismatch")
    if tuple(r["case_id"] for r in answers) != CASE_IDS or tuple(r["case_id"] for r in scores) != CASE_IDS:
        raise ValueError("gate1_report_answer_or_score_population_mismatch")
    if before != protected_hashes():
        raise ValueError("gate1_report_protected_files_changed")
    gold, baseline, projection = read_jsonl(GOLD), read_jsonl(STEP2 / "cases.jsonl"), read_jsonl(PROJECTION)
    raw_by_id = {r["case_id"]: r for r in inputs}
    if len(generation) < 8 or len(judge) < 16:
        raise ValueError("gate1_report_incomplete_call_trace")
    for raw, answer, score in zip(inputs, answers, scores, strict=True):
        expected, provenance = prepare_sample(gold, baseline, projection, raw["case_id"])
        for key in ("question", "ground_truth", "contexts"):
            if raw[key] != expected[key] or answer[key] != raw[key]:
                raise ValueError("gate1_report_gold_or_context_drift")
        if raw["answer"] is not None or not answer["answer"].strip():
            raise ValueError("gate1_report_missing_generated_answer")
        if [p["content_sha256"] for p in raw["provenance"]] != [p["content_sha256"] for p in provenance]:
            raise ValueError("gate1_report_context_provenance_mismatch")
        if set(score["scores"]) != set(METRIC_KEYS):
            raise ValueError("gate1_report_metric_columns_mismatch")
        for value in score["scores"].values():
            if value is not None and (not math.isfinite(value) or not 0 <= value <= 1):
                raise ValueError("gate1_report_score_out_of_range")
    for call in [*generation, *judge]:
        if call["case_id"] not in CASE_IDS or call["request"].get("temperature") != 0:
            raise ValueError("gate1_report_actual_request_scope_or_temperature_mismatch")
        if call.get("offline") or call.get("status_code") != 200 or not call.get("usage"):
            raise ValueError("gate1_report_incomplete_provider_response")
        usage = call["usage"]
        left, right = usage.get("input_tokens", usage.get("prompt_tokens")), usage.get("output_tokens", usage.get("completion_tokens"))
        if not isinstance(left, int) or not isinstance(right, int) or left + right != usage["total_tokens"]:
            raise ValueError("gate1_report_provider_usage_invalid")
        if "headers" in call or "authorization" in call:
            raise ValueError("gate1_report_exported_headers")
        if call["stage"] == "generation":
            payload = json.loads(call["request"]["input"][0]["content"])
            raw = raw_by_id[call["case_id"]]
            if [s["excerpt"] for s in payload["sources"]] != list(dict.fromkeys(raw["contexts"])):
                raise ValueError("gate1_report_generation_evidence_mismatch")
        elif call["stage"] == "judge":
            if call["request"]["model"] != "deepseek-chat" or call["metric"] not in {"faithfulness", "factual_correctness"}:
                raise ValueError("gate1_report_judge_config_mismatch")
        else:
            raise ValueError("gate1_report_unknown_stage")
    if {r["case_id"] for r in generation} != set(CASE_IDS) or {r["case_id"] for r in judge} != set(CASE_IDS):
        raise ValueError("gate1_report_calls_missing_case")
    return {"status": "passed", "cases": 8, "contexts_verified": 96,
            "protected_unchanged": True, "all_actual_temperatures_zero": True,
            "only_selected_metrics": True, "nan_cells": sum(v is None for r in scores for v in r["scores"].values()),
            "http_responses_and_usage_verified": len(generation) + len(judge)}


def exported_reasons(judge):
    result = defaultdict(lambda: defaultdict(list))
    for call in judge:
        for choice in call["response"].get("choices", []):
            message = choice["message"]
            content = message.get("content")
            try:
                parsed = json.loads(content)
            except (TypeError, ValueError):
                parsed = None
            result[call["case_id"]][call["metric"]].append({
                "call_number": call["call_number"], "finish_reason": choice.get("finish_reason"),
                "raw_content": content, "parsed": parsed, "usage": call["usage"]})
    return dict(result)


def replay_metric_scores(rows, judge):
    """Recompute legacy defaults from the saved NLI verdicts, no new judging."""
    replay = {}
    for row in rows:
        evidence = defaultdict(list)
        for call in judge:
            if call["case_id"] != row["case_id"]:
                continue
            reply = json.loads(call["response"]["choices"][0]["message"]["content"])
            statements = reply.get("statements")
            if not statements or not isinstance(statements[0], dict) or "verdict" not in statements[0]:
                continue  # Extraction/decomposition calls are not NLI verdicts.
            text = call["request"]["messages"][-1]["content"]
            value, _ = json.JSONDecoder().raw_decode(text.rsplit("input: ", 1)[1].lstrip())
            evidence[call["metric"]].append((value["context"], [bool(s["verdict"]) for s in statements]))
        if len(evidence["faithfulness"]) != 1 or len(evidence["factual_correctness"]) != 2:
            raise ValueError("gate1_metric_replay_nli_population_mismatch")
        context, faithful = evidence["faithfulness"][0]
        if context != "\n".join(row["contexts"]):
            raise ValueError("gate1_metric_replay_faithfulness_context_mismatch")
        faithful_score = sum(faithful) / len(faithful)
        by_context = dict(evidence["factual_correctness"])
        if set(by_context) != {row["ground_truth"], row["answer"]}:
            raise ValueError("gate1_metric_replay_factual_context_mismatch")
        answer_verdicts = by_context[row["ground_truth"]]
        reference_verdicts = by_context[row["answer"]]
        tp = sum(answer_verdicts)
        fp = len(answer_verdicts) - tp
        fn = len(reference_verdicts) - sum(reference_verdicts)
        # Ragas' fbeta_score(beta=1) and np.round(..., 2).
        factual_score = round(2 * tp / (2 * tp + fp + fn + 1e-8), 2)
        if (not math.isclose(faithful_score, row["faithfulness"], abs_tol=1e-12)
                or factual_score != row["factual_correctness(mode=f1)"]):
            raise ValueError("gate1_metric_replay_score_mismatch")
        replay[row["case_id"]] = {"faithfulness": faithful_score, "faithful_claims": sum(faithful),
            "extracted_answer_claims": len(faithful), "factual_correctness(mode=f1)": factual_score,
            "tp": tp, "fp": fp, "fn": fn}
    return replay


def main():
    preparation = json.loads((OUTPUT / "preparation.json").read_text(encoding="utf-8"))
    from evals.ragas_gate0 import AI_ROOT, sha256
    if any(sha256(AI_ROOT / path) != digest for path, digest in preparation["source_hashes"].items()):
        raise ValueError("gate1_report_production_generation_code_changed")
    inputs = json.loads((OUTPUT / "inputs.json").read_text(encoding="utf-8"))
    answers = [json.loads((OUTPUT / "answers" / (case_id + ".json")).read_text(encoding="utf-8")) for case_id in CASE_IDS]
    scores = json.loads((OUTPUT / "scores.json").read_text(encoding="utf-8"))
    generation, judge = load_calls(OUTPUT / "generation_calls"), load_calls(OUTPUT / "judge_calls")
    validation = validate(inputs, answers, scores, generation, judge, preparation["protected_before"])
    usage = {"generation": summarize_calls(generation), "judge": summarize_calls(judge),
             "total": summarize_calls([*generation, *judge]),
             "per_case": {case_id: {"generation": summarize_calls([c for c in generation if c["case_id"] == case_id]),
                                    "judge": summarize_calls([c for c in judge if c["case_id"] == case_id])} for case_id in CASE_IDS}}
    means = {key: {"valid_cases": sum(r["scores"][key] is not None for r in scores),
                   "mean": (math.fsum(r["scores"][key] for r in scores if r["scores"][key] is not None)
                            / sum(r["scores"][key] is not None for r in scores))
                   if any(r["scores"][key] is not None for r in scores) else None} for key in METRIC_KEYS}
    rows = [{"case_id": a["case_id"], "answerability": a["answerability"],
             **{k: a[k] for k in ("question", "ground_truth", "contexts", "answer")},
             "generation_status": a.get("generation_status", "success"),
             "generation_failure": a.get("generation_failure"),
             **s["scores"], "insufficient_faithfulness_note": s["insufficient_faithfulness_note"]}
            for a, s in zip(answers, scores, strict=True)]
    write_json(OUTPUT / "metric_replay.json", replay_metric_scores(rows, judge))
    validation["metric_replay_from_raw_nli"] = "exact_match_all_16_scores"
    (OUTPUT / "eight_cases.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False, allow_nan=False) + "\n" for r in rows), encoding="utf-8")
    write_json(OUTPUT / "judge_reasons.json", exported_reasons(judge))
    write_json(OUTPUT / "usage.json", usage)
    insufficient_review = [{"case_id": r["case_id"], "raw_faithfulness": r["faithfulness"],
        "delivered_answer_kind": "service_failure" if r["generation_status"] != "success" else "generated_narrative",
        "generation_status": r["generation_status"],
        "interpretation": "No evidence-based answer was delivered: production validation returned a service-failure message. Do not treat a low/undefined faithfulness of that message as proof of hallucination or penalize an honest abstention. Raw scores are unchanged; honest-refusal delivery is not verified."}
        for r in rows if r["answerability"] == "insufficient"]
    write_json(OUTPUT / "insufficient_review.json", insufficient_review)
    write_json(OUTPUT / "validation.json", validation)
    write_json(OUTPUT / "judge_config.json", {"ragas_version": "0.4.3", "api": "legacy evaluate",
        "evaluate_batches": 1, "rows": 8, "metrics": ["Faithfulness()", "FactualCorrectness()"],
        "llm_wrapper": "LangchainLLMWrapper", "bypass_temperature": True,
        "chat_model": "deepseek-chat", "base_url": "https://api.deepseek.com/v1", "temperature": 0,
        "actual_request_temperatures": sorted({c["request"]["temperature"] for c in judge}),
        "run_config": {"timeout": 180, "max_workers": 1, "max_retries": 1},
        "chat_openai_max_retries": 0, "raise_exceptions": False,
        "embeddings": None, "provenance": "Summary of the executed evaluator code and recorded outbound requests."})
    failures = [r["case_id"] for r in rows if r["generation_status"] != "success"]
    write_json(OUTPUT / "summary.json", {"raw_eight_case_means": means, "case_count": 8,
                "means_include_service_failure_messages": True,
                "generation_failed_cases": failures, "all_generations_passed": not failures,
                "labels": LABELS, "not_population_estimates": True, "all_cells_finite": validation["nan_cells"] == 0})
    report(rows, generation, judge, usage, validation, means)
    cases_document(rows, exported_reasons(judge))
    artifact_hashes = {p.relative_to(OUTPUT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                       for p in sorted(OUTPUT.rglob("*")) if p.is_file() and p.name != "completion.json"}
    write_json(OUTPUT / "completion.json", {"timestamp": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
                "status": "completed_with_generation_failures" if failures else "completed_with_nan" if validation["nan_cells"] else "complete",
                "gate": 1, "step4_started": False, "protected_after": protected_hashes(), "artifact_hashes": artifact_hashes})
    print(json.dumps({"validation": validation, "usage": {k: usage[k] for k in ("generation", "judge", "total")}, "means": means}))


def report(rows, generation, judge, usage, validation, means):
    passed_generation = sum(r["generation_status"] == "success" for r in rows)
    lines = ["# Step 3 Gate 1 · Gold V5 Ragas 八题生成层 smoke", "",
             f"**8题测量已完成；正式生成校验{passed_generation}/8题通过，{8 - passed_generation}题失败。不能认定生成链路全部通过。** 两指标原始分数、判分理由及真实用量均已保存；没有扩大到50题。", "",
             "West 已在聊天中批准本 Gate 的 DeepSeek 调用，并单独批准本次在内存中读取 ai-service/.env 的 LLM 配置与密钥。密钥未输出、未落盘、未提交。", "",
             "## 范围与生成方式", "",
             "固定4 full + 2 partial + 2 insufficient，共8题；名单在任何真实调用之前写入 inputs.json/preparation.json，运行后未换题。Gold V4/V5、projection、Step 2记录及生产requirements/venv配置前后哈希一致。",
             "",
             "生成调用的是现有 `StructuredWorldLineGenerator.generate_narrative → ProviderWorldLineRuntime → create_generation_response`，使用线上 `NARRATIVE_SYSTEM_PROMPT`、`NarrativeDraft` schema、`evidence_row` 引用和既有 grounding/repair。这里只调用正式生成阶段，不运行聊天路由、query planner、WorldLine检索/建图/分支管线，不查音乐目录或Qdrant。",
             "",
             "生成fixture的 root_entity 来自本 release registry 中的 artist:jay_chou；catalog_facts为空，graph只有该根节点、无边。唯一替换的数据源是RagSource：用Step 2该题rerank_top12的12段 `projection.payload.content` 原始Child文本重放。没有改写、追加Parent原文、补充Gold答案或按answerability提示模型。系统提示和引用格式直接复用代码，没有为此smoke新写生成指令。",
             "",
             "评分数据的 contexts 始终保留12段原文。正式context装配器会按原文去重：允许完全相同文本保留第一条；HTTP边界逐题核对所有独有文本和顺序都完整保留，没有截断独有文本。实际送入sources数量见逐题表。此smoke验证固定证据下的生成和评分，不能等同完整线上路由/实体图流程。", "",
             "## 评分配置与结果", "",
             "`.venv-ragas` / ragas==0.4.3 / legacy `evaluate()`仅执行一次8行批次；只使用 `Faithfulness()` + `FactualCorrectness()` 默认F1。没有使用AnswerCorrectness、answer_relevancy或embeddings。",
             "",
             "judge为 `LangchainLLMWrapper(ChatOpenAI(model=\"deepseek-chat\", base_url=\"https://api.deepseek.com/v1\", temperature=0), bypass_temperature=True)`。HTTP请求钩子在发送前校验实际JSON的temperature严格为0、model为deepseek-chat；所有真实generation/judge请求也均为0。没有只检查构造参数。",
             "",
             "| case | answerability | question | 生成状态 | 原contexts/送入sources | faithfulness | factual_correctness(F1) |",
             "|---|---|---|---|---:|---:|---:|"]
    for row in rows:
        calls = [c for c in generation if c["case_id"] == row["case_id"]]
        sent = json.loads(calls[-1]["request"]["input"][0]["content"])
        values = ["NaN" if row[k] is None else f"{row[k]:.6f}" for k in METRIC_KEYS]
        lines.append(f"| {row['case_id']} | {row['answerability']} | {row['question']} | {row['generation_status']} | 12/{len(sent['sources'])} | " + " | ".join(values) + " |")
    failures = [r for r in rows if r["generation_status"] != "success"]
    if failures:
        lines += ["", "**本次smoke暴露了生成失败，不能声称8题生成链路全部通过。** 以下题目已经用完既有结构校验重试，answer取线上图层既有的 `NARRATIVE_UNAVAILABLE_MESSAGE`，未编辑被拒绝的输出、未改提示词或校验、未换题、未额外重抽。评分保留失败文案的原始结果，并单列生成状态：", ""]
        for row in failures:
            lines.append(f"- {row['case_id']}：{json.dumps(row['generation_failure'], ensure_ascii=False)}；answer={row['answer']}")
    lines += ["", "原始8题均值（仅smoke描述，不外推50题）：", ""]
    for key in METRIC_KEYS:
        lines.append(f"- {key}：{means[key]['mean']:.6f}；有效单元格 {means[key]['valid_cases']}/8，包含服务失败文案的原始评分。")
    lines += ["", "逐题question/ground_truth/contexts/answer与分数见 `eight_cases.jsonl`；完整输入与出处见 `inputs.json`。每次judge的请求和原始返回保存在 `judge_calls/NNN.json`，按题和指标整理的原始判定/理由见 `judge_reasons.json`，没有另调模型生成理由。", "",
              "## insufficient两题单独检查", "",
              "以下保留Ragas原始faithfulness，不把诚实拒答自动算成错误，也不人工改分。若无可抽取事实导致NaN，按不可评分处理，不填0或1；生成内容的越界事实仍按原始判定审阅。", ""]
    for row in rows:
        if row["answerability"] == "insufficient":
            lines += [f"### {row['case_id']} · faithfulness={row['faithfulness']}", "", row["question"], "",
                      row["answer"], "", "本题交付的是生产校验失败后的服务文案，未交付基于证据的答案。低分/NaN不能直接当作幻觉，也不能据此扣诚实拒答的分；原始分数保留，不人工填1。真实模型曾尝试说明证据不足，原始尝试见generation_calls，但未通过引用/schema校验，诚实拒答的最终交付路径未验证通过。", ""]
    lines += ["## 实际调用与token", "",
              "按HTTP实际尝试次数计数，包含正式结构校验需要的重试；模型初始化、离线mock和本地校验不计入。token取provider原始usage，未使用字符估算。缓存输入token是输入token的子集，不再加到total。", "",
              "| 阶段 | HTTP次数 | HTTP 200 | 输入tokens | 输出tokens | 总tokens | 缓存输入tokens |",
              "|---|---:|---:|---:|---:|---:|---:|"]
    for key in ("generation", "judge", "total"):
        u = usage[key]
        lines.append(f"| {key} | {u['http_attempts']} | {u['successful_responses']} | {u['input_tokens']} | {u['output_tokens']} | {u['total_tokens']} | {u['cached_input_tokens']} |")
    lines += ["", "逐题/阶段用量见 `usage.json`。没有取得账单级货币费用，不将token估算伪称实际扣费。generation沿用已有配置的deepseek-flash；judge固定deepseek-chat。", "",
              "## 验证与限制", "",
              f"- 冻结来源复核：8题/96段Child文本逐字及SHA256匹配；温度、请求范围、provider usage均复核通过。16项分数按原始NLI verdict离线复算完全一致；详见metric_replay.json。指标NaN单元格：{validation['nan_cells']}。",
              "- `python -m evals.ragas_smoke_v5 dry-run-generate`：8题mock通过，真实API 0；使用现有生成装配/校验流程。",
              "- 完整命令、隔离/生产解释器选择、unittest、Ruff、compileall及provider记录离线复核结果见 validation_checks.json。",
              "- 生成中断后，评测器只读取并重新校验已经保存的失败响应，将线上既有失败文案补齐到answer，然后继续未运行题目。没有对失败题追加API重跑；每题最多使用生产原有的2次生成尝试。",
              "- 本轮不做生成质量调参或改提示词。小样本与LLM judge评分存在波动；未验证全量50题、聊天路由、检索、音乐目录、WorldLine深度管线或UI。",
              "- 原文命中与faithfulness只判断上下文支持，不证明外部事实完整；FactualCorrectness对照冻结reference，不能代替人的证据审核。",
              "- 所有改动仅为评测脚本、测试和本次产物；未修改生产实现、Gold V4/V5、Qdrant或配置。未push，未开始Step 4，停下等待West审阅。", ""]
    (OUTPUT / "report.md").write_text("\n".join(lines), encoding="utf-8")


def cases_document(rows, reasons):
    lines = ["# 八题四列、分数和judge原始理由", "",
             "contexts是原始Step 2 top-12 Child文本；以下未添加或改写判分理由。", ""]
    for row in rows:
        lines += [f"## {row['case_id']} · {row['answerability']}", "",
                  f"生成状态：{row['generation_status']}", "", "### question", "", row["question"], "",
                  "### ground_truth", "", row["ground_truth"], "", "### contexts", ""]
        for rank, context in enumerate(row["contexts"], 1):
            lines += [f"#### Child排名 {rank}", "", context, ""]
        lines += ["### answer", "", row["answer"], "", "### 原始分数", "",
                  json.dumps({key: row[key] for key in METRIC_KEYS}, ensure_ascii=False), "",
                  "### judge原始返回（含判定理由）", ""]
        for metric, calls in reasons[row["case_id"]].items():
            lines += [f"#### {metric}", ""]
            for call in calls:
                lines += [f"调用 {call['call_number']}，finish_reason={call['finish_reason']}：", "", "```json",
                          json.dumps(call["parsed"], ensure_ascii=False, indent=2) if call["parsed"] is not None
                          else str(call["raw_content"]), "```", ""]
    (OUTPUT / "cases.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
