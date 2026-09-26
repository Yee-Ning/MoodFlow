"""One offline run through the unchanged Era -> Scene -> Influence services."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import subprocess
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from app.agent.checkpointing import create_memory_checkpointer
from app.agent.foundation.subgraphs.worldline.era_expansion import EraExpansionService
from app.agent.foundation.subgraphs.worldline.scene_expansion import SceneExpansionService
from app.agent.foundation.subgraphs.worldline.influence_expansion import InfluenceExpansionService
from app.agent.foundation.subgraphs.worldline.models import ResolvedEntity, WorldLineSessionResult
from evals.semantic_relations_metrics import aggregate, diagnose, relation_key
from evals.semantic_relations_models import PREDICATES, ReplayInput, load_suite


SERVICE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SUITE = SERVICE_ROOT / "evals" / "semantic_relations_v1"
DEFAULT_OUTPUT = SERVICE_ROOT / "evals" / "reports" / "semantic_relations_v1"
SERVICES = {"era": EraExpansionService, "scene": SceneExpansionService,
            "influence": InfluenceExpansionService}


def execute_replay(replay: ReplayInput) -> tuple[WorldLineSessionResult, dict]:
    """Only independent runtime inputs are visible here, never Gold labels."""

    graph = replay.graph.model_copy(deep=True)
    sources = []
    relation_lists = {}
    trace = {"query": replay.query, "retrieval_mode": replay.retrieval_mode,
             "search_plan": replay.search_plan.model_dump(mode="json"),
             "retrieved_evidence": [doc.model_dump(mode="json") for doc in replay.documents],
             "candidate_relations": {}, "filtering_scoring": {}}
    for family, service_type in SERVICES.items():
        service = service_type()
        result = service.expand(replay.query, replay.search_plan, graph, sources, replay.documents)
        candidates = service.extractor.extract(replay.search_plan, graph, replay.documents) if result.trace.requested else []
        _, decisions = service.validator.validate(graph, replay.documents, candidates)
        trace["candidate_relations"][family] = [item.model_dump(mode="json") for item in candidates]
        trace["filtering_scoring"][family] = {
            "trace": result.trace.model_dump(mode="json"),
            "decisions": [item.model_dump(mode="json") for item in decisions],
        }
        graph, sources = result.graph, result.sources
        relation_lists[f"{family}_relations"] = result.relations
    root = next(node for node in replay.graph.nodes if node.id == replay.graph.root)
    result = WorldLineSessionResult(
        root_entity=ResolvedEntity(entity_id=root.id, entity_type=root.type.value,
                                   name=root.name, source="eval"),
        graph=graph, sources=sources, narrative="Evaluation context replay.",
        branches=[], citations=[], **relation_lists,
    )
    return result, trace


def normalize_result(result: WorldLineSessionResult, replay: ReplayInput, sources: dict) -> list[dict]:
    """Resolve edge -> source -> document -> chunk -> claim -> snapshot span."""

    source_map = {item.id: item for item in result.sources}
    document_map = {item.id: item for item in replay.documents}
    rows = []
    for family in SERVICES:
        for relation in getattr(result, f"{family}_relations"):
            target = relation.target_entity
            if target is None:
                target = getattr(relation, "era", None) or getattr(relation, "scene", None)
            edge = next((edge for edge in result.graph.edges
                         if (edge.source, edge.target, edge.relation.value)
                         == (relation.source_entity.node_id, target.node_id, relation.relation_type.value)), None)
            evidence = []
            for ev in relation.evidence:
                source = source_map.get(ev.source_id)
                metadata = source.metadata if source else {}
                doc = document_map.get(metadata.get("document_id"))
                snapshot_id = doc.metadata.get("snapshot_source_id") if doc else None
                snapshot = sources.get(snapshot_id)
                claim = next((item for item in doc.metadata.get("supported_claims", [])
                              if item.get("claim_id") == ev.claim_id), None) if doc else None
                traceable = bool(
                    edge and ev.source_id in edge.evidence_ids and source and doc and snapshot and claim
                    and source.excerpt == ev.exact_span == claim.get("evidence_span")
                    and metadata.get("claim_id") == ev.claim_id
                    and metadata.get("child_id") == doc.metadata.get("chunk_id")
                    and metadata.get("parent_id") == doc.metadata.get("parent_id")
                    and metadata.get("source_chunk_id") == claim.get("source_chunk_id")
                    and doc.content == snapshot.content and ev.exact_span in snapshot.content
                )
                evidence.append({**ev.model_dump(mode="json"), "traceable": traceable,
                                 "snapshot_source_id": snapshot_id, "source_metadata": metadata,
                                 "span_start": snapshot.content.find(ev.exact_span) if snapshot else None,
                                 "span_end": snapshot.content.find(ev.exact_span) + len(ev.exact_span) if snapshot else None})
            rows.append({"relation_type": family, "source_entity": relation.source_entity.node_id,
                         "target_entity": target.node_id, "predicate": relation.relation_type.value,
                         "evidence": evidence, "raw": relation.model_dump(mode="json")})
    # A graph-only semantic edge is an output too: do not hide contamination or
    # unsupported facts simply because the corresponding DTO was omitted.
    dto_keys = {relation_key(row) for row in rows}
    for edge in result.graph.edges:
        family = next((family for family, predicates in PREDICATES.items()
                       if edge.relation.value in predicates), None)
        if family and (edge.source, edge.target, edge.relation.value) not in dto_keys:
            rows.append({"relation_type": family, "source_entity": edge.source,
                         "target_entity": edge.target, "predicate": edge.relation.value,
                         "evidence": [], "raw": edge.model_dump(mode="json")})
    return rows


class CheckpointState(TypedDict):
    result: dict


def checkpoint_replay(result: WorldLineSessionResult, replay: ReplayInput) -> dict:
    """Use the application saver/serializer and recreate the graph reader."""

    saver = create_memory_checkpointer()
    def compile_graph():
        builder = StateGraph(CheckpointState)
        builder.add_node("store", lambda state: {"result": state["result"]})
        builder.add_edge(START, "store")
        builder.add_edge("store", END)
        return builder.compile(checkpointer=saver)
    config = {"configurable": {"thread_id": f"semantic:{replay.case_id}",
                               "session_id": f"semantic:{replay.case_id}"}}
    payload = result.model_dump(mode="json")
    compile_graph().invoke({"result": payload}, config)
    reader = compile_graph()
    restored = WorldLineSessionResult.model_validate(reader.get_state(config).values["result"])
    repeated, _ = execute_replay(replay)
    # Read after an unrelated state turn as well as immediately after restore.
    reader.invoke({"result": restored.model_dump(mode="json")}, config)
    continued = WorldLineSessionResult.model_validate(reader.get_state(config).values["result"])
    return {"backend": "application_in_memory_saver", "scope": "result_contract_and_query_replay",
            "restore_equal": restored.model_dump(mode="json") == payload,
            "query_equal": repeated.model_dump(mode="json") == payload,
            "continuation_equal": continued.model_dump(mode="json") == payload,
            "passed": all(item.model_dump(mode="json") == payload for item in (restored, repeated, continued)),
            "production_restart_verified": False}


def render_report(payload: dict, rows: list[dict]) -> str:
    lines = ["# Semantic Relations Evaluation v1", "", f"Run: {payload['created_at']}",
             "", "## 审计与边界", "",
             "复用 V2.4A/B/C schema、SearchPlan、MusicKnowledgeDocument、extractor/validator/projector、"
             "WorldLineSessionResult 和 application checkpoint saver；运行时模块未修改。",
             "旧 worldline_eval_runner / Gold v1/v1.1 主要覆盖 artist→genre、identity、merge，"
             "其 expected→fixture adapter 不适合三类关系质量评测。本 harness 的输入文件与标签独立。",
             "生产路径是 Query Understanding → hybrid retrieval/rerank → ContextPack → build_graph "
             "(Era→Scene→Influence) → session。当前离线回放从冻结 ContextPack 文档边界开始；"
             "既有身份映射和 supported_claims 是人工/评测注释，不是生产检索或 claim extraction 的测量。",
             "", "## 数据与可复现性", "",
             f"Gold/input/source/schema freeze hashes: `{json.dumps(payload['manifest']['files'], sort_keys=True)}`",
             f"Code revision: `{payload['code_revision']}`; dirty at run: `{payload['code_dirty']}`.",
             "正式 Gold 只接纳有匹配标签哈希的人工审批 receipt；待审核真实样本和 synthetic 回归分开计分。"
             "检索来源快照保存短原文、URL、publisher、采集时间、locator、SHA256；span offsets 为快照 Unicode 字符偏移。"
             "从原文提取的片段不是网页全文，定位需同时查看 URL/locator。评测不读取或写入生产索引。",
             "", "## 指标 baseline", "",
             "以下每个单元格为 numerator/denominator（百分比）；分母为 0 时为 N/A，不能当作 100%。"]
    for cohort, groups in payload["metrics"].items():
        lines.extend(["", f"### {cohort}", "", "| Metric | overall | era | scene | influence |",
                      "|---|---|---|---|---|"])
        for metric in groups["overall"]["metrics"]:
            cells = []
            for family in ("overall", *SERVICES):
                value = groups[family]["metrics"][metric]
                cells.append("N/A (0/0)" if value["value"] is None else
                             f"{value['numerator']}/{value['denominator']} ({value['value']:.1%})")
            lines.append(f"| {metric} | " + " | ".join(cells) + " |")
        lines.append(f"Cases: {groups['overall']['case_count']}; exact case passes: {groups['overall']['passed']}.")
    lines.extend(["", "指标定义见 README：precision 以输出关系计；coverage 以 Gold 正例所需 claim 计；"
                  "traceability 以输出证据计；unsupported 为未被 Gold+证据支持的输出关系；"
                  "direction 分母包含 Influence 正例的 abstention。weak/negation 仅计负例；"
                  "conflict 要求所有断言、标记和不确定字段；isolation 检查全部三个模块及 graph-only 边。",
                  "", "## Failure cases 与下一轮优先级", "",
                  "完整链路在 results.jsonl；所有失败在 failures.jsonl。pending 的失败是诊断候选，"
                  "未获人工确认前不能称为真实 Gold failure。synthetic 不计入真实失败优先级。",
                  "", "| Priority | Category | Real reviewed failures | Pending real diagnostic failures | Suggested fix point |",
                  "|---|---|---|---|---|"])
    priorities = [
        ("unsupported relation", "先复核原文语义与支持阈值；检查实体、claim 与字段约束"),
        ("conflict failure", "确认反证保留、conflict metadata 与汇总不确定字段"),
        ("direction error", "方向与实体别名/代词解析；保持保守拒绝"),
        ("cross-relation contamination", "显式查询意图与三模块 gating，检查时间词/scene/影响共现"),
        ("negation failure", "明确否定作用域与对应关系"),
        ("weak evidence accepted", "限定词与弱推断的拒绝"),
        ("evidence mismatch", "exact span、文档与 claim 对齐及可接受表达"),
        ("relation classification", "claim extraction、谓词覆盖、身份映射与自然语言语义分类"),
        ("retrieval failure", "用线上 trace 区分检索漏召回与 ContextPack 丢失"),
        ("checkpoint continuity failure", "恢复 DTO/graph/source 与生产 session persistence"),
    ]
    rank = 0
    for category, fix in priorities:
        approved = sum(category in row["failure_reasons"] for row in rows if row["cohort"] == "approved_real_baseline")
        pending = sum(category in row["failure_reasons"] for row in rows if row["cohort"] == "pending_real_diagnostic")
        if approved or pending:
            rank += 1
            lines.append(f"| {rank} | {category} | {approved} | {pending} | {fix} |")
    lines.extend(["", "### 待审核真实漏检明细", "",
                  "| Case | Query | Failure category | Runtime rejection |",
                  "|---|---|---|---|"])
    for row in rows:
        if row["cohort"] != "pending_real_diagnostic" or row["passed"]:
            continue
        rejection = sorted({reason for stage in row["trace"]["filtering_scoring"].values()
                            for reason in stage["trace"]["rejection_reasons"]})
        lines.append(f"| {row['case_id']} | {row['query']} | {', '.join(row['failure_reasons'])} | {', '.join(rejection)} |")
    lines.extend(["", "这三类表达诊断包括具名 Scene 的地名/代词指代、机构 Influence 列表、采访第一人称创作归因。"
                  "direction_missing 表示没有识别出方向，当前没有生成反向关系；需人工复核后才能确认修复范围。",
                  "", "## 验证及未验证限制", "",
                  "验证命令/结果见 validation.json（交付时记录，普通 harness 运行不会自动执行完整测试）。当前 checkpoint 指标仅代表 application saver "
                  "序列化、重新构图读取及相同输入查询回放；并非完整 WorldLineSessionService 多轮执行或 PostgreSQL 进程重启。"
                  "后者依赖已有 session/graph 测试和 PostgreSQL restart 测试，线上 Qdrant、真实 Music API/LLM 不在此次离线分母。",
                  "真实样本规模和来源多样性有限，且同源 case 相关，不具备生产统计代表性。"
                  "真实 negation/conflict 等缺口按 coverage_inventory.json 明示，不能用 synthetic 填平。",
                  "", "## 进入下一轮判断", "",
                  f"1. 各指标 baseline：见上表；正式人工审核 cohort 有 {payload['metrics']['approved_real_baseline']['overall']['case_count']} 条。",
                  f"2. Blocker：{'；'.join(payload['blockers']) or 'none'}。",
                  "3. 只有通过人工审核且覆盖完成后，才可进入“针对真实失败点修复”；当前可进行审核与诊断复核，不能将 pending 结果作为已验收 baseline。"
                  if not payload["baseline_ready"] else "3. 可以进入针对真实失败点修复；已确认的质量失败不会阻止建立 baseline，仍须保持 Gold 冻结。",
                  "4. Final Worldline Regression 前必须解决：人工审核与真实场景覆盖缺口、已确认的 unsupported/direction/isolation/conflict 错误、"
                  "真实 retrieval/ContextPack 的覆盖验证、生产 persistence/restart 连续性。不得以当前离线通过率替代这些验收。", ""])
    return "\n".join(lines)


def run_suite(directory: Path = DEFAULT_SUITE, output: Path = DEFAULT_OUTPUT) -> dict:
    if output.resolve().is_relative_to(directory.resolve()):
        raise ValueError("evaluation_output_cannot_overwrite_suite")
    cases, inputs, snapshots, manifest = load_suite(directory)
    rows = []
    for case in cases:
        if case.review.status == "rejected":
            continue
        result, trace = execute_replay(inputs[case.case_id])
        actual = {"relations": normalize_result(result, inputs[case.case_id], snapshots),
                  "checkpoint": checkpoint_replay(result, inputs[case.case_id])}
        diagnosis = diagnose(case, actual, trace)
        rows.append({"case_id": case.case_id, "query": case.query,
                     "relation_type": case.relation_type, "cohort": case.cohort,
                     "categories": case.categories, "label": case.label,
                     "trace": {**trace, "final_relations": actual["relations"],
                               "expected_gold": case.model_dump(mode="json"),
                               "failure_reasons": diagnosis["failure_reasons"]},
                     "actual": actual, **diagnosis})
    metrics = aggregate(rows)
    blockers = []
    inventory = {}
    for family in SERVICES:
        reviewed = [case for case in cases if case.relation_type == family and case.cohort == "approved_real_baseline"]
        inventory[family] = {"approved_real": len(reviewed),
                             "pending_real": sum(case.relation_type == family and case.cohort == "pending_real_diagnostic" for case in cases),
                             "approved_category_counts": {category: sum(category in case.categories for case in reviewed)
                                                          for category in ("positive", "negative", "weak_evidence", "negation", "conflict", "confusable", "isolation", "direction_sensitive")}}
        required = {"positive", "negative", "weak_evidence", "negation", "conflict", "confusable", "isolation"}
        if family == "influence":
            required.add("direction_sensitive")
        missing = sorted(category for category in required if not inventory[family]["approved_category_counts"][category])
        if missing:
            blockers.append(f"{family}: approved real coverage missing {', '.join(missing)}")
    baseline_ready = not blockers
    if any(not row["passed"] for row in rows if row["cohort"] == "approved_real_baseline"):
        blockers.append("reviewed real failures require diagnosis before Final Worldline Regression")
    payload = {"schema_version": "semantic-relations-evaluation-v1", "created_at": datetime.now(UTC).isoformat(),
               "manifest": manifest, "metrics": metrics, "blockers": blockers,
               "baseline_ready": baseline_ready, "can_enter_targeted_repairs": baseline_ready,
               "code_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=SERVICE_ROOT, text=True).strip(),
               "code_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=SERVICE_ROOT, text=True).strip()),
               "scope": "frozen_context_replay_with_annotated_claims"}
    output.mkdir(parents=True, exist_ok=True)
    for name, value in (("metrics.json", payload), ("coverage_inventory.json", inventory)):
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for name, selected in (("results.jsonl", rows), ("failures.jsonl", [row for row in rows if not row["passed"]])):
        (output / name).write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in selected), encoding="utf-8")
    (output / "semantic_relations_evaluation_v1_report.md").write_text(render_report(payload, rows), encoding="utf-8")
    review_lines = ["# Semantic Relations v1 — 人工审核包", "",
                    "每条真实样本的审核状态见下方；pending 的 provisional labels 尚未经人工核定。",
                    "请逐条查看完整来源上下文，核对实体、relation predicate、方向、证据强度、否定作用域和冲突。",
                    "negative 表示给定证据不足以支持所询关系，不代表全世界不存在该关系。",
                    "review receipt 必须包含 case_id、reviewer、reviewed_at、status=approved 和完整标签 payload 的 label_sha256。",
                    "新增审核结论需新版本数据与 manifest；不要修改本次冻结 Gold 来消除失败。", ""]
    for case in cases:
        if case.dataset_kind != "real_source":
            continue
        review_lines.extend([f"## {case.case_id}", "", f"Query: {case.query}",
                             f"Family: {case.relation_type}; provisional label: {case.label}",
                             f"Source → target: `{case.source_entity}` → `{case.target_entity}`",
                             f"Proposed relations: `{json.dumps([item.model_dump(mode='json') for item in case.expected_relations], ensure_ascii=False)}`",
                             f"Rationale: {case.rationale}"])
        for span in case.supporting_evidence:
            source = snapshots[span.source_id]
            review_lines.extend([f"Source: [{source.title}]({source.url}) — {source.publisher}",
                                 f"Locator: {source.locator}", f"Source SHA256: `{source.content_hash}`",
                                 f"Exact span offsets: [{span.span_start}, {span.span_end}); claim: `{span.claim_id}`",
                                 f"> {span.exact_span.replace(chr(10), ' / ')}"])
        review_lines.extend(["", f"Human decision: {case.review.status}; reviewer: {case.review.reviewer or '—'}; "
                             f"reviewed_at: {case.review.reviewed_at or '—'}; notes: {case.review.notes or '—'}", ""])
    (output / "human_review_pack.md").write_text("\n".join(review_lines), encoding="utf-8")
    # Verify the run has not mutated any input, label or snapshot.
    load_suite(directory)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    payload = run_suite(args.suite, args.output)
    print(json.dumps({"output": str(args.output), "blockers": payload["blockers"],
                      "cohort_case_counts": {name: groups["overall"]["case_count"]
                                             for name, groups in payload["metrics"].items()}}, ensure_ascii=False))
    # Missing approved Gold is a visible quality gate, not a successful baseline.
    return 2 if payload["blockers"] else 0
