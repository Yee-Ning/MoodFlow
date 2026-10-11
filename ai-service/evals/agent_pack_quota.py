"""Offline quota replay over frozen labels and child evidence; never tunes policy.

Memory uses the actual bounded profile/history Sources. RAG replays the archived
retriever window and scores through the same Child projection. Envelope/session
pressure inputs are explicitly synthetic projections, not authenticated receipts
or a sample of production traffic. Actual receipt boundaries live in E regression.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
import argparse
import gzip
from hashlib import sha256
from html import escape
import json
from math import ceil, isfinite
from pathlib import Path
from types import SimpleNamespace

from app.agent.foundation.agent_pack import AgentPackBuilder, AgentPackInputs, AgentPackInvocation
from app.agent.foundation.context_contracts import PackManifest
from app.agent.foundation.context_engineering import ContextItem, POLICIES
from app.agent.foundation.context_sources import (
    HistoryMemorySource, SourceRequest, UserProfileSource, load_context_policy, source_item,
)
from app.agent.foundation.long_horizon.models import HistoryEventType
from app.agent.foundation.routing import RouteKey, RouteTask
from app.agent.foundation.user_profile import ProfileDocument, ProfileEntry
from app.rag.reranking.config import load_rerank_config

ROOT = Path(__file__).resolve().parent
NOW = datetime(2026, 10, 8, tzinfo=UTC)
ENVELOPE_LIMITS = {"general_chat": (16, 768), "curator": (24, 1024), "explorer": (24, 1280)}
OWNER_ROUTE = {"general_chat": RouteKey.GENERAL_CHAT, "curator": RouteKey.TOOL_CALLING, "explorer": RouteKey.WORLDLINE}
PROMPTS = {"general_chat": ("answer", "general-chat-base/v1"),
           "curator": ("tool_step", "curator-tools-base/v1"), "explorer": ("narrative", "explorer-narrative/v1")}
ASSETS = (
    "datasets/context_gold_audit_v3.jsonl", "datasets/memory_eval_v2.jsonl",
    "datasets/synthetic_memories_v2.jsonl", "datasets/rag_eval_gold_v4.frozen.jsonl",
    "reports/gold_v4_hybrid_shadow_retrieval_50_20260917.jsonl.gz",
    "reports/gold_v4_hybrid_shadow_trace_50_20260917.rerank_diagnostics.jsonl.gz",
)


@dataclass(frozen=True)
class QuotaCase:
    case_id: str
    suite: str
    owner: str
    source: str
    query: str
    items: tuple[ContextItem, ...]
    important_ids: frozenset[str]
    upstream_count: int
    spans: dict[str, tuple[str, ...]] = field(default_factory=dict)
    collection_cap_dropped: int = 0
    collection_cap_missing_important: frozenset[str] = frozenset()


def read_rows(path):
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8-sig") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def quota(source, owner):
    if source == "received_envelope":
        return ENVELOPE_LIMITS[owner]
    config = load_context_policy()["sources"][source]
    return config["candidate_limit"], config["tokens"]


def evidence_text(item):
    """Decode Source's JSON text wrapper before comparing exact Gold spans."""
    try:
        value = json.loads(item.content)
    except (TypeError, ValueError):
        return item.content
    return value if isinstance(value, str) else item.content


