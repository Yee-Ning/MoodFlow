"""Offline unit tests and real 50-case artifact contract checks."""

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import align_gold_v5 as alignment


def row(chunk_id, text, parent_id=None, source="source-a"):
    return {"chunk_id": chunk_id, "parent_id": parent_id, "content": text,
            "metadata": {"source_id": source}}


def anchor(text="a b", source="source-a"):
    return {"anchor_id": "span-1", "exact_text": text, "source_id": source,
            "claim": "a b"}


def fact(status, kind="evidence"):
    return {"text": "fact", "kind": kind, "status": status}


class MatchingTests(unittest.TestCase):
    def test_only_whitespace_is_normalized(self):
        self.assertEqual(alignment.normalized("  晴天\n\t a   b  "), "晴天 a b")
        corpus = alignment.Corpus([row("p", "甲，乙")], [])
        self.assertEqual(corpus.locate(anchor("甲,乙"))["status"], "text_changed")
        self.assertEqual(corpus.locate(anchor("甲 乙"))["status"], "text_changed")

    def test_parent_and_children_collapse_to_one_location(self):
        corpus = alignment.Corpus([row("p", "a\n b"), row("c1", "a b", "p"),
                                   row("c2", "prefix a b suffix", "p")], [])
        result = corpus.locate(anchor())
        self.assertEqual(result["status"], "matched")
        self.assertEqual(len(result["accepted_locations"]), 1)
        self.assertEqual(result["accepted_locations"][0]["child_ids"], ["c1", "c2"])
        self.assertEqual(result["accepted_locations"][0]["matches"][0]["quote"], "a\n b")

    def test_cross_child_quote_can_match_parent_only(self):
        corpus = alignment.Corpus([row("p", "alpha beta gamma"),
                                   row("c1", "alpha beta", "p"), row("c2", "gamma", "p")], [])
        result = corpus.locate(anchor("beta gamma"))
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["accepted_locations"][0]["child_ids"], [])

    def test_different_parent_candidates_remain_ambiguous(self):
        corpus = alignment.Corpus([row("p1", "a b"), row("p2", "a b")], [])
        result = corpus.locate(anchor())
        self.assertEqual(result["status"], "ambiguous")
        self.assertEqual(result["accepted_locations"], [])
        self.assertEqual(len(result["all_exact_candidates"]), 2)

    def test_explicit_source_disambiguates_duplicate_sources(self):
        corpus = alignment.Corpus([row("p1", "a b"), row("p2", "a b", source="source-b")], [])
        result = corpus.locate(anchor())
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["accepted_locations"][0]["parent_id"], "p1")
        self.assertEqual(len(result["all_exact_candidates"]), 2)

    def test_unique_cross_source_quote_records_source_change(self):
        corpus = alignment.Corpus([row("p", "a b", source="replacement")], [])
        result = corpus.locate(anchor())
        self.assertEqual(result["status"], "matched")
        self.assertTrue(result["source_changed"])

    def test_absent_source_recovers_only_as_quarantine_text(self):
        corpus = alignment.Corpus([row("p", "unrelated", source="other")], [{
            "source_id": "source-a", "document_id": "doc-a", "markdown": "a\n b",
            "path": "quarantine/document.json"}])
        result = corpus.locate(anchor())
        self.assertEqual(result["status"], "source_absent")
        self.assertEqual(result["recovery"], "recoverable_by_unquarantine")
        self.assertEqual(result["accepted_locations"], [])
        self.assertIsNone(result["quarantine_matches"][0]["parent_id"])
        self.assertFalse(result["quarantine_matches"][0]["usable_in_release"])

    def test_absent_source_and_absent_text_is_out_of_scope_candidate(self):
        result = alignment.Corpus([], []).locate(anchor())
        self.assertEqual(result["status"], "source_absent")
        self.assertEqual(result["recovery"], "题超纲候选")

    def test_present_source_text_change_does_not_accept_similarity(self):
        corpus = alignment.Corpus([row("p", "a changed b")], [])
        result = corpus.locate(anchor())
        self.assertEqual(result["status"], "text_changed")
        self.assertEqual(result["accepted_locations"], [])
        self.assertTrue(result["suggestions"])
        self.assertFalse(result["suggestions"][0]["accepted_as_evidence"])

    def test_table_row_requires_correct_header(self):
        corpus = alignment.Corpus([row("p1", "作词\na b"), row("p2", "编曲\na b")], [])
        request = {**anchor(), "required_context": "作词"}
        result = corpus.locate(request)
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["accepted_locations"][0]["parent_id"], "p1")
        self.assertEqual(corpus.locate({**anchor(), "required_context": "导演"})["status"], "text_changed")

    def test_empty_quote_is_never_a_match(self):
        with self.assertRaises(ValueError):
            alignment.Corpus([], []).locate(anchor("  \n "))


