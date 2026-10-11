"""Read-only song conversation; metadata never becomes playback confirmation."""

import json
import re
from typing import Annotated

from langchain_core.messages import AIMessage
from pydantic import BaseModel, ConfigDict, Field

from app.agent.foundation.subgraphs.general_chat.results import (
    ResultReceipt,
    ResultState,
)
from app.agent.foundation.subgraphs.general_chat.structured import ordinal


class SongMetadata(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)
    song_id: Annotated[int, Field(gt=0, strict=True)] = Field(alias="songId")
    name: str = Field(min_length=1, max_length=500)
    artist: str = Field(min_length=1, max_length=500)
    album: str = Field(default="", max_length=500)


def is_song_conversation(text):
    return bool(
        re.search(
            r"当前.*歌|这首|正在听|现在.*(?:歌|听)|在听什么|这是什么歌|哪首歌|切歌|切换.*歌|歌.*切换|聊.*歌曲",
            text,
        )
    )


def song_label(song):
    return f"《{song.name}》—{song.artist}" + (
        f"；专辑《{song.album}》" if song.album else "；资料未提供专辑"
    )


def dialogue(content, *, needs_input=False, data=None):
    return AIMessage(
        content=content,
        additional_kwargs={
            "general_chat_clarification": needs_input,
            "general_chat_song": data or {},
        },
    )


def unavailable_song_response():
    return dialogue(
        "当前歌曲资料暂时无法核对。你可以稍后再试，或告诉我想聊哪首；我不会猜测歌曲或切歌结果。",
        needs_input=True,
        data={"reason": "context_unavailable"},
    )


def pending_switch(receipt: ResultReceipt, prior_results):
    changing = {
        "play_song",
        "play_next",
        "play_previous",
        "play_album",
        "play_playlist",
        "jump_to_queue_item",
        "clear_queue",
    }
    actions = [
        a
        for result in prior_results
        for a in result.data.get("client_actions", [])
        if isinstance(a, dict) and a.get("name") in changing
    ]
    if not actions:
        return None
    relevant = [f for f in receipt.facts if f.subject in changing]
    if not relevant or any(
        f.state in {ResultState.GENERATED, ResultState.UNKNOWN} for f in relevant
    ):
        wording = (
            "\n".join(f.wording() for f in relevant)
            or "切歌效果未知，尚无可核对的执行回执。"
        )
        return dialogue(
            wording + "\n尚不能说歌曲已切换。可以等播放器确认，或先告诉我你想聊哪首。",
            needs_input=True,
            data={
                "reason": "pending_switch",
                "result_facts": receipt.projection()["result_facts"],
            },
        )
    return None


