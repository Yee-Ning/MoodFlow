from __future__ import annotations

import copy
import importlib.util
import tempfile
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "qa_repair", Path(__file__).with_name("repair.py")
)
repair = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(repair)


class ClaimRepairTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.audit = repair.read_json(repair.GATE0)
        source = repair.read_rows(
            Path(cls.audit["run_root"])
            / "data/knowledge/v1/knowledge_enriched_chunks.jsonl"
        )
        parents = {
            row["parent_id"]
            for row in source
            if row.get("parent_id") and row["knowledge"]["claims"]
        }
        cls.rows = [
            row
            for row in source
            if row["knowledge"]["claims"] or row["chunk_id"] in parents
        ]

    def test_exact_three_changes_and_72_verified_claims_unchanged(self):
        before = copy.deepcopy(self.rows)
        revised, changes, carried = repair.revise_claims(self.rows, self.audit)
        self.assertEqual(self.rows, before)
        self.assertEqual(len(changes), 3)
        self.assertEqual(len(carried), 72)
        final = {
            item["claim"]["claim_id"]: item
            for row in revised
            for item in row["knowledge"]["claims"]
        }
        self.assertEqual(len(final), 73)
        self.assertTrue(repair.REMOVED.isdisjoint(final))
        self.assertNotIn(repair.REVISED, final)
        changed = next(c for c in changes if c["old_claim_id"] == repair.REVISED)
        self.assertNotEqual(changed["old_claim_id"], changed["new_claim_id"])
        self.assertEqual(
            changed["old_verified_claim"]["claim"]["time"],
            changed["new_verified_claim"]["claim"]["time"],
        )
        self.assertNotIn(
            "鍾興民", changed["new_verified_claim"]["claim"]["evidence_span"]
        )
        for old, new in zip(self.rows, revised, strict=True):
            self.assertEqual(
                {k: v for k, v in old.items() if k != "knowledge"},
                {k: v for k, v in new.items() if k != "knowledge"},
            )

    def test_missing_authorized_claim_stops_instead_of_best_effort(self):
        rows = copy.deepcopy(self.rows)
        for row in rows:
            row["knowledge"]["claims"] = [
                item
                for item in row["knowledge"]["claims"]
                if item["claim"]["claim_id"] != repair.REVISED
            ]
        with self.assertRaisesRegex(ValueError, "scope_not_exact"):
            repair.revise_claims(rows, self.audit)

    def test_replacement_quote_missing_stops(self):
        audit = copy.deepcopy(self.audit)
        next(r for r in audit["claim_findings"] if r["ordinal"] == "C074")[
            "proposed_evidence_span"
        ] = "fabricated quote"
        with self.assertRaisesRegex(ValueError, "not_unique"):
            repair.revise_claims(self.rows, audit)

    def test_duplicate_chunk_ids_stop(self):
        with self.assertRaisesRegex(ValueError, "duplicate_input_chunk"):
            repair.revise_claims(self.rows + self.rows[:1], self.audit)


class OutputSafetyTests(unittest.TestCase):
    def test_output_cannot_target_input_or_namespace_root(self):
        for path in [
            repair.REPO,
            repair.OUTPUT_NAMESPACE,
            Path(repair.read_json(repair.GATE0)["run_root"]),
        ]:
            with (
                self.subTest(path=path),
                self.assertRaisesRegex(ValueError, "new_repair_namespace"),
            ):
                repair.guard_output(path)

    def test_json_writer_never_overwrites(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "new.json"
            repair.write_json(path, {"first": True})
            with self.assertRaises(FileExistsError):
                repair.write_json(path, {"first": False})
            self.assertEqual(repair.read_json(path), {"first": True})

    def test_rows_writer_never_overwrites(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "new.jsonl"
            repair.write_rows(path, [{"first": True}])
            with self.assertRaises(FileExistsError):
                repair.write_rows(path, [{"first": False}])
            self.assertEqual(repair.read_rows(path), [{"first": True}])


if __name__ == "__main__":
    unittest.main()