def evaluate(case):
    if case.owner not in OWNER_ROUTE or case.source not in load_context_policy()["sources"]:
        raise ValueError("unsupported quota scope")
    if len({item.id for item in case.items}) != len(case.items):
        raise ValueError("duplicate candidate identity")
    if (case.upstream_count < len(case.items) or case.collection_cap_dropped < 0
            or any(item.source != case.source or item.required for item in case.items)):
        raise ValueError("invalid optional candidate pool")
    limit, tokens = quota(case.source, case.owner)
    candidates = case.items[:limit]
    route = OWNER_ROUTE[case.owner]
    phase, prompt = PROMPTS[case.owner]
    policy = POLICIES[route].model_copy(update={"source_budgets": {
        **POLICIES[route].source_budgets, case.source: tokens}})
    value = AgentPackInvocation(owner_agent=case.owner, run_id="eval:quota", phase=phase,
        recipe_id=case.owner + "/v1", recipe_version="v1", prompt_refs=(prompt,),
        task=RouteTask(task_id="eval:quota:" + case.case_id, route_key=route, input_text=case.query),
        inputs=AgentPackInputs(registered_items=list(candidates)))
    pack = AgentPackBuilder().build(value, policy=policy, intent={"routes": [route.value]})
    PackManifest.model_validate(pack.manifest)
    selected = {item.id: item for item in pack.items if item.source == case.source}
    available = {item.id for item in candidates}
    important_available = case.important_ids & available
    reasons = Counter(event.reason for event in pack.trace.entries
                      if event.stage == "dropped" and event.item_id in available)
    quota_drops = {event.item_id for event in pack.trace.entries if event.stage == "dropped"
                   and event.reason in {case.source + "_budget_exceeded", "total_budget_exceeded"}}
    projected = {item.id for item in candidates
                 if item.metadata.get("token_before_projection", item.token_count) > item.token_count}
    eligible_spans = [(item.id, span) for item in candidates for span in case.spans.get(item.id, ())
                      if span in evidence_text(item)]
    return {
        "case_id": case.case_id, "suite": case.suite, "owner": case.owner, "source": case.source,
        "candidate_limit": limit, "token_quota": tokens, "upstream_count": case.upstream_count,
        "pre_cap_count": len(case.items), "collected_count": len(candidates), "selected_count": len(selected),
        "candidate_cap_dropped": max(0, len(case.items) - limit) + case.collection_cap_dropped,
        "candidate_cap_missing_important": sorted((case.important_ids & {item.id for item in case.items[limit:]})
                                                  | case.collection_cap_missing_important),
        "candidate_tokens": sum(item.token_count for item in candidates),
        "final_tokens": sum(item.token_count for item in selected.values()),
        "important_total": len(case.important_ids), "important_available": len(important_available),
        "important_kept": len(case.important_ids & selected.keys()),
        "upstream_or_cap_missing_important": sorted(case.important_ids - available),
        "false_dropped_important": sorted(important_available - selected.keys()),
        "important_dropped_by_quota": sorted(important_available & quota_drops),
        "projected_important_items": len(important_available & projected),
        "selected_projected_important_items": len(case.important_ids & selected.keys() & projected),
        "important_candidate_tokens": sum(item.token_count for item in candidates if item.id in case.important_ids),
        "eligible_supporting_spans": len(eligible_spans),
        "retained_supporting_spans": sum(item_id in selected and span in evidence_text(selected[item_id])
                                        for item_id, span in eligible_spans),
        "retained_supporting_text_any_identity": sum(any(span in evidence_text(item) for item in selected.values())
                                                    for _, span in eligible_spans),
        "drop_reasons": dict(reasons), "dropped_items": [item for item in pack.dropped_items if item in available],
        "selected_ids": sorted(selected), "pack_tokens": pack.total_tokens, "pack_budget": pack.budget,
    }


class FrozenHistory:
    """Read-only public event view of frozen episodic candidates, without a DB."""
    def __init__(self, items):
        self.events = [SimpleNamespace(event_type=HistoryEventType.USER_MESSAGE, event_id=row["memory_id"],
            task_id="eval:history", session_id="eval:session", request_id="eval:old",
            timestamp=NOW, payload={"text": row["content"]}) for row in items]

    def require_session(self, session_id):
        if session_id != "eval:session":
            raise ValueError("frozen session mismatch")
        return SimpleNamespace(conversation_id="eval:conversation", task_id="eval:history")

    def tasks_for_conversation(self, conversation_id):
        return [SimpleNamespace(task_id="eval:history")]

    def query_task_history(self, task_id, *, limit, descending):
        return self.events[:limit]


