"""Repair accounting from saved evidence only; no business executor is available."""

import re

from app.agent.handoff.continuity import transfer_identity

from .execution import ContinuityConflict
from .history_writer import digest
from .models import HistoryEventType, HistoryPointer
from .public_events import PublicRevalidation, RecoveryGap


def pointer(event):
    return HistoryPointer(kind="event", target_id=event.event_id)


class RecoveryReceiptRepair:
    def __init__(self, writer, journal):
        self.writer, self.journal = writer, journal

    def event(self, identity, key):
        return self.writer.store.get_history_event(self.writer.event_id(identity, key))

    def scan(self, snapshot, operations):
        """Compare the saver-owned run with durable receipts, not caller input."""
        gaps, run, identity = [], snapshot.run, snapshot.identity
        # Migration keeps ACK ownership on the original action/session. Reading
        # the same server-owned task must not label an existing ACK as missing.
        acknowledgements, cursor = {}, 0
        while True:
            rows = self.writer.store.query_task_history(identity.continuity_task_id,
                event_type=HistoryEventType.ACTION_ACK, after_seq=cursor, limit=100)
            if not rows:
                break
            for row in rows:
                acknowledgements[row.payload["action_id"]] = row
                cursor = row.event_seq
        for oid in sorted(operations.unknown_writes(run.run_id)):
            gaps.append(RecoveryGap(kind="write_success", subject_id=oid, reason="operation_effect_unknown"))
        for hop in run.hops:
            tid = transfer_identity(run.run_id, hop.tool_call_id)
            if not any(self.event(identity, tid + ":" + phase) for phase in ("committed", "reconciled")):
                proposal = self.event(identity, tid + ":proposed")
                gaps.append(RecoveryGap(kind="transfer_committed", subject_id=tid,
                    reason="missing_committed_receipt" if proposal else "transfer_proposal_missing",
                    evidence_refs=(pointer(proposal),) if proposal else ()))
        for result in run.results:
            key = f"result:{run.run_id}:{result.task_id}"
            if self.event(identity, key) is None:
                gaps.append(RecoveryGap(kind="task_result", subject_id=result.task_id,
                    reason="result_receipt_missing"))
            for action in result.data.get("client_actions", []):
                aid = action["action_id"]
                receipt = acknowledgements.get(aid)
                if receipt is None or receipt.payload["status"] != "applied":
                    gaps.append(RecoveryGap(kind="action_ack", subject_id=aid,
                        reason="action_ack_failed" if receipt else "action_ack_missing",
                        evidence_refs=(pointer(receipt),) if receipt else ()))
        if len(gaps) > 64:
            raise ContinuityConflict("recovery gap window requires bounded drilldown")
        return tuple(gaps)

    async def repair(self, snapshot, operations, *, authority_matches=True):
        before = self.scan(snapshot, operations)
        results = []
        for gap in before:
            operations.accounting.lease.check()
            if not authority_matches:
                results.append(gap.model_copy(update={"status": "blocked", "reason": "auth_scope_mismatch"}))
                continue
            if gap.kind == "write_success":
                # A owns every write-state transition and every success append.
                try:
                    state = await operations.reconcile(gap.subject_id)
                    refs = tuple(HistoryPointer.model_validate(state[key]) for key in
                        ("proposal_ref", "receipt_ref", "reconciliation_ref") if state[key])
                    reason = "readback_confirmed" if state["state"] == "confirmed" else "readback_client_unavailable"
                    if state["state"] == "unknown" and state["reconciliation_ref"]:
                        evidence = operations.read_artifact(state["reconciliation_ref"])
                        reason = evidence.get("error_code") or "effect_not_observed"
                    results.append(gap.model_copy(update={
                        "status": "repaired" if state["state"] == "confirmed" else "blocked",
                        "reason": reason, "evidence_refs": refs}))
                except (ContinuityConflict, ValueError):
                    # A's integrity fence remains closed; never fabricate a receipt.
                    results.append(gap.model_copy(update={"status": "blocked", "reason": "write_evidence_or_scope_conflict"}))
            elif gap.kind in {"transfer_committed", "task_result"}:
                try:
                    # Existing journal verifies proposals, payloads and lineage.
                    # Receipt repair is independent of permission to resume work.
                    self.journal.confirm_checkpoint(snapshot, reconcile=True)
                    results.append(gap.model_copy(update={"status": "repaired", "reason": "checkpoint_receipt_reconciled"}))
                except (ValueError, TypeError, KeyError):
                    results.append(gap.model_copy(update={"status": "blocked", "reason": "checkpoint_evidence_conflict"}))
            else:
                # Only the existing ACK endpoint can account for a client receipt.
                results.append(gap.model_copy(update={"status": "blocked"}))
        operations.accounting.lease.check()
        return tuple(results)

    def record(self, snapshot, gaps, *, action="needs_input", reason=None):
        body = PublicRevalidation(run_id=snapshot.run.run_id, field_results={},
            transfer_integrity="missing_receipt" if any(g.status == "blocked" and g.kind == "transfer_committed" for g in gaps) else "unknown",
            action=action, reason=reason, checkpoint_id=snapshot.ref.checkpoint_id, receipt_gaps=gaps)
        return self.writer.append_public_event(snapshot.identity,
            "recovery-receipts:" + digest([snapshot.ref.checkpoint_id, body.model_dump(mode="json")])[7:],
            HistoryEventType.REALITY_REVALIDATION, body)


