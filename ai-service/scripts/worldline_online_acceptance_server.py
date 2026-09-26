"""Observe the unchanged production ASGI app during a real online acceptance.

No services, generators, validators or checkpoint savers are replaced. Python's
profile hook records selected completed calls, never changes their return value.
Do not use this observer as a substitute for real HTTP/browser requests.
"""

from __future__ import annotations

import argparse
from dataclasses import fields, is_dataclass
from datetime import datetime, timezone
import dis
import json
import logging
import os
from pathlib import Path
import sys
import threading
from time import perf_counter

import uvicorn


TARGETS = {
    "app.llm.client": {"_create_response"},
    "app.rag.retrieval.service": {"HybridRetrievalService.retrieve"},
    "app.agent.foundation.subgraphs.worldline.retrieval": {"_pack_documents", "_log_context_pack"},
    "app.agent.foundation.subgraphs.worldline.music_service": {
        "JavaMusicEntityService.search_entities", "JavaMusicEntityService.load_entity_facts",
    },
    "app.agent.foundation.subgraphs.worldline.era_expansion": {"EraExpansionService.expand"},
    "app.agent.foundation.subgraphs.worldline.scene_expansion": {"SceneExpansionService.expand"},
    "app.agent.foundation.subgraphs.worldline.influence_expansion": {"InfluenceExpansionService.expand"},
    "app.agent.foundation.subgraphs.worldline.session": {
        "WorldLineSessionService.create", "WorldLineSessionService.explore",
        "WorldLineSessionService.recover", "WorldLineSessionService._commit",
    },
    "app.agent.foundation.subgraphs.worldline.graph": {
        "build_worldline_subgraph.<locals>.prepare_query_context",
        "build_worldline_subgraph.<locals>.resolve_entity",
        "build_worldline_subgraph.<locals>.understand_query",
        "build_worldline_subgraph.<locals>.load_entity_facts",
        "build_worldline_subgraph.<locals>.retrieve_music_knowledge",
        "build_worldline_subgraph.<locals>.build_graph",
        "build_worldline_subgraph.<locals>.generate_narrative",
        "build_worldline_subgraph.<locals>.generate_branches",
    },
}
OMIT_FIELDS = {"parser_metadata", "api_key", "authorization", "password", "token", "headers", "settings"}


def observation(value):
    """Copy only serializable observations; omit credentials and parser trees."""
    if hasattr(value, "model_dump"):
        return observation(value.model_dump(mode="json"))
    if is_dataclass(value) and not isinstance(value, type):
        return observation({field.name: getattr(value, field.name) for field in fields(value)})
    if isinstance(value, dict):
        return {str(k): observation(v) for k, v in value.items() if str(k).lower() not in OMIT_FIELDS}
    if isinstance(value, (list, tuple)):
        return [observation(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return {"observed_type": type(value).__name__}


class AcceptanceObserver:
    def __init__(self, output: Path):
        output.parent.mkdir(parents=True, exist_ok=True)
        self.handle = output.open("a", encoding="utf-8", buffering=1)
        self.starts = {}
        self.error_count = 0
        self.write("observer_started", {
            "pid": os.getpid(), "app": "app.main:app", "mechanism": "passive_sys_setprofile",
            "service_or_result_overrides": False,
        })

    def write(self, stage: str, payload) -> None:
        self.handle.write(json.dumps({
            "at": datetime.now(timezone.utc).isoformat(), "pid": os.getpid(),
            "stage": stage, "observer_error_count": self.error_count, "payload": observation(payload),
        }, ensure_ascii=False) + "\n")

    def profile(self, frame, event, arg):
        module = frame.f_globals.get("__name__", "")
        if module not in TARGETS or frame.f_code.co_qualname not in TARGETS[module]:
            return
        try:
            key = id(frame)
            if event == "call":
                self.starts.setdefault(key, perf_counter())
                return
            if event != "return" or dis.opname[frame.f_code.co_code[frame.f_lasti]] not in {"RETURN_VALUE", "RETURN_CONST"}:
                return  # Coroutine await/yield is not a completed call.
            local = frame.f_locals
            payload = {"return": arg, "latency_ms": round((perf_counter() - self.starts.pop(key, perf_counter())) * 1000, 2)}
            for field in ("query", "plan", "session_id", "branch_id", "pack"):
                if field in local:
                    payload[field] = local[field]
            state = local.get("state")
            if isinstance(state, dict):
                payload["task_id"] = state.get("task_id")
                payload["user_query"] = state.get("user_query")
            if module == "app.llm.client":
                request = local.get("request", {})
                payload.update(model=request.get("model"), schema_name=(request.get("text", {}).get("format", {})).get("name"),
                               response_id=getattr(arg, "id", None), status=getattr(arg, "status", None))
            if module == "app.rag.retrieval.service":
                service = local["self"]
                payload["bound_manifest"] = str(service.binding.manifest_path)
                payload["index_manifest"] = service.binding.manifest
                payload["backend_class"] = type(service.backend).__name__
                payload["encoder_class"] = type(service.encoder).__name__
            if frame.f_code.co_qualname.endswith("._commit"):
                payload["committed_state"] = state
                payload["checkpointer_class"] = type(local["self"]._snapshots.checkpointer).__name__
            self.write(module + ":" + frame.f_code.co_qualname, payload)
        except Exception:
            # Observability cannot change production request success/failure.
            self.error_count += 1

    def close(self):
        self.write("observer_stopped", {"observation_errors": self.error_count})
        self.handle.close()


class EvidenceLogHandler(logging.Handler):
    def __init__(self, observer):
        super().__init__(logging.INFO)
        self.observer = observer

    def emit(self, record):
        try:
            self._emit_observation(record)
        except Exception:
            self.observer.error_count += 1

    def _emit_observation(self, record):
        if record.name.startswith("app."):
            names = {"thread_id", "node_name", "entity_id", "latency_ms", "success", "error_code",
                     "plan_id", "collection_name", "retrieval_status", "candidate_count", "failed_channels",
                     "retrieval_warnings", "retrieval_total_ms", "context_count", "context_token_count",
                     "context_token_budget", "context_warnings", "accepted_count", "rejected_count",
                     "schema_name", "attempt", "first_pass_success", "retry_count", "repair_applied",
                     "failure_type", "last_failure_type", "final_status", "era_expansion_trace",
                     "scene_expansion_trace", "influence_expansion_trace", "semantic_expansion_trace"}
            self.observer.write("application_log", {
                "logger": record.name, "level": record.levelname,
                "event": record.msg if isinstance(record.msg, str) else type(record.msg).__name__,
                **{name: record.__dict__[name] for name in names if name in record.__dict__},
                "exception_type": record.exc_info[0].__name__ if record.exc_info else None,
            })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    observer = AcceptanceObserver(args.trace)
    app_logger = logging.getLogger("app")
    app_logger.setLevel(logging.INFO)
    app_logger.propagate = False
    app_logger.addHandler(EvidenceLogHandler(observer))
    sys.setprofile(observer.profile)
    threading.setprofile(observer.profile)
    try:
        uvicorn.run("app.main:app", host="127.0.0.1", port=args.port)
    finally:
        sys.setprofile(None)
        threading.setprofile(None)
        observer.close()


if __name__ == "__main__":
    main()
