import unittest

from pydantic import ValidationError

from app.agent.foundation.routing import (
    IntentItem,
    IntentSource,
    IntentType,
    PlayerState,
    RoutingContext,
)
from app.agent.foundation.rule_routing import RuleMatch, route_by_rules
from app.agent.foundation.rule_routing.merger import (
    deduplicate_matches,
    resolve_conflicts,
)
from app.agent.foundation.rule_routing.normalizer import normalize_text
from app.agent.foundation.rule_routing.rule_registry import get_default_rules


class RuleRouterTestCase(unittest.TestCase):
    def assert_intents(
        self,
        query: str,
        expected: list[tuple[IntentType, str]],
    ):
        decision = route_by_rules(query)

        self.assertEqual(
            [(item.intent, item.sub_intent) for item in decision.intents],
            expected,
        )
        for order, item in enumerate(decision.intents):
            self.assertEqual(item.order, order)
            self.assertLess(item.start, item.end)
            self.assertEqual(item.evidence, query[item.start : item.end])
            self.assertEqual(item.source, IntentSource.RULE)
        return decision

    def test_routes_pause_to_tool_calling(self) -> None:
        decision = self.assert_intents(
            "暂停一下",
            [(IntentType.TOOL_CALLING, "pause_music")],
        )

        self.assertTrue(decision.is_complete)
        self.assertFalse(decision.needs_semantic)
        self.assertEqual(decision.unresolved_text, "")

    def test_routes_next_song_to_tool_calling(self) -> None:
        decision = self.assert_intents(
            "下一首",
            [(IntentType.TOOL_CALLING, "next_music")],
        )

        self.assertTrue(decision.is_complete)

    def test_routes_favorite_current_song_to_tool_calling(self) -> None:
        decision = self.assert_intents(
            "收藏这首歌",
            [(IntentType.TOOL_CALLING, "favorite_song")],
        )

        self.assertTrue(decision.is_complete)

    def test_routes_explicit_current_song_analysis(self) -> None:
        for query in ("分析一下现在这首歌", "这首歌表达了什么"):
            with self.subTest(query=query):
                decision = self.assert_intents(
                    query,
                    [(IntentType.CURRENT_SONG, "analyze_current_song")],
                )
                self.assertTrue(decision.is_complete)

    def test_routes_strong_worldline_counterfactual(self) -> None:
        for query in (
            "如果周杰伦没有发行范特西会怎样",
            "如果这首歌从来没有发行会发生什么",
        ):
            with self.subTest(query=query):
                decision = self.assert_intents(
                    query,
                    [(IntentType.WORLDLINE, "music_counterfactual")],
                )
                self.assertTrue(decision.is_complete)
                self.assertEqual(decision.coverage, 1.0)

    def test_routes_analysis_and_favorite_as_two_intents(self) -> None:
        decision = self.assert_intents(
            "分析一下这首歌，然后帮我收藏",
            [
                (IntentType.CURRENT_SONG, "analyze_current_song"),
                (IntentType.TOOL_CALLING, "favorite_song"),
            ],
        )

        self.assertTrue(decision.is_complete)
        self.assertEqual(decision.unresolved_text, "")

    def test_routes_pause_and_lyric_analysis_as_two_intents(self) -> None:
        decision = self.assert_intents(
            "暂停一下，然后告诉我这首歌歌词什么意思",
            [
                (IntentType.TOOL_CALLING, "pause_music"),
                (IntentType.CURRENT_SONG, "analyze_current_song"),
            ],
        )

        self.assertTrue(decision.is_complete)

    def test_worldline_conflict_does_not_emit_current_song_intent(self) -> None:
        decision = self.assert_intents(
            "如果这首歌从来没有发行会发生什么",
            [(IntentType.WORLDLINE, "music_counterfactual")],
        )

        self.assertNotIn(
            IntentType.CURRENT_SONG,
            [item.intent for item in decision.intents],
        )

    def test_routes_worldline_and_favorite_as_two_intents(self) -> None:
        decision = self.assert_intents(
            "如果这首歌没发行，再帮我收藏它",
            [
                (IntentType.WORLDLINE, "music_counterfactual"),
                (IntentType.TOOL_CALLING, "favorite_song"),
            ],
        )

        self.assertTrue(decision.is_complete)
        self.assertEqual(decision.unresolved_text, "")

    def test_conditional_volume_request_is_not_worldline(self) -> None:
        decision = self.assert_intents(
            "如果声音太大就帮我调小一点",
            [(IntentType.TOOL_CALLING, "lower_volume")],
        )

        self.assertNotIn(
            IntentType.WORLDLINE,
            [item.intent for item in decision.intents],
        )

    def test_unknown_complex_query_is_left_for_semantic_router(self) -> None:
        query = "结合我的情绪变化设计一个适合深夜的音乐仪式"
        decision = self.assert_intents(query, [])

        self.assertEqual(decision.unresolved_text, query)
        self.assertFalse(decision.is_complete)
        self.assertTrue(decision.needs_semantic)
        self.assertFalse(decision.needs_llm)
        self.assertEqual(decision.coverage, 0.0)

    def test_partial_resolution_keeps_only_unresolved_clause(self) -> None:
        decision = self.assert_intents(
            "暂停一下，然后给我推荐类似这种风格的音乐",
            [(IntentType.TOOL_CALLING, "pause_music")],
        )

        self.assertEqual(decision.unresolved_text, "给我推荐类似这种风格的音乐")
        self.assertFalse(decision.is_complete)
        self.assertTrue(decision.needs_semantic)
        self.assertGreater(decision.coverage, 0.0)
        self.assertLess(decision.coverage, 1.0)

    def test_negated_actions_are_not_changed_or_routed(self) -> None:
        normalized = normalize_text("  别暂停！！！  ")

        self.assertEqual(normalized.text, "别暂停!")
        for query in ("别暂停", "不要帮我暂停", "不要下一首", "不要收藏这首歌"):
            with self.subTest(query=query):
                decision = route_by_rules(query)
                self.assertEqual(decision.intents, [])
                self.assertEqual(decision.unresolved_text, query)
                self.assertTrue(decision.needs_semantic)

    def test_normalizer_preserves_original_span_after_fullwidth_cleanup(self) -> None:
        query = "  暂停一下，，，然后收藏这首歌  "
        normalized = normalize_text(query)
        decision = route_by_rules(query)

        self.assertEqual(normalized.text, "暂停一下,然后收藏这首歌")
        self.assertEqual(len(decision.intents), 2)
        for item in decision.intents:
            self.assertEqual(item.evidence, query[item.start : item.end])


