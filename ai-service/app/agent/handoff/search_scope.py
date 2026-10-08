"""Conservative tool scope for a single read-only catalog search clause."""

import re

from app.agent.foundation.rule_routing import RuleRouter
from app.rag.query_understanding.context import _CURRENT_SONG_REFERENCE

SEARCH_TOOLS = frozenset({"search_music", "search_entities_tool"})
_CLAUSES = re.compile(r"[,，。;；]|然后|接着|随后|并且|同时|以及")
_NEGATED = re.compile(r"^(?:不要|别|不用|无需|请勿)")
_SEARCH = re.compile(r"^(?:请|麻烦)?(?:帮我)?(?:搜索|查找)(?:一下)?")
_ADDITIONAL_READ = re.compile(r"歌词|详情|分析|解释|查看|介绍|创作背景")


def scoped_search_tools(text, tools):
    original = list(tools)
    clauses = [
        part.strip()
        for part in _CLAUSES.split(text)
        if part.strip() and not _NEGATED.match(part.strip())
    ]
    if len(clauses) != 1 or not _SEARCH.match(clauses[0]):
        return original
    if _ADDITIONAL_READ.search(clauses[0]):
        return original
    if _CURRENT_SONG_REFERENCE.search(text):
        return original
    decision = RuleRouter().route(text)
    if not decision.is_complete or any(
        intent.sub_intent != "search_music" for intent in decision.intents
    ):
        return original
    # Reuse the executor's authorization check: any explicitly requested write
    # keeps the full graph, including mixed search-and-play clauses.
    from langchain_core.messages import AIMessage
    from app.agent.foundation.subgraphs.tool_calling.graph import _action_intent_errors

    calls = [{"id": item.name, "name": item.name, "args": {}} for item in original]
    errors = _action_intent_errors(
        {"current_input": text, "messages": [AIMessage(content="", tool_calls=calls)]}
    )
    mutation_names = {name for name in errors}
    from app.agent.foundation.subgraphs.tool_calling.graph import _ACTION_REQUESTS

    if any(
        item.name in _ACTION_REQUESTS and item.name not in mutation_names
        for item in original
    ):
        return original
    selected = [item for item in original if item.name in SEARCH_TOOLS]
    return selected or original
