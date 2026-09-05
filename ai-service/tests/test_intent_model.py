import tempfile
import unittest
from pathlib import Path

import numpy as np
import torch
from torch import nn
from transformers import BertConfig, BertModel, BertTokenizer

from app.agent.foundation.intent_dataset.config import load_label_mapping
from app.agent.foundation.intent_dataset.io import write_jsonl
from app.agent.foundation.intent_dataset.schemas import DatasetSource, IntentDatasetSample
from app.agent.foundation.intent_model.artifacts import load_model_bundle, save_model_bundle
from app.agent.foundation.intent_model.config import TrainingConfig
from app.agent.foundation.intent_model.dataset import (
    IntentClassificationDataset,
    encode_multi_hot,
)
from app.agent.foundation.intent_model.inference import predict_text
from app.agent.foundation.intent_model.metrics import (
    accepted_labels,
    apply_thresholds,
    compute_multilabel_metrics,
)
from app.agent.foundation.intent_model.model import BgeMultiLabelClassifier
from app.agent.foundation.intent_model.thresholds import tune_thresholds
from app.agent.foundation.routing import IntentType

AI_SERVICE_ROOT = Path(__file__).resolve().parents[1]
LABEL_MAPPING_PATH = AI_SERVICE_ROOT / "data" / "intent" / "label_mapping.json"


class IntentModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temporary_directory.name)
        self.label_mapping = load_label_mapping(LABEL_MAPPING_PATH)
        vocab_path = self.temp_path / "vocab.txt"
        vocab_path.write_text(
            "\n".join(
                [
                    "[PAD]",
                    "[UNK]",
                    "[CLS]",
                    "[SEP]",
                    "[MASK]",
                    "分",
                    "析",
                    "这",
                    "首",
                    "歌",
                    "收",
                    "藏",
                ]
            ),
            encoding="utf-8",
        )
        self.tokenizer = BertTokenizer(vocab_file=str(vocab_path))

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _model(self) -> BgeMultiLabelClassifier:
        encoder = BertModel(
            BertConfig(
                vocab_size=self.tokenizer.vocab_size,
                hidden_size=16,
                num_hidden_layers=1,
                num_attention_heads=2,
                intermediate_size=32,
                max_position_embeddings=32,
            )
        )
        return BgeMultiLabelClassifier(
            encoder,
            num_labels=len(self.label_mapping),
            dropout=0.0,
        )

    def test_label_mapping_has_stable_expected_order(self) -> None:
        self.assertEqual(
            self.label_mapping,
            {
                "CURRENT_SONG": 0,
                "TOOL_CALLING": 1,
                "WORLDLINE": 2,
                "GENERAL_CHAT": 3,
            },
        )

    def test_single_label_multi_hot_encoding(self) -> None:
        vector = encode_multi_hot([IntentType.CURRENT_SONG], self.label_mapping)

        torch.testing.assert_close(vector, torch.tensor([1.0, 0.0, 0.0, 0.0]))

    def test_multi_label_multi_hot_encoding(self) -> None:
        vector = encode_multi_hot(
            [IntentType.CURRENT_SONG, IntentType.TOOL_CALLING], self.label_mapping
        )

        torch.testing.assert_close(vector, torch.tensor([1.0, 1.0, 0.0, 0.0]))

    def test_unknown_cannot_be_encoded(self) -> None:
        with self.assertRaisesRegex(ValueError, "UNKNOWN"):
            encode_multi_hot([IntentType.UNKNOWN], self.label_mapping)

    def test_dataset_returns_fixed_token_and_label_shapes(self) -> None:
        dataset_path = self.temp_path / "samples.jsonl"
        write_jsonl(
            dataset_path,
            [
                IntentDatasetSample(
                    id="multi-1",
                    text="分析这首歌然后收藏",
                    labels=["CURRENT_SONG", "TOOL_CALLING"],
                    source=DatasetSource.MANUAL,
                )
            ],
        )
        dataset = IntentClassificationDataset(
            dataset_path,
            self.tokenizer,
            self.label_mapping,
            max_length=16,
        )

        item = dataset[0]
        self.assertEqual(item["input_ids"].shape, torch.Size([16]))
        self.assertEqual(item["attention_mask"].shape, torch.Size([16]))
        self.assertEqual(item["labels"].shape, torch.Size([4]))
        self.assertEqual(int(item["labels"].sum()), 2)
        self.assertTrue(item["is_multi_intent"])

    def test_model_forward_returns_one_logit_per_label(self) -> None:
        model = self._model()
        encoded = self.tokenizer(
            ["分析这首歌", "收藏这首歌"],
            padding="max_length",
            truncation=True,
            max_length=16,
            return_tensors="pt",
        )

        logits = model(**encoded)

        self.assertEqual(logits.shape, torch.Size([2, 4]))

    def test_bce_with_logits_loss_and_sigmoid_are_multi_label_compatible(self) -> None:
        logits = torch.tensor([[2.0, -1.0, 0.5, -3.0]], requires_grad=True)
        labels = torch.tensor([[1.0, 0.0, 1.0, 0.0]])

        loss = nn.BCEWithLogitsLoss()(logits, labels)
        loss.backward()
        probabilities = torch.sigmoid(logits)

        self.assertTrue(torch.isfinite(loss))
        self.assertTrue(torch.all((probabilities >= 0) & (probabilities <= 1)))
        self.assertIsNotNone(logits.grad)

    def test_thresholding_allows_multiple_or_zero_labels(self) -> None:
        thresholds = {label: 0.6 for label in self.label_mapping}

        self.assertEqual(
            accepted_labels([0.9, 0.7, 0.2, 0.1], self.label_mapping, thresholds),
            ["CURRENT_SONG", "TOOL_CALLING"],
        )
        self.assertEqual(
            accepted_labels([0.5, 0.4, 0.2, 0.1], self.label_mapping, thresholds),
            [],
        )

    def test_metrics_report_micro_macro_and_per_label_values(self) -> None:
        y_true = np.asarray([[1, 1, 0, 0], [0, 0, 0, 1]])
        probabilities = np.asarray([[0.9, 0.8, 0.1, 0.2], [0.1, 0.2, 0.1, 0.9]])

        metrics = compute_multilabel_metrics(
            y_true, probabilities, self.label_mapping, 0.5
        )

        self.assertEqual(metrics["micro_f1"], 1.0)
        self.assertEqual(metrics["macro_f1"], 0.75)
        self.assertEqual(set(metrics["per_label"]), set(self.label_mapping))

    def test_threshold_tuning_is_driven_by_given_validation_arrays(self) -> None:
        y_true = np.asarray([[1, 0, 0, 1], [0, 1, 1, 0], [1, 0, 0, 0]])
        probabilities = np.asarray(
            [[0.45, 0.2, 0.1, 0.7], [0.1, 0.55, 0.6, 0.2], [0.4, 0.1, 0.2, 0.1]]
        )

        result = tune_thresholds(
            y_true,
            probabilities,
            self.label_mapping,
            candidates=(0.3, 0.5, 0.7),
        )

        self.assertEqual(set(result["per_label_thresholds"]), set(self.label_mapping))
        self.assertEqual(result["per_label_thresholds"]["CURRENT_SONG"], 0.3)

    def test_artifact_round_trip_and_prediction_output(self) -> None:
        model = self._model()
        with torch.no_grad():
            model.classifier.weight.zero_()
            model.classifier.bias.zero_()
        artifact_dir = self.temp_path / "artifact"
        thresholds = {label: 0.8 for label in self.label_mapping}
        config = TrainingConfig(
            model_name="local-tiny-bert", max_length=16, batch_size=2, epochs=1
        )
        save_model_bundle(
            artifact_dir,
            model=model,
            tokenizer=self.tokenizer,
            label_mapping=self.label_mapping,
            thresholds=thresholds,
            training_config=config,
            metrics={"test": True},
        )

        bundle = load_model_bundle(artifact_dir, device="cpu")
        encoded = bundle.tokenizer(
            "分析这首歌",
            padding="max_length",
            truncation=True,
            max_length=16,
            return_tensors="pt",
        )
        self.assertEqual(model(**encoded).shape, bundle.model(**encoded).shape)
        prediction = predict_text(
            bundle.model,
            bundle.tokenizer,
            "分析这首歌",
            bundle.label_mapping,
            bundle.thresholds,
            max_length=bundle.training_config.max_length,
            device=bundle.device,
        )

        self.assertEqual(bundle.label_mapping, self.label_mapping)
        self.assertEqual(set(prediction["scores"]), set(self.label_mapping))
        self.assertEqual(prediction["accepted_labels"], [])
        self.assertTrue(prediction["needs_llm"])
        self.assertTrue((artifact_dir / "classifier_head.pt").is_file())
        self.assertTrue((artifact_dir / "encoder" / "config.json").is_file())

    def test_apply_thresholds_rejects_wrong_output_shape(self) -> None:
        with self.assertRaisesRegex(ValueError, "shape"):
            apply_thresholds(np.asarray([0.5, 0.5]), self.label_mapping, 0.5)


if __name__ == "__main__":
    unittest.main()
