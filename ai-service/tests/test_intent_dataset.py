import json
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from app.agent.foundation.intent_dataset.cleaning import (
    clean_dataset,
    normalized_text_key,
)
from app.agent.foundation.intent_dataset.config import (
    load_label_mapping,
    load_taxonomy,
)
from app.agent.foundation.intent_dataset.io import load_samples, write_jsonl
from app.agent.foundation.intent_dataset.generation import generate_dataset
from app.agent.foundation.intent_dataset.schemas import (
    DatasetProvenance,
    DatasetSource,
    IntentDatasetSample,
)
from app.agent.foundation.intent_dataset.splitting import split_dataset
from app.agent.foundation.routing import IntentType

AI_SERVICE_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = AI_SERVICE_ROOT / "data" / "intent"


def _sample(
    sample_id: str,
    text: str,
    labels: list[str],
    *,
    hard: bool = False,
    seed_id: str | None = None,
) -> IntentDatasetSample:
    return IntentDatasetSample(
        id=sample_id,
        text=text,
        labels=labels,
        source=DatasetSource.MANUAL,
        is_hard_case=hard,
        provenance=DatasetProvenance(seed_id=seed_id or sample_id),
    )


class IntentDatasetSchemaTests(unittest.TestCase):
    def test_single_intent_schema(self) -> None:
        sample = _sample("single-1", "分析这首歌", ["CURRENT_SONG"])

        self.assertEqual(sample.labels, [IntentType.CURRENT_SONG])
        self.assertFalse(sample.is_multi_intent)
        self.assertEqual(
            sample.model_dump(mode="json")["labels"],
            ["CURRENT_SONG"],
        )

    def test_multi_intent_schema_derives_flag(self) -> None:
        sample = IntentDatasetSample(
            id="multi-1",
            text="分析这首歌然后收藏",
            labels=["current_song", "TOOL_CALLING"],
            source="manual",
            is_multi_intent=False,
        )

        self.assertEqual(
            sample.labels,
            [IntentType.CURRENT_SONG, IntentType.TOOL_CALLING],
        )
        self.assertTrue(sample.is_multi_intent)

    def test_unknown_cannot_be_training_label(self) -> None:
        with self.assertRaises(ValidationError):
            _sample("unknown-1", "无法识别", ["UNKNOWN"])

    def test_duplicate_labels_are_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            _sample(
                "duplicate-label-1",
                "暂停一下",
                ["TOOL_CALLING", "tool_calling"],
            )

    def test_taxonomy_and_label_mapping_track_existing_enum(self) -> None:
        taxonomy = load_taxonomy(DATA_ROOT / "taxonomy.json")
        mapping = load_label_mapping(DATA_ROOT / "label_mapping.json")

        self.assertEqual(
            mapping,
            {
                "CURRENT_SONG": 0,
                "TOOL_CALLING": 1,
                "WORLDLINE": 2,
                "GENERAL_CHAT": 3,
            },
        )
        self.assertEqual(
            {entry["name"] for entry in taxonomy["intents"]},
            {intent.name for intent in IntentType},
        )
        self.assertNotIn("UNKNOWN", mapping)


class IntentDatasetCleaningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.input_path = self.root / "raw.jsonl"
        self.cleaned_path = self.root / "cleaned.jsonl"
        self.conflicts_path = self.root / "conflicts.jsonl"
        self.rejected_path = self.root / "rejected.jsonl"

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _write_raw(self, records: list[dict[str, object]]) -> None:
        self.input_path.write_text(
            "".join(
                f"{json.dumps(record, ensure_ascii=False)}\n" for record in records
            ),
            encoding="utf-8",
        )

    def _clean(self):
        return clean_dataset(
            self.input_path,
            self.cleaned_path,
            self.conflicts_path,
            self.rejected_path,
        )

    def test_empty_text_is_rejected(self) -> None:
        self._write_raw(
            [
                {
                    "id": "empty-1",
                    "text": "　 \t ",
                    "labels": ["GENERAL_CHAT"],
                    "source": "manual",
                    "is_hard_case": False,
                }
            ]
        )

        summary = self._clean()

        self.assertEqual(summary.rejected_samples, 1)
        self.assertEqual(load_samples(self.cleaned_path), [])
        self.assertEqual(len(self.rejected_path.read_text(encoding="utf-8").splitlines()), 1)

    def test_same_text_and_labels_are_deduplicated(self) -> None:
        first = _sample("duplicate-1", "暂停一下", ["TOOL_CALLING"])
        second = _sample("duplicate-2", "暂停一下", ["TOOL_CALLING"])
        self._write_raw(
            [
                first.model_dump(mode="json", exclude_none=True),
                second.model_dump(mode="json", exclude_none=True),
            ]
        )

        summary = self._clean()

        self.assertEqual(summary.cleaned_samples, 1)
        self.assertEqual(summary.exact_duplicates, 1)
        self.assertEqual(summary.conflict_groups, 0)

    def test_normalized_text_with_different_labels_becomes_conflict(self) -> None:
        self._write_raw(
            [
                _sample("conflict-1", "暂停一下！", ["TOOL_CALLING"]).model_dump(
                    mode="json", exclude_none=True
                ),
                _sample("conflict-2", "暂停一下!", ["GENERAL_CHAT"]).model_dump(
                    mode="json", exclude_none=True
                ),
            ]
        )

        summary = self._clean()

        self.assertEqual(summary.conflict_groups, 1)
        self.assertEqual(load_samples(self.cleaned_path), [])
        conflict = json.loads(
            self.conflicts_path.read_text(encoding="utf-8").splitlines()[0]
        )
        self.assertEqual(
            {tuple(labels) for labels in conflict["label_sets"]},
            {("TOOL_CALLING",), ("GENERAL_CHAT",)},
        )

    def test_multi_intent_sample_is_not_split(self) -> None:
        sample = _sample(
            "multi-clean-1",
            "分析一下这首歌然后帮我收藏",
            ["CURRENT_SONG", "TOOL_CALLING"],
        )
        self._write_raw([sample.model_dump(mode="json", exclude_none=True)])

        self._clean()
        cleaned = load_samples(self.cleaned_path)

        self.assertEqual(len(cleaned), 1)
        self.assertEqual(len(cleaned[0].labels), 2)
        self.assertTrue(cleaned[0].is_multi_intent)

    def test_cleaning_preserves_negation_semantics(self) -> None:
        sample = _sample("negation-1", "　别暂停　", ["TOOL_CALLING"], hard=True)
        self._write_raw([sample.model_dump(mode="json", exclude_none=True)])

        self._clean()
        cleaned = load_samples(self.cleaned_path)

        self.assertEqual(cleaned[0].text, "别暂停")
        self.assertIn("别", cleaned[0].text)


class IntentDatasetGenerationTests(unittest.TestCase):
    def test_generation_keeps_multi_labels_and_records_seed_lineage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            seeds_path = root / "seeds.jsonl"
            output_path = root / "generated.jsonl"
            seed = _sample(
                "manual-seed-1",
                "分析这首歌然后收藏",
                ["CURRENT_SONG", "TOOL_CALLING"],
            )
            write_jsonl(seeds_path, [seed])

            summary = generate_dataset(
                seeds_path,
                output_path,
                DATA_ROOT / "taxonomy.json",
                random_seed=7,
                variants_per_seed=1,
            )
            generated = load_samples(output_path)

        self.assertEqual(summary, {"manual": 1, "synthetic": 1, "rule_bootstrap": 0, "total": 2})
        self.assertEqual(len(generated), 2)
        self.assertEqual(len(generated[1].labels), 2)
        self.assertTrue(generated[1].is_multi_intent)
        self.assertEqual(generated[1].source, DatasetSource.SYNTHETIC)
        self.assertEqual(generated[1].provenance.seed_id, "manual-seed-1")


class IntentDatasetSplitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.input_path = self.root / "cleaned.jsonl"
        samples: list[IntentDatasetSample] = []
        for label in (
            "CURRENT_SONG",
            "TOOL_CALLING",
            "WORLDLINE",
            "GENERAL_CHAT",
        ):
            for index in range(8):
                sample_id = f"{label.lower()}-{index}"
                samples.append(_sample(sample_id, f"{label} 样本 {index}", [label]))
        for index in range(6):
            sample_id = f"multi-{index}"
            samples.append(
                _sample(
                    sample_id,
                    f"分析歌曲并执行操作 {index}",
                    ["CURRENT_SONG", "TOOL_CALLING"],
                )
            )
        for index in range(6):
            sample_id = f"hard-{index}"
            samples.append(
                _sample(
                    sample_id,
                    f"如果音量大就调小 {index}",
                    ["TOOL_CALLING"],
                    hard=True,
                )
            )
        samples.extend(
            [
                _sample(
                    "seed-pair-a",
                    "请分析这个种子样本",
                    ["CURRENT_SONG"],
                    seed_id="shared-seed",
                ),
                IntentDatasetSample(
                    id="seed-pair-b",
                    text="麻烦分析这个种子样本",
                    labels=["CURRENT_SONG"],
                    source="synthetic",
                    provenance={"seed_id": "shared-seed"},
                ),
            ]
        )
        write_jsonl(self.input_path, samples)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_split_is_reproducible_and_has_no_text_leakage(self) -> None:
        first_dir = self.root / "first"
        second_dir = self.root / "second"

        first = split_dataset(self.input_path, first_dir, random_seed=2026)
        second = split_dataset(self.input_path, second_dir, random_seed=2026)

        self.assertEqual(first["counts"], second["counts"])
        for split_name in ("train", "val", "test"):
            self.assertEqual(
                (first_dir / f"{split_name}.jsonl").read_text(encoding="utf-8"),
                (second_dir / f"{split_name}.jsonl").read_text(encoding="utf-8"),
            )

        texts_by_split = {
            split_name: {
                normalized_text_key(sample.text)
                for sample in load_samples(first_dir / f"{split_name}.jsonl")
            }
            for split_name in ("train", "val", "test")
        }
        self.assertTrue(texts_by_split["train"].isdisjoint(texts_by_split["val"]))
        self.assertTrue(texts_by_split["train"].isdisjoint(texts_by_split["test"]))
        self.assertTrue(texts_by_split["val"].isdisjoint(texts_by_split["test"]))

        shared_seed_locations = []
        for split_name in ("train", "val", "test"):
            samples = load_samples(first_dir / f"{split_name}.jsonl")
            if any(
                sample.provenance is not None
                and sample.provenance.seed_id == "shared-seed"
                for sample in samples
            ):
                shared_seed_locations.append(split_name)
        self.assertEqual(len(shared_seed_locations), 1)
        test_samples = load_samples(first_dir / "test.jsonl")
        self.assertTrue(
            all(sample.source is DatasetSource.MANUAL for sample in test_samples)
        )

    def test_each_primary_intent_and_boundary_type_has_split_coverage(self) -> None:
        output_dir = self.root / "distribution"
        split_dataset(self.input_path, output_dir, random_seed=42)

        for split_name in ("train", "val", "test"):
            samples = load_samples(output_dir / f"{split_name}.jsonl")
            labels = {label for sample in samples for label in sample.labels}
            self.assertEqual(
                labels,
                {
                    IntentType.CURRENT_SONG,
                    IntentType.TOOL_CALLING,
                    IntentType.WORLDLINE,
                    IntentType.GENERAL_CHAT,
                },
            )
            self.assertTrue(any(sample.is_multi_intent for sample in samples))
            self.assertTrue(any(sample.is_hard_case for sample in samples))

    def test_manual_test_file_is_not_overwritten(self) -> None:
        output_dir = self.root / "manual-test-output"
        output_dir.mkdir()
        manual_test_path = output_dir / "manual_test.jsonl"
        manual_sample = _sample(
            "reserved-manual-test",
            "这是人工维护的测试问题",
            ["GENERAL_CHAT"],
        )
        write_jsonl(manual_test_path, [manual_sample])
        original_content = manual_test_path.read_text(encoding="utf-8")

        split_dataset(
            self.input_path,
            output_dir,
            random_seed=42,
            manual_test_path=manual_test_path,
        )

        self.assertEqual(
            manual_test_path.read_text(encoding="utf-8"),
            original_content,
        )
        test_samples = load_samples(output_dir / "test.jsonl")
        self.assertIn("reserved-manual-test", {sample.id for sample in test_samples})

    def test_test_split_keeps_manual_seed_and_excludes_derived_variant(self) -> None:
        paired_input = self.root / "paired.jsonl"
        paired_samples: list[IntentDatasetSample] = []
        labels = (
            "CURRENT_SONG",
            "TOOL_CALLING",
            "WORLDLINE",
            "GENERAL_CHAT",
        )
        for index in range(20):
            seed_id = f"paired-seed-{index}"
            label = labels[index % len(labels)]
            paired_samples.extend(
                [
                    _sample(seed_id, f"人工样本 {index}", [label]),
                    IntentDatasetSample(
                        id=f"syn-{seed_id}",
                        text=f"麻烦你，人工样本 {index}",
                        labels=[label],
                        source="synthetic",
                        provenance={
                            "seed_id": seed_id,
                            "generation_method": "deterministic_surface_expansion_v1",
                        },
                    ),
                ]
            )
        write_jsonl(paired_input, paired_samples)
        output_dir = self.root / "paired-output"

        summary = split_dataset(
            paired_input,
            output_dir,
            train_ratio=0.6,
            val_ratio=0.2,
            test_ratio=0.2,
            random_seed=42,
        )

        test_samples = load_samples(output_dir / "test.jsonl")
        self.assertGreater(len(test_samples), 0)
        self.assertTrue(
            all(sample.source is DatasetSource.MANUAL for sample in test_samples)
        )
        self.assertGreater(summary["excluded_derived_samples"], 0)


if __name__ == "__main__":
    unittest.main()
