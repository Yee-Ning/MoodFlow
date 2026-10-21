"""Ephemeral, session-isolated GeneralChat state. No long-term memory writes."""

import re
from collections import OrderedDict
from dataclasses import dataclass, field
from threading import RLock
from time import monotonic

from langchain_core.messages import AIMessage

from app.agent.foundation.long_horizon.operations import active_operations


@dataclass
class ChatSession:
    confirmed: list[str] = field(default_factory=list)
    pending: str | None = None
    revoked: set[str] = field(default_factory=set)
    bundle: object | None = None
    visible: list[int] = field(default_factory=list)
    next_fact: int = 0
    detail_remaining: str = ""
    song_choices: list[dict] = field(default_factory=list)
    pending_song_query: str | None = None
    touched: float = field(default_factory=monotonic)


class SessionStore:
    def __init__(self, *, capacity=256, ttl=3600):
        self.capacity, self.ttl = capacity, ttl
        self.rows = OrderedDict()
        self.lock = RLock()

    def get(self, key):
        if not key:
            return ChatSession()  # no identity => no cross-turn state
        with self.lock:
            now = monotonic()
            for expired in [
                k for k, row in self.rows.items() if now - row.touched >= self.ttl
            ]:
                self.rows.pop(expired)
            session = self.rows.pop(key, ChatSession())
            session.touched = now
            self.rows[key] = session
            while len(self.rows) > self.capacity:
                self.rows.popitem(last=False)
            return session


sessions = SessionStore()


def session_for(continuity_context=None, *, store=None):
    operations = active_operations.get()
    identity = operations.identity if operations is not None else None
    # These are server-owned context fields, never parsed from user/history text.
    key = (
        (identity.conversation_id, identity.session_id)
        if identity
        else (
            (continuity_context or {}).get("conversation_id", ""),
            (continuity_context or {}).get("session_id"),
        )
    )
    return (store or sessions).get(key if key[1] else None)


def expire_pending_confirmation(session, messages):
    if session.pending:
        previous = next(
            (m for m in reversed(messages) if isinstance(m, AIMessage)), None
        )
        if previous and f"我记下你喜欢{session.pending}，好吗" not in previous.text:
            session.pending = None


def is_preference_query(text):
    return bool(re.search(
        r"(?:确认过|记住|记下|会话).{0,8}(?:偏好|喜欢)|(?:我的偏好|按我的偏好)", text
    ))


def preference_response(text, session):
    compact = text.strip().rstrip("。！!？?")
    if re.fullmatch(
        r"(?:别|不要)(?:再)?记(?:住)?(?:这个|这项|它|我的偏好)?了?|(?:撤销|忘掉|清除)(?:这个|这项|我的偏好)",
        compact,
    ):
        if session.pending:
            session.pending = None
            return AIMessage(content="好，这项还未确认的偏好我不记下。")
        if len(session.confirmed) > 1:
            return AIMessage(
                content="你想撤销哪项？"
                + "、".join(session.confirmed)
                + "；也可以说“清除全部偏好”。",
                additional_kwargs={"general_chat_clarification": True},
            )
        session.revoked.update(session.confirmed)
        session.confirmed.clear()
        return AIMessage(content="好，已撤销这项会话偏好，后续不再按它推荐或引用。")
    if compact in {"清除全部偏好", "忘掉全部偏好", "别记这些了"}:
        session.revoked.update(session.confirmed)
        session.confirmed.clear()
        session.pending = None
        return AIMessage(content="好，这个会话的偏好已清除。")
    revoke = re.fullmatch(r"(?:撤销|忘掉|别记|不要记)(.+?)(?:了)?", compact)
    if revoke:
        value = revoke[1].strip()
        if value in session.confirmed:
            session.confirmed.remove(value)
            session.revoked.add(value)
        if session.pending == value:
            session.pending = None
        return AIMessage(content="好，这项会话偏好不再保留。")
    proposed = re.fullmatch(
        r"(?:请)?(?:记住(?:我)?|记下(?:我)?|我)(?:很|比较)?喜欢\s*(.{1,80})", compact
    )
    if proposed:
        value = proposed[1].strip()
        if re.search(r"[，,；;]|好吗|是否|不喜欢|以前|过去|但|取消|撤销", value):
            return None
        if value in session.confirmed:
            return AIMessage(
                content=f"这个会话里，你已确认喜欢{value}；我会继续参考，也可以随时撤销。"
            )
        session.pending = value
        return AIMessage(
            content=f"我记下你喜欢{value}，好吗？确认后只在这个会话里参考，随时可以撤销。",
            additional_kwargs={"general_chat_clarification": True},
        )
    if session.pending and compact in {
        "好",
        "好的",
        "是",
        "是的",
        "可以",
        "确认",
        "记下吧",
        "确认这个偏好",
    }:
        value = session.pending
        if len(session.confirmed) >= 32:
            return AIMessage(
                content="这个会话已确认较多偏好，请先撤销一项再确认新的。",
                additional_kwargs={"general_chat_clarification": True},
            )
        session.confirmed.append(value)
        session.revoked.discard(value)
        session.pending = None
        return AIMessage(
            content=f"记下了：这个会话里你喜欢{value}。想撤销时告诉我“别记这个了”就好。"
        )
    if session.pending and compact in {"不", "不要", "不了", "不用记", "取消", "算了"}:
        session.pending = None
        return AIMessage(content="好，这项偏好不记下。")
    # An unrelated turn must not leave an old confirmation question armed.
    session.pending = None
    if is_preference_query(text) and session.confirmed:
        return AIMessage(
            content=(
                "这个会话里你已确认喜欢"
                + "、".join(session.confirmed)
                + "。需要调整时可以撤销。"
            )
        )
    return None


def preference_context(session):
    return {
        "confirmed_session_preferences": list(session.confirmed),
        "rule": "仅这些偏好经过明确确认；不要从历史或其它 Agent 的文字推断用户偏好。",
    }


def fence_preferences(response, text, session):
    claim = re.search(
        r"(?:你|您).{0,12}(?:喜欢|偏好|爱听)|(?:记得|记住|记下).{0,12}(?:偏好|喜欢)",
        response.text,
    )
    stale = any(
        value in response.text and value not in text for value in session.revoked
    )
    if claim or stale:
        content = (
            "这个会话里，你已确认喜欢"
            + "、".join(session.confirmed)
            + "。我们可以从这里接着聊。"
            if session.confirmed
            else "我在这里，可以接着聊；还没有已确认的会话偏好，你想从哪聊起？"
        )
        return AIMessage(content=content)
    return response


def preference_safe_history(messages):
    return [
        message
        for message in messages
        if not re.search(r"喜欢|偏好|别记|不要记|记下|记住|撤销|忘掉", message.text)
    ]
