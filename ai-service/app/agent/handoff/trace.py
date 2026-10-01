"""Allowlisted internal trace events; never include user text or credentials."""

import json
import logging
from hashlib import sha256

from app.agent.handoff.contracts import HandoffTrace

logger = logging.getLogger(__name__)


class TraceRecorder:
    def __init__(self, run_id, agent, *, events=(), sink=None):
        self.run_id = run_id
        self.agent = agent
        self.events = list(events)
        self.sink = sink

    def emit(
        self, *, code, tokens=0, duration_ms=0, target=None, call_id=None, score=None
    ):
        # Model-supplied call IDs are hashed as they may themselves contain text.
        identity = sha256(
            f"{self.run_id}:{call_id or len(self.events)}:{code}".encode()
        ).hexdigest()
        event = HandoffTrace(
            trace_id=self.run_id,
            run_id=self.run_id,
            tool_call_id=identity,
            source_agent=self.agent,
            target_agent=target or self.agent,
            code=code,
            guard_score=score,
            duration_ms=duration_ms,
            tokens=tokens,
        ).model_dump(mode="json")
        self.events.append(event)
        logger.info("handoff.trace %s", json.dumps(event, separators=(",", ":")))
        if self.sink is not None:
            try:
                self.sink(event.copy())
            except Exception:
                logger.warning("handoff.trace_sink_failed")