def memory_cases(root):
    config = load_context_policy()["sources"]
    for row in read_rows(root / ASSETS[1]):
        for kind, source in (("profile", "user_profile"), ("episodic", "history_memory")):
            pool = [candidate for candidate in row["candidate_memories"] if candidate["memory_type"] == kind]
            retrieved = [candidate for candidate in pool if candidate["retrieved"]]
            context = {"user_id": row["user_id"], "conversation_id": "eval:conversation",
                       "task_id": "eval:history", "session_id": "eval:session", "request_id": "eval:current"}
            request = SourceRequest(row["query"], context, history_store=FrozenHistory(retrieved))
            if kind == "profile":
                document = ProfileDocument(user_id=row["user_id"], version=1, updated_at=NOW,
                    entries=tuple(ProfileEntry(key=item["memory_id"], value=item["content"], source="frozen-memory-v2",
                        updated_at=NOW, confidence=1, intents=(RouteKey.GENERAL_CHAT,)) for item in retrieved))
                context["user_profile"] = document.model_dump(mode="json")
                items = UserProfileSource().collect(request, limit=config[source]["candidate_limit"])
                ids = {item["memory_id"]: "profile:" + item["memory_id"] for item in pool}
            else:
                items = HistoryMemorySource().collect(request, limit=config[source]["candidate_limit"])
                ids = {item["memory_id"]: "recall:" + item["memory_id"] for item in pool}
            important = frozenset(ids[key] for key in row["gold_memory_ids"] if key in ids)
            yield QuotaCase(row["case_id"] + ":" + source, "frozen_memory_source_replay", "general_chat", source,
                row["query"], tuple(items), important, len(pool))


def context_cases(root):
    for row in read_rows(root / ASSETS[0]):
        pool = [item for item in row["items"] if item["source_type"] == "long_term_memory"]
        items = tuple(ContextItem.model_validate(item["context_item"]).model_copy(update={
            "source": "history_memory", "required": False, "metadata": {"intents": ["general_chat"]}}) for item in pool)
        important = frozenset(item["context_id"] for item in pool if item["gold_label"] in {"required", "useful"})
        yield QuotaCase(row["case_id"] + ":history", "frozen_context_item_replay", "general_chat",
                        "history_memory", row["query"], items, important, len(items))


def rag_cases(root):
    gold = {row["case_id"]: row for row in read_rows(root / ASSETS[3])}
    scores = {row["case_id"]: {item["parent_id"]: item for item in row.get("inputs", [])}
              for row in read_rows(root / ASSETS[5])}
    limit = load_context_policy()["sources"]["rag"]["candidate_limit"]
    for row in read_rows(root / ASSETS[4]):
        if row["case_id"] not in gold:
            raise ValueError("frozen retrieval has no matching Gold")
        observed = scores.get(row["case_id"], {})
        upstream = (row.get("retrieval") or {}).get("candidates", [])
        valid = [item for item in upstream if all(str(item.get(key, "")).strip() for key in (
            "doc_id", "source_id", "source_name", "primary_child_id", "primary_child_content"))
            and isfinite(item["fusion_score"])]
        bounded = valid[:limit]
        if any(item["parent_id"] not in observed for item in bounded):
            raise ValueError("frozen candidate omitted rerank score; cannot invent one")
        ranked = sorted(bounded, key=lambda item: observed[item["parent_id"]]["reranker_rank_full"])
        ranked = ranked[:load_rerank_config().top_k]
        items = []
        for candidate in ranked:
            score = observed[candidate["parent_id"]]["reranker_score"]
            metadata = {"evidence_id": candidate["parent_id"], "citation": escape(candidate["source_id"], quote=True),
                "doc_id": candidate["doc_id"], "source_id": candidate["source_id"],
                "source_name": candidate["source_name"], "source_url": candidate.get("source_url"),
                "title": candidate["title"], "chunk_id": candidate["primary_child_id"],
                "fusion_score": candidate["fusion_score"], "rerank_score": score, "score": score,
                "score_basis": "rerank", "retrieval_rank": observed[candidate["parent_id"]]["retriever_rank"],
                "rerank_rank": len(items) + 1}
            item = source_item("evidence:rag:" + candidate["parent_id"], "rag_evidence",
                candidate["primary_child_content"], source="rag", intents=["worldline"], metadata=metadata,
                references=[candidate["doc_id"], candidate["source_id"]])
            items.append(item.model_copy(update={"relevance": score}))
        case = gold[row["case_id"]]
        important = frozenset("evidence:rag:" + item for item in case["relevant_parent_ids"])
        spans = defaultdict(list)
        for span in case["supporting_spans"]:
            spans["evidence:rag:" + span["parent_id"]].append(span["exact_text"])
        blocked_ids = {"evidence:rag:" + item["parent_id"] for item in valid[limit:]}
        yield QuotaCase(row["case_id"], "frozen_rag_child_score_replay", "explorer", "rag", row["query"],
            tuple(items), important, len(upstream), {key: tuple(value) for key, value in spans.items()},
            len(valid) - len(bounded), frozenset(important & blocked_ids))


