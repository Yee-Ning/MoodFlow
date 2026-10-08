"""Account local retrieval inference through the existing reserve/settle ledger."""

from threading import RLock
from types import SimpleNamespace
from uuid import uuid4

from app.llm.runtime import CallUsage, RuntimeLimitError, active_ledger
from app.rag.chunk.token_counter import UnicodeHeuristicTokenCounter


class RagBudgetRefusal(RuntimeLimitError):
    """Preserve the runtime refusal type across optional-source error handling."""


class RagAccountingFailure(RuntimeError):
    """A missing durable bill must abort rather than become optional-source fallback."""


class RetrievalUsage:
    def __init__(self):
        self.ledger = active_ledger.get()
        self.records = []
        self.accounting_failure = False

    def check(self):
        if self.accounting_failure:
            raise RagAccountingFailure("rag_accounting_unavailable")
        if self.ledger is not None:
            try:
                self.ledger.check()
            except RuntimeLimitError as exc:
                raise RagBudgetRefusal(str(exc)) from exc

    def run(self, operation, *, stage, model, revision, tokens, estimated=False):
        """Reserve before forward; unknown failures retain the full reservation bill."""
        ledger = self.ledger
        owner = ledger.current_agent if ledger is not None else None
        accounting = ledger.accounting if ledger is not None else None
        basis = "unicode_heuristic" if estimated else "local_model_tokenizer"
        record = {"call_id": uuid4().hex, "stage": stage, "model": model,
                  "model_revision": revision, "input_tokens": tokens, "output_tokens": 0,
                  "total_tokens": tokens, "estimated": estimated, "usage_basis": basis,
                  "status": "unknown", "budget_scope": "l2+l3" if ledger else "unscoped"}
        if ledger is not None:
            try:
                ledger.reserve(tokens)
            except RuntimeLimitError as exc:
                raise RagBudgetRefusal(str(exc)) from exc
            try:
                if accounting is not None:
                    # Non-generative calls have no output cap. PublicUsage requires a
                    # positive cap when supplied, so deliberately leave it absent.
                    record["call_id"] = accounting.reserve(ledger, tokens, input_tokens=tokens, model=model)
            except BaseException:
                ledger.reserved -= tokens
                self.accounting_failure = True
                raise
        self.records.append(record)
        completed = False
        try:
            result = operation()
            completed = True
            return result
        finally:
            unknown = not completed
            usage = CallUsage(tokens, 0, estimated or unknown,
                              "reserved_unknown" if unknown else basis)
            response = None if usage.estimated else SimpleNamespace(
                usage_metadata={"input_tokens": tokens, "output_tokens": 0, "total_tokens": tokens})
            if ledger is not None:
                ledger.settle(tokens, response, usage=usage, owner=owner)
                if accounting is not None and completed:
                    try:
                        accounting.settle(ledger, record["call_id"], tokens, response,
                                          usage=usage, model=model, owner=owner)
                    except Exception:
                        self.accounting_failure = True
                        raise
                    from app.agent.foundation.context_pointers import EventPointer
                    identity = accounting.identity
                    record["usage_ref"] = EventPointer(task_id=identity.continuity_task_id,
                        event_id=accounting.writer.event_id(identity, f"usage:{record['call_id']}:settled")) \
                        .model_dump(mode="json")
                elif accounting is not None:
                    # A reserve is the durable conservative bill until reconciliation.
                    from app.agent.foundation.context_pointers import EventPointer
                    identity = accounting.identity
                    record["usage_ref"] = EventPointer(task_id=identity.continuity_task_id,
                        event_id=accounting.writer.event_id(identity, f"usage:{record['call_id']}:reserved")) \
                        .model_dump(mode="json")
            record.update(status="completed" if completed else "unknown",
                          estimated=usage.estimated, usage_basis=usage.basis)


class AccountedQueryEncoder:
    def __init__(self, encoder, config, usage, lock=None):
        self.encoder, self.config, self.usage = encoder, config, usage
        self.lock = lock or RLock()

    def encode_queries(self, texts):
        with self.lock:
            self.usage.check()
            # Count precisely the formatted, truncated sequences used by Qwen.
            from app.rag.retrieval.encoder import QwenQueryEncoder
            if isinstance(self.encoder, QwenQueryEncoder):
                dense = self.encoder._encoder
                dense._ensure_loaded()
                formatted = [self.encoder._format_query(text) for text in texts]
                encoded = dense._tokenizer(formatted, padding=False, truncation=True,
                                           max_length=self.config.max_length)
                tokens = sum(len(ids) for ids in encoded["input_ids"])
                estimated = False
            else:
                tokens = sum(UnicodeHeuristicTokenCounter().count(text) for text in texts)
                estimated = True
            return self.usage.run(lambda: self.encoder.encode_queries(texts), stage="embedding",
                model="rag-embedding:" + self.config.model_name, revision=self.config.model_revision,
                tokens=tokens, estimated=estimated)


class AccountedRerankScorer:
    def __init__(self, scorer, config, usage, lock=None):
        self.scorer, self.config, self.usage = scorer, config, usage
        self.lock = lock or RLock()

    def prepare(self):
        with self.lock:
            self.usage.check()
            if hasattr(self.scorer, "prepare"):
                self.scorer.prepare()

    def score(self, inputs):
        with self.lock:
            self.usage.check()
            from app.rag.reranking.runtime import QwenRerankScorer
            if isinstance(self.scorer, QwenRerankScorer):
                self.scorer.prepare()
                tokens = sum(len(self.scorer._encode_pair(item)) for item in inputs)
                estimated = False
            else:
                tokens = sum(UnicodeHeuristicTokenCounter().count(item.query + item.document) for item in inputs)
                estimated = True
            return self.usage.run(lambda: self.scorer.score(inputs), stage="rerank",
                model="rag-rerank:" + self.config.name, revision=self.config.revision,
                tokens=tokens, estimated=estimated)
