"""GeneralChat's read-only result projection and deterministic user wording."""

import re
from dataclasses import asdict, dataclass
from enum import StrEnum

from langchain_core.messages import AIMessage

from app.agent.foundation.long_horizon.execution import ContinuityConflict
from app.agent.foundation.long_horizon.models import HistoryEventType
from app.agent.foundation.long_horizon.operations import WRITES, active_operations
from app.agent.foundation.long_horizon.public_events import PublicAction, PublicActionAck
from app.agent.foundation.models import TaskResult
from app.agent.foundation.routing import RouteKey


class ResultState(StrEnum):
    GENERATED = "generated"
    EXECUTED = "executed"
    CONFIRMED = "confirmed"
    UNKNOWN = "unknown"


STATE_WORDING = {
    ResultState.GENERATED: "已生成指令，待客户端执行/确认；尚未确认实际效果。",
    ResultState.EXECUTED: "已执行：客户端 ACK 已确认该指令执行；这不代表此刻仍在播放。",
    ResultState.CONFIRMED: "已确认：只读核对已确认目标效果。",
    ResultState.UNKNOWN: "效果未知，尚不能确认；不会据此重复执行。",
}
LABELS = {
    "tool_calling": "音乐操作", "worldline": "世界线探索",
    "current_song": "歌曲分析", "general_chat": "聊天",
    "play_song": "播放", "pause_player": "暂停", "resume_player": "继续播放",
    "set_liked": "本地红心", "set_song_liked_tool": "云端红心",
    "create_playlist_tool": "新建歌单", "delete_playlist_tool": "删除歌单",
    "add_song_to_playlist": "歌单加歌", "remove_song_from_playlist_tool": "歌单移除歌曲",
}


@dataclass(frozen=True)
class ResultFact:
    subject: str
    state: ResultState | None
    source: str
    reference: str | None = None
    # A successful backend response is evidence, but is not a readback or ACK.
    detail: str | None = None

    def wording(self) -> str:
        return f"{LABELS.get(self.subject, '播放器操作')}：{self.detail or STATE_WORDING[self.state]}"


@dataclass(frozen=True)
class ResultReceipt:
    facts: tuple[ResultFact, ...] = ()
    tasks: tuple[dict, ...] = ()

    def projection(self) -> dict:
        return {"result_facts": [asdict(fact) for fact in self.facts], "tasks": list(self.tasks)}

    def wording(self) -> str:
        rows = []
        for task in self.tasks:
            label = f"第{task['position']}步（{LABELS.get(task['route_key'], '任务')}）"
            status = task["status"]
            if status == "failed":
                detail = "失败，尚未完成"
            elif status == "needs_input":
                detail = "需要补充信息，尚未完成"
            elif status == "pending":
                detail = "待办，尚未执行"
            elif task["route_key"] == "tool_calling":
                detail = "指令/工具结果已返回，实际效果以回执为准"
            else:
                detail = "结果已返回"
            rows.append(f"{label}：{detail}。")
        rows.extend(fact.wording() for fact in self.facts)
        return "\n".join(rows) or "没有可核对的结果记录，效果未知，无法确认是否已完成。"


def _history(operations, kind):
    """Query only the server-owned continuity task, including migrated sessions."""
    cursor = 0
    while True:
        rows = operations.writer.store.query_task_history(
            operations.identity.continuity_task_id, event_type=kind, after_seq=cursor, limit=100,
        )
        if not rows:
            return
        yield from rows
        cursor = rows[-1].event_seq


def _action_fact(action, operations) -> ResultFact:
    name, action_id = action.get("name", ""), action.get("action_id")
    if operations is not None and action_id:
        try:
            proposals = [row for row in _history(operations, HistoryEventType.CLIENT_ACTION)
                         if row.payload.get("action_id") == action_id]
            acknowledgements = [row for row in _history(operations, HistoryEventType.ACTION_ACK)
                                if row.payload.get("action_id") == action_id]
            if proposals and acknowledgements:
                proposal, receipt = proposals[-1], acknowledgements[-1]
                public = PublicAction.model_validate(proposal.payload)
                ack = PublicActionAck.model_validate(receipt.payload)
                # An ID alone or an ACK from another action/session cannot prove execution.
                if (public.name == name and ack.action_ref.target_id == proposal.event_id
                        and receipt.session_id == proposal.session_id):
                    return ResultFact(name, ResultState.EXECUTED if ack.status == "applied"
                                      else ResultState.UNKNOWN, "action_ack", receipt.event_id)
        except (ContinuityConflict, ValueError, KeyError):
            return ResultFact(name, ResultState.UNKNOWN, "action_ack")
    return ResultFact(name, ResultState.GENERATED, "combination_projection", action_id)


