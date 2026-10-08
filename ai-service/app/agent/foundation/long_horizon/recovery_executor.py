"""Execute only the plan's permitted reads, then revalidate before execution."""

from .models import LiveSnapshot
from .public_events import PublicRevalidation
from .models import HistoryEventType
from .recovery import RecoveryPlanner
from .revalidation import RealityRevalidator
from .history_writer import digest


class RecoveryExecutor:
    def __init__(self, writer, journal, *, read_facts=None, read_effects=None):
        self.writer, self.journal = writer, journal
        self.read_facts, self.read_effects = read_facts, read_effects
        self.validator = RealityRevalidator(writer.store)
        self.planner = RecoveryPlanner()

    async def execute(self, projection, live, snapshot, *, operations=None, receipt_gaps=()):
        def verify(current_live):
            report = self.validator.revalidate(projection, current_live, snapshot)
            report = report.model_copy(update={"receipt_gaps": receipt_gaps})
            if operations is not None:
                totals = operations.accounting.totals(snapshot.run.run_id,
                    [item.model_dump(mode="json") for item in snapshot.run.usage])
                if (sum(item["tokens"] for item in totals) >= snapshot.run.limits.run_token_budget
                    or any(item["tokens"] >= snapshot.run.limits.agent_token_budget
                        or item["retries"] > snapshot.run.limits.max_retries_per_agent for item in totals)):
                    report = report.model_copy(update={"budget_state": "exhausted"})
            if operations is not None and operations.unknown_writes(snapshot.run.run_id):
                report = report.model_copy(update={"write_status": "unknown",
                    "reasons": (*report.reasons, "operation_effect_unknown")})
            return report
        report = verify(live)
        plan = self.planner.decide(report, snapshot)
        for attempt in range(3):
            body = PublicRevalidation(run_id=snapshot.run.run_id, field_results=report.field_results,
                transfer_integrity=report.transfer_integrity, action=plan.action, reason=plan.blocked_reason,
                checkpoint_id=snapshot.ref.checkpoint_id, receipt_gaps=receipt_gaps,
                revalidation_reasons=tuple(dict.fromkeys(report.reasons)))
            self.writer.append_public_event(snapshot.identity,
                "revalidation:" + digest([snapshot.identity.request_id, snapshot.ref.checkpoint_id, attempt,
                    body.model_dump(mode="json")])[7:], HistoryEventType.REALITY_REVALIDATION, body)
            if "reconcile_committed_receipt" in plan.required_reads:
                self.journal.confirm_checkpoint(snapshot, reconcile=True)
            elif "read_action_ack_or_operation_status" in plan.required_reads and self.read_effects is not None:
                live = LiveSnapshot.model_validate(await self.read_effects(snapshot, live))
            elif plan.action == "refresh_facts" and self.read_facts is not None:
                refreshed = LiveSnapshot.model_validate(await self.read_facts(plan.required_reads, snapshot, live))
                # A read can resolve an unknown, but a changed user target requires
                # new authorization; never rewrite the old goal to make it valid.
                live = refreshed
            else:
                break
            report = verify(live)
            plan = self.planner.decide(report, snapshot)
        if operations is not None and operations.unknown_writes(snapshot.run.run_id):
            report = report.model_copy(update={"write_status": "unknown", "reasons": (*report.reasons, "operation_effect_unknown")})
            plan = self.planner.decide(report, snapshot)
        if plan.action == "refresh_facts":
            # Fresh facts still disagree with an original task's input/constraints.
            # Propagate changed/unknown to context and wait; reads grant no write authority.
            plan = plan.model_copy(update={"action": "needs_input", "blocked_reason": "fresh_facts_require_authorization"})
        final = PublicRevalidation(run_id=snapshot.run.run_id, field_results=report.field_results,
            transfer_integrity=report.transfer_integrity, action=plan.action, reason=plan.blocked_reason,
            checkpoint_id=snapshot.ref.checkpoint_id, receipt_gaps=receipt_gaps,
            revalidation_reasons=tuple(dict.fromkeys(report.reasons)))
        self.writer.append_public_event(snapshot.identity,
            "revalidation-final:" + digest([snapshot.identity.request_id, final.model_dump(mode="json")])[7:],
            HistoryEventType.REALITY_REVALIDATION, final)
        return plan, report, live
