"""H0: bounded template exemptions and narrative-only retry instructions."""

import asyncio
import json
import unittest

from pydantic import ValidationError

from app.agent.foundation.subgraphs.worldline.generation import (
    StructuredWorldLineGenerator, WorldLineGenerationError,
)
from app.agent.foundation.subgraphs.worldline.grounding import (
    WorldLineGroundingError, WorldLineGroundingValidator, inline_citation_ids,
)
from app.agent.foundation.subgraphs.worldline.models import (
    EvidenceSource, MusicEntityFacts, MusicKnowledgeDocument, NarrativeDraft, NarrativeOutput,
)
from app.agent.foundation.subgraphs.worldline.prompts import NARRATIVE_SYSTEM_PROMPT
from tests.test_worldline_generation import ScriptedRuntime, entity, graph


LIMIT = "当前提供的证据不足以回答这个问题，暂时无法确认。"
ASK = "请提供具体歌曲名称或相关资料。"
FACT = "这首歌曲收录在给定的专辑中。[knowledge:test]"


def source(**changes):
    return EvidenceSource.model_validate({"id": "knowledge:test", "source_type": "knowledge",
        "title": "资料", "source": "test", "excerpt": "给定歌曲与专辑的资料。", **changes})


class AbstentionGroundingTests(unittest.TestCase):
    def check(self, text, *, sources=None, citation_ids=None):
        WorldLineGroundingValidator().validate(NarrativeOutput(
            narrative=text, citation_ids=inline_citation_ids(text) if citation_ids is None else citation_ids),
            [source()] if sources is None else sources)

    def assert_code(self, text, code, **kwargs):
        with self.assertRaises(WorldLineGroundingError) as error:
            self.check(text, **kwargs)
        self.assertEqual(error.exception.code, code)

    def test_only_fixed_templates_can_be_delivered_without_citations(self):
        for text in (LIMIT, ASK, LIMIT + ASK, ASK + LIMIT, LIMIT + "\n\n" + ASK):
            with self.subTest(text=text):
                self.check(text)

    def test_whitespace_and_bounded_punctuation_normalization(self):
        for text in (" 当前提供的证据不足以回答这个问题, 暂时无法确认! ",
                     "请 提供具体歌曲名称或相关资料；", "\u3000" + LIMIT + "\u3000", LIMIT.rstrip("。")):
            with self.subTest(text=text):
                self.check(text)

    def test_valid_facts_and_independent_template_paragraphs_are_accepted(self):
        for text in (FACT + "\n" + LIMIT, ASK + "\n\n" + FACT + "\n" + LIMIT):
            with self.subTest(text=text):
                self.check(text)

    def test_factual_or_freeform_abstention_never_gets_whole_text_exemption(self):
        for text in ("虽然证据不足，但这首歌于2004年发行。", "据说由方文山作词，但无法核实。",
                     LIMIT + "这首歌于2004年发行。", ASK + "具体是周杰伦的《晴天》。",
                     LIMIT + "\n这首歌于2004年发行。", "限制说明：" + LIMIT,
                     "“" + LIMIT + "”", LIMIT + "[source_id:knowledge:test]"):
            with self.subTest(text=text):
                self.assert_code(text, "unknown_citation" if "[source_id:" in text else "missing_citation")

    def test_uncited_factual_paragraph_still_fails_beside_templates_and_valid_citations(self):
        for bad in ("虽然证据不足，但这首歌于2004年发行。", LIMIT + "这首歌于2004年发行。",
                    "这首歌于2004年发行。", "当前图谱只有一个根节点，没有任何关系边。"):
            with self.subTest(bad=bad):
                self.assert_code(FACT + "\n" + LIMIT + "\n" + bad, "paragraph_missing_citation")

    def test_input_graph_boundary_extension_is_not_enabled(self):
        self.assert_code("当前图谱只有一个根节点，没有任何关系边。", "missing_citation")

    def test_unknown_ids_are_never_exempted_or_prefix_repaired(self):
        for citation in ("knowledge:missing", "source_id:knowledge:test", "source:knowledge:test"):
            with self.subTest(citation=citation):
                self.assert_code(LIMIT + "[" + citation + "]", "unknown_citation")

    def test_citation_order_mismatch_is_never_exempted(self):
        self.assert_code(LIMIT, "citation_order_mismatch", citation_ids=["knowledge:test"])
        self.assert_code(FACT + "\n" + ASK, "citation_order_mismatch", citation_ids=[])

    def test_empty_knowledge_excerpt_still_fails_when_cited(self):
        self.assert_code(FACT + "\n" + LIMIT, "missing_knowledge_excerpt", sources=[source(excerpt="")])

    def test_empty_lines_do_not_make_an_empty_answer_a_pure_abstention(self):
        self.assert_code("\n \n", "missing_citation")

    def test_no_sources_and_ordinary_cited_answers_keep_previous_behavior(self):
        self.check("当前没有可用的来源。", sources=[])
        self.check(FACT)
        self.assert_code("普通的未引用音乐事实不能直接输出。", "missing_citation")


