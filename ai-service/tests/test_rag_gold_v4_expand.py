"""Frozen-source regression checks for the additive 50-case Gold V4 candidate."""

from __future__ import annotations

import hashlib
import json
import unittest

from app.rag.evaluation.gold_evidence import load_frozen_parents
from app.rag.evaluation.io import load_evaluation_cases
from app.rag.evaluation.v4_answerability import score_v4_case, supported_evidence_units
from run_rag_gold_v4_audit import validate
from run_rag_gold_v4_expand import (
    AUDIT, MANIFEST, OUT, REPORT, SEED, SEED_SHA256, _exact_span, build_new_cases,
)


class GoldV4ExpansionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _, _, parent_list = load_frozen_parents(MANIFEST)
        cls.parents = {parent.parent_id: parent for parent in parent_list}
        cls.seed = load_evaluation_cases(SEED)
        cls.expanded = load_evaluation_cases(OUT)
        cls.new = cls.expanded[30:]

    def test_seed_is_byte_identical_prefix_and_new_cases_are_unique(self) -> None:
        seed = SEED.read_bytes()
        self.assertEqual(hashlib.sha256(seed).hexdigest(), SEED_SHA256)
        self.assertTrue(OUT.read_bytes().startswith(seed))
        self.assertEqual(len(self.seed), 30)
        self.assertEqual(len(self.expanded), 50)
        self.assertEqual([case.model_dump(mode="json") for case in self.seed],
                         [case.model_dump(mode="json") for case in self.expanded[:30]])
        self.assertEqual(len({case.case_id for case in self.expanded}), 50)
        self.assertEqual(len({case.query for case in self.expanded}), 50)

    def test_new_cases_rebuild_from_frozen_parents_and_validate(self) -> None:
        rebuilt, audit = build_new_cases(self.parents)
        self.assertEqual([case.model_dump(mode="json") for case in rebuilt],
                         [case.model_dump(mode="json") for case in self.new])
        self.assertEqual(len(audit), 20)
        self.assertIsInstance(validate(self.new, self.parents), list)
        self.assertIsInstance(validate(self.expanded, self.parents), list)
        self.assertEqual(len(AUDIT.read_text(encoding="utf-8").splitlines()), 20)
        self.assertTrue(REPORT.is_file())

    def test_positive_claims_are_atomic_labeled_and_negative_evidence_is_excluded(self) -> None:
        outcomes = {level: sum(case.answerability_level == level for case in self.new)
                    for level in ("fully_answerable", "partially_answerable", "insufficient_evidence")}
        self.assertEqual(outcomes, {"fully_answerable": 16, "partially_answerable": 2,
                                    "insufficient_evidence": 1})
        self.assertEqual(sum(case.expected_outcome.value == "needs_input" for case in self.new), 1)
        mapping = {"fully_answerable": "answerable",
                   "partially_answerable": "insufficient_evidence",
                   "insufficient_evidence": "insufficient_evidence",
                   None: "needs_input"}
        self.assertTrue(all(case.expected_outcome.value == mapping[case.answerability_level]
                            for case in self.new))
        self.assertTrue(all(span.claim and span.support_level in ("full", "partial")
                            for case in self.new for span in case.supporting_spans))
        for case in self.new:
            with self.subTest(case_id=case.case_id):
                if case.answerability_level in ("fully_answerable", "partially_answerable"):
                    units = supported_evidence_units(case)
                    self.assertEqual(case.supported_claims, [unit.claim for unit in units])
                    self.assertEqual(len(case.supported_claims), len(set(case.supported_claims)))
                else:
                    self.assertEqual(supported_evidence_units(case), ())
                    self.assertFalse(case.supporting_spans)
        self.assertFalse(any(set(case.supported_claims) & set(case.unsupported_claims)
                             for case in self.new))

    def test_duplicate_award_parent_is_claim_scoped(self) -> None:
        award = self.new[0]
        self.assertEqual(award.case_id, "rag-v0-049")
        self.assertEqual(len(award.supported_claims), 3)
        self.assertEqual(len(award.evidence_groups), 3)
        self.assertEqual(len(supported_evidence_units(award)), 3)
        self.assertEqual({tuple(group.acceptable_parent_ids) for group in award.evidence_groups}, {
            ("parent-2e6c60113d8a12a45bb69d5e", "parent-8d6fb6a17ca9c6a0c7a56e15")
        })
        self.assertEqual({group.claim for group in award.evidence_groups}, set(award.supported_claims))
        for pid in award.evidence_groups[0].acceptable_parent_ids:
            with self.subTest(parent_id=pid):
                result = score_v4_case(award, [pid], [pid], [pid])
                self.assertEqual(result.evidence_unit_count, 3)
                self.assertEqual([unit.top40_hit for unit in result.units], [True] * 3)
                self.assertEqual(result.evidence_recall_at_40, 1)

    def test_frozen_review_is_complete_and_needs_input_has_no_evidence(self) -> None:
        pending = [case.case_id for case in self.new if case.annotation_status.value == "pending"]
        self.assertEqual(pending, [])
        reviewed = {case.case_id: case for case in self.new[16:19]}
        self.assertEqual(set(reviewed), {"rag-v0-065", "rag-v0-066", "rag-v0-067"})
        self.assertEqual([case.answerability_level for case in reviewed.values()],
                         ["partially_answerable", "partially_answerable", "insufficient_evidence"])
        for case in reviewed.values():
            self.assertEqual(case.annotation_status.value, "approved")
            self.assertNotIn("manual_review", case.tags)
            self.assertIn("frozen_review_completed:", case.notes)
            self.assertTrue(case.unsupported_claims)
        needs = self.new[-1]
        self.assertEqual(needs.case_id, "rag-v0-068")
        self.assertIsNone(needs.answerability_level)
        self.assertIsNone(needs.reference_answer)
        self.assertFalse(needs.relevant_parent_ids)
        for marker in ("missing_input:", "why_not_inferable:", "clarification_needed:"):
            self.assertIn(marker, needs.notes)
        audit = [json.loads(line) for line in AUDIT.read_text(encoding="utf-8").splitlines()]
        self.assertFalse(audit[-1]["question_clear"])
        self.assertEqual([row["case_id"] for row in audit if row["manual_review"]], pending)
        self.assertTrue(all(not row["manual_review_reason"] for row in audit))
        self.assertTrue((REPORT.parent / "gold_v4_065_067_frozen_review.md").is_file())

    def test_span_extraction_rejects_missing_or_noisy_evidence(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing span end"):
            _exact_span("完整句子。", "完整", "不存在", "example")
        with self.assertRaisesRegex(ValueError, "non-minimal/noisy"):
            _exact_span("![](image.png)正文。", "![](", "正文。", "example")


if __name__ == "__main__":
    unittest.main()
