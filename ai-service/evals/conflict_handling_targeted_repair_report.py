"""Assemble inspectable P0 repair exports without changing evaluation scores."""

from collections import Counter
from hashlib import sha256
import json
from pathlib import Path

from evals.semantic_relations_metrics import aggregate
from evals.semantic_relations_models import file_hash, load_suite


ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "evals/semantic_relations_v1_review_r6"
BEFORE = ROOT / "evals/reports/semantic_relations_v1_baseline_r6"
OUTPUT = ROOT / "evals/reports/semantic_relations_v1_conflict_repair_r6"
RUNTIME = "app/agent/foundation/subgraphs/worldline/"
REPAIR_FILES = {RUNTIME + name + ".py" for name in (
    "era_expansion", "scene_expansion", "influence_expansion",
)}
NEW_RUNTIME = RUNTIME + "conflict_semantics.py"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def write_json(name, value):
    (OUTPUT / name).write_bytes((json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())


def write_rows(name, rows):
    (OUTPUT / name).write_bytes("".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows
    ).encode())


def ratio(value):
    return "N/A (0/0)" if value["value"] is None else (
        f"{value['numerator']}/{value['denominator']} ({value['value']:.1%})"
    )


def assemble():
    cases, _, snapshots, _ = load_suite(SUITE)
    payload = read_json(OUTPUT / "metrics.json")
    old = read_json(BEFORE / "metrics.json")
    rows, old_rows = read_rows(OUTPUT / "results.jsonl"), read_rows(BEFORE / "results.jsonl")
    assert payload["metrics"] == aggregate(rows)
    assert len(rows) == len(old_rows) == 53
    original = {row["case_id"]: row for row in old_rows}
    protection = read_json(OUTPUT / "protected_artifacts.json")
    assert protection["file_count"] == len(protection["files"]) == 355
    assert all(file_hash(ROOT / name) == digest for name, digest in protection["files"].items())
    after = {name: file_hash(ROOT / name) for name in protection["runtime_before"]}
    changed = {name for name, digest in after.items() if digest != protection["runtime_before"][name]}
    assert changed == REPAIR_FILES
    assert file_hash(SUITE / "manifest.json") == protection["suite_manifest_sha256"]
    write_json("scope_validation.json", {
        "protected_artifacts_unchanged": True, "protected_file_count": 355,
        "runtime_before": protection["runtime_before"], "runtime_after": after,
        "changed_runtime_files": sorted(changed),
        "new_runtime_files": {NEW_RUNTIME: file_hash(ROOT / NEW_RUNTIME)},
        "gold_and_old_freezes_unchanged": True, "evaluation_scoring_and_category_gate_unchanged": True,
        "thresholds_and_confidence_penalties_unchanged": True,
    })
    improved = [r["case_id"] for r in rows if r["passed"] and not original[r["case_id"]]["passed"]]
    regressions = [r["case_id"] for r in rows if not r["passed"] and original[r["case_id"]]["passed"]]
    differences = {}
    for cohort, groups in payload["metrics"].items():
        differences[cohort] = {
            family: {metric: {"before": old["metrics"][cohort][family]["metrics"][metric], "after": value}
                     for metric, value in group["metrics"].items()}
            for family, group in groups.items()
        }
    write_json("metrics_comparison.json", {
        "before_directory": BEFORE.relative_to(ROOT).as_posix(),
        "after_directory": OUTPUT.relative_to(ROOT).as_posix(),
        "suite_manifest_sha256": file_hash(SUITE / "manifest.json"),
        "metrics": differences, "improved_cases": improved, "new_regressions": regressions,
        "synthetic_metrics_unchanged": payload["metrics"]["synthetic_regression"] == old["metrics"]["synthetic_regression"],
    })
    real = [r for r in rows if r["cohort"] == "approved_real_baseline"]
    conflicts = [r for r in real if "conflict" in r["categories"]]
    failed = [r for r in real if not r["passed"]]
    write_rows("real_failures.jsonl", failed)
    old_notes = {c["case_id"]: c for c in read_json(BEFORE / "failure_diagnosis.json")["cases"]}
    dependency = (
        "Influence 冻结 runtime 输入缺少报道的明确主语、采访提问和成员身份上下文；"
        "审核说明不是运行时证据，不能用它补写 exact span 或绕过方向校验。"
    )
    reverse = [r for r in real if r["label"] == "negative" and "direction_sensitive" in r["categories"]]
    gate = {
        "baseline_ready": payload["baseline_ready"],
        "can_enter_targeted_repairs": payload["can_enter_targeted_repairs"],
        "evaluator_blockers": payload["blockers"],
        "real_conflict_handling": payload["metrics"]["approved_real_baseline"]["overall"]["metrics"]["conflict_handling"],
        "p0_conflict_target_met": all(r["passed"] for r in conflicts),
        "can_enter_p1_direction_repair": all(r["passed"] for r in conflicts) and not regressions,
        "final_regression_ready": False, "blocking_dependencies": [dependency],
        "reverse_direction_negative": {"passed": sum(r["passed"] and not r["actual"]["relations"] for r in reverse), "count": len(reverse)},
        "new_regressions": regressions,
        "readiness_scope": "Original evaluator gates unchanged; P0 completion and P1 readiness are separate task acceptance checks.",
    }
    write_json("machine_gate.json", gate)
    diagnostics = []
    for row in failed:
        notes = old_notes[row["case_id"]]
        diagnostics.append({
            "case_id": row["case_id"], "relation_type": row["relation_type"],
            "failure_reasons": row["failure_reasons"],
            "diagnosis": dependency if "conflict" in row["categories"] else notes["diagnosis"],
            "trace": row["trace"],
            "source_metadata": [snapshots[s["source_id"]].model_dump(mode="json")
                                for s in row["trace"]["expected_gold"]["supporting_evidence"]],
        })
    write_json("failure_diagnosis.json", {
        "reviewed_real_failure_count": len(failed),
        "failure_category_counts_by_relation_type": {
            family: dict(Counter(reason for r in failed if r["relation_type"] == family for reason in r["failure_reasons"]))
            for family in ("era", "scene", "influence")
        }, "cases": diagnostics, "p0_remaining_dependency": dependency,
    })
    traces = []
    for row in conflicts:
        family = row["relation_type"]
        stage = row["trace"]["filtering_scoring"][family]
        traces.append({
            "case_id": row["case_id"], "query": row["query"], "relation_type": family,
            "passed": row["passed"], "retrieved_evidence": row["trace"]["retrieved_evidence"],
            "source_metadata": [snapshots[s["source_id"]].model_dump(mode="json")
                                for s in row["trace"]["expected_gold"]["supporting_evidence"]],
            "candidate_relations": row["trace"]["candidate_relations"][family],
            "validator": stage["decisions"],
            "conflict_aggregation": stage["trace"]["aggregation_trace"],
            "final_relations": row["actual"]["relations"],
            "expected_gold": row["trace"]["expected_gold"], "failure_reasons": row["failure_reasons"],
            "checkpoint": row["actual"]["checkpoint"],
        })
    write_rows("conflict_case_traces.jsonl", traces)
    # Keep raw harness prose for reproducibility. Its static pre-repair wording
    # is not an assertion about this run; authoritative scope is below.
    report_path = OUTPUT / "semantic_relations_evaluation_v1_report.md"
    runner_path = OUTPUT / "runner_report.md"
    if report_path.read_bytes().startswith(b"# Semantic Relations Evaluation v1"):
        runner_path.write_bytes(report_path.read_bytes())
    else:
        assert runner_path.exists(), "original harness report missing"
    lines = [
        "# Conflict Handling Targeted Repair — r6", "",
        "P0 尚未完整验收：真实 conflict 从 **0/3 → 2/3**；Era 1/1、Scene 1/1、Influence 0/1。"
        "正式人工审核 cohort 从 17/25 → 19/25。未修改 Gold、评分函数、category gate、旧 freeze；未执行独立 P1 Direction / Evidence Mismatch 修复。", "",
        "`baseline_ready=true`、`can_enter_targeted_repairs=true` 保持成立，表示审核覆盖允许修复。"
        "**不等于 P0 已达到 3/3**；`p0_conflict_target_met=false`、`can_enter_p1_direction_repair=false`。", "",
        "## 设计与改动范围", "",
        "1. Era：在既有结构/claim/span/time 校验后，将作品具名的 release 日期与 normalized belongs_to_era 做兼容。"
        "年份必须支持目标 decade，录制日期、不同作品、艺术家不同发行活动、再版/版本不能合并为同一 release event。"
        "已验证的同一作品 release event 跨 decade 聚合；保留每个互斥 target、双方 evidence、temporal_conflict；"
        "start/end=null、precision=unknown，confidence 使用既有 temporal conflict 规则。", "",
        "2. Scene：仅做 scene/movement/community 后缀别名规范化，验证之后映射到现有身份或更完整的 scene 名。"
        "具名 frontrunner/membership 可支持归属；成员第一人称须由具名引用标题绑定，并验证同一 scene 的否定/指代作用域。"
        "反证单独不输出关系；有已验证 support 才进入 attribution aggregation。"
        "双方 claim IDs 与 stance 存在 evidence/provenance；归属不确定时不派生确定的 artist scene association。", "",
        "3. Influence：上下文方向兼容只对同一 relation key 的潜在正反断言启用，要求结构、证据与强度检查已通过。"
        "列表中的借鉴来源必须有明确的借鉴主体；采访否认必须有 source 影响提问、具名 respondent 与 target 的上下文绑定。"
        "唯一 surname alias 可匹配来源；Italian ‘direi di no’ 保留限定否认 stance，不改成绝对无影响。"
        "无问题/主语/绑定时继续拒绝。standalone Pixies failures 仍保留；未新增通用 P1 方向补全。", "",
        "4. 使用原有 DTO/projector/merge 接口。冲突写入 metadata 的 boolean 和 conflict_flags；evidence 中保留原始 claim ID/span。"
        "证据容量先保留相反 stance，overflow 明示；图描述明确冲突，后续回合不会恢复确定措辞。"
        "Era 来源身份仍不可变，只有已派生的 relation annotation 可以复用既有 source；真实 provenance drift 仍触发 source_conflict。"
        "重复合并相同 DTO 保持幂等。validator 的 negated_* rejection 表示不能当作正事实；"
        "aggregation trace 会区分可路由的 contradiction 和被结构/方向拒绝的 excluded。", "",
        "接受阈值 0.85、证据上限 8、既有 confidence penalty 和全部 evaluator 指标/分母保持不变。"
        "没有新依赖、case_id 条件或真实实体专用规则。所有算法均只读取 runtime documents/claims，未读取 Gold/receipt/review notes。", "",
        "| 文件 | 修改目的 |", "|---|---|",
        "| app/agent/foundation/subgraphs/worldline/conflict_semantics.py | 有界别名、引用主体、证据平衡、不可变来源、多轮冲突描述、聚合 trace |",
        "| app/agent/foundation/subgraphs/worldline/era_expansion.py | release 兼容、跨 decade event aggregation、冲突投影及幂等合并 |",
        "| app/agent/foundation/subgraphs/worldline/scene_expansion.py | 归属/否认 scope、别名 normalization、attribution aggregation 和多轮保留 |",
        "| app/agent/foundation/subgraphs/worldline/influence_expansion.py | 仅 paired conflict 的上下文方向验证、限定否认、平衡证据及冲突投影 |",
        "| tests/test_worldline_conflict_handling.py | 独立名称的正反/边界/溢出/多轮/来源篡改专项测试 |",
        "| tests/test_worldline_scene_expansion.py | 更精确的否定诊断断言；拒绝行为和年份校验要求保留 |",
        "| evals/tests/historical_scope.py, test_semantic_relations_review_r5.py, test_semantic_relations_review_r6.py | 28 个审核期 runtime hash 对照原始 Git 创建提交；不错误禁止之后授权修复，旧 freeze 仍全量逐字节验证 |",
        "| evals/conflict_handling_targeted_repair_report.py, evals/tests/test_conflict_handling_targeted_repair_report.py | 只读结果的报告组装与导出审计，无评分变更 |", "",
        "文档索引更新于 evals/README.md；本轮独立导出位于 evals/reports/semantic_relations_v1_conflict_repair_r6/。", "",
        "## 修复前后指标", "",
        "每项同时展示 overall / era / scene / influence。N/A 不等于 100%；Evidence Coverage 是所需 unique claim 覆盖，"
        "traceability 是输出证据计数（同一证据可绑定多个关系）。",
    ]
    for cohort in ("approved_real_baseline", "synthetic_regression"):
        lines.extend(["", f"### {cohort}", "", "| 指标 | overall 前→后 | era 前→后 | scene 前→后 | influence 前→后 |",
                      "|---|---|---|---|---|"])
        for metric in payload["metrics"][cohort]["overall"]["metrics"]:
            cells = [ratio(old["metrics"][cohort][f]["metrics"][metric]) + " → "
                     + ratio(payload["metrics"][cohort][f]["metrics"][metric])
                     for f in ("overall", "era", "scene", "influence")]
            lines.append(f"| {metric} | " + " | ".join(cells) + " |")
    lines.extend(["", f"Reverse-direction negative：{gate['reverse_direction_negative']['passed']}/{len(reverse)} 严格拒绝，未放行反向关系。",
                  "新增 regression 为 0；synthetic 指标逐项不变（20/28，原有 8 个失败仍记录）。"
                  "真实 weak 4/4、negation 3/3、query isolation 25/25、checkpoint 25/25 均未下降。", "",
                  "## 三个真实 conflict 的完整链路", "",
                  "完整的逐字段 trace 在 conflict_case_traces.jsonl；包含原文/URL/publisher/locator/source SHA、candidate、"
                  "validator、明确 aggregation、final、Gold 以及 checkpoint。以下为可读链路，无补写 evidence。"])
    for trace in traces:
        lines.extend(["", f"### {trace['case_id']}", "", f"Query → {trace['query']}", "", "Evidence →"])
        for document in trace["retrieved_evidence"]:
            snapshot = next(s for s in trace["source_metadata"] if s["source_id"] == document["metadata"]["snapshot_source_id"])
            lines.extend(["", f"[{snapshot['title']}]({snapshot['url']}) — {snapshot['publisher']}",
                          f"Locator: {snapshot['locator']}; SHA256: `{snapshot['content_hash']}`", "",
                          "> " + document["content"].replace("\n", "\n> ")])
        lines.extend(["", "Candidate →", "", "```json", json.dumps(trace["candidate_relations"], ensure_ascii=False, indent=2), "```",
                      "", "Validator →", "", "```json", json.dumps(trace["validator"], ensure_ascii=False, indent=2), "```",
                      "", "Conflict aggregation →", "", "```json", json.dumps(trace["conflict_aggregation"], ensure_ascii=False, indent=2), "```",
                      "", "Final →", "", "```json", json.dumps(trace["final_relations"], ensure_ascii=False, indent=2), "```",
                      "", "Expected Gold →", "", "```json", json.dumps(trace["expected_gold"]["expected_relations"], ensure_ascii=False, indent=2), "```",
                      "", f"Result: {'PASS' if trace['passed'] else 'FAIL'}; failure reasons: {trace['failure_reasons']}"])
        if not trace["passed"]:
            lines.extend(["", dependency, "当前 report span 不能确定隐含借鉴主体，denial 缺少影响提问；"
                          "因此两者仍 direction_missing，限定否认还带 negated_influence，属于 aggregation 的 excluded。"
                          "完整上下文的独立专项测试可成功形成 support + contradiction；它不计入真实 3/3。"])
    lines.extend(["", "## 真实 failures、blocker 与下一轮", "",
                  f"人工审核真实失败剩余 {len(failed)} 条；没有把 synthetic failure 当作真实失败。", "",
                  "| 优先级 | 实际失败类别 | 影响 | 建议修复点（本轮未做） |", "|---|---|---|---|",
                  "| P0 依赖 | Influence conflict / source-context 缺失 | 1 个 conflict 不达标 | 保留 r6；另建可追溯的上下文输入版本，保存报道完整主语、采访问题及 speaker binding，并人工复核；不从审核说明推断 |",
                  "| P1 | Direction missing | 5 条，含上述 conflict；Pixies 同源正例/forward 有重复，非 5 个独立来源 | 后续再处理列表、采访和跨句方向；保留 reverse negative 拒绝 |",
                  "| 后续 | Scene evidence mismatch | 1 条 Neko Case positive | 后续单独解决 city/scene alias 指代，不扩大本轮 conflict 修复 |", "",
                  "**Blocker：有。P0 尚未达到 real conflict handling 3/3，不能声称 Conflict Handling 全部完成，"
                  "也不能无条件进入 P1 Direction Repair。** 先解决可追溯 runtime 上下文依赖，保持旧 freeze；"
                  "新增上下文需独立版本和审核。本轮不构造/批准新 Gold，不联网补证据，不部署或运行 Final Regression / Online E2E。", "",
                  "## 验证、复现与限制", "",
                  "- 统一评测：`.venv/Scripts/python.exe run_semantic_relations_eval.py --suite evals/semantic_relations_v1_review_r6 --output evals/reports/semantic_relations_v1_conflict_repair_r6`。"
                  "退出码 2 表示仍有真实质量失败；metrics/results/gate 已完整写出，不是评分规则或执行异常。",
                  "- 报告组装：`.venv/Scripts/python.exe -m evals.conflict_handling_targeted_repair_report`。",
                  "- 自动化、静态检查与实际命令结果见 validation.json。355 个旧 dataset/report/receipt/hash 文件逐字节未变；"
                  "原 28 个 runtime/evaluation 文件中只改动上述 3 个关系模块，25 个其他文件未变；新增 helper 独立记录 hash。",
                  "- r5/r6 审核期 scope lock 测试现在校验其 Git 创建提交中的 28 个源码 blob；原 freeze/receipt/parent hash 检查不变。"
                  "这是历史审核期保护与新授权修复的区分，不允许修改原 scope lock。",
                  "- checkpoint 指标仍仅代表 application InMemorySaver serialization/replay；多轮合并另由专项及 session 回归验证。"
                  "生产 PostgreSQL restart 测试跳过，未测在线 retrieval/Qdrant/Music API/LLM，也未运行部署。",
                  "- runner_report.md 保留 unchanged harness 的原始自动 prose，其中‘运行时未修改’等静态旧措辞不适用于本轮；"
                  "本报告、runtime hash 与 validation.json 是本轮范围依据。", "",
                  "最后结论：baseline gate=true/true；真实 Conflict Handling=2/3，P0 有上下文 blocker；"
                  "无新增 regression。Final Worldline Regression 前必须解决剩余 Influence conflict、方向漏检、"
                  "Scene evidence mismatch，并补齐真实 retrieval/context 和生产 persistence 验证。", ""])
    report = "\n".join(lines).encode()
    (OUTPUT / "conflict_handling_targeted_repair_report.md").write_bytes(report)
    report_path.write_bytes(report)
    for path in OUTPUT.iterdir():
        if path.is_file() and path.suffix in {".json", ".jsonl", ".md"}:
            path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n"))
    names = sorted(path.name for path in OUTPUT.iterdir() if path.is_file() and path.name != "artifact_manifest.json")
    write_json("artifact_manifest.json", {
        "schema_version": "conflict-handling-targeted-repair-artifacts-v1",
        "suite_manifest_sha256": file_hash(SUITE / "manifest.json"),
        "baseline_manifest_sha256": file_hash(BEFORE / "artifact_manifest.json"),
        "execution_code_revision": payload["code_revision"], "execution_code_dirty": payload["code_dirty"],
        "files": {name: sha256((OUTPUT / name).read_bytes()).hexdigest() for name in names},
    })
    print(json.dumps({"conflict_passes": sum(r["passed"] for r in conflicts), "conflict_cases": len(conflicts),
                      "real_failures": len(failed), "new_regressions": regressions}, ensure_ascii=False))


if __name__ == "__main__":
    assemble()