def pressure_cases(root):
    seeds = [item["context_item"]["content"] for row in read_rows(root / ASSETS[0]) for item in row["items"]
             if item["source_type"] == "handoff_projection"]
    if not seeds:
        raise ValueError("frozen handoff seeds missing")
    for source in ("received_envelope", "session_view"):
        for owner in OWNER_ROUTE:
            for count in (0, 1, 4, 8, 16, 24, 32):
                items = []
                for index in range(count):
                    item = source_item(f"pressure:{source}:{owner}:{index}", "business_state",
                        {"task_id": f"eval:task:{index}", "status": "completed",
                         "summary": seeds[index % len(seeds)][:160]}, source=source,
                        intents=[OWNER_ROUTE[owner].value], references=[f"eval:projection:{index}"])
                    items.append(item)
                yield QuotaCase(f"pressure:{source}:{owner}:{count}", "synthetic_projection_pressure", owner, source,
                    "承接已完成的公开音乐任务", tuple(items), frozenset(item.id for item in items), len(items))


def ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[max(0, ceil(len(ordered) * fraction) - 1)] if ordered else None


def summarize(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[(row["suite"], row["owner"], row["source"])].append(row)
    result = []
    for (suite, owner, source), cases in sorted(groups.items()):
        totals = {key: sum(row[key] for row in cases) for key in (
            "important_total", "important_available", "important_kept", "candidate_tokens", "final_tokens",
            "eligible_supporting_spans", "retained_supporting_spans", "candidate_cap_dropped")}
        false_drops = sum(len(row["false_dropped_important"]) for row in cases)
        quota_drops = sum(len(row["important_dropped_by_quota"]) for row in cases)
        drop_cases = sum(bool(row["dropped_items"]) for row in cases)
        nonempty = sum(row["collected_count"] > 0 for row in cases)
        demand = percentile([row["important_candidate_tokens"] for row in cases if row["important_available"]], .95)
        current = cases[0]["token_quota"]
        proposed = max(current, ceil((demand or current) / 128) * 128) if quota_drops else current
        result.append({"suite": suite, "owner": owner, "source": source, "cases": len(cases),
            "nonempty_cases": nonempty, **totals, "available_useful_recall": ratio(totals["important_kept"], totals["important_available"]),
            "end_to_end_label_capture": ratio(totals["important_kept"], totals["important_total"]),
            "false_dropped_important": false_drops, "dropped_case_frequency": ratio(drop_cases, nonempty),
            "important_dropped_by_quota": quota_drops,
            "candidate_cap_case_frequency": ratio(sum(row["candidate_cap_dropped"] > 0 for row in cases), nonempty),
            "candidate_cap_missing_important": sum(len(row["candidate_cap_missing_important"]) for row in cases),
            "upstream_or_cap_missing_important": sum(len(row["upstream_or_cap_missing_important"]) for row in cases),
            "projected_important_items": sum(row["projected_important_items"] for row in cases),
            "selected_projected_important_items": sum(row["selected_projected_important_items"] for row in cases),
            "retained_supporting_text_any_identity": sum(row["retained_supporting_text_any_identity"] for row in cases),
            "important_demand_tokens_p95": demand, "final_tokens_p95": percentile([row["final_tokens"] for row in cases], .95),
            "drop_reasons": dict(sum((Counter(row["drop_reasons"]) for row in cases), Counter())),
            "recommendation": {"current_tokens": current, "candidate_limit": cases[0]["candidate_limit"],
                "suggested_review_tokens": proposed, "direction": "review_upward" if proposed > current else "maintain",
                "confidence": "stress_only" if suite.startswith("synthetic") else "frozen_replay_only",
                "candidate_direction": "retain_current_pending_live_distribution",
                "applied": False}})
    return result


def run(root=ROOT):
    policy_path = ROOT.parent / "app/agent/foundation/context_policy.json"
    before = policy_path.read_bytes()
    policies_before = {key.value: value.model_dump() for key, value in POLICIES.items()}
    hashes = {name: sha256((root / name).read_bytes()).hexdigest() for name in ASSETS}
    cases = [*context_cases(root), *memory_cases(root), *rag_cases(root), *pressure_cases(root)]
    if len({case.case_id for case in cases}) != len(cases):
        raise ValueError("case IDs must be unique")
    rows = [evaluate(case) for case in cases]
    if before != policy_path.read_bytes() or policies_before != {key.value: value.model_dump() for key, value in POLICIES.items()}:
        raise RuntimeError("production policy changed during offline eval")
    return {"schema_version": 1, "mode": "offline_frozen_replay", "production_policy_sha256": sha256(before).hexdigest(),
        "asset_sha256": hashes, "case_count": len(rows), "production_policy_unchanged": True,
        "limits": {owner: {source: dict(zip(("candidate_limit", "tokens"), quota(source, owner), strict=True))
                           for source in load_context_policy()["sources"]} for owner in OWNER_ROUTE},
        "limitations": ["Source replay and synthetic projections are scored separately; no production-distribution claim.",
            "Archived RAG scores are restricted to the first 24 valid retriever candidates, then original top-k; no model calls.",
            "Gold absent before L1 is reported separately from important items dropped by Pack selection.",
            "Span recall is conditional on exact supporting text already present in the Child; no Parent expansion.",
            "Important identity dedup is separate from quota drops; text retained under another ID is counted separately.",
            "Profile/history excerpted important items are counted; absent span labels cannot establish semantic loss.",
            "Single-source replay does not measure competition in a full business Pack or provider framing.",
            "Recommendations are review values only; quotas and production files remain unchanged."],
        "summary": summarize(rows), "cases": rows}


def markdown(result):
    lines = ["# Agent Pack E 离线软配额评测", "", f"案例数：{result['case_count']}。生产策略 SHA-256：`{result['production_policy_sha256']}`。",
        "", "所有建议仅供 West 决策；未修改生产阈值。冻结回放和合成压力场景分别计分。", "",
        "| suite | owner / source | cases | 可用重要项保留率 | dropped 案例频率 | 重要项ID丢弃 / 配额误裁 | 当前 → 建议审查 token |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |"]
    for row in result["summary"]:
        recall = "N/A" if row["available_useful_recall"] is None else f"{row['available_useful_recall']:.1%}"
        frequency = "N/A" if row["dropped_case_frequency"] is None else f"{row['dropped_case_frequency']:.1%}"
        recommendation = row["recommendation"]
        lines.append(f"| {row['suite']} | {row['owner']} / {row['source']} | {row['cases']} | {recall} | {frequency} | "
                     f"{row['false_dropped_important']} / {row['important_dropped_by_quota']} | {recommendation['current_tokens']} → {recommendation['suggested_review_tokens']} |")
    lines.extend(["", "建议按固定样本可用重要项 token 需求的 P95 向上取整至 128；不因该样本缩小现值。候选上限暂维持。",
        "合成压力建议不能直接用于生产调参；需补充真实业务分布。命中率不等于在线检索或回答质量。", "", "## 指标与限制", "",
        "可用重要项保留率的分母只包含进入 L1 的有标签候选；总标签捕获率、上游/候选窗口缺失及 supporting span 条件覆盖率另见 JSON。",
        "dropped 频率以非空候选案例为分母；去重、intent、来源配额和整体 L1 原因分别保存。原始文本不写入评测结果。", ""])
    lines.extend("- " + item for item in result["limitations"])
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New directory; existing results are never overwritten")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output directory already exists; select a new path")
    result = run()
    args.output.mkdir(parents=True)
    (args.output / "quota-eval.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.output / "quota-eval.md").write_text(markdown(result), encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("mode", "case_count", "production_policy_unchanged")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