def render_song_read(raw, text, receipt, session):
    """Raw must come from get_current_song_context, never user/model prose."""
    snapshot = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(snapshot, dict):
        raise ValueError("song context must be an object")
    if snapshot.get("status") != "success" and (
        snapshot.get("candidates") or snapshot.get("song")
    ):
        raise ValueError("unsuccessful read cannot resolve a song")
    candidates = [
        SongMetadata.model_validate(row) for row in snapshot.get("candidates", [])
    ]
    if len(candidates) > 20 or len({s.song_id for s in candidates}) != len(candidates):
        raise ValueError("invalid song candidates")
    selected = ordinal(text)
    digit = re.fullmatch(r"(?:选|就|第)?(\d+)(?:首|个|吧)?[。！!]*", text.strip())
    if selected is None and digit:
        selected = int(digit[1])
    if session.song_choices and selected is not None:
        if not 1 <= selected <= len(session.song_choices):
            return dialogue(
                "这个序号不在刚才的选项里，请选列出的编号。", needs_input=True
            )
        wanted = session.song_choices[selected - 1]["song_id"]
        candidates = [song for song in candidates if song.song_id == wanted]
        if not candidates and snapshot.get("song"):
            song = SongMetadata.model_validate(snapshot["song"])
            candidates = [song] if song.song_id == wanted else []
        if not candidates:
            session.song_choices.clear()
            return dialogue(
                "刚才的候选已不在这次只读资料里，请重新确认想聊哪首。", needs_input=True
            )
    if len(candidates) > 1:
        session.song_choices = [song.model_dump() for song in candidates]
        session.pending_song_query = text
        rows = [
            f"{i}. 《{song.name[:80]}》—{song.artist[:80]} [song_id:{song.song_id}]"
            for i, song in enumerate(candidates, 1)
        ]
        return dialogue(
            "读到多个候选，想聊哪首？选一个编号就好，我不会代选或执行播放。\n"
            + "\n".join(rows),
            needs_input=True,
            data={"reason": "multiple_candidates", "candidates": session.song_choices},
        )
    if candidates:
        song = candidates[0]
    elif snapshot.get("reason") == "pending_queue_change":
        return dialogue(
            receipt.wording() + "\n切歌尚未确认，等播放器确认后我们再接着聊。",
            needs_input=True,
            data={"reason": "pending_queue_change"},
        )
    elif snapshot.get("reason") == "no_current_song":
        return dialogue(
            "现在还没有可核对的当前歌曲。可以先选一首歌，再告诉我想聊歌名、歌手还是听歌时的心情；我也可以先陪你聊聊。",
            needs_input=True,
            data={"reason": "no_current_song"},
        )
    elif snapshot.get("status") != "success" or not snapshot.get("song"):
        return dialogue(
            "当前歌曲资料暂时没读到，我还不能确定是哪首。你可以稍后再试，或告诉我想聊哪首。",
            needs_input=True,
            data={"reason": "context_unavailable"},
        )
    else:
        song = SongMetadata.model_validate(snapshot["song"])
        if snapshot.get("song_id") is not None and snapshot["song_id"] != song.song_id:
            raise ValueError("song context identity mismatch")
    original = session.pending_song_query or text
    session.song_choices.clear()
    session.pending_song_query = None
    prefix = (
        "只读资料中的歌曲是"
        if snapshot.get("target_source") != "prior_play_action"
        else "刚才指令的目标歌曲资料是"
    )
    mood = (
        "听起来你有些难过。我在这里，我们可以慢慢聊，你愿意说说此刻最在意的事吗？"
        if re.search(r"难过|伤心|孤独|低落", original)
        else "忙到现在辛苦了。我们可以慢慢聊，你此刻更想放松一下，还是说说今天的事？"
        if re.search(r"累|加班|疲惫", original)
        else "你是想聊这位歌手、这张专辑，还是这首歌让你想到的事？我愿意听。"
    )
    if re.search(r"歌词|背景|故事|含义|表达|讲.*什么|年代|发行|风格", original):
        mood = "目前只核对了歌名、歌手和专辑，没有可核对的歌词含义或背景依据。你可以说说自己的感受，我们从你的感受聊起。"
    ack = (
        "\n"
        + "\n".join(
            f.wording()
            for f in receipt.facts
            if f.subject.startswith("play_") or f.subject == "jump_to_queue_item"
        )
        if receipt.facts
        else ""
    )
    return dialogue(
        prefix + song_label(song) + "。[来源：get_current_song_context]\n" + mood + ack,
        data={
            "reason": "resolved",
            "song": song.model_dump(),
            "source": "get_current_song_context",
        },
    )


def safe_song_payload(raw):
    snapshot = json.loads(raw) if isinstance(raw, str) else raw
    data = {
        key: snapshot[key]
        for key in ("status", "reason", "song_id", "target_source")
        if key in snapshot
    }
    if snapshot.get("song"):
        data["song"] = SongMetadata.model_validate(snapshot["song"]).model_dump()
    if snapshot.get("candidates"):
        data["candidates"] = [
            SongMetadata.model_validate(s).model_dump() for s in snapshot["candidates"]
        ]
    return json.dumps(data, ensure_ascii=False)
