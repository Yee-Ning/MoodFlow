"""Round 2 strict normalization audits and input-bound boundary exemptions."""

import asyncio
from copy import deepcopy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.agent.structured_output import StructuredOutputError, StructuredOutputService
from app.agent.structured_output.parser import ModelOutput
from app.agent.foundation.subgraphs.worldline import narrative_normalization as normalization
from app.agent.foundation.subgraphs.worldline import stage_pack
from app.agent.foundation.subgraphs.worldline.generation import StructuredWorldLineGenerator, WorldLineGenerationError
from app.agent.foundation.subgraphs.worldline.grounding import (
    WorldLineGroundingError, WorldLineGroundingValidator, inline_citation_ids,
)
from app.agent.foundation.subgraphs.worldline.models import NarrativeDraft, NarrativeOutput, MusicEntityFacts
from app.agent.foundation.subgraphs.worldline.narrative_boundary import boundary_context_from_input
from tests.test_worldline_h0_abstention import LIMIT, FACT, source
from tests.test_worldline_generation import entity, graph, ScriptedRuntime


SENT = {"worldline_graph": {"root": "artist:jay_chou", "nodes": [
    {"id": "artist:jay_chou", "name": "周杰伦", "type": "artist"}], "edges": []}, "catalog_facts": []}
G1 = "从当前的世界线图来看，它只包含一个根节点“周杰伦”，没有任何边。"
G2 = "当前世界线图谱里只有“周杰伦”这一个艺人根节点。"
F1 = "本次输入中的目录事实为空。"


class NormalizerTests(unittest.TestCase):
    def service(self, **kwargs):
        return StructuredOutputService("worldline_narrative", NarrativeDraft,
            normalizer=lambda raw, attempt: normalization.normalize_narrative(
                raw, attempt, invocation_id="invocation", allowed_keys=NarrativeDraft.model_fields), **kwargs)

    def test_arbitrary_keys_values_are_losslessly_audited_and_input_not_mutated(self):
        raw = {"narrative": "  " + LIMIT + "\n", "renamed": None, "narrative_note": "",
               "sources_used": ["id", 4], "additionalProperties": False, "number": 1.25,
               "obj": {"untrusted": [True, "x" * 300]}}
        before = deepcopy(raw)
        with normalization.narrative_audit_scope() as events:
            output = self.service().validate(raw)
        self.assertEqual(raw, before)
        self.assertEqual(output.narrative, raw["narrative"])
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["invocation_id"], "invocation")
        self.assertEqual(event["attempt"], 1)
        self.assertEqual(event["raw_payload_sha256"], normalization.payload_sha256(raw))
        self.assertEqual(event["normalized_payload_sha256"], normalization.payload_sha256({"narrative": raw["narrative"]}))
        self.assertEqual({r["key"]: r["value"] for r in event["removed_fields"]}, {k: v for k, v in raw.items() if k != "narrative"})
        for removed in event["removed_fields"]:
            self.assertEqual(removed["value_sha256"], normalization.payload_sha256(removed["value"]))
            self.assertEqual(removed["value_type"], type(removed["value"]).__name__)
        self.assertTrue(next(r for r in event["removed_fields"] if r["key"] == "obj")["summary_truncated"])

    def test_without_hook_extra_keys_still_fail_schema(self):
        with self.assertRaises(StructuredOutputError):
            StructuredOutputService("other_schema", NarrativeDraft).validate({"narrative": LIMIT, "whatever": 0})

    def test_no_extra_keys_emit_no_deletion_event(self):
        with normalization.narrative_audit_scope() as events:
            self.service().validate({"narrative": LIMIT})
        self.assertEqual(events, [])

    def test_invalid_known_field_is_never_cleaned_or_inferred(self):
        for raw in ({"note": LIMIT}, {"narrative": None}, {"narrative": {"text": LIMIT}},
                    {"narrative": ""}, {"narrative": "x" * 6001}):
            with self.subTest(raw=str(raw)[:80]), self.assertRaises(StructuredOutputError):
                self.service().validate(raw)

    def test_audit_is_written_before_later_schema_failure(self):
        with normalization.narrative_audit_scope() as events, self.assertRaises(StructuredOutputError):
            self.service().validate({"narrative": None, "note": "important"})
        self.assertEqual(events[0]["removed_fields"][0]["value"], "important")

    def test_audit_fault_stops_sync_and_async_without_retry(self):
        calls = []
        async def generate(attempt, feedback):
            calls.append(attempt)
            return json.dumps({"narrative": LIMIT, "note": 4})
        with patch.object(normalization, "emit_narrative_audit", side_effect=RuntimeError("audit_failed")):
            with self.assertRaises(StructuredOutputError) as error:
                asyncio.run(self.service().run(generate))
            self.assertEqual(error.exception.attempts, 1)
            with self.assertRaises(StructuredOutputError):
                self.service().validate({"narrative": LIMIT, "note": 4})
        self.assertEqual(calls, [1])

    def test_parse_and_incomplete_errors_never_reach_normalizer(self):
        for raw in ("broken", "[]", ModelOutput("{}", status="incomplete")):
            normalizer = unittest.mock.Mock()
            async def generate(attempt, feedback):
                return raw
            with self.subTest(raw=raw), self.assertRaises(StructuredOutputError):
                asyncio.run(StructuredOutputService("test", NarrativeDraft, normalizer=normalizer).run(generate))
            normalizer.assert_not_called()


