from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer


def create_memory_checkpointer() -> InMemorySaver:
    """Create the development checkpointer shared by top-level agent graphs."""
    serializer = JsonPlusSerializer(
        allowed_msgpack_modules=[
            ("app.schemas.context", "SongContext"),
            ("app.agent.foundation.models", "GraphError"),
            ("app.agent.foundation.models", "TaskResult"),
            ("app.agent.foundation.models", "TaskStatus"),
            ("app.agent.foundation.routing", "RouteDecision"),
            ("app.agent.foundation.routing", "RouteKey"),
            ("app.agent.foundation.routing", "RouteTask"),
        ]
    )
    return InMemorySaver(serde=serializer)
