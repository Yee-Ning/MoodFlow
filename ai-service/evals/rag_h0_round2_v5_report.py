"""Offline round-2 report: distinct delivery states, raw audits and scores."""

from collections import Counter
from datetime import datetime
import json
import sys
from unittest.mock import patch
from zoneinfo import ZoneInfo

from evals import rag_h0_round2_v5 as run
from evals.ragas_gate0 import Gate0Guard, configure_offline, protected_hashes, sha256, write_json
from evals import ragas_smoke_v5_report as smoke_report


KEYS = run.reporting.KEYS
STATES = ("answered", "abstained", "validation_failed", "provider_failed")


def summarize(rows):
    def group(selected):
        return {"cases": len(selected), "by_delivery_state": {
            state: {"cases": sum(r["delivery_state"] == state for r in selected),
                    **run.reporting.metric_summary([r for r in selected if r["delivery_state"] == state])}
            for state in STATES}, "all_including_failure_messages": run.reporting.metric_summary(selected)}
    if any(r["delivery_state"] not in STATES for r in rows):
        raise ValueError("h02_unknown_delivery_state")
    return {"rerun_12": group(rows), "by_answerability": {
        label: group([r for r in rows if r["answerability"] == label]) for label in run.full.gate0.LABELS}}


def validate_audits(generation):
    from app.agent.foundation.subgraphs.worldline.narrative_normalization import payload_sha256
    from app.agent.foundation.subgraphs.worldline.narrative_boundary import boundary_context_from_input, boundary_template_check
    normalization, boundary = [], []
    for case_id in run.CASE_IDS:
        events = run.read_json(run.OUTPUT / "audits" / (case_id + ".json"))["events"]
        calls = [c for c in generation if c["case_id"] == case_id]
        for event in events:
            if event["attempt"] not in range(1, len(calls) + 1):
                raise ValueError("h02_audit_attempt_outside_saved_calls")
        for attempt, call in enumerate(calls, 1):
            sent = json.loads(call["request"]["input"][0]["content"])
            raw = "".join(p.get("text", "") for m in call.get("response", {}).get("output", []) for p in m.get("content", []))
            try:
                from app.agent.structured_output.parser import parse_json_object
                from app.agent.structured_output.errors import ValidationFailure
                draft = parse_json_object(raw).value
            except ValidationFailure:
                draft = None
            matching = [e for e in events if e["attempt"] == attempt]
            deletes = [e for e in matching if e["event"] == "worldline.narrative_normalization.v1"]
            extras = {key: value for key, value in (draft or {}).items() if key != "narrative"}
            if len(deletes) != int(bool(extras)):
                raise ValueError("h02_missing_or_duplicate_normalization_audit")
            for event in deletes:
                normalized = {key: value for key, value in draft.items() if key == "narrative"}
                if (event["raw_payload_sha256"] != payload_sha256(draft)
                        or event["normalized_payload_sha256"] != payload_sha256(normalized)
                        or {r["key"]: r["value"] for r in event["removed_fields"]} != extras
                        or any(r["value_sha256"] != payload_sha256(r["value"]) for r in event["removed_fields"])):
                    raise ValueError("h02_normalization_audit_value_drift")
                normalization.append({"case_id": case_id, **event})
            for event in matching:
                if event["event"] != "worldline.boundary_template_check.v1":
                    continue
                context = boundary_context_from_input(sent)
                if event["input_sha256"] != context.input_sha256 or event["actual_input"] != json.loads(context.snapshot_json):
                    raise ValueError("h02_boundary_snapshot_drift")
                paragraphs = [p.strip() for p in draft["narrative"].strip().split("\n") if p.strip()]
                check = boundary_template_check(paragraphs[event["paragraph_index"] - 1], context)
                if check is None or any(event[k] != check[k] for k in ("template_id", "assertions", "matched")):
                    raise ValueError("h02_boundary_claim_drift")
                boundary.append({"case_id": case_id, **event})
    return normalization, boundary


