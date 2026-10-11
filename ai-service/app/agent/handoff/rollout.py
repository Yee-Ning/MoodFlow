"""Closed-by-default, deterministic per-conversation graph selection."""

import hashlib


def deterministic_bucket(namespace, identifier, *, bucket_count=10_000):
    """Shared hashing primitive; callers choose their own identity and namespace."""
    if type(bucket_count) is not int or bucket_count <= 0:
        raise ValueError("bucket count must be a positive integer")
    return int.from_bytes(hashlib.sha256((namespace + "\0" + identifier).encode("utf-8")).digest()[:8], "big") % bucket_count


def select_chat_graph(settings, thread_id):
    mode = settings.agent_graph_mode
    if (
        mode != "foundation"
        or not getattr(settings, "handoff_canary_enabled", False)
        or not thread_id
    ):
        return mode
    salt = settings.handoff_canary_salt
    bucket = deterministic_bucket(salt, thread_id)
    return "handoff" if bucket < settings.handoff_canary_percentage * 100 else mode
