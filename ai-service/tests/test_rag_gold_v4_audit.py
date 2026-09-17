"""Structural regression checks for the frozen-source Gold V4 candidate."""
from __future__ import annotations

import hashlib
import unittest

from app.rag.evaluation.gold_evidence import load_frozen_parents
from app.rag.evaluation.io import load_evaluation_cases
from app.rag.evaluation.v4_answerability import supported_evidence_units
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

    def test_every_positive_span_and_unit_has_an_explicit_claim(self) -> None:
        for case in self.cases:
            if case.answerability_level not in ("fully_answerable", "partially_answerable"):
                continue
            with self.subTest(case_id=case.case_id):
                self.assertTrue(all(span.claim for span in case.supporting_spans
                                    if span.support_level in ("full", "partial")))
                units = supported_evidence_units(case)
                self.assertEqual(case.supported_claims, [unit.claim for unit in units])
                self.assertEqual(len(case.supported_claims), len(set(case.supported_claims)))
        self.assertEqual(sum(len(case.supported_claims) for case in self.cases), 65)

    def test_missing_positive_claim_is_rejected(self) -> None:
        case = self.by_id["005"]
        spans = list(case.supporting_spans)
        spans[0] = spans[0].model_copy(update={"claim": None})
        with self.assertRaisesRegex(ValueError, "positive evidence lacks claim"):
            validate([case.model_copy(update={"supporting_spans": spans})], self.parents)

    def test_partial_supported_and_unsupported_claims_are_separate(self) -> None:
        for cid in ("003", "034", "036", "039"):
            case = self.by_id[cid]
            with self.subTest(case_id=cid):
                self.assertTrue(case.supported_claims)
                self.assertTrue(case.unsupported_claims)
                self.assertFalse(set(case.supported_claims) & set(case.unsupported_claims))
        case = self.by_id["005"]
        with self.assertRaisesRegex(ValueError, "partial case lacks unsupported claims"):
            validate([case.model_copy(update={"unsupported_claims": []})], self.parents)
        with self.assertRaisesRegex(ValueError, "supported claims/evidence mismatch"):
            validate([case.model_copy(update={"supported_claims": ["no evidence"]})], self.parents)

    def test_insufficient_span_does_not_become_a_positive_claim(self) -> None:
        case = self.by_id["005"]
        spans = list(case.supporting_spans)
        spans[1] = spans[1].model_copy(update={"support_level": "insufficient"})
        remaining_claim = spans[0].claim
        revised = case.model_copy(update={"supporting_spans": spans,
                                          "supported_claims": [remaining_claim]})
        self.assertEqual(len(supported_evidence_units(revised)), 1)
        self.assertIsInstance(validate([revised], self.parents), list)

    def test_duplicate_group_is_one_scored_claim_with_labeled_components(self) -> None:
        case = self.by_id["026"]
        units = supported_evidence_units(case)
        self.assertEqual(len(units), 2)
        self.assertEqual(len(case.supporting_spans), 5)
        self.assertTrue(all(span.claim for span in case.supporting_spans))
        self.assertEqual(len(case.supported_claims), 2)
        group = case.evidence_groups[0].model_copy(update={"claim": ""})
        with self.assertRaisesRegex(ValueError, "positive evidence unit lacks claim"):
            validate([case.model_copy(update={"evidence_groups": [group, case.evidence_groups[1]]})],
                     self.parents)

    def test_unresolved_needs_input_cases_remain_unscored(self) -> None:
        for cid in ("046", "047", "048"):
            case = self.by_id[cid]
            with self.subTest(case_id=cid):
                self.assertEqual(case.expected_outcome.value, "needs_input")
                self.assertIsNone(case.answerability_level)
                self.assertEqual(case.supported_claims, [])
                self.assertEqual(supported_evidence_units(case), ())


if __name__ == "__main__":
    unittest.main()
