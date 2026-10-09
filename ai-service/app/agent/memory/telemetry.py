"""Aggregate lifecycle signals; never include users, prompts or memory contents."""

import json
import logging


def record_memory_event(operation, *, outcome, latency_ms=None, count=None):
    fields = {"operation": operation, "outcome": outcome}
    if latency_ms is not None:
        fields["latency_ms"] = round(latency_ms, 2)
    if count is not None:
        fields["count"] = count
    logging.getLogger("moodflow.memory").info("memory.lifecycle %s", json.dumps(fields))
