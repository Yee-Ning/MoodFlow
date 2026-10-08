"""Isolated rule/V5 comparison; capture provider answers, then replay offline.

Samples are existing public seed/test utterances, not production traffic.
No subgraph is dispatched. V5 fallback reuses the rule baseline as in Shadow,
and is always excluded from decision agreement rather than counted as success.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import hashlib
import json
import random
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from app.agent.decision.jev.client import (
    DecisionAnswer, DecisionResponse, JevClient, JevDecisionError,
)
from app.agent.decision.jev.router_v5 import JevRouterV5
from app.agent.foundation.routing import RoutingContext
from app.agent.handoff.routing import HandoffEntryRouter, plan_signature

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT.parent / "docs/reports/rule-layer-r2-20261003"
CONTEXT = RoutingContext(has_current_song=True, current_song_id=11)
SOURCES = [
    "data/intent/raw/manual_seeds.jsonl",
    "tests/test_rule_router.py", "tests/test_handoff_entry_rules.py",
    "tests/test_handoff_network.py", "tests/test_jev_router_v5.py",
    "tests/test_hybrid_graph.py", "tests/test_handoff_jev_routing.py",
]


def digest(value):
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()


def rule_plan(text):
    adapter = HandoffEntryRouter(None)
    clauses = adapter.preflight.inspect(text)
    if not clauses or adapter.preflight.preflight_question(clauses):
        return None
    decision = adapter.rule_plan(text, CONTEXT, clauses)
    return decision if decision and decision.is_complete and decision.tasks else None


def select_sample():
    samples, seen = [], set()
    for source in SOURCES:
        path = ROOT / source
        if path.suffix == ".jsonl":
            entries = [(index, json.loads(line)["text"])
                       for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)]
        else:
            entries = [(node.lineno, node.value)
                       for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
                       if isinstance(node, ast.Constant) and isinstance(node.value, str)]
        for line, text in sorted(entries):
            if not 2 <= len(text) <= 120 or text in seen or rule_plan(text) is None:
                continue
            seen.add(text)
            samples.append({"source": source, "line": line, "query_sha256": digest(text)})
    if len(samples) < 50:
        raise RuntimeError(f"Only {len(samples)} distinct fast-path utterances available")
    return random.Random(20261003).sample(samples, 50)


def query_for(sample):
    path = ROOT / sample["source"]
    content = path.read_text(encoding="utf-8")
    if path.suffix == ".jsonl":
        text = json.loads(content.splitlines()[sample["line"] - 1])["text"]
    else:
        text = next(node.value for node in ast.walk(ast.parse(content))
                    if isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and node.lineno == sample["line"]
                    and digest(node.value) == sample["query_sha256"])
    if digest(text) != sample["query_sha256"]:
        raise ValueError("sample_source_changed")
    return text


class CapturingClient:
    def __init__(self, delegate):
        self.delegate, self.calls = delegate, []

    async def decide(self, state, questions):
        row = {"state_sha256": digest(state), "questions": sorted(questions)}
        self.calls.append(row)
        try:
            response = await self.delegate.decide(state, questions)
        except JevDecisionError as exc:
            row["error_type"] = str(exc)
            raise
        row["response"] = asdict(response)
        return response


class ReplayClient:
    def __init__(self, calls):
        self.calls, self.used = calls, set()

    async def decide(self, state, questions):
        index = next((index for index, row in enumerate(self.calls)
                      if index not in self.used and row["state_sha256"] == digest(state)
                      and row["questions"] == sorted(questions)), None)
        if index is None:
            raise RuntimeError("missing_captured_answer")
        self.used.add(index)
        row = self.calls[index]
        if "error_type" in row:
            raise JevDecisionError(row["error_type"])
        response = {**row["response"], "answers": {
            name: DecisionAnswer(**answer)
            for name, answer in row["response"]["answers"].items()
        }}
        return DecisionResponse(**response)


def comparison(fast, trace):
    if trace.router_source == "legacy_hybrid_fallback":
        return {"comparable": False, "agrees": None,
                "attribution": trace.fallback_reason}
    agrees = plan_signature(fast) == plan_signature(trace.decision)
    fast_routes = [str(task.route_key) for task in fast.tasks]
    v5_routes = [str(task.route_key) for task in trace.decision.tasks]
    attribution = "agreement" if agrees else (
        "route_or_clarification" if fast_routes != v5_routes else "fragment_or_dependency"
    )
    return {"comparable": True, "agrees": agrees, "attribution": attribution}


async def run(mode):
    capture_path = REPORT / "provider-capture.json"
    if mode == "capture":
        samples = select_sample()
        provider = JevClient()
        capture = {"sample_origin": "public_manual_seed_and_test_utterances",
                   "production_traffic_count": 0, "context": CONTEXT.model_dump(mode="json"),
                   "sources_sha256": {source: digest((ROOT / source).read_text(encoding="utf-8"))
                                      for source in SOURCES}, "samples": samples, "cases": []}
    else:
        capture = json.loads(capture_path.read_text(encoding="utf-8"))
        samples = capture["samples"]
        if any(digest((ROOT / source).read_text(encoding="utf-8")) != checksum
               for source, checksum in capture["sources_sha256"].items()):
            raise ValueError("sample_source_changed")
    rows = []
    REPORT.mkdir(parents=True, exist_ok=True)
    for index, sample in enumerate(samples):
        text = query_for(sample)
        fast = rule_plan(text)
        if fast is None:
            raise RuntimeError("sample_no_longer_hits_fast_path")
        client = CapturingClient(provider) if mode == "capture" else ReplayClient(
            capture["cases"][index]["calls"],
        )
        trace = await JevRouterV5(client).route_with_trace(
            text, CONTEXT, fallback_decision=fast,
        )
        row = {**sample, **comparison(fast, trace),
               "rule_signature": plan_signature(fast),
               "v5_signature": plan_signature(trace.decision),
               "router_source": trace.router_source, "fallback_reason": trace.fallback_reason,
               "response_model": trace.response_model, "api_calls": trace.api_calls}
        rows.append(row)
        if mode == "capture":
            capture["cases"].append({"calls": client.calls, "comparison": row})
            capture_path.write_text(json.dumps(capture, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        elif len(client.used) != len(client.calls):
            raise RuntimeError("unused_captured_answer")
        print(f"{index + 1}/50 {trace.router_source} {row['attribution']}", flush=True)
    comparable = sum(row["comparable"] for row in rows)
    disagreement = sum(row["agrees"] is False for row in rows)
    summary = {"sample_size": len(rows), "production_traffic_count": 0,
               "comparable": comparable, "disagreements": disagreement,
               "disagreement_rate": disagreement / comparable if comparable else None,
               "fallback_count": len(rows) - comparable,
               "attribution": dict(Counter(row["attribution"] for row in rows)), "cases": rows}
    (REPORT / f"{mode}-comparison.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("capture", "replay"))
    asyncio.run(run(parser.parse_args().mode))
