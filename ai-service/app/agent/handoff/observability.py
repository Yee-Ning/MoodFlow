"""Side-channel metadata only: never select plans, settle usage or apply receipts."""

import asyncio
import json
import logging
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import wraps
from hashlib import sha256
from time import perf_counter
from uuid import uuid4

logger = logging.getLogger(__name__)
current_observation = ContextVar("handoff_observation", default=None)
_logging_errors = 0


def digest(value):
    return sha256(value.encode()).hexdigest()


@dataclass
class Observation:
    graph_mode: str
    cohort: str
    sample_kind: str
    request_key: str
    thread_key: str
    started: float
    worker_started: bool = False
    finished: bool = False


def emit(marker, fields, *, warning=False):
    """Only callers' allowlisted metadata enters this channel; failures are counted."""
    global _logging_errors
    context = current_observation.get()
    metadata = {
        "graph_mode": context.graph_mode if context else "unknown",
        "cohort": context.cohort if context else "unknown",
        "sample_kind": context.sample_kind if context else "unknown",
        "request_key": context.request_key if context else None,
        "thread_key": context.thread_key if context else None,
    }
    event = {
        **metadata,
        **fields,
        "event_id": uuid4().hex,
        "at_utc": datetime.now(UTC).isoformat(),
        "logging_error_count": _logging_errors,
    }
    try:
        method = logger.warning if warning else logger.info
        method("%s %s", marker, json.dumps(event, separators=(",", ":")))
    except Exception:
        _logging_errors += 1
    return event


def begin_observation(
    graph_mode, thread_id, sample_kind="production", *, entrypoint="explicit"
):
    sample_kind = (
        sample_kind
        if sample_kind in {"production", "acceptance", "synthetic"}
        else "unknown"
    )
    context = Observation(
        graph_mode,
        ("canary" if graph_mode == "handoff" else "non_canary")
        if entrypoint == "chat"
        else "explicit",
        sample_kind,
        uuid4().hex,
        digest(str(thread_id)),
        perf_counter(),
    )
    token = current_observation.set(context)
    emit("handoff.request", {"phase": "started", "entrypoint": entrypoint})
    return context, token


def finish_observation(context, status):
    if context.finished:
        return
    context.finished = True
    emit(
        "handoff.request",
        {
            "phase": "finished",
            "status": str(status),
            "latency_ms": round((perf_counter() - context.started) * 1000, 2),
        },
    )


def end_observation(token):
    current_observation.reset(token)


def action_key(graph_mode, thread_key, action_id):
    return digest(f"{graph_mode}\0{thread_key}\0{action_id}")


def record_action(action):
    context = current_observation.get()
    if context is None:
        return
    emit(
        "handoff.action",
        {
            "phase": "generated",
            "name": str(action.name),
            "action_key": action_key(
                context.graph_mode, context.thread_key, action.action_id
            ),
        },
    )


def record_ack(identity, action_id, status):
    # Ownership was already resolved and the immutable receipt already saved.
    # Join to generated events at the receiver, preserving the original cohort/source.
    thread = identity.conversation_id
    if identity.graph_mode == "handoff":
        thread = thread.removeprefix("handoff:v1:")
    emit(
        "handoff.ack",
        {
            "graph_mode": identity.graph_mode,
            "action_key": action_key(identity.graph_mode, digest(thread), action_id),
            "status": "applied" if status == "applied" else "rejected",
        },
    )


def record_fallback(fields):
    event = emit("handoff.fallback", fields, warning=True)
    emit(
        "handoff.alert",
        {
            "alert_code": "v5_hybrid_fallback",
            "fallback_event_id": event["event_id"],
            "fallback_reason": fields.get("fallback_reason"),
            "attempt_class": fields.get("attempt_class"),
        },
        warning=True,
    )


def observe_execution(function):
    """Keep telemetry in the detached worker even after its SSE consumer leaves."""

    @wraps(function)
    async def observed(*args, **kwargs):
        context = current_observation.get()
        token = None
        if context is None:
            context, token = begin_observation(
                kwargs.get("graph_mode", "foundation"),
                kwargs.get("thread_id"),
                kwargs.get("telemetry_sample_kind", "production"),
            )
        context.worker_started = True
        original_emit = kwargs.get("emit", lambda event: None)

        def observed_emit(event):
            if getattr(event, "type", None) == "action":
                record_action(event.action)
            original_emit(event)

        kwargs["emit"] = observed_emit
        try:
            result = await function(*args, **kwargs)
        except asyncio.CancelledError:
            finish_observation(context, "cancelled")
            raise
        except Exception:
            finish_observation(context, "failed")
            raise
        else:
            finish_observation(context, getattr(result, "status", "unknown"))
            return result
        finally:
            if token is not None:
                end_observation(token)

    return observed


def observe_foundation_routing(function):
    @wraps(function)
    async def observed(*args, **kwargs):
        started = perf_counter()
        try:
            result = await function(*args, **kwargs)
        except Exception:
            emit(
                "foundation.routing",
                {
                    "status": "failed",
                    "latency_ms": round((perf_counter() - started) * 1000, 2),
                },
            )
            raise
        else:
            decision = result["route_decision"]
            emit(
                "foundation.routing",
                {
                    "status": "success",
                    "router_source": "hybrid",
                    "needs_clarification": decision.needs_clarification,
                    "tasks": len(decision.tasks),
                    "latency_ms": round((perf_counter() - started) * 1000, 2),
                },
            )
            return result

    return observed


def score_bucket(score):
    if score < 0.7:
        return "[0,.7)"
    if score < 0.8:
        return "[.7,.8)"
    if score < 0.9:
        return "[.8,.9)"
    return "[.9,1]"
