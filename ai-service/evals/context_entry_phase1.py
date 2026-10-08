"""Paired lite/full first-hop routing eval, with frozen V5 and contextual cases.

Run from ai-service: python -m evals.context_entry_phase1 --output ../docs/reports/...
No production flags are modified. The configured Jev client is used directly.
"""

import argparse
import asyncio
import json
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage

from app.agent.decision.jev.client import JevClient
from app.agent.decision.jev.router_v5 import JevRouterV5
from app.agent.foundation.context_engineering import build_context
from app.agent.foundation.entry_context import build_lite_pack, triage_scope
from app.agent.foundation.routing import RouteKey, RoutingContext
from app.agent.foundation.state.main import create_main_state

CONTEXTUAL_CASES = [
    ("ctx-01", "那就收藏它", "我们刚才找到歌曲《红豆》。", "tool_calling", {}),
    ("ctx-02", "换一首吧", "当前正在播放《红豆》。", "tool_calling", {"has_current_song": True, "current_song_id": 101}),
    ("ctx-03", "刚才那段歌词在表达什么", "刚才读了当前歌曲的歌词。", "current_song", {"has_current_song": True, "current_song_id": 101}),
    ("ctx-04", "继续探索它和爵士的关系", "当前探索根节点是歌手周杰伦。", "worldline", {}),
    ("ctx-05", "那我这种心情应该怎么办", "用户说今天心情低落。", "general_chat", {}),
    ("ctx-06", "这首歌为什么听起来悲伤", "我们正在讨论当前歌曲的歌词。", "current_song", {"has_current_song": True, "current_song_id": 101}),
    ("ctx-07", "把刚才找到的那首加到播放队列", "搜索结果歌曲 ID 为 101。", "tool_calling", {}),
    ("ctx-08", "继续说说后摇的特点", "用户想理解音乐术语后摇。", "general_chat", {}),
]


def cases():
    path = Path(__file__).parent / "datasets/router_e2e_blind_v5.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    rows += [{"case_id": case_id, "query": query, "history": history, "context": context,
              "expected_routes": [route], "expected_clarify": False}
             for case_id, query, history, route, context in CONTEXTUAL_CASES]
    return rows


def packs(row):
    older = [AIMessage(content=f"此前第{index}轮：我们讨论音乐风格和聆听习惯，这是已结束的闲聊。")
             for index in range(10)] if row.get("history") else []
    history = [*older, HumanMessage(content="更早的讨论：音乐理论与收藏。"),
               AIMessage(content=row["history"])] if row.get("history") else []
    state = create_main_state(row["query"], messages=history, routing_context=RoutingContext(**row.get("context", {})),
        continuity_context={"handoff": {"schema_version": 2, "last_agent": "general_chat",
                                      "run_status": "completed"}} if history else {})
    lite = build_lite_pack(state)
    full = build_context(RouteKey.GENERAL_CHAT, query=row["query"], system="",
                         messages=history, continuity_context=state["continuity_context"])
    return state, lite, full


def signature(decision):
    return {"routes": list(dict.fromkeys(task.route_key.value for task in decision.tasks)),
            "clarify": decision.needs_clarification}


async def evaluate(router, rows=None, *, progress=None):
    results = []
    for row in rows or cases():
        state, lite, full = packs(row)
        record = {"case_id": row["case_id"], "expected_routes": row["expected_routes"],
                  "expected_clarify": row["expected_clarify"], "contextual": bool(row.get("history"))}
        for label, pack in (("lite", lite), ("full", full)):
            try:
                with triage_scope(pack):
                    if hasattr(router, "route_with_trace"):
                        trace = await router.route_with_trace(row["query"], state["routing_context"])
                        decision = trace.decision
                        provenance = {"router_source": trace.router_source, "fallback_reason": trace.fallback_reason,
                                      "api_calls": trace.api_calls, "response_model": trace.response_model}
                    else:
                        decision = await router.route(row["query"], state["routing_context"])
                        provenance = {}
                record[label] = {**signature(decision), "pack_tokens": pack.total_tokens,
                    **provenance,
                    "correct": (decision.needs_clarification if row["expected_clarify"] else
                                not decision.needs_clarification and signature(decision)["routes"] == row["expected_routes"])}
            except Exception as exc:
                record[label] = {"error_type": type(exc).__name__, "correct": False, "pack_tokens": pack.total_tokens}
        results.append(record)
        if progress is not None:
            progress(results)
    scores = {name: sum(row[name]["correct"] for row in results) / len(results) for name in ("lite", "full")}
    return {"sample_size": len(results), "accuracy": scores,
            "lite_minus_full": scores["lite"] - scores["full"],
            "max_allowed_drop": .05, "regression_gate_passed": scores["lite"] >= scores["full"] - .05,
            "contextual_sample_size": sum(row["contextual"] for row in results),
            "fallback_counts": {name: sum(row[name].get("router_source") == "legacy_hybrid_fallback" for row in results)
                                for name in ("lite", "full")},
            "results": results}


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    def progress(rows):
        args.output.with_suffix(".partial.json").write_text(
            json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    result = await evaluate(JevRouterV5(JevClient()), progress=progress)
    result["mode"] = "live_jev_v5_paired"
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "results"}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