class AbstentionGenerationTests(unittest.TestCase):
    def generate(self, runtime, *, knowledge=True):
        documents = [MusicKnowledgeDocument(id="child-test", title="资料", content="资料", source="test")] if knowledge else []
        return asyncio.run(StructuredWorldLineGenerator(runtime).generate_narrative(
            entity(), MusicEntityFacts(entity=entity()), documents, graph(), [source()] if knowledge else []))

    def test_pure_template_with_evidence_succeeds_in_one_production_attempt(self):
        runtime = ScriptedRuntime([json.dumps({"narrative": LIMIT})])
        output = self.generate(runtime)
        self.assertEqual(output.narrative, LIMIT)
        self.assertEqual(output.citation_ids, [])
        self.assertEqual(len(runtime.calls), 1)
        self.assertEqual(runtime.calls[0]["schema"]["required"], ["narrative"])
        self.assertEqual(set(runtime.calls[0]["schema"]["properties"]), {"narrative"})
        self.assertFalse(runtime.calls[0]["schema"]["additionalProperties"])

    def test_no_knowledge_disclosure_and_existing_repair_remain_compatible(self):
        output = self.generate(ScriptedRuntime([json.dumps({"narrative": LIMIT})]), knowledge=False)
        self.assertEqual(output.narrative, "当前未检索到补充音乐知识，以下内容仅基于音乐目录事实。\n" + LIMIT)

    def test_extra_fields_are_forbidden_even_when_self_reported_abstention(self):
        for key, value in (("abstention", True), ("narrative_note", "无事实"),
                           ("narrative_note", None), ("narrative_length", 0), ("additionalProperties", False)):
            with self.subTest(key=key, value=value), self.assertRaises(ValidationError):
                NarrativeDraft.model_validate({"narrative": LIMIT, key: value})

    def test_extra_fields_are_normalized_but_invalid_prose_still_has_two_attempt_boundary(self):
        for value in ("x", "", None, "placeholder"):
            runtime = ScriptedRuntime([json.dumps({"narrative": "虽然证据不足，但这首歌于2004年发行。", "narrative_note": value})] * 2)
            with self.subTest(value=value), self.assertRaises(WorldLineGenerationError):
                self.generate(runtime)
            self.assertEqual(len(runtime.calls), 2)
            self.assertIn("missing_citation", runtime.calls[1]["instructions"])

    def test_extra_field_is_removed_before_validation_without_extra_model_call(self):
        runtime = ScriptedRuntime([json.dumps({"narrative": LIMIT, "narrative_note": ""}),
                                   json.dumps({"narrative": LIMIT})])
        self.assertEqual(self.generate(runtime).narrative, LIMIT)
        self.assertEqual([c["retry"] for c in runtime.calls], [False])

    def test_unknown_citation_feedback_is_specific_but_does_not_repair(self):
        text = "这首歌曲有补充资料。[source_id:knowledge:test]"
        runtime = ScriptedRuntime([json.dumps({"narrative": text})] * 2)
        with self.assertRaises(WorldLineGenerationError):
            self.generate(runtime)
        feedback = runtime.calls[1]["instructions"]
        self.assertIn("Validation code: unknown_citation", feedback)
        self.assertIn("[source_id:knowledge:rag:abc]", feedback)
        self.assertIn("直接使用本次 sources.id 原值", feedback)
        self.assertEqual(len(runtime.calls), 2)

    def test_factual_pseudo_abstention_retries_then_accepts_fixed_template(self):
        runtime = ScriptedRuntime([json.dumps({"narrative": "虽然证据不足，但这首歌于2004年发行。"}),
                                   json.dumps({"narrative": LIMIT})])
        self.assertEqual(self.generate(runtime).narrative, LIMIT)
        self.assertIn("missing_citation", runtime.calls[1]["instructions"])
        self.assertNotIn("删除额外键", runtime.calls[1]["instructions"])

    def test_prompt_contains_only_approved_templates_and_single_field_contract(self):
        self.assertIn(LIMIT, NARRATIVE_SYSTEM_PROMPT)
        self.assertIn(ASK, NARRATIVE_SYSTEM_PROMPT)
        for text in ('顶层唯一键为 narrative', 'narrative_note', 'narrative_length', 'additionalProperties',
                     '[knowledge:rag:abc]', '[source_id:knowledge:rag:abc]'):
            self.assertIn(text, NARRATIVE_SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
