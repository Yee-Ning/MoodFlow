"""Opt-in, real-provider combo probe with allowlisted, prompt-free evidence.

Default entry is preserved. ``--entry hybrid`` isolates execution behind the
real baseline Hybrid router; it is explicitly not default-entry acceptance.
No journal, ACK, production configuration, or existing container is mutated.
"""

import argparse
import asyncio
import json
import logging
import os
import traceback
from pathlib import Path

from dotenv import load_dotenv


CASES = {
    "original": ("播放周杰伦的晴天，并探索周杰伦的世界线", False),
    "play_explore": ("播放陈奕迅的十年；探索陈奕迅的世界线", False),
    "explore_pause": ("先探索陈奕迅的音乐世界线，然后暂停播放", True),
}


async def probe(args):
    load_dotenv(args.environment_root / ".env", override=False)
    os.environ.update({"SPRING_BOOT_BASE_URL": args.java_url,
                       "HANDOFF_CANARY_ENABLED": "false", "MEMORY_BACKEND": "disabled",
                       "NO_PROXY": "localhost,127.0.0.1,::1", "no_proxy": "localhost,127.0.0.1,::1"})
    # Third-party exception logs can include response bodies; export only the
    # fields assembled below, never raw SDK logs or exception strings.
    logging.disable(logging.CRITICAL)
    import httpx
    from langchain_core.messages import HumanMessage
    from app.agent.foundation.hybrid_routing import create_hybrid_router
    from app.agent.foundation.routing import RoutingContext
    from app.agent.foundation.state.main import create_main_state
    from app.agent.foundation.subgraphs.worldline import graph as world_graph
    from app.agent.foundation.subgraphs.worldline.retrieval import (
        RetrievalRuntimeSettings, create_worldline_retrieval_runtime,
    )
    from app.agent.handoff import graph as handoff_graph
    from app.agent.handoff.state import HandoffRun
    from app.config.settings import get_settings
    from app.llm.runtime import RequestLedger
    from app.schemas.context import MusicAgentContext

    settings = get_settings()
    if settings.agent_graph_mode != "foundation" or settings.handoff_canary_enabled:
        raise ValueError("live probe requires unchanged foundation default and disabled canary")
    rows = [{"kind": "configuration", "entry": args.entry, "case": args.case,
             "default_graph": settings.agent_graph_mode, "canary": False,
             "persistence": "isolated_process_no_journal", "mock": False}]
    send, sync_send = httpx.AsyncClient.send, httpx.Client.send
    owned, reserve = handoff_graph.execute_owned_task, RequestLedger.reserve
    get_retrieval = world_graph.get_worldline_retrieval_runtime
    create_router = handoff_graph.create_hybrid_router

    async def observed_send(client, request, **kwargs):
        response = await send(client, request, **kwargs)
        rows.append({"kind": "http", "host": request.url.host,
                     "path": request.url.path, "status": response.status_code})
        return response

    def observed_sync_send(client, request, **kwargs):
        response = sync_send(client, request, **kwargs)
        if request.url.port == 6333:
            rows.append({"kind": "qdrant_http", "path": request.url.path,
                         "status": response.status_code})
        return response

    def observed_reserve(ledger, amount):
        rows.append({"kind": "budget", "agent": ledger.agent,
                     "requested_tokens": amount, "remaining_tokens": ledger.remaining})
        return reserve(ledger, amount)

    async def observed_owned(*args, **kwargs):
        try:
            result = await owned(*args, **kwargs)
            rows.append({"kind": "task_result", "route_key": str(result.route_key),
                         "status": result.status.value, "reason": result.data.get("reason"),
                         "steps": [{"name": item["name"], "status": item["status"],
                                    "error": item.get("error")}
                                   for item in result.data.get("tool_call_details", [])],
                         "actions": [item["name"] for item in result.data.get("client_actions", [])]})
            return result
        except Exception as exc:
            rows.append({"kind": "exception", "error_type": type(exc).__name__,
                         "reason": getattr(exc, "reason", None),
                         "frames": [{"file": Path(frame.filename).name, "function": frame.name,
                                     "line": frame.lineno}
                                    for frame in traceback.extract_tb(exc.__traceback__)]})
            raise

    def router():
        return create_hybrid_router(model_dir=args.environment_root / "artifacts/intent_model")

    def retrieval():
        config = RetrievalRuntimeSettings()
        if config.retrieval_mode == "empty":
            raise ValueError("real retrieval required")
        return create_worldline_retrieval_runtime(settings=config, project_root=args.environment_root)

    httpx.AsyncClient.send, httpx.Client.send = observed_send, observed_sync_send
    handoff_graph.execute_owned_task, RequestLedger.reserve = observed_owned, observed_reserve
    handoff_graph.create_hybrid_router = router
    world_graph.get_worldline_retrieval_runtime = retrieval
    try:
        text, player = CASES[args.case]
        builder = handoff_graph.build_handoff_graph(**(
            {"intent_router": router()} if args.entry == "hybrid" else {}
        ))
        graph = builder.compile()
        context = RoutingContext(has_current_song=player, current_song_id=11 if player else None)
        output = await graph.ainvoke(create_main_state(text, messages=[HumanMessage(content=text)],
                                                       routing_context=context), context=MusicAgentContext())
        run = HandoffRun.model_validate(output["handoff_run"])
        rows.append({"kind": "run", "run_id": run.run_id, "status": run.status.value,
                     "stop_reason": run.stop_reason.value,
                     "tasks": [{"task_id": task.task_id, "route_key": task.route_key.value,
                                "status": next((result.status.value for result in run.results
                                                if result.task_id == task.task_id), "pending")}
                               for task in run.plan.tasks],
                     "chain": [agent.value for agent in run.handoff_chain]})
    finally:
        httpx.AsyncClient.send, httpx.Client.send = send, sync_send
        handoff_graph.execute_owned_task, RequestLedger.reserve = owned, reserve
        handoff_graph.create_hybrid_router = create_router
        world_graph.get_worldline_retrieval_runtime = get_retrieval
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(rows[-1], ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment-root", type=Path, required=True, help="Existing ai-service runtime asset directory")
    parser.add_argument("--java-url", default="http://127.0.0.1:18088")
    parser.add_argument("--entry", choices=("default", "hybrid"), default="default")
    parser.add_argument("--case", choices=CASES, default="original")
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(probe(parser.parse_args()))


if __name__ == "__main__":
    main()
