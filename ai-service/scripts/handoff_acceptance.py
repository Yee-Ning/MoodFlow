"""Opt-in real HTTP acceptance. No graph, model, tool or result replacements."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
import logging
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import httpx

from app.schemas.chat import FoundationResponse
from app.schemas.client_actions import ClientAction


PRIVATE_KEYS = frozenset(
    {
        "handoff_run",
        "handoff_trace",
        "scratchpad",
        "original_input",
        "agent_usage",
        "api_key",
        "authorization",
        "password",
        "context_summary",
        "pending_writes",
    }
)


def assert_public(value):
    if isinstance(value, dict):
        if PRIVATE_KEYS.intersection(str(key).lower() for key in value):
            raise ValueError("private_field_in_public_protocol")
        for item in value.values():
            assert_public(item)
    elif isinstance(value, list):
        for item in value:
            assert_public(item)


def parse_sse(body: str) -> list[tuple[str, dict]]:
    events = []
    for block in body.replace("\r\n", "\n").split("\n\n"):
        if not block.strip() or block.startswith(":"):
            continue
        names, data = [], []
        for line in block.splitlines():
            if line.startswith("event:"):
                names.append(line[6:].strip())
            elif line.startswith("data:"):
                data.append(line[5:].lstrip())
        if len(names) != 1 or not data:
            raise ValueError("invalid_sse_frame")
        events.append((names[0], json.loads("\n".join(data))))
    return events


def validate_exchange(
    body,
    *,
    streaming=False,
    status="success",
    routes=(),
    actions=(),
    sources=False,
    allow_early_clarification=False,
):
    events = parse_sse(body) if streaming else []
    emitted = []
    if streaming:
        if not events or events[-1] != ("done", {}):
            raise ValueError("missing_terminal_done")
        if sum(name == "done" for name, _ in events) != 1:
            raise ValueError("duplicate_done")
        if any(
            name not in {"message", "update", "action", "final", "done"}
            for name, _ in events
        ):
            raise ValueError("unexpected_sse_event")
        finals = [data for name, data in events if name == "final"]
        if len(finals) != 1 or events[-2][0] != "final":
            raise ValueError("invalid_final_order")
        body = finals[0]
        emitted = [
            ClientAction.model_validate(data)
            for name, data in events
            if name == "action"
        ]
        if len({item.action_id for item in emitted}) != len(emitted):
            raise ValueError("duplicate_action")
        for _, data in events:
            assert_public(data)
    assert_public(body)
    response = FoundationResponse.model_validate(body)
    if response.status != status:
        raise ValueError("unexpected_response_status")
    early_clarification = (
        allow_early_clarification and status == "needs_input" and not response.results
    )
    if not early_clarification and tuple(
        item.subgraph for item in response.results
    ) != tuple(routes):
        raise ValueError("unexpected_task_plan")
    final_actions = [
        ClientAction.model_validate(raw)
        for item in response.results
        for raw in item.metadata.get("client_actions", [])
    ]
    if tuple(item.name.value for item in final_actions) != tuple(actions):
        raise ValueError("unexpected_client_actions")
    if len({item.action_id for item in final_actions}) != len(final_actions):
        raise ValueError("duplicate_final_action")
    if streaming and [item.model_dump() for item in emitted] != [
        item.model_dump() for item in final_actions
    ]:
        raise ValueError("action_final_mismatch")
    if sources and not any(
        item.sources and item.citations for item in response.results
    ):
        raise ValueError("missing_grounded_sources")
    return {
        "status": response.status,
        "routes": [item.subgraph for item in response.results],
        "action_names": [item.name.value for item in final_actions],
        "event_names": [name for name, _ in events],
        "source_count": sum(len(item.sources) for item in response.results),
        "citation_count": sum(len(item.citations) for item in response.results),
    }


def cases():
    player = {
        "has_current_song": True,
        "current_song_id": 186016,
        "player_state": "playing",
        "queue_size": 1,
        "current_index": 0,
    }
    return [
        dict(
            id="foundation_compat",
            endpoint="foundation",
            message="你好",
            routes=["general_chat"],
        ),
        dict(
            id="handoff_chat",
            endpoint="handoff",
            message="你好",
            routes=["general_chat"],
        ),
        dict(
            id="missing_song",
            endpoint="handoff",
            message="分析当前这首歌",
            routes=["current_song"],
            status="needs_input",
            allow_early_clarification=True,
        ),
        dict(
            id="song_read_only",
            endpoint="handoff",
            message="分析当前这首歌的情绪，不要播放或收藏",
            routes=["current_song"],
            player_context=player,
        ),
        dict(
            id="search_read_only",
            endpoint="handoff",
            message="搜索周杰伦的晴天，不要播放",
            routes=["tool_calling"],
        ),
        dict(
            id="pause_sse",
            endpoint="chat",
            message="暂停播放",
            routes=["tool_calling"],
            actions=["pause_player"],
            player_context=player,
        ),
        dict(
            id="multi_agent_sse",
            endpoint="chat",
            message="先暂停播放，然后陪我聊聊",
            routes=["tool_calling", "general_chat"],
            actions=["pause_player"],
            player_context=player,
        ),
        dict(
            id="worldline_grounding",
            endpoint="handoff",
            message="探索周杰伦的音乐世界线",
            routes=["worldline"],
            sources=True,
        ),
    ]


def run_acceptance(base_url, output):
    prefix = "phase6:" + uuid4().hex[:12]
    rows = []
    with httpx.Client(
        base_url=base_url,
        timeout=180,
        trust_env=False,
        headers={"X-MoodFlow-Telemetry-Class": "acceptance"},
    ) as client:
        try:
            health = client.get("/health")
            health_ok = health.status_code == 200 and health.json() == {"status": "ok"}
        except Exception as exc:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                json.dumps(
                    {
                        "passed": False,
                        "cases": [
                            {
                                "id": "health",
                                "passed": False,
                                "error_type": type(exc).__name__,
                            },
                        ],
                        "business_cases_performed": False,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            return 2
        rows.append(
            {
                "id": "health",
                "passed": health_ok,
            }
        )
        for index, invalid in enumerate(
            [
                {"message": " "},
                {"message": "x", "unknown": True},
                {"message": "x", "player_context": {"volume": 1.1}},
            ]
        ):
            response = client.post("/api/ai/handoff", json=invalid)
            rows.append(
                {
                    "id": f"validation_{index}",
                    "http_status": response.status_code,
                    "passed": response.status_code == 422,
                }
            )
        for case in cases():
            response = None
            thread = prefix + ":" + case["id"]
            payload = {"message": case["message"], "thread_id": thread}
            if "player_context" in case:
                payload["player_context"] = case["player_context"]
            started = perf_counter()
            row = {
                "id": case["id"],
                "thread_id": thread,
                "endpoint": case["endpoint"],
                "passed": False,
            }
            try:
                response = client.post("/api/ai/" + case["endpoint"], json=payload)
                row["http_status"] = response.status_code
                response.raise_for_status()
                row.update(
                    validate_exchange(
                        response.text
                        if case["endpoint"] == "chat"
                        else response.json(),
                        streaming=case["endpoint"] == "chat",
                        status=case.get("status", "success"),
                        routes=case["routes"],
                        actions=case.get("actions", ()),
                        sources=case.get("sources", False),
                        allow_early_clarification=case.get(
                            "allow_early_clarification", False
                        ),
                    )
                )
                row["passed"] = True
            except Exception as exc:
                row["error_type"] = type(exc).__name__
                if isinstance(exc, ValueError) and str(exc) in {
                    "unexpected_response_status",
                    "unexpected_task_plan",
                    "unexpected_client_actions",
                    "missing_grounded_sources",
                }:
                    row["failure_code"] = str(exc)
                # Only public validated schema is retained; never save headers or upstream errors.
                if response is not None and response.is_success:
                    try:
                        public = (
                            [
                                data
                                for name, data in parse_sse(response.text)
                                if name == "final"
                            ][0]
                            if case["endpoint"] == "chat"
                            else response.json()
                        )
                        assert_public(public)
                        row["observed_response"] = FoundationResponse.model_validate(
                            public
                        ).model_dump(mode="json")
                    except Exception:
                        pass
            row["latency_ms"] = round((perf_counter() - started) * 1000, 2)
            rows.append(row)
            print(
                json.dumps(
                    {
                        key: row[key]
                        for key in ("id", "passed", "latency_ms", "failure_code")
                        if key in row
                    }
                ),
                flush=True,
            )
    report = {
        "at": datetime.now(UTC).isoformat(),
        "synthetic_http_inputs": True,
        "service_or_result_overrides": False,
        "prefix": prefix,
        "cases": rows,
        "passed": all(row["passed"] for row in rows),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return 0 if report["passed"] else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8001")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    return run_acceptance(args.base_url, args.output)


if __name__ == "__main__":
    raise SystemExit(main())