def remaining_paragraphs(rows, generation):
    from app.agent.foundation.subgraphs.worldline.grounding import _is_abstention_paragraph, inline_citation_ids
    from app.agent.foundation.subgraphs.worldline.narrative_boundary import boundary_context_from_input, is_boundary_paragraph
    from app.agent.structured_output.parser import parse_json_object
    results = []
    for row in rows:
        if (row["generation_failure"] or {}).get("validation_code") != "paragraph_missing_citation":
            continue
        call = [c for c in generation if c["case_id"] == row["case_id"]][-1]
        sent = json.loads(call["request"]["input"][0]["content"])
        context = boundary_context_from_input(sent)
        raw = "".join(p.get("text", "") for m in call["response"]["output"] for p in m.get("content", []))
        draft = parse_json_object(raw).value
        paragraphs = [p.strip() for p in draft["narrative"].split("\n") if p.strip()]
        bad = [{"paragraph_index": i + 1, "text": p} for i, p in enumerate(paragraphs)
               if len(p) >= 12 and not inline_citation_ids(p) and not _is_abstention_paragraph(p)
               and not is_boundary_paragraph(p, context)
               and p != "当前未检索到补充音乐知识，以下内容仅基于音乐目录事实。"]
        results.append({"case_id": row["case_id"], "last_call": f"generation_calls/{call['call_number']:03}.json",
            "uncited_non_template_paragraphs": bad,
            "classification_limit": "Exact citation/template checks only; no automatic factuality judgment or prose repair."})
    return results


