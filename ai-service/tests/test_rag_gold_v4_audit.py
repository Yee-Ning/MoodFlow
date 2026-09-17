"""Structural regression checks for the frozen-source Gold V4 candidate."""
from __future__ import annotations

import hashlib
import unittest

from app.rag.evaluation.gold_evidence import load_frozen_parents
from app.rag.evaluation.io import load_evaluation_cases
from run_rag_gold_v4_audit import MANIFEST, V3, V3_SHA256, V4, validate


class GoldV4AuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _, _, parent_list = load_frozen_parents(MANIFEST)
        cls.parents = {item.parent_id: item for item in parent_list}
        cls.cases = load_evaluation_cases(V4)
        cls.by_id = {case.case_id[-3:]: case for case in cls.cases}

    def test_v3_remains_frozen_and_v4_validates(self) -> None:
        self.assertEqual(hashlib.sha256(V3.read_bytes()).hexdigest(), V3_SHA256)
        self.assertEqual(len(self.cases), 30)
        self.assertIsInstance(validate(self.cases, self.parents), list)

    def test_priority_outcomes_and_scope(self) -> None:
        adjudicated = {
            "003": ("insufficient_evidence", "partially_answerable"),
            "034": ("insufficient_evidence", "partially_answerable"),
            "036": ("insufficient_evidence", "partially_answerable"),
            "038": ("insufficient_evidence", "insufficient_evidence"),
            "039": ("insufficient_evidence", "partially_answerable"),
            "042": ("answerable", "fully_answerable"),
        }
        for cid, (outcome, level) in adjudicated.items():
            with self.subTest(case_id=cid):
                self.assertEqual(self.by_id[cid].expected_outcome.value, outcome)
                self.assertEqual(self.by_id[cid].answerability_level, level)
        self.assertIn("黄俊郎", self.by_id["024"].reference_answer)
        self.assertIn("现场演出", self.by_id["044"].forbidden_claims[0])

    def test_bad_parent_or_span_is_rejected(self) -> None:
        case = self.by_id["024"]
        bad_parent = case.model_copy(update={"relevant_parent_ids": ["parent-missing"]})
        with self.assertRaisesRegex(ValueError, "unknown relevant Parent"):
            validate([bad_parent], self.parents)
        spans = list(case.supporting_spans)
        spans[0] = spans[0].model_copy(update={"exact_text": "not in frozen parent"})
        bad_span = case.model_copy(update={"supporting_spans": spans})
        with self.assertRaisesRegex(ValueError, "unlocatable evidence"):
            validate([bad_span], self.parents)

    def test_invalid_duplicate_group_is_rejected(self) -> None:
        case = self.by_id["026"]
        group = case.evidence_groups[0].model_copy(update={
            "acceptable_parent_ids": [
                "parent-cfb9f049a0cfdf86866257fd",
                "parent-a734651048b7159210d5c4a6",
            ]
        })
        bad = case.model_copy(update={"evidence_groups": [group]})
        with self.assertRaisesRegex(ValueError, "non-identical duplicate group"):
            validate([bad], self.parents)

    def test_no_mechanical_span_cap(self) -> None:
        self.assertTrue(all(len(span.exact_text) < 350
                            for case in self.cases for span in case.supporting_spans))


if __name__ == "__main__":
    unittest.main()