class FactAndLabelTests(unittest.TestCase):
    def test_conjunction_does_not_pass_with_one_missing_part(self):
        item = {"text": "two facts", "kind": "evidence", "any_of": [["a", "b"]]}
        result = alignment.fact_alignment(item, {"a": {"status": "matched"},
                                                 "b": {"status": "source_absent"}})
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["proof_anchor_ids"], [])
        self.assertEqual(result["partial_anchor_ids"], ["a"])

    def test_explicit_alternative_can_replace_missing_original(self):
        item = {"text": "same claim", "kind": "evidence", "any_of": [["old"], ["new"]]}
        result = alignment.fact_alignment(item, {"old": {"status": "source_absent"},
                                                 "new": {"status": "matched"}})
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["proof_anchor_ids"], ["new"])

    def test_downgrades_and_no_automatic_upgrade(self):
        matched, missing = fact("matched"), fact("source_absent")
        self.assertEqual(alignment.suggest_label("full", [matched]), "full")
        self.assertEqual(alignment.suggest_label("full", [matched, missing]), "partial")
        self.assertEqual(alignment.suggest_label("full", [missing]), "insufficient")
        self.assertEqual(alignment.suggest_label("partial", [matched]), "partial")
        self.assertEqual(alignment.suggest_label("insufficient", [matched]), "insufficient")
        self.assertEqual(alignment.suggest_label("full", [fact("ambiguous")]), "insufficient")

    def test_scope_limits_and_empty_anchors_never_make_full(self):
        self.assertEqual(alignment.suggest_label("full", []), "insufficient")
        self.assertEqual(alignment.suggest_label("full", [fact("scope", "scope_limit")]), "insufficient")
        no_anchor = alignment.fact_alignment({"text": "claim", "kind": "evidence", "any_of": []}, {})
        self.assertEqual(no_anchor["status"], "unresolved_no_anchor")

    def test_needs_input_and_pending_clarification_take_precedence(self):
        self.assertEqual(alignment.suggest_label("needs_input", [fact("matched")]), "needs_input")
        self.assertEqual(alignment.suggest_label("full", [fact("matched")], clarify=True), "needs_input")

    def test_partly_matched_compound_fact_retains_source_absent_cause(self):
        case = {"case_id": "compound-case", "query": "compound question",
                "expected_outcome": "answerable", "answerability_level": "fully_answerable",
                "reference_answer": "both claims", "required_facts": ["both claims"],
                "evidence_groups": [], "supporting_spans": [
                    {"source_id": "source-a", "exact_text": "present", "claim": "first"},
                    {"source_id": "missing-source", "exact_text": "absent", "claim": "second"}]}
        entry = {"extra_anchors": [], "notes": [], "groups": [], "facts": [
            {"text": "both claims", "kind": "evidence", "any_of": [["span-1", "span-2"]]}]}
        result = alignment.align_case(case, entry, alignment.Corpus([row("p", "present")], []), 1)
        self.assertEqual(result["suggested_label"], "partial")
        self.assertEqual(result["evidence_status"], "source_absent")
        self.assertEqual(result["fact_alignment"][0]["status"], "partial")


