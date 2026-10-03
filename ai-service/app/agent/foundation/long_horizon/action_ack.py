"""Resolve receipts through persisted action ownership, never rollout defaults."""

from .execution import ContinuityConflict
from .history_writer import digest
from .models import ContinuityIdentity, HistoryEventType


class ActionAckConflict(ContinuityConflict):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def resolve_action_owner(store, locks, *, graph_mode, thread_id, user_id, action_id):
    if graph_mode not in {None, "foundation", "handoff"}:
        raise ActionAckConflict("ack_graph_mode_invalid", "回执图类型无效")

    def find(mode):
        conversation = f"handoff:v1:{thread_id}" if mode == "handoff" else thread_id
        try:
            locks.require_scope(conversation, digest([mode, user_id]))
        except ContinuityConflict:
            # Do not inspect actions belonging to another authenticated user.
            return []
        matches = []
        for task in store.tasks_for_conversation(conversation):
            cursor = 0
            while True:
                rows = store.query_task_history(task.task_id, event_type=HistoryEventType.CLIENT_ACTION,
                                                after_seq=cursor, limit=100)
                if not rows:
                    break
                for event in rows:
                    if event.payload["action_id"] == action_id:
                        identity = ContinuityIdentity(conversation_id=conversation,
                            continuity_task_id=task.task_id, session_id=event.session_id,
                            request_id="ack:" + action_id, graph_mode=mode)
                        matches.append((identity, event))
                    cursor = event.event_seq
        return matches

    modes = [graph_mode] if graph_mode else ["foundation", "handoff"]
    matches = [match for mode in modes for match in find(mode)]
    if len(matches) > 1:
        raise ActionAckConflict("ack_action_ambiguous", "动作归属不唯一，请携带原动作的图类型")
    if matches:
        return matches[0]
    if graph_mode and find("foundation" if graph_mode == "handoff" else "handoff"):
        raise ActionAckConflict("ack_graph_mode_mismatch", "回执图类型与原动作所属会话不一致")
    raise ActionAckConflict("ack_action_unavailable", "当前身份和会话中未找到原动作，无法确定回执归属")
