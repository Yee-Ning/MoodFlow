"""Narrative-only projection with lossless, per-attempt deletion audits."""

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from hashlib import sha256
import json
import logging

logger = logging.getLogger(__name__)
_audit_events = ContextVar("worldline_narrative_audit_events", default=None)


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def payload_sha256(value):
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


@contextmanager
def narrative_audit_scope():
    """Optional call-local collector; production also writes complete JSON logs."""
    events = []
    token = _audit_events.set(events)
    try:
        yield events
    finally:
        _audit_events.reset(token)


def emit_narrative_audit(event):
    encoded = canonical_json(event)
    logger.info("%s %s", event["event"], encoded)
    collector = _audit_events.get()
    if collector is not None:
        collector.append(deepcopy(event))


def normalize_narrative(payload, attempt, *, invocation_id, allowed_keys):
    normalized = {key: value for key, value in payload.items() if key in allowed_keys}
    removed = []
    for key, value in payload.items():
        if key in allowed_keys:
            continue
        encoded = canonical_json(value)
        removed.append({"key": key, "value_type": type(value).__name__,
            "value_summary": encoded[:256], "summary_truncated": len(encoded) > 256,
            "value": deepcopy(value), "value_sha256": payload_sha256(value)})
    if removed:
        emit_narrative_audit({"event": "worldline.narrative_normalization.v1",
            "schema_name": "worldline_narrative", "invocation_id": invocation_id,
            "attempt": attempt, "allowed_keys": sorted(allowed_keys),
            "removed_count": len(removed), "removed_fields": removed,
            "raw_payload_sha256": payload_sha256(payload),
            "normalized_payload_sha256": payload_sha256(normalized),
            "payload_hash_encoding": "canonical JSON UTF-8, sorted keys, no spaces",
            "normalization_applied": True})
    return normalized