class ProtectionTests(unittest.TestCase):
    def test_hash_mismatch_stops_without_creating_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gold.jsonl"
            path.write_bytes(b"not the frozen Gold\n")
            with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
                alignment.check_gold(path)
            self.assertEqual(list(Path(tmp).iterdir()), [path])

    def test_duplicate_case_and_original_needs_contract_are_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gold.jsonl"
            cases = [{"case_id": f"case-{i}", "expected_outcome": "answerable",
                      "reference_answer": "answer"} for i in range(50)]
            cases[1]["case_id"] = cases[0]["case_id"]
            raw = ''.join(json.dumps(case) + '\n' for case in cases).encode()
            path.write_bytes(raw)
            with patch.object(alignment, "GOLD_SHA256", hashlib.sha256(raw).hexdigest()):
                with self.assertRaisesRegex(ValueError, "duplicate"):
                    alignment.check_gold(path)
            cases[1]["case_id"] = "different"
            raw = ''.join(json.dumps(case) + '\n' for case in cases).encode()
            path.write_bytes(raw)
            with patch.object(alignment, "GOLD_SHA256", hashlib.sha256(raw).hexdigest()):
                with self.assertRaisesRegex(ValueError, "needs_input contract"):
                    alignment.check_gold(path)

    def test_check_mode_and_idempotent_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "new.json"
            with self.assertRaisesRegex(ValueError, "Missing output"):
                alignment.publish_outputs({path: b"result"}, check_only=True)
            self.assertFalse(path.exists())
            alignment.publish_outputs({path: b"result"})
            alignment.publish_outputs({path: b"result"})
            alignment.publish_outputs({path: b"result"}, check_only=True)
            self.assertEqual(path.read_bytes(), b"result")

    def test_different_existing_output_preflights_all_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            untouched = Path(tmp) / "new.json"
            existing = Path(tmp) / "existing.md"
            existing.write_bytes(b"West edits")
            with self.assertRaisesRegex(ValueError, "Refusing to overwrite"):
                alignment.publish_outputs({untouched: b"new", existing: b"replacement"})
            self.assertFalse(untouched.exists())
            self.assertEqual(existing.read_bytes(), b"West edits")


class RealInputIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = Path(__file__).resolve().parents[3]
        cls.plan_path = Path(__file__).with_name("review_plan.json")
        cls.inventory_path = cls.repo / "workspace/moodflow-review/gold-v5-Gate0-inventory.json"
        cls.gold_path = cls.repo / "ai-service/evals/datasets/rag_eval_gold_v4.frozen.jsonl"
        cls.before = cls.gold_path.read_bytes()
        cls.plan = json.loads(cls.plan_path.read_text(encoding="utf-8"))
        cls.outputs = alignment.build_outputs(cls.repo, cls.inventory_path, cls.plan_path)
        cls.audit = json.loads(cls.outputs[cls.repo / "workspace/moodflow-review/gold-v5-alignment-audit.json"])
        cls.cases = {case["case_id"]: case for case in cls.audit["cases"]}
        cls.gold = alignment.check_gold(cls.gold_path)

    def test_50_case_order_and_exact_four_ragas_columns(self):
        path = self.repo / "ai-service/evals/datasets/rag_eval_gold_v5.candidate.jsonl"
        candidates = [json.loads(line) for line in self.outputs[path].decode().splitlines()]
        self.assertEqual(len(candidates), 50)
        for original, candidate, reviewed in zip(self.gold, candidates, self.audit["cases"]):
            self.assertEqual(set(candidate), {"question", "ground_truth", "contexts", "answer"})
            self.assertEqual(candidate["question"], original["query"])
            self.assertEqual(candidate["ground_truth"], reviewed["proposed_ground_truth"])
            self.assertEqual(candidate["contexts"], [])
            self.assertEqual(candidate["answer"], "")
            self.assertEqual(reviewed["case_id"], original["case_id"])
            self.assertEqual(reviewed["review_status"], "pending_west")

    def test_original_four_empty_answers_and_three_new_proposals_are_distinct(self):
        carried = {key for key, case in self.cases.items()
                   if case["changes"]["original_needs_input_carried_over"]}
        self.assertEqual(carried, alignment.ORIGINAL_NEEDS_INPUT)
        for key in carried:
            self.assertIsNone(self.cases[key]["original_reference_answer"])
            self.assertEqual(self.cases[key]["proposed_ground_truth"], "")
            self.assertEqual(self.cases[key]["proposed_required_facts"], [])
        for key in alignment.CLARIFY_CASES:
            self.assertEqual(self.cases[key]["suggested_label"], "needs_input")
            self.assertEqual(len(self.cases[key]["alternatives"]), 2)
            self.assertFalse(self.cases[key]["changes"]["original_needs_input_carried_over"])

    def test_accepted_quotes_point_to_actual_new_chunks(self):
        inv = json.loads(self.inventory_path.read_bytes())
        root = Path(inv["run_root"])
        records = alignment.load_jsonl(root / "data/knowledge/v2/knowledge_enriched_chunks.jsonl")
        by_id = {record["chunk_id"]: record for record in records}
        for case in self.audit["cases"]:
            for evidence in case["anchors"]:
                for location in evidence["accepted_locations"]:
                    self.assertIn(location["parent_id"], by_id)
                    for match in location["matches"]:
                        source = by_id[match["chunk_id"]]["content"]
                        self.assertEqual(source[match["raw_start"]:match["raw_end"]], match["quote"])
                        self.assertEqual(alignment.normalized(match["quote"]),
                                         alignment.normalized(evidence["exact_text"]))
                    for kid in location["child_ids"]:
                        self.assertEqual(by_id[kid]["parent_id"], location["parent_id"])
                if evidence["status"] == "source_absent":
                    self.assertEqual(evidence["accepted_locations"], [])
                    self.assertTrue(evidence["quarantine_matches"])

    def test_special_rewrites_are_grounded_and_do_not_upgrade_nine(self):
        for key in ["rag-v0-001", "rag-v0-024", "rag-v0-009"]:
            case = self.cases[key]
            self.assertTrue(case["changes"]["reference_answer_changed"])
            self.assertTrue(case["changes"]["required_facts_changed"])
            self.assertTrue(all(f["status"] == "matched" for f in case["fact_alignment"]
                                if f["kind"] == "evidence"))
        self.assertIn("宋健彰", self.cases["rag-v0-024"]["proposed_ground_truth"])
        self.assertEqual(self.cases["rag-v0-009"]["suggested_label"], "insufficient")
        self.assertEqual(len(self.cases["rag-v0-049"]["evidence_groups"]), 3)
        self.assertTrue(all(g["status"] == "matched" for g in self.cases["rag-v0-049"]["evidence_groups"]))

    def test_legacy_ids_do_not_influence_alignment(self):
        inv = json.loads(self.inventory_path.read_bytes())
        root = Path(inv["run_root"])
        corpus = alignment.Corpus(alignment.load_jsonl(root / "data/knowledge/v2/knowledge_enriched_chunks.jsonl"), [])
        original = next(c for c in self.gold if c["case_id"] == "rag-v0-026")
        mutated = copy.deepcopy(original)
        mutated["relevant_parent_ids"] = ["deliberately-wrong-id"]
        for span in mutated["supporting_spans"]:
            span["parent_id"] = "deliberately-wrong-id"
        for group in mutated["evidence_groups"]:
            group["acceptable_parent_ids"] = ["deliberately-wrong-id"]
        entry = self.plan["cases"][original["case_id"]]
        self.assertEqual(alignment.align_case(original, entry, corpus, 13),
                         alignment.align_case(mutated, entry, corpus, 13))

    def test_missing_plan_reference_fails_and_frozen_bytes_are_unchanged(self):
        broken = copy.deepcopy(self.plan)
        broken["cases"]["rag-v0-001"]["facts"][0]["any_of"] = [["missing-anchor"]]
        with self.assertRaisesRegex(ValueError, "missing"):
            alignment.validate_plan(self.gold, broken)
        self.assertEqual(self.gold_path.read_bytes(), self.before)
        self.assertEqual(alignment.digest(self.before), alignment.GOLD_SHA256)

    def test_gate0_input_hash_drift_is_rejected(self):
        inventory = json.loads(self.inventory_path.read_bytes())
        inventory["artifacts"][0]["sha256"] = "0" * 64
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "inventory.json"
            path.write_text(json.dumps(inventory), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Gate 0 input changed"):
                alignment.build_outputs(self.repo, path, self.plan_path)
            self.assertEqual(list(Path(tmp).iterdir()), [path])

    def test_release_count_drift_is_rejected(self):
        original_loader = alignment.load_jsonl

        def changed_projection(path):
            values = original_loader(path)
            return values[:-1] if path.name == "index_projection.jsonl" else values

        with patch.object(alignment, "load_jsonl", side_effect=changed_projection):
            with self.assertRaisesRegex(ValueError, "release counts changed"):
                alignment.build_outputs(self.repo, self.inventory_path, self.plan_path)

    def test_real_original_needs_input_assets_stay_identical(self):
        for original in self.gold:
            if original["case_id"] not in alignment.ORIGINAL_NEEDS_INPUT:
                continue
            record = self.cases[original["case_id"]]
            self.assertEqual(record["question"], original["query"])
            self.assertEqual(record["old_expected_outcome"], original["expected_outcome"])
            self.assertEqual(record["old_answerability_level"], original["answerability_level"])
            self.assertEqual(record["original_required_facts"], original["required_facts"])
            self.assertEqual(record["anchors"], [])
            self.assertEqual(record["unanchored_claim_searches"], [])


if __name__ == "__main__":
    unittest.main()
