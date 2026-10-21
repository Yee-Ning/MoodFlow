from __future__ import annotations

import copy
import dataclasses
import json
import tempfile
import unittest
from pathlib import Path

from app.rag.knowledge.config import KnowledgeConfig
from app.rag.knowledge.entity_linking.b1_scope import B1Scope
from app.rag.knowledge.entity_linking.grounded import GroundedEntityLinker
from app.rag.knowledge.models import KnowledgeEntities, KnowledgeStats
from tests.test_grounded_b1_linker import scope_for
from tests.test_grounded_entity_qa import as_knowledge
from tests.test_rag_knowledge import _enriched
from tools.replay_linker_b1 import (
    REVIEW,
    build_scope,
    guard_output,
    metrics,
    replay_chunk,
    validate_delta,
    validate_proof_preservation,
)


class ReplayB1Tests(unittest.TestCase):
    def setUp(self):
        self.v6 = GroundedEntityLinker(
            KnowledgeConfig(entity_linking_version="v6", llm_enabled=False)
        )
        base = _enriched("私はJayがデビューした。歌曲嘻哈空姐。")
        self.before = as_knowledge(base, self.v6.link_with_evidence(base))
        self.scope = scope_for(base, self.v6.link_with_evidence(base), "Jay")
        self.v7 = GroundedEntityLinker(
            KnowledgeConfig(entity_linking_version="v7", llm_enabled=False),
            b1_scope=self.scope,
        )

    def replay(self):
        return replay_chunk(self.before.model_dump(mode="json"), self.v7, self.scope)

    def test_approved_scope_has_150_bindings_and_no_answers(self):
        review = json.loads(REVIEW.read_text(encoding="utf-8"))
        scope = build_scope(review)
        self.assertEqual(len(scope.entries), 150)
        self.assertEqual(len(review["rows"]), 289)
        payload = json.dumps(scope.payload())
        self.assertNotIn("expected_targets", payload)
        self.assertNotIn("classification", payload)
        self.assertNotIn("proposed_existing_entity_ids", payload)
        self.assertEqual(
            scope.sha256, B1Scope(list(reversed(list(scope.entries.values())))).sha256
        )

    def test_reviewed_answers_do_not_drive_runtime_scope(self):
        review = json.loads(REVIEW.read_text(encoding="utf-8"))
        original = build_scope(review)
        changed = copy.deepcopy(review)
        for row in changed["rows"]:
            row["expected_targets"] = ["other:wrong_answer"]
        self.assertEqual(original.payload(), build_scope(changed).payload())

    def test_replay_is_deterministic_and_preserves_knowledge_and_source(self):
        original = self.before.model_dump(mode="json")
        after, proof, baseline = self.replay()
        repeated, repeated_proof, _ = self.replay()
        self.assertEqual(after, repeated)
        self.assertEqual(proof.payload(), repeated_proof.payload())
        self.assertEqual(original, self.before.model_dump(mode="json"))
        self.assertEqual(self.before.knowledge.claims, after.knowledge.claims)
        self.assertEqual(self.before.content, after.content)
        self.assertEqual(after.knowledge.provenance.entity_linking_version, "v7")
        self.assertEqual(baseline.policy_version, "body-grounding-v6")
        self.assertEqual(after.knowledge.stats.entity_count, 1)
        self.assertEqual(after.knowledge.stats.unresolved_count, 1)
        self.assertEqual(metrics([after.model_dump(mode="json")])["ratio"], 0.5)

    def test_replay_rejects_v6_baseline_mismatch(self):
        raw = self.before.model_dump(mode="json")
        raw["knowledge"]["entities"]["unresolved_mentions"] = []
        with self.assertRaisesRegex(ValueError, "baseline"):
            replay_chunk(raw, self.v7, self.scope)

    def test_delta_rejects_changed_source_or_claims(self):
        after, proof, _ = self.replay()
        bad_source = after.model_copy(update={"content": "different"})
        with self.assertRaisesRegex(ValueError, "nonknowledge"):
            validate_delta(
                self.before, bad_source, self.scope.for_chunk(after.chunk_id), proof
            )
        changed_quality = after.knowledge.quality.model_copy(
            update={"warnings": ["changed"]}
        )
        bad_quality = after.model_copy(
            update={
                "knowledge": after.knowledge.model_copy(
                    update={"quality": changed_quality}
                )
            }
        )
        with self.assertRaisesRegex(ValueError, "preserved_knowledge"):
            validate_delta(
                self.before, bad_quality, self.scope.for_chunk(after.chunk_id), proof
            )

    def test_delta_rejects_clearing_outside_scope_and_unproved_additions(self):
        after, proof, _ = self.replay()
        bad_entities = after.knowledge.entities.model_copy(
            update={"unresolved_mentions": []}
        )
        changed = after.model_copy(
            update={
                "knowledge": after.knowledge.model_copy(
                    update={"entities": bad_entities}
                )
            }
        )
        with self.assertRaisesRegex(ValueError, "out_of_scope"):
            validate_delta(
                self.before, changed, self.scope.for_chunk(after.chunk_id), proof
            )
        with self.assertRaisesRegex(ValueError, "out_of_scope"):
            validate_delta(self.before, after, (), proof)

    def test_delta_rejects_removing_original_link(self):
        base = _enriched("周杰倫。私はJayがデビューした。")
        before = as_knowledge(base, self.v6.link_with_evidence(base))
        proof = self.v6.link_with_evidence(base)
        empty = KnowledgeEntities()
        after = before.model_copy(
            update={
                "knowledge": before.knowledge.model_copy(
                    update={
                        "entities": empty,
                        "stats": KnowledgeStats.build(empty, []),
                    }
                )
            }
        )
        with self.assertRaisesRegex(ValueError, "existing_link"):
            validate_delta(before, after, (), proof)

    def test_body_proof_preservation_fails_on_context_or_rejection_change(self):
        after, proof, baseline = self.replay()
        for changed in [
            dataclasses.replace(proof, context_only_mentions=({"surface": "wrong"},)),
            dataclasses.replace(proof, rejected=()),
            dataclasses.replace(proof, body_sha256="0" * 64),
        ]:
            with (
                self.subTest(changed=changed),
                self.assertRaisesRegex(ValueError, "mutated"),
            ):
                validate_proof_preservation(baseline, changed)
        self.assertEqual(after.content, self.before.content)

    def test_output_boundary_and_immutable_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            self.assertEqual(guard_output(base / "new", base), base / "new")
            for path in (base, base.parent / "escape", base / ".." / "escape"):
                with (
                    self.subTest(path=path),
                    self.assertRaisesRegex(ValueError, "outside"),
                ):
                    guard_output(path, base)
            (base / "exists").mkdir()
            with self.assertRaisesRegex(ValueError, "exists"):
                guard_output(base / "exists", base)

    def test_metrics_use_stored_link_and_candidate_counts(self):
        raw = self.before.model_dump(mode="json")
        expected = metrics([raw])
        raw["knowledge"]["entities"]["unresolved_mentions"].append(
            {
                "candidate_entity_ids": [],
            }
        )
        self.assertEqual(metrics([raw]), expected)


if __name__ == "__main__":
    unittest.main()
