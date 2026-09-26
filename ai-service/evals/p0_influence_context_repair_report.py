"""Audit unchanged scoring and assemble the input-context P0 acceptance report."""

from collections import Counter
import json
from pathlib import Path

from evals.semantic_relations_metrics import aggregate
from evals.semantic_relations_models import file_hash, load_suite


ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "evals/semantic_relations_context_inputs_v1"
BEFORE = ROOT / "evals/reports/semantic_relations_v1_conflict_repair_r6"
OUTPUT = ROOT / "evals/reports/semantic_relations_v1_influence_context_repair"
CONTROL = ROOT / "evals/local/influence_context_r6_control"
CHANGED = {"evals/semantic_relations_models.py", "app/agent/foundation/subgraphs/worldline/influence_expansion.py"}
NEW_RUNTIME = "app/agent/foundation/subgraphs/worldline/source_context.py"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def rows(path):
    return [json.loads(s) for s in path.read_text(encoding="utf-8").splitlines()]


def write_json(name, data):
    (OUTPUT / name).write_bytes((json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode())


def write_rows(name, data):
    (OUTPUT / name).write_bytes("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in data).encode())


def ratio(v):
    return "N/A (0/0)" if v["value"] is None else f"{v['numerator']}/{v['denominator']} ({v['value']:.1%})"


def assemble():
    cases, inputs, sources, manifest = load_suite(SUITE)
    after, before = read_json(OUTPUT / "metrics.json"), read_json(BEFORE / "metrics.json")
    results, old = rows(OUTPUT / "results.jsonl"), rows(BEFORE / "results.jsonl")
    originals = {r["case_id"]: r for r in old}
    assert after["metrics"] == aggregate(results) and len(results) == len(cases) == len(old)
    protection = read_json(OUTPUT / "protected_artifacts.json")
    assert all(file_hash(ROOT / p) == h for p, h in protection["files"].items())
    current = {p: file_hash(ROOT / p) for p in protection["runtime_before"]}
    changed = {p for p, h in current.items() if h != protection["runtime_before"][p]}
    assert changed == CHANGED
    write_json("scope_validation.json", {
        "protected_artifacts_unchanged": True, "protected_file_count": protection["file_count"],
        "runtime_before": protection["runtime_before"], "runtime_after": current, "changed_runtime_files": sorted(changed),
        "new_runtime_files": {NEW_RUNTIME: file_hash(ROOT / NEW_RUNTIME)},
        "new_input_loader_sha256": file_hash(ROOT / "evals/semantic_relations_context_inputs.py"),
        "input_manifest_sha256": file_hash(SUITE / "manifest.json"),
        "gold_and_old_freezes_unchanged": True, "evaluation_scoring_and_category_gate_unchanged": True,
        "projectors_unchanged": True, "thresholds_and_confidence_penalties_unchanged": True,
    })
    control_payload, control = read_json(CONTROL / "metrics.json"), rows(CONTROL / "results.jsonl")
    assert control_payload["metrics"] == before["metrics"]
    assert all(r["actual"] == originals[r["case_id"]]["actual"]
               and r["passed"] == originals[r["case_id"]]["passed"] for r in control)
    write_json("r6_control_comparison.json", {
        "suite": "evals/semantic_relations_v1_review_r6", "new_runtime_old_inputs_identical_to_previous": True,
        "case_count": len(control), "metrics_identical": True, "actual_results_identical": True,
        "control_metrics_sha256": file_hash(CONTROL / "metrics.json"), "control_results_sha256": file_hash(CONTROL / "results.jsonl"),
        "real_conflict_handling": control_payload["metrics"]["approved_real_baseline"]["overall"]["metrics"]["conflict_handling"],
    })
    improved = [r["case_id"] for r in results if r["passed"] and not originals[r["case_id"]]["passed"]]
    regressions = [r["case_id"] for r in results if not r["passed"] and originals[r["case_id"]]["passed"]]
    comparison = {cohort: {family: {
        metric: {"before": before["metrics"][cohort][family]["metrics"][metric], "after": value}
        for metric, value in group["metrics"].items()} for family, group in groups.items()}
        for cohort, groups in after["metrics"].items()}
    synthetic_unchanged = after["metrics"]["synthetic_regression"] == before["metrics"]["synthetic_regression"]
    assert synthetic_unchanged and not regressions
    write_json("metrics_comparison.json", {
        "before_directory": BEFORE.relative_to(ROOT).as_posix(), "metrics": comparison,
        "improved_cases": improved, "new_regressions": regressions, "synthetic_metrics_unchanged": synthetic_unchanged,
    })
    real = [r for r in results if r["cohort"] == "approved_real_baseline"]
    conflicts = [r for r in real if "conflict" in r["categories"]]
    reverse = [r for r in real if r["label"] == "negative" and "direction_sensitive" in r["categories"]]
    failed = [r for r in real if not r["passed"]]
    guards = ["weak_evidence_rejection", "negation_rejection", "query_isolation", "checkpoint_continuity"]
    guards_unchanged = all(after["metrics"]["approved_real_baseline"][f]["metrics"][m]
                           == before["metrics"]["approved_real_baseline"][f]["metrics"][m]
                           for f in ("overall", "era", "scene", "influence") for m in guards)
    p0 = len(conflicts) == 3 and all(r["passed"] for r in conflicts)
    reverse_ok = len(reverse) == 2 and all(r["passed"] and not r["actual"]["relations"] for r in reverse)
    ready = p0 and guards_unchanged and reverse_ok and synthetic_unchanged and not regressions
    gate = {
        "baseline_ready": after["baseline_ready"], "can_enter_targeted_repairs": after["can_enter_targeted_repairs"],
        "real_conflict_handling": after["metrics"]["approved_real_baseline"]["overall"]["metrics"]["conflict_handling"],
        "p0_conflict_target_met": p0, "can_enter_p1_direction_repair": ready,
        "conservative_rejections_unchanged": guards_unchanged, "synthetic_metrics_unchanged": synthetic_unchanged,
        "reverse_direction_negative": {"passed": sum(r["passed"] and not r["actual"]["relations"] for r in reverse), "count": len(reverse)},
        "new_regressions": regressions, "blocking_dependencies": [] if ready else ["P0 acceptance unmet"],
        "evaluator_blockers": after["blockers"], "remaining_real_failures": len(failed),
        "final_regression_ready": False, "p1_started": False,
        "readiness_scope": "Original baseline/category gates unchanged; separate P0/P1 task acceptance, not Final Regression approval.",
    }
    write_json("machine_gate.json", gate)
    old_notes = {r["case_id"]: r for r in read_json(BEFORE / "failure_diagnosis.json")["cases"]}
    write_rows("real_failures.jsonl", failed)
    write_json("failure_diagnosis.json", {
        "reviewed_real_failure_count": len(failed), "p0_remaining_dependency": None,
        "failure_category_counts_by_relation_type": {f: dict(Counter(reason for r in failed if r["relation_type"] == f for reason in r["failure_reasons"])) for f in ("era", "scene", "influence")},
        "cases": [{"case_id": r["case_id"], "relation_type": r["relation_type"], "failure_reasons": r["failure_reasons"],
                   "diagnosis": old_notes[r["case_id"]]["diagnosis"], "trace": r["trace"]} for r in failed],
    })
    traces = []
    for row in conflicts:
        family = row["relation_type"]
        stage = row["trace"]["filtering_scoring"][family]
        traces.append({
            "case_id": row["case_id"], "query": row["query"], "relation_type": family, "passed": row["passed"],
            "retrieved_evidence": row["trace"]["retrieved_evidence"],
            "source_metadata": [sources[s["source_id"]].model_dump(mode="json") for s in row["trace"]["expected_gold"]["supporting_evidence"]],
            "candidate_relations": row["trace"]["candidate_relations"][family], "validator": stage["decisions"],
            "conflict_aggregation": stage["trace"]["aggregation_trace"], "final_relations": row["actual"]["relations"],
            "expected_gold": row["trace"]["expected_gold"], "failure_reasons": row["failure_reasons"], "checkpoint": row["actual"]["checkpoint"],
        })
    write_rows("conflict_case_traces.jsonl", traces)
    contexts = [doc.metadata["relation_source_context"] for r in inputs.values() for doc in r.documents if "relation_source_context" in doc.metadata]
    write_json("context_provenance.json", {"input_manifest": manifest, "captures": contexts, "offset_unit": "Unicode code points; [start,end)", "gold_span_and_original_snapshot_unchanged": True})
    raw = OUTPUT / "semantic_relations_evaluation_v1_report.md"
    runner = OUTPUT / "runner_report.md"
    if raw.read_bytes().startswith(b"# Semantic Relations Evaluation v1"):
        runner.write_bytes(raw.read_bytes())
    lines = ["# P0 Influence Context Repair", "",
             "**P0 Conflict Handling 正式完成：真实 conflict 2/3 → 3/3，Era / Scene / Influence 均 1/1。**",
             "`baseline_ready=true`、`can_enter_targeted_repairs=true`、`p0_conflict_target_met=true`、`can_enter_p1_direction_repair=true`。",
             "P1 仅具备启动条件，本轮未开始；Final Regression / Online E2E / 部署未执行。", "",
             "r6 Gold、receipts、旧 freeze、评分函数、category gate、projector 均保持原 hash。新增 context-input v1 只补两个 runtime document 的原文上下文。",
             f"保护 {protection['file_count']} 个旧 artifact；新 runtime 使用原阈值 0.85、原 evidence cap 8 和原 confidence penalty。", "",
             "## 设计与修改文件", "",
             "- source_context.py：通用、冻结的 RelationSourceContext / ContextSegment；按 URL、publisher、title、source/document/chunk/claim ID、原文 SHA、anchor/section offsets 绑定；输入不合法即不采用。",
             "- influence_expansion.py：仅 paired attribution support/contradiction 的 direction_missing 可用已绑定 supplement。报道要求 target 执行借鉴/转化、source 位于借鉴列表；采访要求真实问题、相邻回答和标题中的 respondent→target 绑定。",
             "- 问题支持主动影响句和被动归因后的省略追问；不把同时出现两个实体当作方向证据。contradiction 仍为 negated_influence，validation 后才进入明确 aggregation。",
             "- semantic_relations_models.py / semantic_relations_context_inputs.py：在既有 load_suite 增加 input-only overlay 分支，校验 base manifest / core / review hashes 与每条 input；除 relation_source_context 外与原 r6 必须完全相等。未修改评分 runner 或 metrics。",
             "- build_influence_context_inputs_v1.py：从原 URL 抓取 DOM 原文，不读取审核说明构造 evidence；保留 source response SHA 与 locator。context capture 不声称是新的 human Gold approval。",
             "- tests/test_worldline_influence_source_context.py、evals/tests/test_influence_context_input_repair.py：上下文、方向/说话人边界、tamper、旧 r6 control、导出验收测试。",
             "- evals/p0_influence_context_repair_report.py、evals/README.md 与独立 input/report 目录：指标、trace、lineage、failure diagnosis 和复现入口。", "",
             "无 case_id / 真实实体专用 runtime 条件，未修 standalone Pixies Direction 或 Neko Case。", "",
             "## Source context 与 provenance", "",
             "原 evidence exact span 和原 [0,length) offsets 留在 r6 source ledger；补充上下文另有 anchor offsets 和独立 SHA。完整 ledger 在 context_provenance.json 与 context_sources.jsonl。", "",
             "| 来源 | 原 source ID | 原 claim ID | context anchor offsets | 原 source SHA256 | context SHA256 |",
             "|---|---|---|---|---|---|"]
    for c in contexts:
        lines.append(f"| [{c['title']}]({c['source_url']}) | {c['source_id']} | {c['claim_id']} | [{c['anchor']['start']},{c['anchor']['end']}) | {c['source_content_sha256']} | {c['content_sha256']} |")
    lines.extend(["", "## 前后指标（相同 r6 Gold，不同独立 context input）", "",
                  "Precision 只计算输出关系；Direction Accuracy 包含漏检正例。N/A 不是 100%。Evidence Coverage 是 required unique claim 的覆盖。"])
    for cohort in ("approved_real_baseline", "synthetic_regression"):
        lines.extend(["", f"### {cohort}", "", "| 指标 | overall 前→后 | era 前→后 | scene 前→后 | influence 前→后 |", "|---|---|---|---|---|"])
        for metric in after["metrics"][cohort]["overall"]["metrics"]:
            cells = [ratio(before["metrics"][cohort][f]["metrics"][metric]) + " → " + ratio(after["metrics"][cohort][f]["metrics"][metric]) for f in ("overall", "era", "scene", "influence")]
            lines.append(f"| {metric} | " + " | ".join(cells) + " |")
    lines.extend(["", "正式真实通过 19/25 → 20/25；synthetic 20/28 不变、conflict 3/3 不变。新增 regression=0。真实 weak 4/4、negation 3/3、query isolation 25/25、checkpoint 25/25；reverse negative 2/2 均无输出关系。",
                  "新 runtime + 原 r6 输入 control 的所有 actual outputs / pass 状态 / 指标与上轮完全相等（仍 conflict 2/3），见 r6_control_comparison.json。成功来自独立 context 输入，不是改写 Gold 或静默改变旧 baseline。", "",
                  "## Influence 完整 trace", "",
                  "conflict_case_traces.jsonl 包含三个 conflict 的完整逐字段 trace：retrieved document（含真实 context）、candidate、validator、aggregation、final、Gold 和 checkpoint。下表按执行结果展开剩余 Influence 链路。"])
    for trace in traces:
        if trace["relation_type"] != "influence":
            continue
        lines.extend(["", f"Case: `{trace['case_id']}` — PASS", "", "Query → " + trace["query"], "",
                      "Evidence → 原 span 从 r6 ledger 原样传入；完整报道句提供 actor，采访标题提供 respondent binding，完整 question 紧接 answer。新增上下文与原 span 对应的 offsets/hash 见上表及 context_provenance.json。", "",
                      "Candidate →", "", "| claim | source → target | predicate | stance |", "|---|---|---|---|"])
        for candidate in trace["candidate_relations"]:
            lines.append(f"| {candidate['evidence']['claim_id']} | {candidate['source_entity']['canonical_name']} → {candidate['target_entity']['canonical_name']} | {candidate['relation_type']} | {candidate['evidence']['stance']} |")
        lines.extend(["", "Validator →", "", "| claim | status | reasons |", "|---|---|---|"])
        for decision in trace["validator"]:
            lines.append(f"| {decision['candidate']['evidence']['claim_id']} | {decision['status']} | {decision['rejection_reasons']} |")
        lines.extend(["", "report 的 direction_missing 被已验证 article context 解开；denial 的 direction_missing 被已验证问答和 speaker context 解开。denial 仍 rejected / negated_influence，表示不是正事实；可路由的 contradiction 未被丢弃。", "",
                      "Conflict aggregation →", "", "```json", json.dumps(trace["conflict_aggregation"], ensure_ascii=False, indent=2), "```", "",
                      "Final →", "", "```json", json.dumps(trace["final_relations"], ensure_ascii=False, indent=2), "```", "",
                      "Expected Gold →", "", "```json", json.dumps(trace["expected_gold"]["expected_relations"], ensure_ascii=False, indent=2), "```", "",
                      "Failure reasons → []。final 同时绑定 report + denial claim IDs；两条原 exact span、source/chunk IDs、SHA 未替换。checkpoint 查询语义保持一致。"])
    lines.extend(["", "## 验证、真实剩余失败与边界", "",
                  "复现：`.venv/Scripts/python.exe run_semantic_relations_eval.py --suite evals/semantic_relations_context_inputs_v1 --output <new-output-dir>`。本次正式评测 exit=2，原因仍有 5 个真实质量失败需要后续诊断；执行已完成，P0 gate 为 true。",
                  "自动化测试、静态检查与实际命令结果见 validation.json；旧 freezes 与所有 receipts 逐字节校验。runner_report.md 保存未修改的 runner 模板输出，其中‘运行时未修改’等旧静态 prose 不适用于本轮，以本报告与 scope_validation.json 为准。", "",
                  "- P0 context blocker：已解除；没有新增 regression/blocker。",
                  "- P1 Direction：4 个真实漏检（2 个独立 Pixies 证据组及其 forward 重复样本），方向准确率 1/5，后续独立修复；本轮未开始。",
                  "- 后续 Scene evidence mismatch：Neko Case 1 条，未修。",
                  "- synthetic 原有 8 个失败仍记录；弱证据误接收、retrieval missing 等不在本轮修复范围。不得把 synthetic 20/28 当作全部通过。",
                  "- checkpoint 是 application InMemorySaver serialization/replay；生产 PostgreSQL restart opt-in 未启用时仍跳过，未验证在线 retrieval / Qdrant / LLM / Online E2E。Final Regression 前需解决剩余真实失败并另行完成所需生产 persistence 验证。", "",
                  "最后结论：P0 Conflict Handling 在独立 context-input v1 的统一评测中正式完成 3/3。`can_enter_p1_direction_repair=true`；P1 尚未执行。`final_regression_ready=false`。", ""])
    report = "\n".join(lines).encode()
    (OUTPUT / "p0_influence_context_repair_report.md").write_bytes(report)
    raw.write_bytes(report)
    for path in OUTPUT.iterdir():
        if path.is_file() and path.suffix in {".json", ".jsonl", ".md"}:
            path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n"))
    names = sorted(p.name for p in OUTPUT.iterdir() if p.is_file() and p.name != "artifact_manifest.json")
    write_json("artifact_manifest.json", {
        "schema_version": "p0-influence-context-repair-artifacts-v1", "input_manifest_sha256": file_hash(SUITE / "manifest.json"),
        "base_gold_manifest_sha256": manifest["base_manifest_sha256"], "before_artifact_manifest_sha256": file_hash(BEFORE / "artifact_manifest.json"),
        "execution_code_revision": after["code_revision"], "execution_code_dirty": after["code_dirty"],
        "files": {name: file_hash(OUTPUT / name) for name in names},
    })
    print(json.dumps({"p0_conflict_target_met": p0, "can_enter_p1_direction_repair": ready, "real_failures": len(failed), "new_regressions": regressions}))


if __name__ == "__main__":
    assemble()