class BoundaryTests(unittest.TestCase):
    def check(self, text, sent=SENT, *, context=True, citation_ids=None):
        return WorldLineGroundingValidator().validate(NarrativeOutput(narrative=text,
            citation_ids=inline_citation_ids(text) if citation_ids is None else citation_ids), [source()],
            boundary_context=boundary_context_from_input(sent) if context else None)

    def test_exact_templates_and_mixed_cited_paragraphs_pass(self):
        for text in (G1, G2, F1, G1 + "\n" + F1 + "\n" + LIMIT, FACT + "\n" + G2,
                     "  " + G1.rstrip("。") + ".  "):
            with self.subTest(text=text):
                self.check(text)

    def test_no_context_preserves_default_rejection(self):
        for text in (G1, G2, F1):
            with self.subTest(text=text), self.assertRaises(WorldLineGroundingError):
                self.check(text, context=False)

    def test_graph_mismatches_never_exempt_graph_templates(self):
        bad = []
        for field, value in (("id", "other"), ("type", "song"), ("name", "另一个人")):
            sent = deepcopy(SENT)
            sent["worldline_graph"]["nodes"][0][field] = value
            bad.append(sent)
        sent = deepcopy(SENT)
        sent["worldline_graph"]["nodes"].append({"id": "b", "type": "song", "name": "作品"})
        bad.append(sent)
        sent = deepcopy(SENT)
        sent["worldline_graph"]["edges"] = [{"source": "a", "target": "b"}]
        bad.append(sent)
        bad.extend(({}, {"worldline_graph": None}))
        for sent in bad:
            for text in (G1, G2):
                with self.subTest(sent=sent, text=text), self.assertRaises(WorldLineGroundingError):
                    self.check(text, sent)

    def test_facts_nonempty_missing_null_or_wrong_type_never_exempt(self):
        for value in ([{"value": "fact"}], None, {}, ""):
            with self.subTest(value=value), self.assertRaises(WorldLineGroundingError):
                self.check(F1, {**SENT, "catalog_facts": value})
        with self.assertRaises(WorldLineGroundingError):
            self.check(F1, {"worldline_graph": SENT["worldline_graph"]})

    def test_mixed_fact_judgment_or_multiple_templates_same_paragraph_not_exempt(self):
        for text in (G1 + "这首歌于2004年发行。", G2 + "所以他们合作过。", F1 + "资料没有乐评。", G1 + F1,
                     "说明：" + G2):
            with self.subTest(text=text), self.assertRaises(WorldLineGroundingError):
                self.check(FACT + "\n" + text)

    def test_unknown_ids_and_citation_order_are_never_exempt(self):
        with self.assertRaisesRegex(WorldLineGroundingError, "unknown"):
            self.check(G1 + "[source_id:knowledge:test]")
        with self.assertRaisesRegex(WorldLineGroundingError, "first-use order"):
            self.check(G1, citation_ids=["knowledge:test"])

    def test_snapshot_is_immutable_and_follows_actual_input(self):
        sent = deepcopy(SENT)
        context = boundary_context_from_input(sent)
        sent["catalog_facts"].append({"value": "new fact"})
        WorldLineGroundingValidator().validate(NarrativeOutput(narrative=F1, citation_ids=[]), [source()], boundary_context=context)
        with self.assertRaises(WorldLineGroundingError):
            self.check(F1, sent)

    def test_boundary_checks_are_auditable(self):
        with normalization.narrative_audit_scope() as events:
            self.check(G1)
        self.assertEqual(events[0]["template_id"], "A2-G1")
        self.assertTrue(events[0]["matched"])
        self.assertEqual(events[0]["actual_input"], SENT)


class AttemptScopeTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_snapshot_uses_rendered_request_not_original_facts(self):
        from app.agent.foundation.subgraphs.worldline import generation
        from app.agent.foundation.subgraphs.worldline.models import MusicFact
        sent = {**deepcopy(SENT), "sources": [source().model_dump(mode="json")], "knowledge_available": True}

        async def prepare(*args, **kwargs):
            return SimpleNamespace(input_items=[{"role": "user", "content": json.dumps(sent)}], instructions="test", budget=None)

        async def response(*args, **kwargs):
            return SimpleNamespace(output_text=json.dumps({"narrative": F1}), status="completed", output=[], incomplete_details=None)

        facts = MusicEntityFacts(entity=entity(), facts=[MusicFact(id="fact", subject_id=entity().entity_id,
            predicate="name", value="nonempty before rendering")])
        with (patch.object(stage_pack, "prepare_explorer_input", prepare),
              patch.object(generation, "create_generation_response", response)):
            output = await StructuredWorldLineGenerator().generate_narrative(entity(), facts, [object()], graph(), [source()])
            self.assertEqual(output.narrative, F1)
            sent["catalog_facts"] = [{"value": "actually sent fact"}]
            with self.assertRaises(WorldLineGenerationError):
                await StructuredWorldLineGenerator().generate_narrative(entity(),
                    MusicEntityFacts(entity=entity()), [object()], graph(), [source()])

    async def test_snapshot_reset_each_attempt_no_stale_exemption(self):
        class Runtime(ScriptedRuntime):
            async def generate(self, payload, **kwargs):
                if not self.calls:
                    stage_pack.record_narrative_input(SENT)
                # First attempt invalid reference; second has no input snapshot.
                return await super().generate(payload, **kwargs)
        runtime = Runtime([json.dumps({"narrative": G1 + "[fake]"}), json.dumps({"narrative": G1})])
        with self.assertRaises(WorldLineGenerationError):
            await StructuredWorldLineGenerator(runtime).generate_narrative(entity(),
                MusicEntityFacts(entity=entity()),
                [object()], graph(), [source()])
        self.assertEqual(len(runtime.calls), 2)
        self.assertIsNone(stage_pack.active_narrative_input.get())

    async def test_concurrent_contexts_do_not_leak(self):
        async def run(name):
            with stage_pack.narrative_input_scope():
                stage_pack.reset_narrative_input(name, 1)
                stage_pack.record_narrative_input({**SENT, "catalog_facts": [{"name": name}]})
                await asyncio.sleep(0)
                return stage_pack.active_narrative_input.get().boundary_context
        a, b = await asyncio.gather(run("a"), run("b"))
        self.assertNotEqual(a.input_sha256, b.input_sha256)
        self.assertEqual((a.invocation_id, b.invocation_id), ("a", "b"))


if __name__ == "__main__":
    unittest.main()
