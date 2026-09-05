from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class BaseAgentState(TypedDict):
    """Fields shared only when both parent and child graphs need them."""

    messages: Annotated[list[AnyMessage], add_messages]
    current_input: str
