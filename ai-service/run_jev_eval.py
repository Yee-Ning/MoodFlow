"""Prepare, replay and report Jev shadow diagnostic cases.

A JSONL path keeps the original decision-summary command available.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from app.agent.decision.jev.evaluation import summarize_decisions


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", help="JSONL path or v1/v2 prepare, replay and report command")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    if args.command == "prepare":
        from evals.jev_shadow_v1 import prepare_context, prepare_router
        print(prepare_router())
        print(prepare_context())
    elif args.command == "router":
        from evals.jev_shadow_v1 import run_router
        print(asyncio.run(run_router(args.limit)))
    elif args.command == "context":
        from evals.jev_shadow_v1 import run_context
        print(asyncio.run(run_context(args.limit)))
    elif args.command == "prepare-memory":
        from evals.jev_shadow_v1 import prepare_memory
        print(prepare_memory())
    elif args.command == "memory":
        from evals.jev_shadow_v1 import run_memory
        print(asyncio.run(run_memory(args.limit)))
    elif args.command == "report":
        from evals.jev_shadow_report import write_report
        print(write_report())
    elif args.command == "prepare-router-v2":
        from evals.jev_shadow_v2 import prepare_router_v2
        print(prepare_router_v2())
    elif args.command == "router-v2":
        from evals.jev_shadow_v2 import run_router_v2
        print(asyncio.run(run_router_v2(args.limit)))
    elif args.command == "prepare-memory-v2":
        from evals.jev_synthetic_memory_v2 import prepare_memory_v2
        print(prepare_memory_v2())
    elif args.command == "prepare-context-v2":
        from evals.jev_context_v2 import prepare_context_v2
        print(prepare_context_v2())
    elif args.command == "context-v2":
        from evals.jev_context_v2 import run_context_v2
        print(asyncio.run(run_context_v2(args.limit)))
    elif args.command == "memory-v2":
        from evals.jev_shadow_v2 import run_memory_v2

        print(asyncio.run(run_memory_v2(args.limit)))
    elif args.command == "report-v2":
        from evals.jev_shadow_v2_report import write_report_v2

        print(write_report_v2())
    else:
        path = Path(args.command)
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        print(json.dumps(summarize_decisions(rows), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