def latest_recovery_status(store, task_id, *, session_id=None):
    cursor, latest = 0, None
    while True:
        rows = store.query_task_history(task_id, session_id=session_id,
            event_type=HistoryEventType.REALITY_REVALIDATION, after_seq=cursor, limit=100)
        if not rows:
            break
        for row in rows:
            # Legacy cold-start observations use this event type with another
            # schema; they are not typed recovery decisions.
            if row.source == "typed_writer:v1":
                latest = row
        cursor = rows[-1].event_seq
    if latest is None:
        return {"status": "unavailable", "reason": "recovery_not_observed", "receipt_gaps": []}
    body = PublicRevalidation.model_validate(latest.payload)
    return {"status": "blocked" if body.action in {"needs_input", "stop"} else "ready",
        **body.model_dump(mode="json"), "event_ref": pointer(latest).model_dump(mode="json")}


REASONS = {
    "operation_effect_unknown": "写操作效果尚未确认", "effect_not_observed": "读回尚未看到目标效果",
    "readback_client_unavailable": "当前没有可用的登录读回客户端", "http_502": "读回服务返回 502",
    "creation_privacy_unobservable": "歌单隐私参数无法从读回数据核验",
    "write_evidence_or_scope_conflict": "写操作证据或账号作用域无法核验",
    "auth_scope_mismatch": "账号作用域与原运行不一致", "checkpoint_evidence_conflict": "checkpoint 与转交证据不一致",
    "action_ack_missing": "缺少播放器 ACK，需要原动作的回执", "action_ack_failed": "播放器回执未确认执行成功",
    "original_run_limit_exhausted": "原运行时间或额度已耗尽", "original_run_cancelled": "原运行已取消",
    "checkpoint_pending_writes_unresolved": "checkpoint 中仍有未核验的待提交数据",
}


def recovery_message(view):
    blocked = [gap for gap in view.get("receipt_gaps", []) if gap["status"] == "blocked"]
    reasons = [view.get("reason"), *view.get("revalidation_reasons", []), *(gap["reason"] for gap in blocked)]
    details = "；".join(dict.fromkeys(REASONS.get(reason, reason) for reason in reasons if reason))
    return "恢复状态：" + ("已阻塞" if view["status"] == "blocked" else "记账核对已完成" if view["status"] == "ready" else "尚无恢复记录") + (
        "。" + details if details else "。") + "；不会重放已派发的业务写操作。"


def is_recovery_query(text):
    return isinstance(text, str) and bool(re.search(r"恢复.{0,12}(?:状态|卡|阻塞|不了|停止|原因)|(?:卡在|卡住).{0,8}(?:哪|步骤)", text))


async def answer_recovery_query(text):
    if not is_recovery_query(text):
        return None
    from .operations import active_operations
    operations = active_operations.get()
    explicit_recovery = "恢复" in text
    if operations is None:
        return "当前没有可查询的恢复账本，无法确认阻塞原因。" if explicit_recovery else None
    status = latest_recovery_status(operations.writer.store,
        operations.identity.continuity_task_id, session_id=operations.identity.session_id)
    if status["status"] == "unavailable" and not explicit_recovery:
        return None
    return recovery_message(status)
