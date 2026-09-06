import argparse
import asyncio
import json
import sys
from pathlib import Path

AI_SERVICE_ROOT = Path(__file__).resolve().parents[2]
if str(AI_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(AI_SERVICE_ROOT))

from app.agent.foundation import create_main_graph, create_main_state
from app.agent.foundation.hybrid_routing import HybridIntentRouter, EscalationPolicy
from app.agent.foundation.llm_routing import LLMRouter
from app.agent.foundation.llm_routing.runtime import ProviderLLMIntentRuntime
from app.agent.foundation.model_routing import IntentClassifierRuntime, ModelRouter


class SmokeLLMRuntime:
    def __init__(self, live: bool):
        self.live = ProviderLLMIntentRuntime() if live else None
        self.calls = 0

    async def classify(self, request, *, instructions, retry):
        self.calls += 1
        if self.live is not None:
            return await self.live.classify(request, instructions=instructions, retry=retry)
        # Offline fixture tests escalation/clarification only, not LLM classification accuracy.
        return json.dumps({"intents": [], "needs_clarification": True})


async def run(args):
    runtime = IntentClassifierRuntime(args.model_dir, device=args.device)
    llm_runtime = SmokeLLMRuntime(args.live_llm)
    router = HybridIntentRouter(
        ModelRouter(runtime=runtime), LLMRouter(args.taxonomy, runtime=llm_runtime),
        policy=EscalationPolicy(args.boundary_margin), thresholds=runtime.thresholds,
    )
    graph = create_main_graph(intent_router=router)
    for index, text in enumerate(args.text or (
        "暂停一下", "暂停一下，然后讲讲这首歌", "如果周杰伦没有发行范特西会怎样",
        "什么叫概念专辑", "分析一下这首歌然后帮我收藏", "当前电影可以吗",
    )):
        before = llm_runtime.calls
        result = await graph.ainvoke(create_main_state(text), {"configurable": {"thread_id": f"smoke-{index}"}})
        print(json.dumps({
            "text": text, "llm_mode": "live" if args.live_llm else "offline_fixture",
            "llm_calls": llm_runtime.calls - before,
            "decision": result["route_decision"].model_dump(mode="json"),
            "executed_routes": [str(item.route_key) for item in result["task_results"]],
            "final_response": result["final_response"],
        }, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Run the complete MainGraph with the saved BGE and optional live LLM.")
    parser.add_argument("--model-dir", type=Path, default=AI_SERVICE_ROOT / "artifacts/intent_model")
    parser.add_argument("--taxonomy", type=Path, default=AI_SERVICE_ROOT / "data/intent/taxonomy.json")
    parser.add_argument("--device", choices=("auto", "cpu", "cuda", "mps"), default="auto")
    parser.add_argument("--boundary-margin", type=float, default=0.0)
    parser.add_argument("--live-llm", action="store_true", help="Use configured provider credentials (billable requests).")
    parser.add_argument("--text", action="append")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
