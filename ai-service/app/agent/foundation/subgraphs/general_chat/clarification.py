"""Clarification inside GeneralChat; no routing or business execution changes."""

import re

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage


def clarification_response(text: str, messages: list[BaseMessage]) -> AIMessage | None:
    compact = re.sub(r"[\s，,。.!！?？]", "", text)
    if re.fullmatch(r"(?:请|麻烦|能不能)?(?:帮我)?(?:弄|处理|操作|安排|搞)(?:一下|下|这个|它)?", compact):
        question = "你想让我帮你做什么？比如找歌、调整播放，或一起聊聊；告诉我具体想做的就好。"
    elif re.fullmatch(r"(?:请|帮我|我想|我要)?(?:播放|放|收藏|删除|移除|添加|加入)(?:一下|下|它|那个|这个)?", compact):
        question = "你想操作哪首歌或哪个歌单？告诉我名称和想做的操作，我再接着帮你。"
    else:
        return None
    # Keep the original request when a second short answer still lacks a target.
    original = text
    for index in range(len(messages) - 1, 0, -1):
        prior = messages[index]
        if isinstance(prior, AIMessage) and prior.additional_kwargs.get("general_chat_clarification"):
            if isinstance(messages[index - 1], HumanMessage):
                original = prior.additional_kwargs.get("pending_request", messages[index - 1].text)
            break
    return AIMessage(content=question, additional_kwargs={
        "general_chat_clarification": True, "pending_request": original[:1000],
    })
