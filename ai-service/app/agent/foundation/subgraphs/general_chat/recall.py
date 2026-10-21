"""Preference query replies from the already selected, bounded Memory context."""

import json

from langchain_core.messages import AIMessage

from app.agent.foundation.subgraphs.general_chat.session import is_preference_query


def recalled_preference_response(text, session, pack):
    if session.confirmed or not is_preference_query(text):
        return None
    values = []
    for item in pack.items:
        # Only canonical Memory facts that survived the existing active-record,
        # owner, scene, intent and budget checks. Never mine conversation prose.
        if not str(item.metadata.get("memory_id", "")).startswith("memory:"):
            continue
        if (item.type, item.source) not in {
            ("user_profile", "user_profile"),
            ("long_term_memory", "long_term_memory"),
        }:
            continue
        try:
            content = json.loads(item.content)
        except (TypeError, ValueError):
            continue
        if not isinstance(content, dict):
            continue
        if item.type == "user_profile":
            value = content.get("value")
        elif content.get("memory_type") == "profile":
            value = content.get("content")
        else:
            continue
        if (isinstance(value, str) and value.strip() and value not in values
                and not any(revoked in value for revoked in session.revoked)):
            values.append(value)
    if values:
        return AIMessage(content="这次召回的长期偏好记录是：\n"
                         + "\n".join(f"- {value}" for value in values))
    return AIMessage(content="这次未召回可用的已确认长期偏好，会话内也暂无已确认偏好。"
                     "这不代表你从未保存过偏好。")
