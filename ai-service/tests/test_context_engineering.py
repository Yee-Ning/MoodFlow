import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agent.foundation.context_engineering import (
    ContextPolicy,
    POLICIES,
    build_context,
)
from app.agent.foundation.routing import RouteKey
from app.agent.foundation.subgraphs.worldline.generation import ProviderWorldLineRuntime


class ContextEngineeringTests(unittest.TestCase):
    def test_four_node_policies_differ_and_chat_excludes_rag(self) -> None:
        self.assertEqual(set(POLICIES), set(RouteKey))
        self.assertNotIn("rag_evidence", POLICIES[RouteKey.GENERAL_CHAT].allowed_types)
        self.assertIn("rag_evidence", POLICIES[RouteKey.WORLDLINE].allowed_types)
        self.assertGreater(POLICIES[RouteKey.CURRENT_SONG].priorities["current_song"],
                           POLICIES[RouteKey.CURRENT_SONG].priorities["conversation"])

    def test_query_duplicate_in_history_is_removed_and_trace_explains_it(self) -> None:
        pack = build_context(RouteKey.GENERAL_CHAT, system="rules", query="hello",
                             messages=[HumanMessage(content="hello"), AIMessage(content="previous")])
        self.assertIn("history:0", pack.dropped_items)
        self.assertEqual(pack.messages[-1].content, "hello")
        self.assertTrue(any(row.item_id == "history:0" and row.stage == "deduplicated"
                            for row in pack.trace.entries))

    def test_required_query_and_constraints_survive_low_budget(self) -> None:
        policy = ContextPolicy(node=RouteKey.GENERAL_CHAT, total_input_budget=45,
                               reserved_output_tokens=0, safety_margin=0,
                               allowed_types=POLICIES[RouteKey.GENERAL_CHAT].allowed_types)
        pack = build_context(RouteKey.GENERAL_CHAT, policy=policy, system="critical rule",
                             query="answer exactly", structured_output={"constraint": "keep"},
                             messages=[AIMessage(content="irrelevant " * 100)])
        self.assertLessEqual(pack.total_tokens, pack.budget)
        self.assertEqual(pack.messages[-1].content, "answer exactly")
        self.assertIn("constraint", str(pack.messages[0].content))
        self.assertIn("history:0", pack.dropped_items)
        self.assertTrue(any(row.item_id == "history:0" and row.reason == "total_budget_exceeded"
                            for row in pack.trace.entries))

    def test_required_context_over_budget_fails_instead_of_silent_loss(self) -> None:
        policy = ContextPolicy(node=RouteKey.GENERAL_CHAT, total_input_budget=2,
                               reserved_output_tokens=0, safety_margin=0,
                               allowed_types=POLICIES[RouteKey.GENERAL_CHAT].allowed_types)
        with self.assertRaisesRegex(ValueError, "required context"):
            build_context(RouteKey.GENERAL_CHAT, policy=policy, system="critical instructions",
                          query="user request")

    def test_tool_raw_json_is_projected_and_tool_pair_is_preserved(self) -> None:
        call = {"name": "search_music", "args": {"keyword": "test"}, "id": "call-1", "type": "tool_call"}
        messages = [AIMessage(content="", tool_calls=[call]),
                    ToolMessage(content=json.dumps({"songs": [{"song_id": 7, "name": "A", "secret": "omit"}],
                                                    "debug": "omit"}),
                                tool_call_id="call-1", name="search_music")]
        pack = build_context(RouteKey.TOOL_CALLING, system="rules", query="play A", messages=messages)
        tool = next(message for message in pack.messages if isinstance(message, ToolMessage))
        self.assertNotIn("debug", tool.content)
        self.assertNotIn("secret", tool.content)
        self.assertIn('"song_id":7', tool.content)
        self.assertEqual(next(message for message in pack.messages if isinstance(message, AIMessage)).tool_calls[0]["id"], "call-1")
        self.assertTrue(any(row.item_id == "tool:call-1" and row.stage == "compressed"
                            and row.reason == "schema_projection" and row.token_after < row.token_before
                            for row in pack.trace.entries))

    def test_large_tool_result_stays_valid_json_after_budget_compression(self) -> None:
        call = {"name": "search_music", "args": {"keyword": "test"}, "id": "call-1", "type": "tool_call"}
        rows = [{"song_id": index, "name": "Track", "artists": ["Artist"],
                 "album": "Album", "cover_url": "", "duration": 100}
                for index in range(1, 11)]
        policy = POLICIES[RouteKey.TOOL_CALLING].model_copy(update={
            "total_input_budget": 150, "reserved_output_tokens": 0, "safety_margin": 0,
        })
        pack = build_context(RouteKey.TOOL_CALLING, policy=policy, system="rules",
                             query="play first", messages=[AIMessage(content="", tool_calls=[call]),
                               ToolMessage(content=json.dumps({"songs": rows}), tool_call_id="call-1")])
        tool = next(message for message in pack.messages if isinstance(message, ToolMessage))
        songs = json.loads(tool.content)["songs"]
        self.assertGreaterEqual(len(songs), 1)
        self.assertEqual(songs[0]["song_id"], 1)
        self.assertLessEqual(pack.total_tokens, pack.budget)

    def test_orphan_tool_call_is_not_sent_as_unanswered_model_call(self) -> None:
        call = {"name": "search_music", "args": {"keyword": "test"}, "id": "orphan", "type": "tool_call"}
        pack = build_context(RouteKey.GENERAL_CHAT, system="rules", query="hello",
                             messages=[AIMessage(content="", tool_calls=[call])])
        self.assertFalse(any(isinstance(message, AIMessage) and message.tool_calls
                             for message in pack.messages))

    def test_rag_citation_survives_selection_and_excess_is_explained(self) -> None:
        evidence = SimpleNamespace(items=[
            SimpleNamespace(parent_id="a", source_id="citation-a", title="A", context="A fact"),
            SimpleNamespace(parent_id="b", source_id="citation-b", title="B", context="B fact"),
        ])
        policy = POLICIES[RouteKey.WORLDLINE].model_copy(update={"source_budgets": {"rag_evidence": 2}})
        pack = build_context(RouteKey.WORLDLINE, policy=policy, system="rules", query="question",
                             rag_pack=evidence)
        self.assertEqual([item.references for item in pack.items if item.type == "rag_evidence"], [["citation-a"]])
        self.assertIn('citation="citation-a"', pack.messages[0].content)
        self.assertTrue(any(row.item_id == "evidence:b" and row.reason == "rag_evidence_budget_exceeded"
                            for row in pack.trace.entries))

    def test_current_song_lyrics_are_extracted_with_well_formed_tags(self) -> None:
        from app.agent.song_assistant import format_song_context
        from app.schemas.context import SongContext

        song = SongContext(song_id=1, name="Track", artist="Artist", album="Album",
                           original_lyrics="歌词" * 500, translated_lyrics="译文" * 500,
                           lyrics_status="AVAILABLE")
        policy = POLICIES[RouteKey.CURRENT_SONG].model_copy(update={
            "total_input_budget": 330, "reserved_output_tokens": 0, "safety_margin": 0,
        })
        pack = build_context(RouteKey.CURRENT_SONG, policy=policy, system="rules",
                             query="分析这首歌", current_song=format_song_context(song))
        self.assertLessEqual(pack.total_tokens, pack.budget)
        self.assertIn("current_song", pack.compressed_items)
        self.assertIn("</song_context>", pack.messages[0].content)
        self.assertIn("</lyrics>", pack.messages[0].content)
        self.assertEqual(pack.messages[-1].content, "分析这首歌")

    def test_required_rag_extraction_preserves_exact_span_and_provenance(self) -> None:
        evidence = SimpleNamespace(items=[{
            "id": "knowledge:one", "source_id": "archive:one", "title": "Archive",
            "excerpt": "intro " * 200 + "EXACT FACT" + " outro" * 200,
            "evidence_span": "EXACT FACT",
        }])
        policy = POLICIES[RouteKey.WORLDLINE].model_copy(update={
            "total_input_budget": 150, "reserved_output_tokens": 0, "safety_margin": 0,
        })
        pack = build_context(RouteKey.WORLDLINE, policy=policy, system="rules",
                             query="question", rag_pack=evidence,
                             rag_required_ids={"knowledge:one"})
        rag = next(item for item in pack.items if item.type == "rag_evidence")
        self.assertIn("EXACT FACT", rag.content)
        self.assertEqual(rag.references, ["archive:one"])
        self.assertIn("evidence:knowledge:one", pack.compressed_items)


class WorldLineContextIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_uses_selected_evidence_and_keeps_required_citation(self) -> None:
        payload = {
            "root_entity": {"name": "Track"},
            "catalog_facts": [],
            "worldline_graph": {"nodes": [], "edges": [{"evidence_ids": ["knowledge:one"]}]},
            "sources": [
                {"id": "knowledge:one", "title": "One", "excerpt": "same fact"},
                {"id": "knowledge:two", "title": "Two", "excerpt": "same fact"},
            ],
        }
        with patch("app.agent.foundation.subgraphs.worldline.generation.create_generation_response",
                   new_callable=AsyncMock) as generate, patch(
                       "app.agent.foundation.subgraphs.worldline.generation.model_output_from_response",
                       return_value="done"):
            result = await ProviderWorldLineRuntime().generate(
                payload, instructions="rules", schema={}, schema_name="test", retry=False,
            )
        self.assertEqual(result, "done")
        sent = json.loads(generate.await_args.args[0][0]["content"])
        self.assertEqual([row["id"] for row in sent["sources"]], ["knowledge:one"])


if __name__ == "__main__":
    unittest.main()
