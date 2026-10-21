from __future__ import annotations

import copy
import unittest
from types import SimpleNamespace

from tools.freeze_recovery_20261019 import (
    QUARANTINE,
    approved_sample_rows,
    validate_inventory,
    validate_replay,
)


class RecoveryFreezeTests(unittest.TestCase):
    def test_carry_requires_identical_sample_including_body_proof(self):
        sample = {"sample_id": "s1", "body_matches": [{"quote": "原文", "start": 1}]}
        self.assertEqual(
            approved_sample_rows([sample], [copy.deepcopy(sample)]), [sample]
        )
        changed = copy.deepcopy(sample)
        changed["body_matches"][0]["start"] = 2
        with self.assertRaisesRegex(ValueError, "approved_qa_sample_changed"):
            approved_sample_rows([changed], [sample])
        with self.assertRaisesRegex(ValueError, "approved_qa_sample_changed"):
            approved_sample_rows([], [sample])

    def test_duplicate_samples_fail_closed(self):
        sample = {"sample_id": "s1"}
        for population, approved in (
            ([sample, sample], [sample]),
            ([sample], [sample, sample]),
        ):
            with self.assertRaisesRegex(ValueError, "duplicate"):
                approved_sample_rows(population, approved)

    def test_same_count_wrong_replay_and_missing_chunk_are_rejected(self):
        chunk = SimpleNamespace(chunk_id="one", content="original")
        validate_replay([chunk], {"one": copy.deepcopy(chunk)})
        for approved in (
            {"other": chunk},
            {"one": SimpleNamespace(chunk_id="one", content="wrong")},
        ):
            with self.assertRaisesRegex(ValueError, "approved_replay_chunk"):
                validate_replay([chunk], approved)

    def test_exact_48_documents_and_eight_quarantines(self):
        old = {f"old{i}" for i in range(22)}
        new = {f"new{i}" for i in range(26)}
        decisions = {
            "documents": [
                *({"document_id": d, "status": "passed_document_qa"} for d in new),
                *(
                    {"document_id": s, "source_id": s, "status": "quarantined"}
                    for s in sorted(QUARANTINE)
                ),
            ]
        }
        chunks = [
            SimpleNamespace(
                doc_id=d,
                parent_id=None,
                enrichment=SimpleNamespace(source=SimpleNamespace(source_id=d)),
            )
            for d in sorted(old | new)
        ]
        self.assertEqual(
            validate_inventory(chunks, old, decisions)["document_count"], 48
        )
        with self.assertRaisesRegex(ValueError, "release_document_inventory"):
            validate_inventory(chunks[:-1], old, decisions)
        chunks[0].enrichment.source.source_id = "jc-v2-048"
        with self.assertRaisesRegex(ValueError, "quarantined_source"):
            validate_inventory(chunks, old, decisions)
        decisions["documents"][-1]["source_id"] = "replacement"
        with self.assertRaisesRegex(ValueError, "quarantine_inventory"):
            validate_inventory(chunks, old, decisions)


if __name__ == "__main__":
    unittest.main()
