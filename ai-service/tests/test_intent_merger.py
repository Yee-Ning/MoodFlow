import unittest

from pydantic import ValidationError

from app.agent.foundation.intent_merging import (
    IntentMerger,
    MergedIntentResult,
    RouteDecisionBuilder,
    SegmentResolution,
)
from app.agent.foundation.llm_routing.schemas import LLMRouteResult
from app.agent.foundation.model_routing.schemas import ModelRouteResult
from app.agent.foundation.routing import (
    IntentItem,
    IntentSource,
    IntentType,
    RouteDecision,
    RouteKey,
    RouteTask,
    UnresolvedSegment,
)
from app.agent.foundation.rule_routing import RuleRouter


def model_result(text, labels=(IntentType.CURRENT_SONG,), needs_llm=False):
    scores = {i.name: (0.8 if i in labels else 0.1) for i in IntentType if i is not IntentType.UNKNOWN}
    return ModelRouteResult(
        intents=[IntentItem(intent=label, confidence=0.8, source=IntentSource.MODEL,
                            evidence=text, start=0, end=len(text), order=order)
                 for order, label in enumerate(labels)],
        scores=scores, accepted_labels=[i.name for i in labels],
        needs_llm=needs_llm or not labels, unresolved_text=text if needs_llm or not labels else "",
    )


class IntentMergerTests(unittest.TestCase):
    def test_rule_and_model_keep_original_order_without_mutation(self):
        text = "暂停一下，然后讲讲这首歌"
        rule = RuleRouter().route(text)
        segment = rule.unresolved_segments[0]
        model = model_result(segment.text)
        original = model.model_dump()
        merged = IntentMerger().merge(text, rule, [SegmentResolution(segment, model)])
        self.assertEqual([i.intent for i in merged.intents], [IntentType.TOOL_CALLING, IntentType.CURRENT_SONG])
        self.assertEqual([i.order for i in merged.intents], [0, 1])
        self.assertEqual(model.model_dump(), original)
        self.assertEqual(merged.intents[0], rule.intents[0])
        for item in merged.intents:
            self.assertEqual(text[item.start:item.end], item.evidence)
        decision = RouteDecisionBuilder().build(text, merged, run_id="fixture")
        self.assertTrue(decision.is_complete)
        self.assertEqual([t.intent_orders for t in decision.tasks], [[0], [1]])
        self.assertEqual(
            [t.input_text for t in decision.tasks],
            ["暂停一下", "讲讲这首歌"],
        )
        self.assertEqual(
            decision.tasks[1].depends_on,
            [decision.tasks[0].task_id],
        )

    def test_llm_replaces_only_its_segment_even_at_lower_confidence(self):
        text = "暂停一下，然后说点什么"
        rule = RuleRouter().route(text)
        segment = rule.unresolved_segments[0]
        model = model_result(segment.text, needs_llm=True)
        llm_item = IntentItem(intent=IntentType.GENERAL_CHAT, confidence=0.5, source=IntentSource.LLM,
                              evidence=segment.text, start=segment.start, end=segment.end, order=0)
        llm = LLMRouteResult(segment=segment, intents=[llm_item], unresolved_segments=[], needs_clarification=False)
        merged = IntentMerger().merge(text, rule, [SegmentResolution(segment, model, llm)])
        self.assertEqual([i.intent for i in merged.intents], [IntentType.TOOL_CALLING, IntentType.GENERAL_CHAT])

    def test_duplicate_same_span_dedup_but_different_tool_subintents_survive(self):
        rule = RuleRouter().route("暂停一下，然后收藏这首歌")
        duplicated = rule.model_copy(update={"intents": rule.intents + rule.intents})
        merged = IntentMerger().merge("暂停一下，然后收藏这首歌", duplicated, [])
        self.assertEqual(len(merged.intents), 2)
        self.assertEqual(len({i.sub_intent for i in merged.intents}), 2)
        decision = RouteDecisionBuilder().build("暂停一下，然后收藏这首歌", merged, run_id="test")
        self.assertEqual(len(decision.tasks), 1)
        self.assertEqual(decision.tasks[0].intent_orders, [0, 1])

    def test_pending_llm_keeps_rule_intent_but_dispatches_no_tasks(self):
        text = "暂停一下，然后阿巴阿巴"
        rule = RuleRouter().route(text)
        merged = IntentMerger().merge(text, rule, [])
        decision = RouteDecisionBuilder().build(text, merged, run_id="test")
        self.assertTrue(decision.needs_llm)
        self.assertFalse(decision.is_complete)
        self.assertEqual(decision.tasks, [])
        self.assertEqual(decision.intents[0].intent, IntentType.TOOL_CALLING)

    def test_long_clarification_text_is_safely_bounded(self):
        text = "不明确" * 1_000
        segment = UnresolvedSegment(
            text=text,
            start=0,
            end=len(text),
            order=0,
        )
        merged = MergedIntentResult(
            intents=[],
            unresolved_segments=[segment],
            needs_llm=False,
            needs_clarification=True,
        )

        decision = RouteDecisionBuilder().build(
            text,
            merged,
            run_id="long-clarification",
        )

        self.assertTrue(decision.needs_clarification)
        self.assertLessEqual(len(decision.clarification_question), 1_000)
        self.assertIn("…", decision.clarification_question)

    def test_multi_label_same_span_not_collapsed_and_general_chat_has_task(self):
        text = "阿巴阿巴"
        rule = RuleRouter().route(text)
        segment = rule.unresolved_segments[0]
        model = model_result(text, (IntentType.CURRENT_SONG, IntentType.GENERAL_CHAT))
        merger = IntentMerger()
        merged = merger.merge(text, rule, [SegmentResolution(segment, model)])
        self.assertEqual(merged, merger.merge(text, rule, [SegmentResolution(segment, model)]))
        decision = RouteDecisionBuilder().build(text, merged, run_id="test")
        self.assertEqual(len(decision.tasks), 2)
        self.assertEqual(decision.tasks[1].route_key, RouteKey.GENERAL_CHAT)

    def test_segment_cannot_escape_to_rule_span(self):
        text = "暂停一下，然后阿巴阿巴"
        rule = RuleRouter().route(text)
        segment = rule.unresolved_segments[0]
        llm = LLMRouteResult(segment=segment, intents=[rule.intents[0].model_copy(update={"source": IntentSource.LLM})],
                             unresolved_segments=[], needs_clarification=False)
        with self.assertRaisesRegex(ValueError, "escape"):
            IntentMerger().merge(text, rule, [SegmentResolution(segment, model_result(segment.text), llm)])

    def test_invalid_final_decision_and_task_reference_rejected(self):
        with self.assertRaises(ValidationError):
            RouteDecision(is_complete=True, needs_llm=True)
        with self.assertRaises(ValidationError):
            RouteDecision(
                is_complete=True,
                tasks=[
                    RouteTask(
                        task_id="t",
                        route_key=RouteKey.CURRENT_SONG,
                        input_text="分析歌曲",
                        intent_orders=[0],
                    )
                ],
            )
