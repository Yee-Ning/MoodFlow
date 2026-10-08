"""Opt-in real-model handoff resume proof against an isolated PostgreSQL DB."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path

from langchain_core.messages import AIMessage, ToolMessage

from app.agent.checkpointing import (
    checkpoint_backend,
    close_runtime_checkpointer,
    create_runtime_checkpointer,
    initialize_runtime_checkpointer,
)
from app.agent.foundation.routing import RoutingContext
from app.agent.foundation.state import create_main_state
from app.agent.handoff.graph import build_handoff_graph
from app.agent.handoff.state import HandoffRun
from app.config.settings import load_runtime_environment
from app.schemas.context import MusicAgentContext


def checkpoint_summary(values):
    run = HandoffRun.model_validate(values["handoff_run"])
    messages = values.get("messages", [])
    calls = [
        call["id"]
        for message in messages
        if isinstance(message, AIMessage)
        for call in message.tool_calls
    ]
    replies = [
        message.tool_call_id for message in messages if isinstance(message, ToolMessage)
    ]
    actions = [
        raw for result in run.results for raw in result.data.get("client_actions", [])
    ]
    return {
        "run_id": run.run_id,
        "status": run.status.value,
        "stop_reason": run.stop_reason.value if run.stop_reason else None,
        "handoff_count": run.handoff_count,
        "handoff_chain": [item.value for item in run.handoff_chain],
        "usage": [item.model_dump(mode="json") for item in run.usage],
        "transfer_call_count": len(calls),
        "transfer_reply_count": len(replies),
        "all_calls_paired": sorted(calls) == sorted(replies),
        "actions": actions,
        "result_statuses": [item.status.value for item in run.results],
    }


async def execute(mode, thread_id):
    if checkpoint_backend() != "postgres":
        raise ValueError("postgres_required")
    if not thread_id.startswith("phase6:"):
        raise ValueError("acceptance_thread_prefix_required")
    await initialize_runtime_checkpointer()
    try:
        saver = create_runtime_checkpointer()
        config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 100}
        previous = await saver.aget_tuple(config)
        if mode == "create" and previous is not None:
            raise ValueError("acceptance_thread_already_exists")
        if mode == "resume" and previous is None:
            raise ValueError("acceptance_checkpoint_missing")
        before = (
            checkpoint_summary(previous.checkpoint["channel_values"])
            if previous
            else None
        )
        graph = build_handoff_graph().compile(
            checkpointer=saver,
            interrupt_before=["curator_agent"] if mode == "create" else [],
        )
        initial = (
            create_main_state(
                "暂停播放",
                routing_context=RoutingContext(
                    has_current_song=True,
                    current_song_id=186016,
                    player_state="playing",
                    queue_size=1,
                    current_index=0,
                ),
            )
            if mode == "create"
            else None
        )
        values = await graph.ainvoke(initial, config, context=MusicAgentContext())
        after = checkpoint_summary(values)
        if mode == "create":
            passed = (
                after["status"] == "running"
                and not after["actions"]
                and after["handoff_count"] == 1
                and after["all_calls_paired"]
            )
        else:
            old_general = next(
                item for item in before["usage"] if item["agent"] == "general_chat"
            )
            new_general = next(
                item for item in after["usage"] if item["agent"] == "general_chat"
            )
            passed = (
                after["status"] == "completed"
                and after["run_id"] == before["run_id"]
                and old_general == new_general
                and after["handoff_count"] == before["handoff_count"]
                and after["transfer_call_count"] == before["transfer_call_count"]
                and after["all_calls_paired"]
                and [item["name"] for item in after["actions"]] == ["pause_player"]
            )
        return {
            "mode": mode,
            "thread_id": thread_id,
            "service_or_result_overrides": False,
            "interruption": "before_curator_node",
            "before": before,
            "after": after,
            "passed": passed,
        }
    finally:
        await close_runtime_checkpointer()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["create", "resume"])
    parser.add_argument("--thread-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    try:
        load_runtime_environment()
        report = asyncio.run(execute(args.mode, args.thread_id))
    except Exception as exc:
        report = {"passed": False, "error_type": type(exc).__name__, "mode": args.mode}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"mode": args.mode, "passed": report["passed"]}))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
