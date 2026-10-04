"""Read-only explanation of partial task execution, without journal access."""

from langchain_core.tools import tool

from app.agent.foundation.models import TaskResult, TaskStatus
from app.agent.handoff.state import HandoffRun


def combination_progress(run: HandoffRun, result: TaskResult | None = None) -> dict:
    results = {item.task_id: item for item in (*run.results, *([result] if result else []))}
    tasks = []
    for index, task in enumerate(run.plan.tasks):
        outcome = results.get(task.task_id)
        tasks.append({
            "position": index + 1, "task_id": task.task_id, "route_key": task.route_key.value,
            "status": outcome.status.value if outcome else "pending",
            "reason": outcome.data.get("reason") if outcome else None,
            "error": outcome.error if outcome else None,
            "steps": outcome.data.get("tool_call_details", []) if outcome else [],
            "pending_client_actions": [
                item["name"] for item in outcome.data.get("client_actions", [])
            ] if outcome else [],
        })
    return {"run_id": run.run_id, "stop_reason": run.stop_reason.value if run.stop_reason else None,
            "blocked_at": next((item["task_id"] for item in tasks
                                if item["status"] in {"failed", "needs_input"}), None), "tasks": tasks}


def with_combination_progress(run: HandoffRun, result: TaskResult) -> TaskResult:
    progress = combination_progress(run, result)
    if result.status is TaskStatus.FAILED:
        progress["stop_reason"] = result.data.get("reason") or "task_failed"
    elif result.status is TaskStatus.NEEDS_INPUT:
        progress["stop_reason"] = "clarification"
    return result.model_copy(update={"data": {**result.data, "combination_progress": progress}})


def latest_combination_progress(results) -> dict | None:
    return next((item.data["combination_progress"] for item in reversed(results)
                 if "combination_progress" in item.data), None)


def create_combination_status_tool(results):
    snapshot = latest_combination_progress(results)

    @tool("get_combination_task_status")
    async def read_status() -> dict:
        """只读查询最近一次组合请求：已完成步骤、失败原因、未执行待办及待客户端确认的动作。"""
        # The existing context collector retains bounded public messages while
        # filtering arbitrary result keys. Supply the explanation through that
        # established field as well as the complete machine-readable snapshot.
        return {**snapshot, "message": explain_progress(snapshot)} if snapshot else {
            "tasks": [], "reason": "no_combination_status", "message": "没有可查询的组合任务状态。",
        }

    return read_status


def explain_combination_progress(run: HandoffRun) -> str:
    return explain_progress(combination_progress(run))


def explain_progress(progress: dict) -> str:
    labels = {"tool_calling": "音乐操作", "worldline": "世界线探索",
              "current_song": "歌曲分析", "general_chat": "聊天"}
    rows = []
    for task in progress["tasks"]:
        label = f"第{task['position']}步（{labels.get(task['route_key'], '任务')}）"
        if task["status"] == "success":
            detail = "任务处理完成"
        elif task["status"] == "failed":
            detail = "失败：" + (task["error"] or "暂时无法完成")
        elif task["status"] == "needs_input":
            detail = "需要补充信息，尚未完成"
        else:
            detail = "待办，尚未执行"
        if task["pending_client_actions"]:
            detail += "；已发送的播放器指令仍待客户端确认"
        if task["steps"]:
            succeeded = sum(item.get("status") == "success" for item in task["steps"])
            failed = sum(item.get("status") == "error" for item in task["steps"])
            detail += f"；工具步骤成功 {succeeded} 项、失败 {failed} 项"
        rows.append(f"{label}：{detail}。")
    return "\n".join(rows)[:10_000]