async def read_result_receipt(results: list[TaskResult]) -> ResultReceipt:
    # C owns task status. Never derive external completion from result.content or SUCCESS.
    from app.agent.handoff.progress import create_combination_status_tool

    snapshot = await create_combination_status_tool(results).ainvoke({})
    tasks = tuple({key: row[key] for key in ("position", "task_id", "route_key", "status")}
                  for row in snapshot.get("tasks", [])[:8])
    operations = active_operations.get()
    facts = []
    by_id = {result.task_id: result for result in results}
    for row in snapshot.get("tasks", [])[:8]:
        result = by_id.get(row["task_id"])
        actions = result.data.get("client_actions", []) if result else []
        for name in row.get("pending_client_actions", [])[:8]:
            matches = [action for action in actions if action.get("name") == name]
            if matches:
                facts.extend(_action_fact(action, operations) for action in matches)
            else:
                facts.append(ResultFact(name, ResultState.GENERATED, "combination_projection"))
        for step in row.get("steps", [])[:8]:
            if step.get("name") in WRITES:
                facts.append(ResultFact(step["name"], ResultState.UNKNOWN, "combination_projection"))
    if not tasks:
        # Foundation has no C snapshot. Its text and caller-supplied ACK fields are not proof.
        for result in results[-8:]:
            if result.route_key == RouteKey.TOOL_CALLING:
                facts.append(ResultFact("tool_calling", ResultState.UNKNOWN, "missing_projection"))
    if operations is not None:
        try:
            proposals = [row for row in operations.write_events()
                         if row.event_type is HistoryEventType.TOOL_CALL][-8:]
            # Narrow to the projected run when one exists; do not mix older write effects.
            if snapshot.get("run_id"):
                proposals = [row for row in proposals if row.payload["run_id"] == snapshot["run_id"]]
            for proposal in proposals:
                view = operations.state(proposal.payload["operation_id"])
                name = view["name"]
                facts = [fact for fact in facts if not (fact.subject == name
                         and fact.source == "combination_projection")]
                if view["state"] == "confirmed" and view["confirmation_source"] == "readback":
                    fact = ResultFact(name, ResultState.CONFIRMED, "operation_journal",
                                      view["operation_id"])
                elif view["state"] == "unknown":
                    fact = ResultFact(name, ResultState.UNKNOWN, "operation_journal", view["operation_id"])
                else:
                    # A's definite failure and original success response are exact
                    # receipt facts, not a new four-state effect confirmation.
                    detail = ("后端已明确拒绝操作，没有成功回执。" if view["state"] == "failed" else
                              "已有后端成功回执，未另行只读核对；客户端执行状态需另查 ACK。")
                    fact = ResultFact(name, None, "operation_journal", view["operation_id"], detail)
                facts.append(fact)
        except (ContinuityConflict, ValueError, KeyError):
            # Closed evidence fence: never replace a journal conflict with a success claim.
            facts = [fact for fact in facts if fact.source != "operation_journal"]
            facts.append(ResultFact("tool_calling", ResultState.UNKNOWN, "operation_journal_unavailable"))
    return ResultReceipt(tuple(facts), tasks)


def is_result_query(text: str) -> bool:
    return bool(re.search(
        r"(?:刚才|刚刚|任务|指令|步骤|操作|播放|暂停|收藏|歌单|结果|执行|确认|ACK).{0,24}"
        r"(?:了吗|了没|没[？?]?$|是否|状态|进度|结果|卡在|怎么样|什么时候|"
        r"(?:成功|完成|执行|确认)(?:了)?(?:没|吗|[？?]))|"
        r"(?:刚才|刚刚).{0,18}(?:做了什么|干了什么|进展|好了)|(?:结果|进度|状态|ACK)[呢吗？?]|"
        r"^(?:好了[吗没]?|完成[了吗没]*|搞定了[吗没]?|成功了[吗没]?|现在在播吗)[？?。！!]*$|"
        r"\b(?:done|completed|applied|status)\s*\?", text, re.IGNORECASE,
    ))


def has_effect_claim(text: str) -> bool:
    """Conservative fallback for spontaneous effect claims in conversational output."""
    return bool(re.search(
        r"^(?:好的?[，,。！!\s]*)?(?:已经|已)?(?:完成了?|搞定了?|办妥了?|办好了?|处理好了?)[。！!\s]*$|"
        r"(?:全部|所有|都|一切).{0,8}(?:完成|成功|搞定|办妥)|(?:已经|已|正在|完成|成功|确认|搞定|好了|办好了|在播).{0,18}"
        r"(?:播放|暂停|收藏|红心|歌单|指令|执行|操作)|"
        r"(?:播放|暂停|收藏|红心|歌单|指令|任务|操作).{0,18}(?:完成|成功|执行|确认|搞定|好了)|"
        r"(?:我|帮你|替你).{0,12}(?:播放了|暂停了|收藏了|删掉了|添加了)|音乐.{0,8}响起来了|"
        r"(?:已经|正在)?在播(?:放)?|\b(?:done|completed|applied|executed)\b|"
        r"\b(?:is|now|already)\s+(?:playing|paused|saved|deleted)\b", text, re.IGNORECASE,
    ))


def grounded_response(response: AIMessage, text: str, receipt: ResultReceipt) -> AIMessage:
    if is_result_query(text) or has_effect_claim(response.text):
        # State language is rendered by code. A model cannot promote a receipt, even
        # when it ignores the prompt or copies an injected prior result/history.
        return AIMessage(content=receipt.wording())
    return response
