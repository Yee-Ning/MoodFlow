"""Lexical Journal selection and stateless pagination; no model or RAG lookup."""

import hashlib
import json
import re
import unicodedata

from .models import HistoryPointer

_WORD = re.compile(r"[a-z0-9_]+|[\u3400-\u9fff]+")
_STOP = frozenset({"the", "and", "for", "this", "that", "with", "what", "why", "how",
    "继续", "刚才", "那个", "这个", "任务", "什么", "怎么", "为何", "当时"})
_ADMIN = frozenset({"schema_version", "projection_version", "run_id", "task_id", "session_id",
    "request_id", "operation_id", "checkpoint_id", "event_id", "target_id", "content_hash",
    "fence", "revision", "token_count", "token_budget", "counter_name"})
_TYPE_WEIGHT = {"user_decision": 5, "structured_output": 4, "assistant_message": 3,
                "user_message": 2, "tool_result": 2, "agent_transfer": 1}
_MASK = (1 << 64) - 1
_TAG = 0x5144  # QD: query cursor, not an event sequence.


def is_query_cursor(cursor):
    return isinstance(cursor, int) and not isinstance(cursor, bool) and cursor >> 224 == _TAG


def normalize(text):
    return unicodedata.normalize("NFKC", text).casefold().strip()


def terms(query):
    weighted = {}
    for word in _WORD.findall(query):
        if word in _STOP:
            continue
        if re.fullmatch(r"[\u3400-\u9fff]+", word):
            if len(word) > 1:
                weighted[word] = 8
            for size, weight in ((2, 1), (3, 3)):
                for index in range(len(word) - size + 1):
                    part = word[index:index + size]
                    if part not in _STOP:
                        weighted.setdefault(part, weight)
        elif len(word) > 1 or word.isdigit():
            weighted[word] = 8
    # Bound lexical work on a long request; no semantic expansion or embedding.
    return tuple(sorted(weighted.items(), key=lambda item: (-item[1], item[0]))[:128])


def public_search_text(payload):
    values = []

    def collect(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key not in _ADMIN and not key.endswith(("_ref", "_refs")):
                    collect(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                collect(item)
        elif isinstance(value, str):
            values.append(normalize(value))
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            values.append(str(value))

    collect(payload)
    return "\n".join(values)


class QuerySelection:
    """A stable ranked snapshot. Existing integer cursor/HistoryPointer formats stay intact."""

    def __init__(self, history, identity, query, cursor, *, pointers=(), expand_artifacts=False):
        self.history, self.identity = history, identity
        self.pointers = tuple({(ref.kind, ref.target_id): ref for ref in pointers}.values())
        self.query = normalize(query)
        if len(query) > 4000:
            raise ValueError("drilldown query exceeds text budget")
        scope = json.dumps([identity.continuity_task_id, identity.conversation_id, self.query,
            [ref.model_dump() for ref in self.pointers], expand_artifacts], ensure_ascii=False, sort_keys=True)
        self.scope = int.from_bytes(hashlib.sha256(scope.encode()).digest()[:12], "big")
        latest = history.store.query_task_history(identity.continuity_task_id, descending=True, limit=1)
        high = latest[0].event_seq if latest else 0
        if cursor:
            if not is_query_cursor(cursor):
                raise ValueError("query drilldown requires its opaque query cursor")
            if (cursor >> 128) & ((1 << 96) - 1) != self.scope:
                raise ValueError("query drilldown cursor scope/query mismatch")
            self.snapshot, self.position = (cursor >> 64) & _MASK, cursor & _MASK
            if self.snapshot > high:
                raise ValueError("query drilldown cursor snapshot unavailable")
        else:
            self.snapshot, self.position = high, 0
        self.weighted_terms = terms(self.query)
        self.matchers = [(re.compile(re.escape(term) if re.search(r"[\u3400-\u9fff]", term)
            else r"(?<![a-z0-9_])" + re.escape(term) + r"(?![a-z0-9_])"), weight)
            for term, weight in self.weighted_terms]

    def cursor(self, position):
        if not 0 <= self.snapshot <= _MASK or not 0 <= position <= _MASK:
            raise ValueError("query drilldown cursor bounds exceeded")
        return (_TAG << 224) | (self.scope << 128) | (self.snapshot << 64) | position

    def _events(self):
        cursor = 0
        while cursor < self.snapshot:
            rows = self.history.store.query_task_history(self.identity.continuity_task_id, after_seq=cursor, limit=100)
            if not rows:
                return
            for row in rows:
                if row.event_seq > self.snapshot:
                    return
                cursor = row.event_seq
                if row.source == "typed_writer:v1":
                    yield HistoryPointer(kind="event", target_id=row.event_id), row

    def ranked(self, expand_artifacts):
        sources = ((ref, None) for ref in self.pointers) if self.pointers else self._events()
        found = {}
        for pointer, event in sources:
            record, payload = self.history.resolve_public(self.identity, pointer, explicit=True,
                expand_artifacts=expand_artifacts)
            if record is None:
                continue  # Selection never fabricates or returns missing refs.
            origin = event or record
            # Public text events can point at a safe artifact instead of inline
            # text. Rank its complete text, return its original lazy artifact ref.
            artifact_ref = payload.get("artifact_ref")
            if artifact_ref:
                pointer = HistoryPointer.model_validate(artifact_ref)
                record, payload = self.history.resolve_public(self.identity, pointer, explicit=True,
                    expand_artifacts=expand_artifacts)
                if record is None:
                    continue
            search = payload
            if pointer.kind == "artifact" and record.artifact_type == "public_text:v1":
                # resolve_public already checked schema, task ownership and hash.
                search = record.payload
            text = public_search_text(search)
            score = sum(weight for pattern, weight in self.matchers if pattern.search(text))
            if not score:
                continue
            kind = getattr(origin, "event_type", None)
            order = (score, _TYPE_WEIGHT.get(kind, 0), getattr(origin, "event_seq", None) or 0, pointer.target_id)
            key = pointer.kind, pointer.target_id
            candidate = (order, {"ref": pointer.model_dump(), "projection": payload})
            if key not in found or order > found[key][0]:
                found[key] = candidate
        ranked = [item for _, item in sorted(found.values(), key=lambda candidate: candidate[0], reverse=True)]
        if self.position > len(ranked):
            raise ValueError("query drilldown cursor position unavailable")
        return ranked
