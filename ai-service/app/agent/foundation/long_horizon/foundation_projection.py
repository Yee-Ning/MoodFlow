"""Read-only, deterministic selection of committed Foundation handoff material."""

from app.agent.foundation.routing import RouteDecision

from .history_writer import _check_public_text, digest
from .models import AgentProgress, HistoryEventType, HistoryPointer, PublicTaskResult
from .public_events import PublicCheckpoint, PublicRoute, PublicText, SafeTextArtifact


def _unique(values):
    return list(dict.fromkeys(values))


class FoundationProgressProjector:
    def __init__(self, service):
        self.service, self.store = service, service.store

    def _rows(self, session, *, event_type=None, task_wide=False):
        cursor = 0
        while True:
            rows = self.store.query_task_history(session.task_id,
                session_id=None if task_wide else session.session_id,
                event_type=event_type, after_seq=cursor, limit=100)
            if not rows:
                return
            for row in rows:
                cursor = row.event_seq
                if row.source == "typed_writer:v1":
                    yield row

    def _text(self, event, session):
        body = PublicText.model_validate(event.payload)
        if body.artifact_ref is None:
            text = body.text
        else:
            artifact = self.store.get_artifact(body.artifact_ref.target_id)
            if (body.artifact_ref.kind != "artifact" or artifact is None
                or artifact.task_id != session.task_id or artifact.artifact_type != "public_text:v1"
                or digest(artifact.payload) != artifact.content_hash):
                raise ValueError("Foundation public text artifact is unproven")
            text = SafeTextArtifact.model_validate(artifact.payload).text
        _check_public_text(text)
        return text

    def project(self, session, previous, draft):
        """Use public history plus saved route state; never synthesize a HandoffRun.

        The first public task input (or inherited goal) anchors continuation-only
        turns. Full source messages remain available by reference. Only typed
        results with a committed checkpoint count as execution progress.
        """
        prior = self.store.get_handoff(session.inherited_handoff_id) if session.inherited_handoff_id else None
        goal_event = next((row for row in self._rows(session, event_type=HistoryEventType.USER_MESSAGE,
            task_wide=True) if self._text(row, session).strip()), None)
        goal = prior.goal if prior else self._text(goal_event, session)[:4000] if goal_event else draft.goal
        context = previous.get("continuity_context", {})
        run_id = context.get("run_id")
        committed_runs, committed_requests, results, unresolved = set(), set(), {}, {}
        public_route, reply, cursor = None, None, 0
        for row in self._rows(session):
            cursor = row.event_seq
            if row.event_type == HistoryEventType.CHECKPOINT:
                committed_runs.add(PublicCheckpoint.model_validate(row.payload).run_id)
                committed_requests.add(row.request_id)
            elif row.event_type == HistoryEventType.ROUTE_DECISION and row.payload["run_id"] == run_id:
                public_route = PublicRoute.model_validate(row.payload)
            elif row.event_type == HistoryEventType.ASSISTANT_MESSAGE and row.request_id in committed_requests:
                reply = row
            elif row.event_type == HistoryEventType.STRUCTURED_OUTPUT:
                result = PublicTaskResult.model_validate(row.payload)
                if result.run_id in committed_runs:
                    results[result.run_id, result.route_task_id] = (row, result)
                    if result.status == "success":
                        unresolved.pop(result.route_task_id, None)
                    else:
                        unresolved[result.route_task_id] = result
                    # The public route contract allows at most 32 tasks. Keep
                    # recent results here; older material stays in immutable history.
                    if len(results) > 32:
                        del results[next(iter(results))]
        successful = [(row, result) for row, result in reversed(list(results.values())) if result.status == "success"]
        completed = _unique([*(result.content or result.route_task_id for _, result in successful),
                             *(prior.completed_steps if prior else [])])
        pending = list(prior.pending_items) if prior else []
        actions = list(prior.next_actions) if prior else []
        raw_route = previous.get("route_decision")
        route = RouteDecision.model_validate(raw_route) if raw_route else None
        if route and public_route and run_id in committed_runs:
            if (tuple((task.task_id, str(task.route_key)) for task in route.tasks) != public_route.tasks
                or route.needs_clarification != public_route.needs_clarification):
                raise ValueError("Foundation saved route disagrees with committed public route")
            for task in route.tasks:
                entry = results.get((run_id, task.task_id))
                if entry and entry[1].status == "success":
                    pending = [item for item in pending if item != task.input_text]
                    actions = [item for item in actions if item != "继续待办 " + task.input_text]
                else:
                    pending.append(task.input_text)
                    actions.append("继续待办 " + task.input_text)
            if route.needs_clarification:
                pending.append(route.clarification_question or goal)
                actions.append("确认 " + (route.clarification_question or goal))
        elif raw_route:
            # Checkpoint state alone is not a public commit receipt.
            raise ValueError("Foundation route lacks committed public evidence")
        current_ids = {task.task_id for task in route.tasks} if route else set()
        for task_id, result in unresolved.items():
            if task_id not in current_ids:
                # Earlier turns have no saved route input, but their public
                # clarification/failure text is still committed pending work.
                item = result.content or task_id
                pending.append(item)
                actions.append("继续待办 " + item)
        summary = _unique([*completed, *([self._text(reply, session)] if reply else []),
                           *([prior.summary] if prior and prior.summary else [])])
        pointers = [HistoryPointer(kind="event", target_id=row.event_id) for row in
                    [*([goal_event] if goal_event else []), *([reply] if reply else []),
                     *(row for row, _ in reversed(list(results.values())))]]
        pointers = list({ref.target_id: ref for ref in [*pointers, *draft.key_message_refs]}.values())[:32]
        latest = [result for _, result in results.values() if result.run_id == run_id]
        status = ("needs_input" if (route and route.needs_clarification) or any(item.status == "needs_input" for item in latest)
                  else "failed" if any(item.status == "failed" for item in latest)
                  else "completed" if route and len(latest) == len(route.tasks) and not pending
                  else "unknown")
        return draft.model_copy(update={
            "goal": goal, "summary": "\n".join(summary)[:12000], "completed_steps": completed,
            "pending_items": _unique(pending), "next_actions": _unique(actions),
            "current_stage": "待确认/继续待办" if pending else "已提交进度" if committed_runs else draft.current_stage,
            "key_message_refs": pointers,
            "agent_progress": AgentProgress(last_run_id=run_id if run_id in committed_runs else None,
                                             run_status=status),
            "source_event_cursor": cursor,
        })
