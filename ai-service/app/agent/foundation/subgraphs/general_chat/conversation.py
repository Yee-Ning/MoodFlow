"""GeneralChat capability dispatch, independent of sender integration."""

import re

from langchain_core.messages import AIMessage

from app.agent.foundation.subgraphs.general_chat.session import preference_response
from app.agent.foundation.subgraphs.general_chat.song_dialogue import (
    is_song_conversation,
    pending_switch,
    render_song_read,
    unavailable_song_response,
)
from app.agent.foundation.subgraphs.general_chat.structured import (
    merge_bundles,
    ordinal,
    page,
    read_detail,
    read_source,
    receive,
)


def structured_message(content, session, *, needs_input=False):
    return AIMessage(
        content=content,
        additional_kwargs={
            "general_chat_clarification": needs_input,
            "general_chat_structured": session.bundle.model_dump(mode="json")
            if session.bundle
            else {},
        },
    )


def detail_message(content, session):
    # Detail pages are explicit, resumable reads; the original remains in the bundle.
    session.detail_remaining = content[8500:]
    visible = content[:8500]
    if session.detail_remaining:
        visible += "\n详情还有后续，回复“继续详情”查看。"
    return structured_message(visible, session)


async def capability_response(
    text, context, prior, receipt, session, *, structured_results=(), song_reader=None
):
    preference = preference_response(text, session)
    if preference is not None:
        return preference
    if structured_results:
        try:
            bundle = merge_bundles([receive(payload) for payload in structured_results])
        except (ValueError, KeyError, TypeError):
            return AIMessage(
                content="结构化资料中的事实或证据引用不完整，请补齐来源后再解释；我不会补造引用。",
                additional_kwargs={"general_chat_clarification": True},
            )
        session.bundle = bundle
        session.detail_remaining = ""
        content, session.visible, session.next_fact = page(bundle, receipt)
        return structured_message(content, session)
    fact_number = re.search(
        r"(?<![A-Za-z0-9_])F(\d+)(?![A-Za-z0-9_])", text, re.IGNORECASE
    )
    source_number = re.search(
        r"(?<![A-Za-z0-9_])S(\d+)(?![A-Za-z0-9_])", text, re.IGNORECASE
    )
    index = ordinal(text)
    reference_query = (
        fact_number
        or source_number
        or (
            index is not None
            and not session.song_choices
            and re.search(r"刚才|结果|事实|引用|原文|详情|说的", text)
        )
    )
    if reference_query and session.bundle:
        bundle = session.bundle
        try:
            if source_number:
                number = int(source_number[1])
                if not 1 <= number <= len(bundle.sources):
                    raise ValueError("source reference out of range")
                return detail_message(read_source(bundle, number - 1, receipt), session)
            if not fact_number and index < 1:
                raise ValueError("ordinal must be positive")
            target = (
                int(fact_number[1]) - 1 if fact_number else session.visible[index - 1]
            )
            return detail_message(read_detail(bundle, target, receipt), session)
        except (IndexError, ValueError):
            return structured_message(
                "这个引用不在刚才的结果里，请给我结果中的 F 或 S 编号。",
                session,
                needs_input=True,
            )
    if reference_query and not session.bundle:
        return AIMessage(
            content="我这里还没有可定位的结构化引用。你指的是哪个结果？可以给我 F 或 S 编号及对应资料。",
            additional_kwargs={"general_chat_clarification": True},
        )
    if session.bundle and text.strip() in {"继续结果", "下一页结果"}:
        content, session.visible, session.next_fact = page(
            session.bundle, receipt, start=session.next_fact
        )
        return structured_message(content, session)
    if text.strip() == "继续详情" and session.detail_remaining:
        return detail_message(session.detail_remaining, session)
    if song_reader and (is_song_conversation(text) or session.song_choices):
        switching = pending_switch(receipt, prior)
        if switching:
            return switching
        try:
            raw = await song_reader()
            return render_song_read(raw, text, receipt, session)
        except Exception:
            return unavailable_song_response()
    return None