class RuleConflictTests(unittest.TestCase):
    @staticmethod
    def _match(
        name: str,
        intent: IntentType,
        start: int,
        end: int,
        *,
        priority: int,
        confidence: float,
    ) -> RuleMatch:
        return RuleMatch(
            rule_name=name,
            intent=intent,
            sub_intent=name,
            confidence=confidence,
            priority=priority,
            evidence="x" * (end - start),
            start=start,
            end=end,
        )

    def test_priority_wins_over_confidence_for_overlapping_span(self) -> None:
        lower_priority = self._match(
            "current_song",
            IntentType.CURRENT_SONG,
            2,
            8,
            priority=200,
            confidence=1.0,
        )
        worldline = self._match(
            "worldline",
            IntentType.WORLDLINE,
            0,
            12,
            priority=300,
            confidence=0.95,
        )

        self.assertEqual(
            resolve_conflicts([lower_priority, worldline]),
            [worldline],
        )

    def test_confidence_breaks_equal_priority_overlap(self) -> None:
        low_confidence = self._match(
            "low",
            IntentType.CURRENT_SONG,
            0,
            5,
            priority=200,
            confidence=0.91,
        )
        high_confidence = self._match(
            "high",
            IntentType.TOOL_CALLING,
            0,
            5,
            priority=200,
            confidence=0.99,
        )

        self.assertEqual(
            resolve_conflicts([low_confidence, high_confidence]),
            [high_confidence],
        )

    def test_deduplication_keeps_strongest_identical_match(self) -> None:
        weak = self._match(
            "duplicate",
            IntentType.TOOL_CALLING,
            0,
            4,
            priority=100,
            confidence=0.90,
        )
        strong = self._match(
            "duplicate",
            IntentType.TOOL_CALLING,
            0,
            4,
            priority=200,
            confidence=0.99,
        )

        self.assertEqual(deduplicate_matches([weak, strong]), [strong])


class RoutingSchemaTests(unittest.TestCase):
    def test_intent_schema_supports_future_sources_and_unknown(self) -> None:
        items = [
            IntentItem(
                intent=IntentType.UNKNOWN,
                sub_intent=None,
                confidence=0.3,
                source=source,
                evidence="复杂请求",
                start=0,
                end=4,
                order=index,
            )
            for index, source in enumerate(
                (IntentSource.RULE, IntentSource.SEMANTIC, IntentSource.LLM)
            )
        ]

        self.assertEqual(
            [item.source for item in items],
            [IntentSource.RULE, IntentSource.SEMANTIC, IntentSource.LLM],
        )

    def test_routing_context_is_read_only_and_consistent(self) -> None:
        context = RoutingContext(
            has_current_song=True,
            current_song_id=185809,
            player_state=PlayerState.PLAYING,
        )

        self.assertEqual(context.player_state, PlayerState.PLAYING)
        with self.assertRaises(ValidationError):
            RoutingContext(has_current_song=False, current_song_id=185809)

    def test_registry_contains_real_agent_and_player_capabilities(self) -> None:
        sub_intents = {rule.sub_intent for rule in get_default_rules()}

        self.assertTrue(
            {
                "search_music",
                "get_song_lyrics",
                "get_user_playlists",
                "add_song_to_playlist",
                "pause_music",
                "resume_music",
                "next_music",
                "previous_music",
                "favorite_song",
                "lower_volume",
            }.issubset(sub_intents)
        )


if __name__ == "__main__":
    unittest.main()
