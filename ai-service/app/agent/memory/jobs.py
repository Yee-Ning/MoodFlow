"""Leased extraction jobs with pre-reserved original budget and bounded dispatch."""

import asyncio
import contextvars
from datetime import UTC, datetime, timedelta
from time import monotonic
from types import SimpleNamespace
from uuid import uuid4

from app.agent.foundation.long_horizon.history_writer import HistoryWriter, digest
from app.agent.foundation.long_horizon.models import ContinuityIdentity, HistoryEventType
from app.agent.foundation.long_horizon.public_events import PublicUsage
from app.agent.structured_output import StructuredOutputError
from app.agent.structured_output.parser import model_output_from_response
from app.llm.client import create_structured_response, _provider_compatible_schema
from app.llm.runtime import ProviderBudget, active_ledger, estimate_tokens, reported_tokens
from .extraction import MEMORY_EXTRACTION_PROMPT, MemoryExtractor
from .models import MemoryExtractionOutput, MemoryJob
from .policy import MemoryAdmissionError, safe_user_message, validate_candidate


class MemoryJobWorker:
    def __init__(self, runtime, *, provider=None, history_store=None):
        self.runtime, self.store = runtime, runtime.service.store
        self.provider = provider or create_structured_response
        self.history_store = history_store
        self.volatile_ledgers = {}
        self.task = None
        self.wake = asyncio.Event()
        self.stopping = False

    def _history(self):
        if self.history_store is not None:
            return self.history_store
        from app.agent.foundation.long_horizon.runtime import get_long_horizon_service
        return get_long_horizon_service().store

    def _budget(self, message):
        from app.config.settings import get_settings
        from app.llm.budget import output_limit
        model = get_settings().model
        output = min(2048, output_limit(model))
        shape = {"model": model, "instructions": MEMORY_EXTRACTION_PROMPT,
            "input": [{"role": "user", "content": message}], "reasoning": {"effort": "none"},
            "max_output_tokens": output, "temperature": 0,
            "text": {"format": {"type": "json_schema", "name": "memory_extraction_v1",
                               "schema": _provider_compatible_schema(MemoryExtractionOutput.model_json_schema()), "strict": True}}}
        input_limit = estimate_tokens(shape) + 512
        if input_limit > 8192:
            return None
        amount = 2 * (input_limit + output + 256)
        scope = {"kind": "standalone", "model": model, "input_limit": input_limit, "output_limit": output,
                 "reserved": amount, "deadline": (datetime.now(UTC) + timedelta(seconds=120)).isoformat()}
        ledger = active_ledger.get()
        if ledger is None:
            return scope
        remaining_time = ledger.deadline - monotonic()
        if ledger.blocked or ledger.remaining < amount or remaining_time <= 0:
            return None
        ledger.reserve(amount)
        scope.update(kind="volatile", agent=ledger.current_agent,
                     deadline=(datetime.now(UTC) + timedelta(seconds=remaining_time)).isoformat())
        if ledger.accounting is not None:
            call_id = ledger.accounting.reserve(ledger, amount, model=model, billing_agent=ledger.current_agent)
            scope.update(kind="durable", call_id=call_id,
                         identity=ledger.accounting.identity.model_dump(mode="json"))
        scope["volatile_id"] = uuid4().hex
        self.volatile_ledgers[scope["volatile_id"]] = ledger
        return scope

    async def enqueue(self, *, user_id, source_thread_id, user_message, source_message_id=None):
        if not safe_user_message(user_message) or not user_message.strip():
            return False
        preferences = await asyncio.to_thread(self.store.preferences, user_id)
        if not preferences.enabled:
            return False
        message_id = source_message_id or "legacy:" + uuid4().hex
        job_id = "memory-job:" + digest([user_id, message_id])[7:]
        source_hash = digest([source_thread_id, user_message])
        previous = await asyncio.to_thread(self.store.get_job, user_id, job_id)
        if previous:
            if previous.source_digest != source_hash:
                raise ValueError("memory job idempotency conflict")
            return True
        scope = self._budget(user_message)
        if scope is None:
            return False
        job = MemoryJob(job_id=job_id, user_id=user_id, source_thread_id=source_thread_id,
            source_message_id=message_id, user_message=user_message, source_digest=source_hash,
            generation=preferences.generation, budget=scope, created_at=datetime.now(UTC))
        try:
            inserted = await asyncio.to_thread(self.store.enqueue_job, job)
        except BaseException:
            await self._settle(job, tokens=0)
            raise
        if not inserted:
            await self._settle(job, tokens=0)
        self.start()
        self.wake.set()
        return True

    def start(self):
        if self.task is None or self.task.done():
            self.stopping = False
            # No expired call lease, prompts, private messages or active ledger
            # leak from the original coroutine into a background provider call.
            self.task = asyncio.create_task(self._loop(), context=contextvars.Context())

    async def stop(self):
        self.stopping = True
        self.wake.set()
        if self.task:
            try:
                await asyncio.wait_for(asyncio.shield(self.task), 30)
            except TimeoutError:
                self.task.cancel()
                await asyncio.gather(self.task, return_exceptions=True)
        self.task = None

    def _authorize(self, job):
        if job.cancelled or job.attempts > 3:
            return False
        preferences = self.store.preferences(job.user_id)
        if not preferences.enabled or preferences.generation != job.generation:
            return False
        if job.extraction is not None:
            return True  # No new dispatch: finish already validated, paid work.
        if datetime.fromisoformat(job.budget["deadline"]) <= datetime.now(UTC):
            return False
        if job.budget["kind"] == "volatile":
            return job.budget.get("volatile_id") in self.volatile_ledgers
        if job.budget["kind"] == "durable":
            identity = ContinuityIdentity.model_validate(job.budget["identity"])
            event = self._history().get_history_event(HistoryWriter.event_id(identity, f"usage:{job.budget['call_id']}:reserved"))
            if event is None:
                return False
            usage = PublicUsage.model_validate(event.payload)
            return (usage.run_id, usage.agent, usage.phase, usage.tokens) == (
                identity.run_id, job.budget["agent"], "reserved", job.budget["reserved"])
        return job.budget["kind"] == "standalone"

    async def _settle(self, job, *, tokens):
        if job.budget["kind"] == "durable":
            if not await asyncio.to_thread(self._authorize_receipt, job):
                return
            identity = ContinuityIdentity.model_validate(job.budget["identity"])
            writer = HistoryWriter(self._history())
            await asyncio.to_thread(writer.append_public_event, identity,
                f"usage:{job.budget['call_id']}:settled", HistoryEventType.MODEL_USAGE,
                PublicUsage(run_id=identity.run_id, agent=job.budget["agent"], call_id=job.budget["call_id"],
                            phase="settled", tokens=tokens, reported=True, model=job.budget["model"]))
        ledger = self.volatile_ledgers.pop(job.budget.get("volatile_id"), None)
        if ledger is not None:
            ledger.settle(job.budget["reserved"], SimpleNamespace(usage={"total_tokens": tokens}),
                          owner=job.budget["agent"])

    def _authorize_receipt(self, job):
        if job.budget["kind"] != "durable":
            return False
        identity = ContinuityIdentity.model_validate(job.budget["identity"])
        event = self._history().get_history_event(HistoryWriter.event_id(identity, f"usage:{job.budget['call_id']}:reserved"))
        if event is None:
            return False
        usage = PublicUsage.model_validate(event.payload)
        return (usage.run_id, usage.agent, usage.call_id, usage.phase, usage.tokens) == (
            identity.run_id, job.budget["agent"], job.budget["call_id"], "reserved", job.budget["reserved"])

    async def process_one(self):
        owner = uuid4().hex
        job = await asyncio.to_thread(self.store.claim_job, owner)
        if job is None:
            return False
        status = "cancelled" if job.cancelled else "blocked"
        try:
            if not await asyncio.to_thread(self._authorize, job):
                return True
            completed = await asyncio.to_thread(self.store.prepare_message, job.user_id, job.source_message_id, job.source_digest)
            if completed:
                status = "completed"
                return True
            async def generate(attempt, feedback):
                if not await asyncio.to_thread(self._authorize, job):
                    raise MemoryAdmissionError("original_memory_budget_unavailable")
                if not await asyncio.to_thread(self.store.dispatch_job, job.job_id, owner):
                    raise MemoryAdmissionError("memory_dispatch_limit")
                response = await self.provider([{"role": "user", "content": job.user_message
                    + (f"\nValidation feedback: {feedback}" if feedback else "")}], MEMORY_EXTRACTION_PROMPT,
                    MemoryExtractionOutput.model_json_schema(), schema_name="memory_extraction_v1",
                    model=job.budget["model"], max_output_tokens=job.budget["output_limit"],
                    request_budget=ProviderBudget(input_tokens=job.budget["input_limit"],
                        total_tokens=job.budget["input_limit"] + job.budget["output_limit"] + 256, safety_margin=256))
                await asyncio.to_thread(self.store.observe_job, job.job_id, owner, reported_tokens(response))
                return model_output_from_response(response)
            if job.extraction is not None:
                output = MemoryExtractionOutput.model_validate(job.extraction)
            else:
                remaining = (datetime.fromisoformat(job.budget["deadline"]) - datetime.now(UTC)).total_seconds()
                async with asyncio.timeout(max(0, remaining)):
                    output = await MemoryExtractor().extract(job.user_message, generator=generate)
                await asyncio.to_thread(self.store.prepare_job, job.job_id, owner, output.model_dump(mode="json"))
            for candidate in output.memories:
                validate_candidate(candidate, user_message=job.user_message)
                await asyncio.to_thread(self.runtime.service.remember, user_id=job.user_id,
                    source_thread_id=job.source_thread_id, source_message_id=job.source_message_id,
                    candidate=candidate, user_message=job.user_message, expected_generation=job.generation)
            await asyncio.to_thread(self.store.complete_message, job.user_id, job.source_message_id)
            await self.runtime.sync_profile(job.user_id)
            status = "completed"
            return True
        except (StructuredOutputError, MemoryAdmissionError):
            status = "rejected"
            return True
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("memory job blocked error_type=%s", type(exc).__name__)
            if job.attempts < 3 and isinstance(exc, (ConnectionError, OSError)):
                status = "pending"
            return True
        finally:
            observed = await asyncio.to_thread(self.store.get_job, job.user_id, job.job_id)
            # A crash between dispatch and observation leaves the entire
            # reservation intact, even if a later retry has known usage.
            if observed.usage_known and observed.observed_dispatches == observed.dispatches:
                await self._settle(observed, tokens=observed.reported_tokens)
            await asyncio.to_thread(self.store.finish_job, job.job_id, owner, status)
            from .telemetry import record_memory_event
            record_memory_event("extract_job", outcome=status)

    async def _loop(self):
        while not self.stopping:
            try:
                if await self.process_one():
                    continue
            except Exception as exc:
                import logging
                logging.getLogger(__name__).warning("memory worker unavailable error_type=%s", type(exc).__name__)
            self.wake.clear()
            try:
                await asyncio.wait_for(self.wake.wait(), 1)
            except TimeoutError:
                pass
