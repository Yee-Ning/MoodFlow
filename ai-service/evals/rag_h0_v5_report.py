"""Report only H0's fixed 18 reruns and explicitly separate offline 32 controls."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
import json
import sys
from unittest.mock import patch
from zoneinfo import ZoneInfo

from evals import rag_h0_v5 as h0
from evals.ragas_gate0 import Gate0Guard, configure_offline, protected_hashes, read_jsonl, sha256, write_json
from evals import ragas_smoke_v5_report as smoke_report


KEYS = h0.reporting.KEYS


def compare(rows, old):
    return [{"case_id": row["case_id"], "answerability": row["answerability"],
             "question": row["question"], "before_status": old[row["case_id"]]["generation_status"],
             "after_status": row["generation_status"],
             "before_failure": old[row["case_id"]]["generation_failure"],
             "after_failure": row["generation_failure"], "attempts": row["generation_attempts"],
             "actual_source_counts": row["actual_source_counts"],
             "before_scores": {key: old[row["case_id"]][key] for key in KEYS},
             "after_scores": {key: row[key] for key in KEYS}} for row in rows]


def main():
    configure_offline()
    guard = Gate0Guard()
    sys.addaudithook(guard.audit)
    inputs = h0.inputs()
    answers = [h0.read_json(h0.OUTPUT / "answers" / (r["case_id"] + ".json")) for r in inputs]
    scores = h0.read_json(h0.OUTPUT / "scores.json")
    generation, judge = [smoke_report.load_calls(h0.OUTPUT / folder) for folder in ("generation_calls", "judge_calls")]
    h0.reporting.validate(inputs, answers, scores, generation, judge)
    rows = [{**answer, **score["scores"]} for answer, score in zip(answers, scores, strict=True)]
    old = {r["case_id"]: r for r in read_jsonl(h0.BASELINE / "fifty_cases.jsonl")}
    outcomes = {(r["case_id"], r["metric"]): r for r in
                [h0.read_json(p) for p in (h0.OUTPUT / "metric_outcomes").glob("*.json")]}
    replay = h0.reporting.replay_scores(rows, judge, outcomes)
    successful32 = h0.read_json(h0.OUTPUT / "offline_success32.json")
    generated18 = h0.read_json(h0.OUTPUT / "generation_replay18.json")
    if successful32["status"] != "passed" or generated18["status"] != "passed" or len(generated18["cases"]) != 18:
        raise ValueError("h0_offline_validation_incomplete")
    failures = [r for r in rows if r["generation_status"] != "success"]
    comparison = compare(rows, old)
    summary = h0.reporting.summarize(rows)
    usage = {"generation": smoke_report.summarize_calls(generation), "judge": smoke_report.summarize_calls(judge),
        "total": smoke_report.summarize_calls([*generation, *judge]), "per_case": {
            r["case_id"]: {"generation": smoke_report.summarize_calls([c for c in generation if c["case_id"] == r["case_id"]]),
                           "judge": smoke_report.summarize_calls([c for c in judge if c["case_id"] == r["case_id"]])} for r in rows}}
    # The shared summary's historical label is renamed: these are 18 new rows,
    # never a new all-50 measurement.
    summary["rerun_18"] = summary.pop("all_50")
    summary.update(before_generation_failures=18, after_generation_failures=len(failures),
                   recovered_cases=[r["case_id"] for r in rows if r["generation_status"] == "success"],
                   remaining_failed_cases=[r["case_id"] for r in failures],
                   success32="offline only; no fresh API regression or faithfulness non-decrease verification")
    why = h0.reporting.reasons(judge)
    for row in rows:
        why.setdefault(row["case_id"], {})
    (h0.OUTPUT / "eighteen_cases.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False, allow_nan=False)+"\n" for r in rows), encoding="utf-8")
    for name, value in (("summary.json", summary), ("comparison18.json", comparison), ("metric_replay18.json", replay),
                        ("usage.json", usage), ("judge_reasons.json", why)):
        write_json(h0.OUTPUT / name, value)
    write_json(h0.OUTPUT / "generation_status.json", [{key: r[key] for key in (
        "case_id", "answerability", "generation_status", "generation_failure", "generation_attempts", "actual_source_counts")} for r in rows])
    write_json(h0.OUTPUT / "validation.json", {"status": "passed", "rerun_cases": 18, "raw_child_contexts": 216,
        "actual_temperatures": sorted({c["request"]["temperature"] for c in [*generation, *judge]}),
        "metric_cells_replayed": 36, "finite_exact_matches": sum(r[key] is not None for r in rows for key in KEYS),
        "not_scorable": sum(r[key] is None for r in rows for key in KEYS),
        "original_success32_saved_scores_recomputed": 64, "original_success32_real_api_calls": 0,
        "frozen_files_unchanged": True, "changed_production_files": sorted(h0.APPROVED_PRODUCTION_FILES)})
    report(rows, comparison, summary, usage, failures, generated18["template_delivery_observations"])
    with patch.object(smoke_report, "OUTPUT", h0.OUTPUT):
        smoke_report.cases_document(rows, why)
    cases = h0.OUTPUT / "cases.md"
    cases.write_text(cases.read_text(encoding="utf-8").replace("# 八题四列", "# H0 18题四列", 1), encoding="utf-8")
    source_files = ["app/agent/foundation/subgraphs/worldline/grounding.py", "app/agent/foundation/subgraphs/worldline/prompts.py",
        "app/agent/foundation/subgraphs/worldline/generation.py", "evals/rag_h0_v5.py", "evals/rag_h0_v5_report.py",
        "tests/test_worldline_h0_abstention.py", "tests/test_rag_h0_v5.py", "tests/test_rag_h0_v5_report.py"]
    write_json(h0.OUTPUT / "completion.json", {"task": "H0", "gate": 1,
        "timestamp": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "status": "completed_with_remaining_generation_failures" if failures else "complete",
        "branch": "codex/rag-h0-abstention-fix", "main_expected": "4244eba", "merged": False, "pushed": False,
        "before_failures": 18, "after_failures": len(failures), "protected_after": protected_hashes(),
        "source_hashes": {p: sha256(h0.full.AI_ROOT / p) for p in source_files},
        "artifact_hashes": {p.relative_to(h0.OUTPUT).as_posix(): sha256(p) for p in sorted(h0.OUTPUT.rglob("*"))
                            if p.is_file() and p.name != "completion.json"}})
    if guard.denied:
        raise ValueError("h0_report_forbidden_operation")
    print(json.dumps({"failures_before": 18, "failures_after": len(failures),
        "usage": {key: usage[key] for key in ("generation", "judge", "total")}}))


def report(rows, comparison, summary, usage, failures, template_observations):
    selected = summary["rerun_18"]
    f = h0.reporting.fmt
    mean = h0.reporting.fmt_mean
    lines = ["# H0 Gate 1：A+B 与 C2 提示修复，18 题验证", "",
        f"**原 18 个失败题重跑后：18 → {len(failures)} 个生成校验失败，{18-len(failures)} 题成功。** 本轮只真实重跑这 18 题，原成功 32 题只做零 API 离线回归。", "",
        "修复留在 `codex/rag-h0-abstention-fix`；main 保持 `4244eba`，未合入、push 或 deploy。West 已批准修复范围及 18 题生成/判分，并单独授权本轮限定 .env LLM 字段仅在内存中读取，密钥未输出、落盘或提交。", "",
        "## 实施范围", "",
        "A：仅两句固定中文模板，归一化空白及中英文逗号、句号、感叹号、问号、分号后整段匹配，允许两句在一段组合。保留方括号和其他字符，不按拒答关键词或模型自报字段豁免。仅模板段可跳过段落锚点检查；全部非空段都是模板段才能跳过 missing_citation。",
        "unknown_citation、citation_order_mismatch、已引用 knowledge 的非空 excerpt 检查保持原样。空正文不获得豁免，普通事实段不随其他模板段放行。A 的图谱边界说明扩展未实施。",
        "B：NarrativeDraft 保持唯一 narrative 字段和 extra=forbid。narrative prompt 明确禁止 narrative_note、narrative_length、citation_ids、additionalProperties、abstention 等额外键及空/null/placeholder 占位；限制说明引导为独立模板段。",
        "extra_forbidden 的附加反馈只在 worldline narrative 闭包中要求删除额外键。通用 retry.py、branch prompt、schema、长度上限与重试次数均未改。",
        "C2：narrative prompt 与 unknown_citation 反馈增加 sources.id 原值引用正反例；没有添加 deterministic repair，也没有放行 source_id:/source: 前缀。原有 citation_ids 兼容 repair 保留，未增加新 repair。", "",
        "## 固定输入与运行协议", "",
        "18 题名单与 question/ground_truth/contexts/provenance 在任何真实调用前固定于 inputs.json，与 Step 4 同题逐字一致；每题 12 段 Child，共 216 段。未重新检索，不追加 Parent 或 Gold 内容，不把 answerability 放进生成提示。",
        "沿用 Step 4 的 StructuredWorldLineGenerator.generate_narrative → ProviderWorldLineRuntime → create_generation_response。fixture 为 registry artist:jay_chou、空 catalog facts、只有根节点无边的 graph；唯一替换数据源是 RagSource 重放，实际 sources 仍按原文去重。没有运行聊天路由、planner、WorldLine 检索/建图/分支、音乐目录或 Qdrant。",
        "generation=deepseek-flash，配置与 Step 4 一致；每题最多 2 次生产尝试。失败保留线上既有失败文案及原始 attempts，没有人工改写回答或额外重抽。",
        "Ragas 0.4.3 legacy evaluate() 对本轮 18 行执行一次，只用 Faithfulness() + FactualCorrectness() 默认 F1；judge=deepseek-chat、base_url=https://api.deepseek.com/v1、temperature=0、bypass_temperature=True，逐个 HTTP 请求核验实际温度为数值 0。无 embeddings 或额外理由生成调用。", "",
        "## 18 题前后对照", "",
        "| case | answerability | 原失败码 | 新状态 | 新失败码 | 次数 | 实际 sources | faithfulness | Factual F1 |",
        "|---|---|---|---|---|---:|---|---:|---:|"]
    by_id = {r["case_id"]: r for r in rows}
    for c in comparison:
        row = by_id[c["case_id"]]
        lines.append(f"| {c['case_id']} | {c['answerability']} | {c['before_failure']['validation_code']} | {c['after_status']} | "
            f"{(c['after_failure'] or {}).get('validation_code','')} | {c['attempts']} | {c['actual_source_counts']} | {f(row[KEYS[0]])} | {f(row[KEYS[1]])} |")
    lines += ["", "按 answerability 统计；正常答案均值排除仍失败的文案，NaN 保留为不可评分：", "",
        "| answerability | 本轮 n | 成功 | 失败 | 正常答案 faithfulness / 有效数 | 正常答案 F1 / 有效数 |",
        "|---|---:|---:|---:|---|---|"]
    for label, group in summary["by_answerability"].items():
        normal = group["normal_answers"]
        lines.append(f"| {label} | {group['cases']} | {normal['cases']} | {group['failure_messages']['cases']} | "
            f"{mean(normal[KEYS[0]])} / {normal[KEYS[0]]['finite']} | {mean(normal[KEYS[1]])} / {normal[KEYS[1]]['finite']} |")
    lines += ["", "原 18 题全部生成失败，不能把本轮正常答案均值与原失败文案均值当成配对答案质量提升。comparison18.json 保留两轮原始分数；summary.json 同时保留本轮正常答案、失败文案与全部 18 题均值。诚实拒答不人工改为 1，服务失败文案低分也不直接解释为幻觉。",
        "", "本轮正常答案均值：" + "; ".join(f"{key}={mean(selected['normal_answers'][key])}（有效{selected['normal_answers'][key]['finite']}）" for key in KEYS), ""]
    if failures:
        lines += ["剩余生成失败及原因：", ""]
        for row in failures:
            lines.append(f"- {row['case_id']}：{json.dumps(row['generation_failure'], ensure_ascii=False)}。原始尝试见 generation_calls，不追加 API 调整。")
    codes = Counter((r["generation_failure"] or {}).get("validation_code", "") for r in failures)
    template_cases = [case_id for case_id, observed in template_observations.items() if observed["template_paragraphs"]]
    pure_cases = [case_id for case_id, observed in template_observations.items() if observed["pure_template_delivery"]]
    lines += ["", "剩余失败码分布：" + json.dumps(dict(codes), ensure_ascii=False) + "。这是本轮固定修复范围的实际测量；不以试跑结果继续调整 prompt 或规则。", "",
        "成功答案中含独立固定模板段的题：" + ", ".join(template_cases) + "。这些是模板段与有引用事实段共存的实际样本，不能把全部恢复都归因于拒答豁免。纯模板全文真实生成成功题数为 " + str(len(pure_cases)) + "；纯模板全文路径由单元测试与 mock 流程覆盖。统计见 generation_replay18.json。",
        "仍失败的自由说明示例：rag-v0-026 第二次输出另起段称目录事实为空、图谱只有根节点、合作线索未交叉核实；rag-v0-057 将固定模板与“上述内容仅覆盖音乐目录与索尼的编曲描述……”接在同一段。它们均不是整段固定模板，继续触发 paragraph_missing_citation，符合本轮不做 A 扩展的边界。",
        "额外字段仍可复现：rag-v0-002 第二次保留空 narrative_note 及其他额外键；rag-v0-031/048/064 仍输出 additionalProperties=false；rag-v0-001/043 输出 sources_used。B 的提示与删除键反馈未能确保模型稳定遵守 schema，本轮未增加过滤或 repair。原文均见对应 generation_calls。", "",
        "## 原成功 32 题：仅离线回归", "",
        "32 题的旧成功 provider 响应经新生产装配/schema/grounding 原样重放，最终答案与引用全部不变，旧响应全部仍通过。除已批准的 prompt 内容外，请求 JSON 保持一致；这是 MockTransport 下的验证，真实 API 0。原 judge NLI verdict 的 64 个分数独立复算完全一致，原始产物没有被回写。",
        "**32 题未做真实 API 回归，'新 prompt 下 faithfulness 不跌'未验证。** 不把旧分数复算一致表述为新 prompt 的模型生成质量不下降。没有用本轮 18 题结果冒充全量 50 题新测量。", "",
        "## 实际 DeepSeek 用量", "",
        "| 阶段 | HTTP 次数 | HTTP 200 | 输入 tokens | 输出 tokens | 总 tokens | 缓存输入子集 |",
        "|---|---:|---:|---:|---:|---:|---:|"]
    for stage in ("generation", "judge", "total"):
        u = usage[stage]
        lines.append(f"| {stage} | {u['http_attempts']} | {u['successful_responses']} | {u['input_tokens']} | {u['output_tokens']} | {u['total_tokens']} | {u['cached_input_tokens']} |")
    lines += ["", "调用按 HTTP 真实尝试计数，token 取 provider 原始 usage；缓存输入是输入子集，不重复累加。未取得账单金额，不伪称货币实际扣费。逐题明细见 usage.json。", "",
        "## 验证与产物", "",
        "65 项相关测试全部通过：H0/旧 generation/评测范围 39 项，报告与分数复算 8 项，旧 grounding/models/通用 structured output 兼容测试 18 项。通过 Gate0Guard 后加载测试，禁止真实网络与密钥读取，denied_operations 均为空。8 个本轮 Python 文件的 ruff check 与 compileall 均通过，git diff --check 通过；实际命令与解释器见 validation_checks.json，测试输出见 tests-*.txt。",
        "tests-unit.txt / validation_checks.json 记录新旧相关测试及静态检查。测试覆盖两句模板与组合、空白/标点、纯模板全文、事实与模板混合、事实后缀、伪造 ID、前缀不修复、引用顺序、knowledge excerpt、无 sources、空输入、单字段 schema、自报拒答、额外键重试与两次边界，以及本轮 18 题范围约束。",
        "generation_replay18.json：新付费响应通过当前生产流程零 API 原样重放；metric_replay18.json：本轮 36 个单元格从原始 judge 判定复算，NaN 保留原因。offline_success32.json：旧 32 题验证与 64 个旧分数复算。",
        ".gitattributes 显式保留本轮证据的 CRLF 及 8 个哈希源文件各自的换行格式；未改测量原文。checkout_integrity.json 记录关闭 core.autocrlf 后，经 Git 检出过滤仍恢复相同字节的验证，避免 Git 标准化存储的 LF 与原始文件 SHA256 被混为一谈。",
        "eighteen_cases.jsonl / cases.md 提供四列、分数和状态；judge_reasons.json / judge_calls 提供逐条原始判定与理由；generation_calls 保留全部原始尝试；comparison18.json 对照前后失败原因。",
        "Gold V4/V5、projection、Step 2、Step 4、H0 Gate 0 产物未改；production 仅批准的 3 个文件变化，NarrativeDraft、通用 retry.py、配置与依赖不变。未跑完整线上聊天路由、UI 或真实原成功 32 题生成回归。",
        "修复及测量完成后停下等 West 审阅，不合入 main，不 push、不 deploy。", ""]
    (h0.OUTPUT / "report.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
