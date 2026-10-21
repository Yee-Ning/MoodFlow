"""Real-corpus freeze checks and rejection tests; never edit frozen inputs."""

from copy import deepcopy
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest

import freeze_gold_v5_2 as freeze


TIMESTAMP = "2026-10-19T12:00:00+08:00"


def encode(rows):
    return b"".join((json.dumps(r, ensure_ascii=False) + "\n").encode() for r in rows)


class FreezeV52Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan, cls.base, cls.corpus, cls.protected = freeze.verify_inputs()
        cls.result = freeze.build_payload(cls.base, cls.plan, cls.corpus, TIMESTAMP)
        cls.lines, cls.rows = freeze.records(cls.result)
        cls.by_id = {row["case_id"]: row for row in cls.rows}

    def test_real_source_hashes_are_the_approved_frozen_inputs(self):
        self.assertEqual(freeze.sha(self.base), freeze.V51_SHA)
        self.assertEqual(freeze.file_sha(freeze.V5), freeze.V5_SHA)
        self.assertTrue(freeze.readonly(freeze.BASE))
        self.assertTrue(freeze.readonly(freeze.V5))

    def test_exact_ten_updates_and_forty_original_raw_lines(self):
        before_lines, before = freeze.records(self.base)
        changed = {
            a["case_id"] for a, x, y in zip(before, before_lines, self.lines) if x != y
        }
        self.assertEqual(changed, set(freeze.LABELS))
        self.assertEqual(len(self.rows), 50)
        self.assertEqual(len({r["case_id"] for r in self.rows}), 50)
        self.assertEqual(sum(x == y for x, y in zip(before_lines, self.lines)), 40)
        self.assertEqual(
            freeze.Counter(r["answerability"] for r in self.rows), freeze.DISTRIBUTION
        )
        for old, new in zip(before, self.rows):
            if old["case_id"] not in freeze.LABELS:
                self.assertEqual(old, new)
            else:
                self.assertEqual(new["schema_version"], "v5")
                self.assertEqual(new["release_id"], freeze.RELEASE_ID)
                self.assertEqual(new["frozen_at"], TIMESTAMP)

    def test_every_parent_child_and_quote_is_grounded_in_the_actual_release(self):
        for row in self.rows:
            if row["case_id"] not in freeze.LABELS:
                continue
            self.assertEqual(
                set(row["relevant_parent_ids"]),
                {a["parent_id"] for a in row["anchors"]},
            )
            for anchor in row["anchors"]:
                parent = self.corpus[anchor["parent_id"]]
                self.assertIsNone(parent["parent_id"])
                for child in anchor["child_ids"]:
                    self.assertEqual(
                        self.corpus[child]["parent_id"], parent["chunk_id"]
                    )
                for quote in anchor["quotes"]:
                    source = self.corpus[quote["chunk_id"]]
                    self.assertEqual(
                        source["content"][quote["start"] : quote["end"]],
                        quote["exact_text"],
                    )
                    self.assertEqual(
                        source["metadata"]["source_id"], anchor["source_id"]
                    )

    def test_raw_whitespace_change_in_an_unchanged_row_is_rejected(self):
        tampered = self.lines.copy()
        index = next(
            i for i, r in enumerate(self.rows) if r["case_id"] not in freeze.LABELS
        )
        tampered[index] = tampered[index].rstrip(b"\n") + b" \n"
        with self.assertRaisesRegex(ValueError, "unapproved_raw_line_changed"):
            freeze.validate_payload(
                self.base, b"".join(tampered), self.plan, self.corpus, TIMESTAMP
            )

    def test_changed_question_and_schema_are_rejected(self):
        for field, value in (
            ("question", "changed"),
            ("schema_version", "v5_2"),
            ("contexts", ["invented"]),
        ):
            rows = deepcopy(self.rows)
            next(r for r in rows if r["case_id"] == "rag-v0-002")[field] = value
            with (
                self.subTest(field=field),
                self.assertRaisesRegex(ValueError, "unapproved_field_changed"),
            ):
                freeze.validate_payload(
                    self.base, encode(rows), self.plan, self.corpus, TIMESTAMP
                )

    def test_duplicate_case_and_order_change_are_rejected(self):
        rows = deepcopy(self.rows)
        rows[1]["case_id"] = rows[0]["case_id"]
        with self.assertRaisesRegex(ValueError, "duplicate_case_id"):
            freeze.records(encode(rows))
        rows = self.rows.copy()
        rows[0], rows[1] = rows[1], rows[0]
        with self.assertRaisesRegex(ValueError, "case_order_changed"):
            freeze.validate_payload(
                self.base, encode(rows), self.plan, self.corpus, TIMESTAMP
            )

    def test_wrong_parent_or_quote_offset_is_rejected(self):
        for field in ("relevant_parent_ids", "anchors"):
            rows = deepcopy(self.rows)
            row = next(r for r in rows if r["case_id"] == "rag-v0-002")
            if field == "anchors":
                row["anchors"][0]["quotes"][0]["start"] += 1
            else:
                row[field] = ["not-in-release"]
            with (
                self.subTest(field=field),
                self.assertRaisesRegex(
                    ValueError, "row_differs_from_plan_or_exact_evidence"
                ),
            ):
                freeze.validate_payload(
                    self.base, encode(rows), self.plan, self.corpus, TIMESTAMP
                )

    def test_plan_tampering_and_unapproved_parent_fail_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "plan.json"
            path.write_bytes(freeze.PLAN.read_bytes() + b" ")
            with self.assertRaisesRegex(ValueError, "approved_plan_hash_mismatch"):
                freeze.load_plan(path)
        case = deepcopy(self.plan["cases"]["rag-v0-006"])
        case["anchors"][0]["parent_id"] = "not-in-release"
        with self.assertRaisesRegex(ValueError, "unapproved_parent"):
            freeze.build_anchors(case, self.corpus)

    def test_006_does_not_substitute_album_cover_location_for_song_mv(self):
        row = deepcopy(self.by_id["rag-v0-006"])
        self.assertIn("日本乡间", row["ground_truth"])
        for wrong in ("海参崴", "海參崴", "海参威", "海參威"):
            changed = deepcopy(row)
            changed["ground_truth"] = row["ground_truth"].replace("日本乡间", wrong)
            with (
                self.subTest(wrong=wrong),
                self.assertRaisesRegex(ValueError, "006_mv_location_landmine"),
            ):
                freeze.validate_landmines(changed)

    def test_023_two_exceptions_are_not_a_lifetime_statistic(self):
        row = deepcopy(self.by_id["rag-v0-023"])
        for qualifier in ("2005年访谈", "当时"):
            changed = deepcopy(row)
            changed["ground_truth"] = changed["ground_truth"].replace(qualifier, "")
            with (
                self.subTest(qualifier=qualifier),
                self.assertRaisesRegex(ValueError, "023_two_exceptions_temporal_scope"),
            ):
                freeze.validate_landmines(changed)

    def test_064_retains_exact_two_selected_songs_and_all_methods(self):
        row = deepcopy(self.by_id["rag-v0-064"])
        for old, new in (
            ("《菊花台》", "《青花瓷》"),
            ("《千里之外》", "《霍元甲》"),
            ("虚拟田野调查", ""),
        ):
            changed = deepcopy(row)
            changed["ground_truth"] = changed["ground_truth"].replace(old, new)
            with (
                self.subTest(new=new),
                self.assertRaisesRegex(
                    ValueError, "064_selected_songs_or_methods_landmine"
                ),
            ):
                freeze.validate_landmines(changed)

    def test_065_and_066_keep_original_gap_facts(self):
        _, original = freeze.records(self.base)
        for key, gap in freeze.GAPS.items():
            self.assertIn(
                gap, next(r for r in original if r["case_id"] == key)["required_facts"]
            )
            row = deepcopy(self.by_id[key])
            self.assertIn(gap, row["required_facts"])
            self.assertTrue(
                any(
                    f["text"] == gap
                    and f["kind"] == "scope_limit"
                    and not f["anchor_ids"]
                    for f in self.plan["cases"][key]["facts"]
                )
            )
            row["required_facts"].remove(gap)
            with (
                self.subTest(key=key),
                self.assertRaisesRegex(ValueError, "065_066_required_gap_removed"),
            ):
                freeze.validate_landmines(row)

    def test_timestamp_requires_the_execution_timezone(self):
        with self.assertRaisesRegex(ValueError, "Asia_Shanghai"):
            freeze.build_payload(
                self.base, self.plan, self.corpus, "2026-10-19T04:00:00+00:00"
            )

    def test_report_bytes_are_identical_across_python_hash_seeds(self):
        code = (
            "from tools.gold_release_freeze_v5_2 import freeze_gold_v5_2 as f;"
            "p,b,c,h=f.verify_inputs();"
            f"t={TIMESTAMP!r};"
            "r=f.build_payload(b,p,c,t);"
            "m,d,s=f.build_reports(b,r,p,t,h);"
            "print(f.sha(f.json_bytes(m)+f.json_bytes(d)+s))"
        )
        digests = []
        for seed in ("1", "2"):
            env = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONDONTWRITEBYTECODE": "1"}
            result = subprocess.run(
                [sys.executable, "-B", "-X", "utf8", "-c", code],
                cwd=freeze.AI_ROOT,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
            digests.append(result.stdout.strip())
        self.assertEqual(digests[0], digests[1])

    def test_publish_sets_readonly_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "frozen.jsonl"
            try:
                freeze.publish_new(path, self.result)
                self.assertEqual(path.read_bytes(), self.result)
                self.assertTrue(freeze.readonly(path))
                with self.assertRaises((FileExistsError, PermissionError)):
                    freeze.publish_new(path, b"overwrite")
                self.assertEqual(path.read_bytes(), self.result)
            finally:
                if path.exists():
                    path.chmod(stat.S_IWRITE | stat.S_IREAD)


if __name__ == "__main__":
    unittest.main()
