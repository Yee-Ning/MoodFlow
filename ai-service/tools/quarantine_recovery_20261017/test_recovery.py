"""Offline admission, provenance, semantic-retention, and reuse safeguards."""

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import recovery as r
from app.rag.clean.config import TemplateDeduplicationConfig
from app.rag.clean.template_dedup import TemplateDeduplicator


class RecoverySafeguards(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_shared_product_facts_survive_but_unreviewed_boilerplate_does_not(self):
        fact = "Preorder 2020/11/6. Sterling Sound original mastering, 180 GRAMS 33 1/3RPM."
        ui = "Contact our website team for advertising and commercial partnerships."
        base = TemplateDeduplicator(TemplateDeduplicationConfig())
        base.train_site_templates(
            [(str(i), "label", fact + "\n\n" + ui) for i in range(3)]
        )
        self.assertNotIn(
            fact,
            base.deduplicate(fact + "\n\n" + ui, doc_id="a", site="label").cleaned_text,
        )
        wrapped = r.ReviewedBlockRetention(base, {"a": {r.block_digest(fact)}})
        result = wrapped.deduplicate(fact + "\n\n" + ui, doc_id="a", site="label")
        self.assertIn(fact, result.cleaned_text)
        self.assertNotIn(ui, result.cleaned_text)
        self.assertEqual(result.removed_blocks, 1)

    def test_retention_cannot_leak_to_another_document(self):
        body = "A repeated factual paragraph of at least twenty characters."
        base = TemplateDeduplicator(TemplateDeduplicationConfig())
        base.train_site_templates([(str(i), "site", body) for i in range(3)])
        wrapped = r.ReviewedBlockRetention(base, {"approved": {r.block_digest(body)}})
        self.assertEqual(
            wrapped.deduplicate(body, doc_id="other", site="site").cleaned_text, ""
        )

    def test_changed_reviewed_text_fails_closed(self):
        base = TemplateDeduplicator()
        wrapped = r.ReviewedBlockRetention(base, {"a": {r.block_digest("year 2001")}})
        with self.assertRaisesRegex(ValueError, "reviewed_block_missing_or_changed"):
            wrapped.deduplicate("year 2002", doc_id="a", site="label")

    def test_no_template_hit_preserves_exact_whitespace(self):
        body = "First paragraph.\n\n\nSecond paragraph.\n"
        wrapped = r.ReviewedBlockRetention(
            TemplateDeduplicator(), {"a": {r.block_digest("First paragraph.")}}
        )
        self.assertEqual(
            wrapped.deduplicate(body, doc_id="a", site="label").cleaned_text, body
        )

    def decision(self):
        raw = self.root / "raw.html"
        normalized = self.root / "data/normalized/sample/document.json"
        raw.write_text("source text", encoding="utf-8")
        normalized.parent.mkdir(parents=True, exist_ok=True)
        normalized.write_text("{}", encoding="utf-8")
        return {
            "source_id": "sample",
            "raw_path": "raw.html",
            "raw_sha256": r.digest(raw),
            "normalized_sha256": r.digest(normalized),
            "status": "passed_document_qa",
            "reviewed_by": "Codex",
            "reason": "Source and body checked",
            "evidence": ["raw title"],
            "reviewed_at": "2026-10-17T15:00:00+08:00",
            "cleaned_text_sha256": "example",
        }

    def test_reviewed_document_with_current_inputs_passes(self):
        r.validate_document_decision(self.decision(), self.root, final=True)

    def test_execution_authorization_is_not_document_approval(self):
        row = self.decision()
        row["status"] = "approved_for_draft"
        with self.assertRaisesRegex(ValueError, "document_qa_not_passed"):
            r.validate_document_decision(row, self.root, final=True)

    def test_provisional_review_can_generate_draft_only(self):
        row = self.decision()
        row["status"] = "approved_for_draft"
        row.pop("cleaned_text_sha256")
        r.validate_document_decision(row, self.root, final=False)

    def test_quarantined_document_is_not_admitted(self):
        row = self.decision()
        row["status"] = "quarantined"
        with self.assertRaisesRegex(ValueError, "document_qa_not_passed"):
            r.validate_document_decision(row, self.root, final=True)

    def test_no_forged_west_document_signature(self):
        row = self.decision()
        row["reviewed_by"] = "West"
        with self.assertRaisesRegex(ValueError, "document_qa_not_passed"):
            r.validate_document_decision(row, self.root, final=True)

    def test_missing_review_evidence_is_rejected(self):
        for field in ["evidence", "reason", "reviewed_at"]:
            with self.subTest(field=field):
                row = self.decision()
                row.pop(field)
                with self.assertRaisesRegex(ValueError, "document_qa_evidence_missing"):
                    r.validate_document_decision(row, self.root, final=True)

    def test_raw_change_invalidates_approval(self):
        row = self.decision()
        (self.root / "raw.html").write_text("changed", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "document_qa_input_changed"):
            r.validate_document_decision(row, self.root, final=True)

    def test_normalized_change_invalidates_approval(self):
        row = self.decision()
        (self.root / "data/normalized/sample/document.json").write_text(
            '{"changed":true}', encoding="utf-8"
        )
        with self.assertRaisesRegex(ValueError, "document_qa_input_changed"):
            r.validate_document_decision(row, self.root, final=True)

    def test_raw_path_escape_is_rejected_even_when_hash_matches(self):
        row = self.decision()
        outside = self.root.parent / (self.root.name + "-outside.html")
        outside.write_text("source text", encoding="utf-8")
        self.addCleanup(outside.unlink)
        row["raw_path"] = str(outside)
        with self.assertRaisesRegex(ValueError, "document_qa_input_changed"):
            r.validate_document_decision(row, self.root, final=True)

    def test_final_clean_review_hash_is_required(self):
        row = self.decision()
        row.pop("cleaned_text_sha256")
        with self.assertRaisesRegex(ValueError, "clean_qa_hash_missing"):
            r.validate_document_decision(row, self.root, final=True)

    def test_exact_clean_text_and_warning_decision_passes(self):
        row = {
            "source_id": "sample",
            "cleaned_text_sha256": hashlib.sha256(b"body").hexdigest(),
            "accepted_clean_warnings": ["removed_ratio_high:0.4000"],
        }
        r.validate_clean_result(row, "body", ["removed_ratio_high:0.4000"])

    def test_changed_text_or_warning_requires_new_review(self):
        row = {
            "source_id": "sample",
            "cleaned_text_sha256": hashlib.sha256(b"body").hexdigest(),
            "accepted_clean_warnings": [],
        }
        for text, warnings in [
            ("changed", []),
            ("body", ["numeric_or_date_facts_lost:count=4,ratio=0.5"]),
        ]:
            with self.subTest(text=text, warnings=warnings):
                with self.assertRaisesRegex(ValueError, "clean_review_stale"):
                    r.validate_clean_result(row, text, warnings)

    def test_merging_preserves_old_bytes_and_new_rows(self):
        old, new, output = [
            self.root / x for x in ["old.jsonl", "new.jsonl", "output.jsonl"]
        ]
        old.write_bytes(b'{ "chunk_id" : "old", "unknown" : 4 }\r\n')
        new.write_bytes(b'{"chunk_id":"new"}\n')
        summary = r.merge_reusing_bytes(old, new, output)
        self.assertTrue(output.read_bytes().startswith(old.read_bytes()))
        self.assertEqual([x["chunk_id"] for x in r.read_jsonl(output)], ["old", "new"])
        self.assertEqual(summary["old_rows_reused"], 1)

    def test_duplicate_ids_do_not_overwrite_output_or_input(self):
        old, new, output = [
            self.root / x for x in ["old.jsonl", "new.jsonl", "output.jsonl"]
        ]
        old.write_text('{"chunk_id":"same"}\n', encoding="utf-8")
        new.write_bytes(old.read_bytes())
        output.write_text("preserve existing output", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "duplicate_or_overlapping_merge_ids"):
            r.merge_reusing_bytes(old, new, output)
        self.assertEqual(output.read_text("utf-8"), "preserve existing output")
        self.assertEqual(old.read_bytes(), new.read_bytes())

    def test_missing_final_newline_does_not_join_json_records(self):
        old, new, output = [
            self.root / x for x in ["old.jsonl", "new.jsonl", "output.jsonl"]
        ]
        old.write_bytes(b'{"chunk_id":"a"}')
        new.write_bytes(b'{"chunk_id":"b"}\n')
        r.merge_reusing_bytes(old, new, output)
        self.assertEqual(len(r.read_jsonl(output)), 2)

    def test_merge_cannot_replace_either_input_file(self):
        old, new = self.root / "old.jsonl", self.root / "new.jsonl"
        old.write_bytes(b'{"chunk_id":"a"}\n')
        new.write_bytes(b'{"chunk_id":"b"}\n')
        for output in [old, new]:
            with self.subTest(output=str(output)):
                with self.assertRaisesRegex(
                    ValueError, "merge_must_not_overwrite_input"
                ):
                    r.merge_reusing_bytes(old, new, output)

    def test_historical_or_default_run_is_refused(self):
        for path in [r.AI_ROOT, r.AI_ROOT / "data/knowledge/v2", self.root]:
            with self.subTest(path=str(path)):
                with self.assertRaisesRegex(ValueError, "outside_dedicated_namespace"):
                    r.guard_run(path)

    def test_changed_protected_input_stops_before_stage(self):
        ai = self.root / "ai"
        run = ai / "data/reruns/jay-chou-quarantine-recovery-20261017/run"
        run.mkdir(parents=True)
        source, plan = self.root / "protected", self.root / "plan.json"
        source.write_text("original", encoding="utf-8")
        plan.write_text("{}", encoding="utf-8")
        r.write_json(
            run / "execution-manifest.json",
            {
                "run_root": str(run),
                "plan_path": str(plan),
                "plan_sha256": r.digest(plan),
                "protected_inputs": [{"path": str(source), "sha256": r.digest(source)}],
            },
        )
        source.write_text("changed", encoding="utf-8")
        with patch.object(r, "AI_ROOT", ai):
            with self.assertRaisesRegex(ValueError, "protected_input_changed"):
                r.guard_run(run)

    def test_equivalent_dense_inputs_and_encoder_allow_reuse(self):
        config = {"dimension": 2, "model_revision": "pinned"}
        r.validate_reusable_vector(
            {"chunk_id": "a", "dense_text": "正文"}, "正文", config, config, [0.3, 0.4]
        )

    def test_dense_text_or_encoder_change_rejects_reuse(self):
        row = {"chunk_id": "a", "dense_text": "original"}
        config = {"dimension": 2, "model_revision": "pinned"}
        for text, other in [
            ("changed", config),
            ("original", {**config, "model_revision": "changed"}),
        ]:
            with self.subTest(text=text, other=other):
                with self.assertRaises(ValueError):
                    r.validate_reusable_vector(row, text, config, other, [0.3, 0.4])

    def test_corrupt_vectors_never_fall_back_to_old_document_reembedding(self):
        config = {"dimension": 2}
        for vector in [
            [0.3],
            [0.3, float("nan")],
            [float("inf"), 0.4],
            [True, 0.4],
            ["0.3", 0.4],
        ]:
            with self.subTest(vector=vector):
                with self.assertRaisesRegex(ValueError, "baseline_vector_invalid"):
                    r.validate_reusable_vector(
                        {"chunk_id": "a", "dense_text": "body"},
                        "body",
                        config,
                        config,
                        vector,
                    )

    def test_normalized_encoder_cannot_reuse_a_zero_or_unnormalized_vector(self):
        config = {"dimension": 2, "normalize": True}
        for vector in [[0.0, 0.0], [0.3, 0.4]]:
            with self.subTest(vector=vector):
                with self.assertRaisesRegex(
                    ValueError, "baseline_vector_not_normalized"
                ):
                    r.validate_reusable_vector(
                        {"chunk_id": "a", "dense_text": "body"},
                        "body",
                        config,
                        config,
                        vector,
                    )

    def signature_fixture(self):
        sampling = {
            "claim_sample_count": 75,
            "entity_link_sample_count": 100,
            "claim_sample_sha256": "claims-hash",
            "entity_link_sample_sha256": "entities-hash",
            "release_selection_sha256": "selection-hash",
        }
        attestation = {
            "attestation_version": "v1",
            "review_status": "approved",
            "reviewer": "West",
            "generation_mode": "rule_only",
            "rejected_sample_ids": [],
            "claim_reviewed_count": 75,
            "entity_link_reviewed_count": 100,
            "claim_sample_sha256": "claims-hash",
            "entity_link_sample_sha256": "entities-hash",
            "release_selection_sha256": "selection-hash",
            "document_supplement_sha256": "sha256:supplement-hash",
            "supplement_reviewed_count": 11,
            "signature_source": "Synthetic unit-test fixture only",
        }
        return attestation, sampling

    def test_complete_human_attestation_with_current_hashes_passes(self):
        attestation, sampling = self.signature_fixture()
        r.validate_human_signoff(attestation, sampling, 11, "supplement-hash")

    def test_execution_or_pending_template_is_not_human_signoff(self):
        for change in [
            {"review_status": "pending"},
            {"reviewer": "Codex"},
            {"claim_reviewed_count": 0},
            {"entity_link_reviewed_count": 99},
            {"supplement_reviewed_count": 0},
            {"rejected_sample_ids": ["rejected-entity"]},
        ]:
            with self.subTest(change=change):
                attestation, sampling = self.signature_fixture()
                with self.assertRaisesRegex(
                    ValueError, "human_qa_signature_missing_or_stale"
                ):
                    r.validate_human_signoff(
                        {**attestation, **change}, sampling, 11, "supplement-hash"
                    )

    def test_stale_sample_selection_or_supplement_invalidates_signoff(self):
        for key in [
            "claim_sample_sha256",
            "entity_link_sample_sha256",
            "release_selection_sha256",
            "document_supplement_sha256",
        ]:
            with self.subTest(key=key):
                attestation, sampling = self.signature_fixture()
                attestation[key] = "stale-hash"
                with self.assertRaisesRegex(
                    ValueError, "human_qa_signature_missing_or_stale"
                ):
                    r.validate_human_signoff(
                        attestation, sampling, 11, "supplement-hash"
                    )

    def test_human_signature_requires_actual_source_record(self):
        attestation, sampling = self.signature_fixture()
        attestation.pop("signature_source")
        with self.assertRaisesRegex(ValueError, "human_signature_source_missing"):
            r.validate_human_signoff(attestation, sampling, 11, "supplement-hash")


if __name__ == "__main__":
    unittest.main()
