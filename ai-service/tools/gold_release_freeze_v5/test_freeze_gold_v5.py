"""Offline Gate 2 protection tests and complete real-dataset validation."""

from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import stat
import tempfile
import unittest

import freeze_gold_v5 as freeze


def knowledge_row(chunk, parent, content, source="new-source"):
    return {"chunk_id": chunk, "parent_id": parent, "content": content,
            "metadata": {"source_id": source}}


def evidence_case(status="matched", label="full"):
    return {"suggested_label": label, "anchors": [{
        "anchor_id": "span-1", "claim": "evidence", "exact_text": "alpha beta", "status": status,
        "accepted_locations": [{"source_id": "new-source", "parent_id": "new-parent",
                                "child_ids": ["new-child"], "matches": [
                                    {"chunk_id": "new-child", "field": "content", "quote": "alpha beta",
                                     "raw_start": 0, "raw_end": 10}]}]}]}


class AnchorProtectionTests(unittest.TestCase):
    def setUp(self):
        self.records = {
            "new-parent": knowledge_row("new-parent", None, "alpha beta"),
            "new-child": knowledge_row("new-child", "new-parent", "alpha beta"),
        }

    def locate(self, case):
        return freeze.freeze_anchors(case, self.records, {"new-parent"}, {"new-child"})

    def test_matched_quotes_keep_new_ids_and_exact_offsets(self):
        result = self.locate(evidence_case())
        self.assertEqual(result[0]["parent_id"], "new-parent")
        self.assertEqual(result[0]["child_ids"], ["new-child"])
        self.assertEqual(result[0]["quotes"][0], {"chunk_id": "new-child", "field": "content",
                                                "exact_text": "alpha beta", "start": 0, "end": 10})

    def test_source_absent_and_ambiguous_locations_are_not_frozen(self):
        for status in ["source_absent", "ambiguous", "text_changed"]:
            with self.subTest(status=status):
                self.assertEqual(self.locate(evidence_case(status=status)), [])

    def test_needs_input_discards_even_matched_locations(self):
        self.assertEqual(self.locate(evidence_case(label="needs_input")), [])

    def test_old_parent_id_is_rejected(self):
        case = evidence_case()
        case["anchors"][0]["accepted_locations"][0]["parent_id"] = "old-parent"
        with self.assertRaisesRegex(ValueError, "not in the new release"):
            self.locate(case)

    def test_wrong_child_parent_is_rejected(self):
        self.records["new-child"]["parent_id"] = "other-parent"
        with self.assertRaisesRegex(ValueError, "Child / Parent mismatch"):
            self.locate(evidence_case())

    def test_wrong_quote_offset_or_source_is_rejected(self):
        case = evidence_case()
        case["anchors"][0]["accepted_locations"][0]["matches"][0]["raw_end"] = 9
        with self.assertRaisesRegex(ValueError, "exact quote"):
            self.locate(case)
        case = evidence_case()
        case["anchors"][0]["accepted_locations"][0]["source_id"] = "wrong-source"
        with self.assertRaisesRegex(ValueError, "source mismatch"):
            self.locate(case)

    def test_empty_accepted_locations_are_rejected(self):
        case = evidence_case()
        case["anchors"][0]["accepted_locations"] = []
        with self.assertRaisesRegex(ValueError, "no accepted location"):
            self.locate(case)


