"""Deterministic recovery plans; never dispatch tools or rewrite execution state."""

from .models import RecoveryPlan, RevalidationReport


class RecoveryPlanner:
    def decide(self, report: RevalidationReport, checkpoint=None, *, limits=None) -> RecoveryPlan:
        base = dict(
            run_id=report.run_id, remaining_deadline=report.remaining_deadline,
            evidence_refs=report.required_drilldown,
        )
        if checkpoint is not None and limits is not None and limits != checkpoint.run.limits:
            return RecoveryPlan(action="stop", blocked_reason="limits_mismatch", **base)
        if report.budget_state == "exhausted":
            return RecoveryPlan(action="stop", blocked_reason="original_run_limit_exhausted", **base)
        if report.run_status in {"completed", "failed"} or "terminal_transfer_rejection" in report.reasons:
            return RecoveryPlan(action="stop", blocked_reason="run_terminal", **base)
        if report.run_status == "needs_input":
            return RecoveryPlan(action="needs_input", blocked_reason="original_run_needs_input", **base)
        if (checkpoint is None or report.budget_state != "valid" or report.checkpoint_integrity != "valid"
                or report.run_id != checkpoint.run.run_id or report.current_agent != checkpoint.run.current_agent.value
                or report.ref_integrity != "valid" or report.transfer_integrity in {"conflict", "unknown"}):
            return RecoveryPlan(action="needs_input", blocked_reason="integrity_unproven", **base)
        if report.transfer_integrity == "missing_receipt":
            return RecoveryPlan(
                action="needs_input", blocked_reason="journal_reconciliation_required",
                required_reads=("reconcile_committed_receipt",), **base,
            )
        if report.write_status == "unknown":
            return RecoveryPlan(action="needs_input", blocked_reason="write_effect_unknown",
                                required_reads=("read_action_ack_or_operation_status",), **base)
        if any(reason in report.reasons for reason in ("current_authority_lost", "current_authority_scope_changed")):
            return RecoveryPlan(action="needs_input", blocked_reason="current_authority_incompatible", **base)
        unknown = tuple(field for field, status in report.field_results.items() if status == "unknown")
        if any(field in {"logged_in", "user_id", "write_permission"} for field in unknown):
            return RecoveryPlan(action="needs_input", blocked_reason="current_authority_unknown", **base)
        if unknown or report.invalidated_dependencies:
            return RecoveryPlan(action="refresh_facts", required_reads=(*unknown, *report.invalidated_dependencies), **base)
        # Finalizing means the last task is already recorded; continue the final node.
        return RecoveryPlan(
            action="resume_source" if report.transfer_integrity == "uncommitted" or not checkpoint.run.hops else "resume_target",
            resume_checkpoint_ref=checkpoint.ref, resume_agent=report.current_agent, **base,
        )
