from typing import Annotated, NotRequired, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from app.agent.foundation.routing import IntentItem


class BaseAgentState(TypedDict):
    """Fields shared only when both parent and child graphs need them."""

    messages: Annotated[list[AnyMessage], add_messages]
    current_input: str
    routing_intents: NotRequired[list[IntentItem]]
