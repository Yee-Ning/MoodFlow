"""Shared bounded conversation-history selection for Foundation subgraphs."""

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

MAX_HISTORY_MESSAGES = 12
MAX_HISTORY_CHARACTERS = 12_000


def _message_text(message: BaseMessage) -> str:
    text = getattr(message, "text", None)
    return str(text if text is not None else message.content)


def select_recent_history(
    messages: list[BaseMessage],
    current_input: str,
) -> list[BaseMessage]:
    """Select recent user/assistant turns without duplicating current input."""
    history = [
        message
        for message in messages
        if isinstance(message, (HumanMessage, AIMessage))
    ]
    if history and isinstance(history[-1], HumanMessage):
        history.pop()

    selected: list[BaseMessage] = []
    used_characters = len(current_input)
    for message in reversed(history):
        length = len(_message_text(message))
        if selected and used_characters + length > MAX_HISTORY_CHARACTERS:
            break
        if length > MAX_HISTORY_CHARACTERS:
            continue
        selected.append(message)
        used_characters += length
        if len(selected) >= MAX_HISTORY_MESSAGES:
            break
    selected.reverse()
    return selected