class PublicationProtectionTests(unittest.TestCase):
    def bundle(self, root):
        sidecar = root / "gold-v5-alignment-audit.json"
        sidecar.write_bytes(b"original sidecar")
        frozen = root / "rag_eval_gold_v5.frozen.jsonl"
        outputs = {frozen: b"frozen result\n", root / "view.jsonl": b"view\n",
                   sidecar: b"approved sidecar"}
        control = {"frozen_path": frozen, "sidecar_before_sha256": freeze.sha(sidecar.read_bytes())}
        return outputs, control

    def test_new_freeze_is_readonly_and_check_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outputs, control = self.bundle(root)
            frozen = control["frozen_path"]
            try:
                freeze.publish_bundle(outputs, control)
                self.assertTrue(freeze.readonly(frozen))
                sidecar = root / "gold-v5-alignment-audit.json"
                control["sidecar_before_sha256"] = freeze.sha(sidecar.read_bytes())
                before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in outputs}
                freeze.publish_bundle(outputs, control, check_only=True)
                self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in outputs})
            finally:
                if frozen.exists():
                    frozen.chmod(stat.S_IREAD | stat.S_IWRITE)

    def test_existing_different_frozen_file_stops_before_other_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outputs, control = self.bundle(root)
            frozen = control["frozen_path"]
            frozen.write_bytes(b"previous frozen bytes")
            with self.assertRaisesRegex(ValueError, "immutable output already exists"):
                freeze.publish_bundle(outputs, control)
            self.assertEqual(frozen.read_bytes(), b"previous frozen bytes")
            self.assertFalse((root / "view.jsonl").exists())
            self.assertEqual((root / "gold-v5-alignment-audit.json").read_bytes(), b"original sidecar")

    def test_sidecar_external_edit_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outputs, control = self.bundle(root)
            sidecar = root / "gold-v5-alignment-audit.json"
            sidecar.write_bytes(b"West edited this")
            with self.assertRaisesRegex(ValueError, "sidecar changed"):
                freeze.publish_bundle(outputs, control)
            self.assertEqual(sidecar.read_bytes(), b"West edited this")
            self.assertFalse(control["frozen_path"].exists())

    def test_missing_output_in_check_mode_is_not_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            outputs, control = self.bundle(Path(tmp))
            with self.assertRaisesRegex(ValueError, "missing or changed"):
                freeze.publish_bundle(outputs, control, check_only=True)
            self.assertFalse(control["frozen_path"].exists())

    def test_lost_readonly_attribute_fails_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outputs, control = self.bundle(root)
            frozen = control["frozen_path"]
            freeze.publish_bundle(outputs, control)
            frozen.chmod(stat.S_IREAD | stat.S_IWRITE)
            control["sidecar_before_sha256"] = freeze.sha((root / "gold-v5-alignment-audit.json").read_bytes())
            with self.assertRaisesRegex(ValueError, "read-only attribute"):
                freeze.publish_bundle(outputs, control, check_only=True)

    def test_gold_v4_hash_mismatch_is_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "v4.jsonl"
            path.write_bytes(b"changed input")
            with self.assertRaisesRegex(ValueError, "Gold V4 SHA256 mismatch"):
                freeze.check_v4(path)


class RealFreezeContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = Path(__file__).resolve().parents[3]
        cls.signoff_path = Path(__file__).with_name("west_signoff.json")
        cls.signoff = json.loads(cls.signoff_path.read_bytes())
        cls.sidecar_path = cls.repo / freeze.SIDECAR
        cls.original_sidecar_bytes = cls.sidecar_path.read_bytes()
        cls.current = json.loads(cls.original_sidecar_bytes)
        cls.source = freeze.unsigned_audit(cls.current, cls.signoff)
        cls.outputs, cls.control = freeze.build_bundle(cls.repo, cls.signoff_path)
        cls.frozen = freeze.read_jsonl(cls.outputs[cls.repo / freeze.FROZEN])
        cls.ragas = freeze.read_jsonl(cls.outputs[cls.repo / freeze.RAGAS])
        cls.approved = json.loads(cls.outputs[cls.repo / freeze.SIDECAR])
        cls.manifest = json.loads(cls.outputs[cls.repo / freeze.MANIFEST])
        cls.by_case = {case["case_id"]: case for case in cls.frozen}

    def test_only_nine_changes_label_and_final_distribution_matches(self):
        previous = {c["case_id"]: c["suggested_label"] for c in self.source["cases"]}
        current = {c["case_id"]: c["answerability"] for c in self.frozen}
        self.assertEqual([key for key in previous if previous[key] != current[key]], ["rag-v0-009"])
        self.assertEqual(current["rag-v0-009"], "partial")
        self.assertEqual(dict(Counter(current.values())), freeze.DISTRIBUTION)
        self.assertEqual(self.approved["summary"]["labels"], freeze.DISTRIBUTION)

    def test_frozen_50_rows_unique_ids_metadata_and_schema(self):
        self.assertEqual(len(self.frozen), 50)
        self.assertEqual(len({r["case_id"] for r in self.frozen}), 50)
        self.assertEqual(len(self.outputs[self.repo / freeze.FROZEN].splitlines()), 50)
        expected = {"release_id", "schema_version", "frozen_by", "frozen_at", "case_id",
                    "question", "ground_truth", "contexts", "answer", "answerability",
                    "required_facts", "relevant_parent_ids", "anchors", "old_label", "changes"}
        for row in self.frozen:
            self.assertEqual(set(row), expected)
            self.assertEqual(list(row)[:4], ["release_id", "schema_version", "frozen_by", "frozen_at"])
            self.assertEqual(row["release_id"], freeze.RELEASE_ID)
            self.assertEqual(row["schema_version"], "v5")
            self.assertEqual(row["frozen_by"], "West")
            self.assertEqual(row["frozen_at"], self.signoff["frozen_at"])

    def test_strict_ragas_projection_and_case_id_row_map(self):
        self.assertEqual(len(self.ragas), 50)
        for i, (view, record, mapping) in enumerate(zip(self.ragas, self.frozen, self.manifest["row_map"]), 1):
            self.assertEqual(list(view), list(freeze.RAGAS_KEYS))
            self.assertNotIn("case_id", view)
            self.assertEqual(view, {k: record[k] for k in freeze.RAGAS_KEYS})
            self.assertEqual(view["contexts"], [])
            self.assertEqual(view["answer"], "")
            self.assertEqual(mapping["line"], i)
            self.assertEqual(mapping["case_id"], record["case_id"])
            self.assertEqual(mapping["ground_truth_sha256"], freeze.sha(view["ground_truth"].encode('utf-8')))

    def test_signed_rewrites_and_seven_empty_needs_input_cases(self):
        for key in freeze.REWRITES:
            row = self.by_case[key]
            self.assertEqual(row["ground_truth"], self.signoff["approved_rewrites"][key]["ground_truth"])
            self.assertEqual(row["required_facts"], self.signoff["approved_rewrites"][key]["required_facts"])
        needs = {r["case_id"] for r in self.frozen if r["answerability"] == "needs_input"}
        self.assertEqual(needs, freeze.ORIGINAL_NEEDS | freeze.CLARIFIED_NEEDS)
        for key in needs:
            row = self.by_case[key]
            self.assertEqual((row["ground_truth"], row["relevant_parent_ids"], row["anchors"]), ("", [], []))

    def test_parent_ids_are_accepted_location_union_and_release_members(self):
        inventory = json.loads((self.repo / "workspace/moodflow-review/gold-v5-Gate0-inventory.json").read_bytes())
        root = Path(inventory["run_root"])
        knowledge = freeze.read_jsonl((root / "data/knowledge/v2/knowledge_enriched_chunks.jsonl").read_bytes())
        by_id = {r["chunk_id"]: r for r in knowledge}
        for row, case in zip(self.frozen, self.approved["cases"]):
            expected = sorted({loc["parent_id"] for anchor in case["anchors"] if anchor["status"] == "matched"
                               for loc in anchor["accepted_locations"]}) if row["answerability"] != "needs_input" else []
            self.assertEqual(row["relevant_parent_ids"], expected)
            for anchor in row["anchors"]:
                self.assertIsNone(by_id[anchor["parent_id"]]["parent_id"])
                for quoted in anchor["quotes"]:
                    self.assertEqual(by_id[quoted["chunk_id"]]["content"][quoted["start"]:quoted["end"]], quoted["exact_text"])
        self.assertTrue(self.by_case["rag-v0-002"]["relevant_parent_ids"])
        self.assertEqual(self.by_case["rag-v0-006"]["relevant_parent_ids"], [])

    def test_rewrites_upgrade_and_all_downgrades_have_change_reasons(self):
        for key in freeze.REWRITES:
            self.assertIn("answer_and_required_facts_rewritten", {c["type"] for c in self.by_case[key]["changes"]})
        self.assertIn("evidence_realigned_and_label_upgraded", {c["type"] for c in self.by_case["rag-v0-009"]["changes"]})
        for row in self.frozen:
            if row["old_label"] != row["answerability"]:
                changes = [c for c in row["changes"] if c.get("from") == row["old_label"] and c.get("to") == row["answerability"]]
                self.assertTrue(changes, row["case_id"])
                self.assertTrue(all(change["reason"] for change in changes))

    def test_unapproved_label_or_rewrite_change_is_rejected(self):
        signoff = deepcopy(self.signoff)
        signoff["approved_labels"]["rag-v0-001"] = "partial"
        with self.assertRaisesRegex(ValueError, "unapproved label change"):
            freeze.approve_audit(self.source, signoff)
        signoff = deepcopy(self.signoff)
        signoff["approved_rewrites"]["rag-v0-024"]["ground_truth"] += " unauthorized"
        with self.assertRaisesRegex(ValueError, "signed rewrite differs"):
            freeze.approve_audit(self.source, signoff)

    def test_wrong_distribution_signoff_count_and_timestamp_are_rejected(self):
        for field, value, message in [
            ("final_distribution", {"full": 19}, "distribution"),
            ("signed_items", [], "seven signoff"),
            ("frozen_at", "2026-10-17T14:00:00", "timezone")]:
            with self.subTest(field=field):
                signoff = deepcopy(self.signoff)
                signoff[field] = value
                with self.assertRaisesRegex(ValueError, message):
                    freeze.approve_audit(self.source, signoff)

    def test_sidecar_edits_outside_signoff_are_rejected(self):
        changed = deepcopy(self.current)
        changed["cases"][1]["question"] += " changed"
        with self.assertRaisesRegex(ValueError, "reviewed Gate 1 snapshot"):
            freeze.approve_audit(changed, self.signoff)

    def test_signed_sidecar_can_be_verified_again_without_history_loss(self):
        restored = freeze.unsigned_audit(self.approved, self.signoff)
        self.assertEqual(restored, self.source)
        for old, new in zip(self.source["cases"], self.approved["cases"]):
            self.assertEqual(old["notes"], new["notes"])
            self.assertEqual(old["alternatives"], new["alternatives"])
        self.assertEqual(self.sidecar_path.read_bytes(), self.original_sidecar_bytes)

    def test_signed_sidecar_label_tampering_is_rejected(self):
        changed = deepcopy(self.approved)
        changed["cases"][0]["suggested_label"] = "partial"
        with self.assertRaisesRegex(ValueError, "signed sidecar labels changed"):
            freeze.approve_audit(changed, self.signoff)

    def test_ragas_mismatch_or_duplicate_case_is_rejected(self):
        bad_view = deepcopy(self.ragas)
        bad_view[0]["ground_truth"] += " mismatch"
        with self.assertRaisesRegex(ValueError, "Ragas view differs"):
            freeze.validate_rows(self.frozen, bad_view, self.approved)
        bad_rows = deepcopy(self.frozen)
        bad_rows[1]["case_id"] = bad_rows[0]["case_id"]
        with self.assertRaisesRegex(ValueError, "unique case_id"):
            freeze.validate_rows(bad_rows, self.ragas, self.approved)

    def test_manifest_hashes_and_scoped_lf_attributes(self):
        for rel, artifact in self.manifest["artifacts"].items():
            self.assertEqual(artifact["sha256"], freeze.sha(self.outputs[self.repo / rel]))
        rules = (self.repo / "ai-service/evals/datasets/.gitattributes").read_text(encoding='utf-8')
        effective = [line for line in rules.splitlines() if line.strip() and not line.startswith('#')]
        self.assertEqual(effective, ["rag_eval_gold_v5.frozen.jsonl text eol=lf", "rag_eval_gold_v5.ragas.jsonl text eol=lf"])
        self.assertEqual(freeze.sha((self.repo / 'ai-service/evals/datasets/rag_eval_gold_v4.frozen.jsonl').read_bytes()), freeze.V4_SHA256)


if __name__ == '__main__':
    unittest.main()