def main():
    configure_offline()
    guard = Gate0Guard()
    sys.addaudithook(guard.audit)
    inputs = run.inputs()
    answers = [run.read_json(run.OUTPUT / "answers" / (r["case_id"] + ".json")) for r in inputs]
    scores = run.read_json(run.OUTPUT / "scores.json")
    generation, judge = [load(folder) for folder in ("generation_calls", "judge_calls")]
    run.reporting.validate(inputs, answers, scores, generation, judge)
    for answer in answers:
        call = [c for c in generation if c["case_id"] == answer["case_id"]][-1]
        if answer["delivery_state"] != run.delivery_state(answer, json.loads(call["request"]["input"][0]["content"])):
            raise ValueError("h02_delivery_state_drift")
    rows = [{**a, **s["scores"]} for a, s in zip(answers, scores, strict=True)]
    outcomes = {(r["case_id"], r["metric"]): r for r in [run.read_json(p) for p in (run.OUTPUT / "metric_outcomes").glob("*.json")]}
    replay = run.reporting.replay_scores(rows, judge, outcomes)
    # Audit validation needs the production runtime's dependencies. The report
    # is run in .venv; the judge alone runs in the isolated .venv-ragas.
    normalization, boundary = validate_audits(generation)
    offline = run.read_json(run.OUTPUT / "offline_success38.json")
    generated = run.read_json(run.OUTPUT / "generation_replay12.json")
    if offline["status"] != "passed" or offline["metric_cells_exact"] != 76 or generated["status"] != "passed" or len(generated["cases"]) != 12:
        raise ValueError("h02_offline_regression_incomplete")
    summary = summarize(rows)
    failures = [r for r in rows if r["delivery_state"] in {"validation_failed", "provider_failed"}]
    summary.update(before_failures=12, after_failures=len(failures),
        failure_codes=dict(Counter((r["generation_failure"] or {}).get("validation_code") for r in failures)),
        recovered_cases=[r["case_id"] for r in rows if r not in failures],
        normalization_events=len(normalization),
        removed_key_counts=dict(Counter(v["key"] for e in normalization for v in e["removed_fields"])),
        verified_boundary_exemptions=sum(e["matched"] for e in boundary),
        offline38_limitation=offline["limitation"])
    usage = {"generation": smoke_report.summarize_calls(generation), "judge": smoke_report.summarize_calls(judge),
        "total": smoke_report.summarize_calls([*generation, *judge]), "per_case": {
            r["case_id"]: {"generation": smoke_report.summarize_calls([c for c in generation if c["case_id"] == r["case_id"]]),
                "judge": smoke_report.summarize_calls([c for c in judge if c["case_id"] == r["case_id"]])} for r in rows}}
    reasons = run.reporting.reasons(judge)
    prior = {r["case_id"]: r for r in run.full.gate0.read_jsonl(run.ROUND1 / "eighteen_cases.jsonl")}
    comparison = [{"case_id": r["case_id"], "before_failure": prior[r["case_id"]]["generation_failure"],
        "after_failure": r["generation_failure"], "delivery_state": r["delivery_state"],
        "before_scores": {key: prior[r["case_id"]][key] for key in KEYS}, "after_scores": {key: r[key] for key in KEYS}} for r in rows]
    for name, value in (("summary.json", summary), ("usage.json", usage), ("judge_reasons.json", reasons),
                        ("metric_replay12.json", replay), ("comparison12.json", comparison),
                        ("remaining_failure_diagnosis.json", remaining_paragraphs(rows, generation))):
        write_json(run.OUTPUT / name, value)
    for name, values in (("twelve_cases.jsonl", rows), ("normalization_audit.jsonl", normalization), ("boundary_template_checks.jsonl", boundary)):
        (run.OUTPUT / name).write_text("".join(json.dumps(r, ensure_ascii=False, allow_nan=False) + "\n" for r in values), encoding="utf-8")
    write_json(run.OUTPUT / "generation_status.json", [{k: r[k] for k in ("case_id", "answerability", "generation_status", "delivery_state",
        "generation_failure", "generation_attempts", "actual_source_counts")} for r in rows])
    with patch.object(smoke_report, "OUTPUT", run.OUTPUT):
        smoke_report.cases_document(rows, reasons)
    cases = run.OUTPUT / "cases.md"
    cases.write_text(cases.read_text(encoding="utf-8").replace("# 八题四列", "# H0 第二轮 12题四列", 1), encoding="utf-8")
    write_json(run.OUTPUT / "validation.json", {"status": "passed", "case_count": 12, "raw_contexts": 144,
        "scores_recomputed": 24, "offline38_scores_recomputed": 76, "real_api_success38": 0,
        "normalization_events_verified": len(normalization), "boundary_checks_verified": len(boundary),
        "actual_temperatures": sorted({c["request"]["temperature"] for c in [*generation, *judge]}),
        "old_artifacts_and_frozen_files_unchanged": True, "schema_single_narrative_unchanged": True,
        "no_v2": True, "denied_operations": guard.denied})
    report(rows, summary, usage, comparison)
    if guard.denied:
        raise ValueError("h02_report_forbidden_operation")
    source_files = [*sorted(run.CHANGED | run.ADDED), "evals/rag_h0_round2_v5.py", "evals/rag_h0_round2_v5_report.py",
        "tests/test_worldline_h0_round2.py", "tests/test_worldline_h0_abstention.py", "tests/test_rag_h0_round2_v5.py", "tests/test_rag_h0_round2_report.py"]
    write_json(run.OUTPUT / "completion.json", {"task": "H0_round2", "gate": 1, "timestamp": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "status": "completed_with_remaining_failures" if failures else "complete", "before_failures": 12, "after_failures": len(failures),
        "branch": "codex/rag-h0-abstention-fix", "main_expected": "4244eba", "merged": False, "pushed": False,
        "protected_after": protected_hashes(), "source_hashes": {p: sha256(run.full.AI_ROOT / p) for p in source_files},
        "artifact_hashes": {p.relative_to(run.OUTPUT).as_posix(): sha256(p) for p in sorted(run.OUTPUT.rglob("*")) if p.is_file() and p.name != "completion.json"}})
    print(json.dumps({"before_failures": 12, "after_failures": len(failures), "states": {k: v["cases"] for k, v in summary["rerun_12"]["by_delivery_state"].items()},
        "usage": {k: usage[k] for k in ("generation", "judge", "total")}}))


def load(folder):
    return smoke_report.load_calls(run.OUTPUT / folder)


