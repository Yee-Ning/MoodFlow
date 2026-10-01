"""Closed-by-default, deterministic per-conversation graph selection."""

import hashlib


def select_chat_graph(settings, thread_id):
    mode = settings.agent_graph_mode
    if (
        mode != "foundation"
        or not getattr(settings, "handoff_canary_enabled", False)
        or not thread_id
    ):
        return mode
    salt = settings.handoff_canary_salt
    bucket = (
        int.from_bytes(
            hashlib.sha256((salt + "\0" + thread_id).encode("utf-8")).digest()[:8],
            "big",
        )
        % 10_000
    )
    return "handoff" if bucket < settings.handoff_canary_percentage * 100 else mode