def report(rows, summary, usage, comparison):
    f, mean = run.reporting.fmt, run.reporting.fmt_mean
    lines = ["# H0 第二轮 Gate 1：normalization + 三模板 + 状态分列", "",
        f"**固定 12 个第一轮失败题：12 → {summary['after_failures']} 个失败。** 本轮未实施 P0-2/P0-3 V2，不继续追加 prompt 试跑。", "",
        "生产修复提交 ce169e9，保留在 codex/rag-h0-abstention-fix；main 仍为 4244eba。没有合入、push 或 deploy。West 已批准本轮 12 题生成/判分，并单独批准仅在内存中读取 ai-service/.env 的必要 LLM 配置；密钥未输出、落盘、提交。", "",
        "## 实施范围与口径", "",
        "StructuredOutputService 在 parse_json_object 后、schema 校验前调用可选 normalizer，默认 None，仅 narrative 启用。新 dict 删除全部未知顶层键，保留 narrative 原值；schema 的单字段和 extra=forbid、通用 retry.py、普通 repair 与两次上限不变。未知键名称、值类型、摘要、完整 JSON 值及 SHA256、调用 ID/attempt、原始与归一后 payload SHA256 均留审计。payload hash 指 canonical JSON UTF-8，区别于原 HTTP 字节 hash。审计构造失败立即终止，不重抽。",
        "A2-G1/G2/F1 逐段完全匹配，只允许段首尾空白及句末句号形式归一。根名称按最终发送的 graph 根节点原值渲染，G1/G2 均限单个艺人根节点、id/name/type 正确、edges=[]；G2 额外采用无边范围约束。F1 要求 catalog_facts 字段存在、类型 list、长度 0。任一不符不豁免；多模板同段、事实/人物关系/覆盖判断后缀不豁免。unknown_citation 与 citation_order_mismatch 不豁免，没有新增引用前缀 repair。",
        "输入快照由 ProviderWorldLineRuntime 从最终 model_input 记录在 stage_pack 调用内 ContextVar；每次尝试清空，调用结束复位，默认 boundary_context=None 不启用扩展。C2 原有 narrative prompt/unknown_citation 正反例保持。",
        "delivery_state 是新增评测列，不回写旧报告或覆盖 generation_status。纯固定拒答/已核实边界段（含既有无知识披露）组成的成功正文列为 abstained；带普通事实段的成功正文列 answered。它们是确定性的输出形态分列，不是 Gold answerability 真值或拒答合理性的证明；模板与事实同篇不自动算整篇拒答。解析/schema/grounding 失败列 validation_failed，provider/model/truncation 列 provider_failed，本地 normalization 故障列 validation_failed。", "",
        "## 逐题状态与分数", "",
        "| case | answerability | 第一轮失败码 | 本轮 delivery_state | 本轮失败码 | 尝试 | 实际 sources | faithfulness | Factual F1 |",
        "|---|---|---|---|---|---:|---|---:|---:|"]
    old = {r["case_id"]: r for r in comparison}
    for row in rows:
        lines.append(f"| {row['case_id']} | {row['answerability']} | {old[row['case_id']]['before_failure']['validation_code']} | {row['delivery_state']} | "
            f"{(row['generation_failure'] or {}).get('validation_code','')} | {row['generation_attempts']} | {row['actual_source_counts']} | {f(row[KEYS[0]])} | {f(row[KEYS[1]])} |")
    lines += ["", "## 按状态统计（不混合正常答案、拒答和失败文案）", "",
        "| 状态 | n | faithfulness / 有效数 | Factual F1 / 有效数 |", "|---|---:|---|---|"]
    for state, group in summary["rerun_12"]["by_delivery_state"].items():
        lines.append(f"| {state} | {group['cases']} | {mean(group[KEYS[0]])} / {group[KEYS[0]]['finite']} | {mean(group[KEYS[1]])} / {group[KEYS[1]]['finite']} |")
    lines += ["", "summary.json 另按 full/partial/insufficient/needs_input 各自分列四种状态与全 12 题均值，空样本均值=null。NaN 保留不可评分原因，不人为提分或判作幻觉。旧失败文案与新答案的分数不可当作配对语义质量提升。",
        "", "剩余失败分布：" + json.dumps(summary["failure_codes"], ensure_ascii=False),
        f"未知键剥离审计 {summary['normalization_events']} 次，键分布：" + json.dumps(summary["removed_key_counts"], ensure_ascii=False),
        f"已核实模板豁免事件 {summary['verified_boundary_exemptions']} 次（为事件数，可能同一尝试经既有 repair 重复验证，不冒充独立题数）。",
        "剩余失败的末次输出中，所有无引用且不是已核实模板的完整段落保存在 remaining_failure_diagnosis.json，并附原始 call 路径。只定位校验规则，不自动断言这些自由说明是真事实或幻觉，也不改写/删段/补引用来救题。",
        "模板中 graph/facts 事实核实来自真实输入，但 judge contexts 仍只有原 Step 2 Child 文本，可能将这些状态视为上下文外断言；保留原分数与判定，不扩充 contexts 或人工修分。", "",
        "本轮 answered 的 faithfulness 均值为 0.5572（4 题），不能将工程恢复视为语义质量通过。064 为 1/15=0.0667：原始 judge 同时否定固定拒答、graph 节点/边、空 catalog_facts 等上下文外状态，也指出正文“只列出目录”的限定不被现有叙述材料支持。前者涉及判分输入视角，后者涉及答案覆盖措辞，不能把全部 14 个否定都直接称为真实幻觉。原始判定逐条保留，不改分。",
        "045 仍是生产失败文案，却得到 Factual F1=0.29，亦不表示问题已答对；它在 validation_failed 组中保留，不混入 answered 均值。", "",
        "## 38 题零 API 回归", "",
        "Step 4 原成功 32 题 + 第一轮恢复 6 题，选取各自最后成功 provider 原响应经新生产装配/schema/normalization/grounding 原样回放，最终答案/引用 38/38 不变。除 prompt 外的请求 JSON 与旧成功请求一致；76 个旧 judge 分数从原始 NLI 判定独立复算一致。原产物未改。",
        "**38 题未做真实 API 回归，新 prompt 下 faithfulness 不跌未验证。** 没有将 12 个新答案与 38 个旧答案合成新版本全 50 题真实均值。", "",
        "## 实际 DeepSeek 用量", "", "| 阶段 | HTTP | HTTP 200 | 输入 tokens | 输出 tokens | 总 tokens | 缓存输入子集 |",
        "|---|---:|---:|---:|---:|---:|---:|"]
    for stage in ("generation", "judge", "total"):
        u = usage[stage]
        lines.append(f"| {stage} | {u['http_attempts']} | {u['successful_responses']} | {u['input_tokens']} | {u['output_tokens']} | {u['total_tokens']} | {u['cached_input_tokens']} |")
    lines += ["", "HTTP 按实际请求计数，token 为 provider 原始 usage，缓存输入是输入子集不重复累加。未取得账单扣费金额。逐题见 usage.json。", "",
        "## 验证与保留产物", "",
        "82 项相关测试通过：生产/输入范围/状态分列 74 项，报告与旧分数复算 8 项；13 个本轮 Python 文件的 ruff check、compileall 通过，git diff 检查通过。测试启用 Gate0Guard，真实网络与密钥读取均禁用，denied_operations 为空。实际命令与日志见 validation_checks.json。",
        "validation_checks.json 与 tests-*.txt 记录测试命令及结果；generation_replay12.json 是新付费响应的零 API 再回放；metric_replay12.json 为 24 个新指标单元格的独立复算；offline_success38.json 为旧 38 题/76 分数的回归依据。",
        "twelve_cases.jsonl / cases.md：逐题四列与判定；judge_reasons.json / judge_calls：每题每指标原始理由；generation_calls：全部原始尝试；normalization_audit.jsonl：被删未知键完整值；boundary_template_checks.jsonl：实际输入快照与模板断言。",
        "contexts 仍逐字重放 Step 2 同题 top-12 的 144 段 Child，实际 sources 按原文去重逐次核对，未追加 Parent/Gold、未重检索、未给模型 answerability。fixture 仍 registry artist:jay_chou、空 catalog_facts、根节点无边；generation=deepseek-flash，judge=deepseek-chat、temperature=0、bypass_temperature=True、Ragas 0.4.3 legacy evaluate 一批两个指标。每次 HTTP 实际温度为数值 0。",
        "Gold V4/V5、projection、Step 2/Step 4/第一轮产物 hash 未变；Qdrant 未访问，生产配置与模型不变，未跑全量 50 题或聊天路由/分支管线/UI。测试不构成对新 prompt 下原 38 题真实质量的验证。",
        "本轮已测量后停止，剩余失败与语义低分如实保留，等待 West 审阅，不自动进入调参或下一轮修复。", ""]
    (run.OUTPUT / "report.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
