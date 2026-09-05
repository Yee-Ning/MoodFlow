import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import torch
from torch import nn
from transformers import BertConfig, BertModel, BertTokenizer

from app.agent.foundation.intent_model.artifacts import (
    load_model_bundle,
    save_model_bundle,
)
from app.agent.foundation.intent_model.config import TrainingConfig
from app.agent.foundation.intent_model.model import BgeMultiLabelClassifier
from app.agent.foundation.model_routing import (
    IntentClassifierRuntime,
    IntentModelPrediction,
    ModelRouter,
    ModelRuntimeLoadError,
)
from app.agent.foundation.rule_routing import RuleRouter
from app.agent.foundation.routing import IntentSource, IntentType


LABEL_MAPPING = {
    "CURRENT_SONG": 0,
    "TOOL_CALLING": 1,
    "WORLDLINE": 2,
    "GENERAL_CHAT": 3,
}


class StubRuntime:
    def __init__(
        self,
        *,
        scores: dict[str, float] | None = None,
        accepted_labels: tuple[str, ...] = (),
    ) -> None:
        self._scores = scores or {label: 0.1 for label in LABEL_MAPPING}
        self._accepted_labels = accepted_labels
        self.calls: list[str] = []

    @property
    def label_mapping(self) -> dict[str, int]:
        return dict(LABEL_MAPPING)

    @property
    def thresholds(self) -> dict[str, float]:
        return {label: 0.5 for label in LABEL_MAPPING}

    def predict(self, text: str) -> IntentModelPrediction:
        self.calls.append(text)
        return IntentModelPrediction(
            scores=dict(self._scores),
            accepted_labels=self._accepted_labels,
            needs_llm=not self._accepted_labels,
        )


class ModelRouterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary_directory = tempfile.TemporaryDirectory()
        cls.temp_path = Path(cls.temporary_directory.name)
        vocab_path = cls.temp_path / "vocab.txt"
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
                ]
            ),
            encoding="utf-8",
        )
        tokenizer = BertTokenizer(vocab_file=str(vocab_path))
        encoder = BertModel(
            BertConfig(
                vocab_size=tokenizer.vocab_size,
                hidden_size=16,
                num_hidden_layers=1,
                num_attention_heads=2,
                intermediate_size=32,
                max_position_embeddings=32,
            )
        )
        model = BgeMultiLabelClassifier(
            encoder,
            num_labels=len(LABEL_MAPPING),
            dropout=0.0,
        )
        with torch.no_grad():
            model.classifier.weight.zero_()
            model.classifier.bias.copy_(torch.tensor([2.0, -2.0, -2.0, -2.0]))
        cls.artifact_dir = cls.temp_path / "intent_model"
        save_model_bundle(
            cls.artifact_dir,
            model=model,
            tokenizer=tokenizer,
            label_mapping=LABEL_MAPPING,
            thresholds={label: 0.5 for label in LABEL_MAPPING},
            training_config=TrainingConfig(
                model_name="local-tiny-bert",
                max_length=16,
                batch_size=2,
                epochs=1,
            ),
            metrics={"fixture": True},
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary_directory.cleanup()

    def test_model_router_successfully_initializes_from_artifact(self) -> None:
        router = ModelRouter(self.artifact_dir, device="cpu")

        result = router.route("分析这首歌")

        self.assertEqual(result.accepted_labels, ["CURRENT_SONG"])
        self.assertEqual(result.intents[0].source, IntentSource.MODEL)

    def test_runtime_loads_mapping_and_thresholds(self) -> None:
        runtime = IntentClassifierRuntime(self.artifact_dir, device="cpu")

        self.assertEqual(runtime.label_mapping, LABEL_MAPPING)
        self.assertEqual(runtime.thresholds, {label: 0.5 for label in LABEL_MAPPING})

    def test_single_intent_prediction_uses_shared_intent_item(self) -> None:
        scores = {**{label: 0.1 for label in LABEL_MAPPING}, "CURRENT_SONG": 0.84}
        router = ModelRouter(
            runtime=StubRuntime(scores=scores, accepted_labels=("CURRENT_SONG",))
        )

        result = router.route("分析一下这首歌")

        self.assertEqual(len(result.intents), 1)
        self.assertIsInstance(result.intents[0].intent, IntentType)
        self.assertEqual(result.intents[0].intent, IntentType.CURRENT_SONG)
        self.assertEqual(result.intents[0].source, IntentSource.MODEL)
        self.assertEqual(result.intents[0].evidence, "分析一下这首歌")

    def test_multi_intent_prediction_is_sorted_by_score(self) -> None:
        scores = {
            "CURRENT_SONG": 0.84,
            "TOOL_CALLING": 0.91,
            "WORLDLINE": 0.04,
            "GENERAL_CHAT": 0.03,
        }
        router = ModelRouter(
            runtime=StubRuntime(
                scores=scores,
                accepted_labels=("CURRENT_SONG", "TOOL_CALLING"),
            )
        )

        result = router.route("分析一下这首歌然后帮我收藏")

        self.assertEqual(
            [item.intent for item in result.intents],
            [IntentType.TOOL_CALLING, IntentType.CURRENT_SONG],
        )
        self.assertEqual([item.order for item in result.intents], [0, 1])
        self.assertFalse(result.needs_llm)

    def test_no_accepted_label_retains_text_for_future_llm(self) -> None:
        router = ModelRouter(runtime=StubRuntime())

        result = router.route("当前电影可以吗")

        self.assertEqual(result.intents, [])
        self.assertEqual(result.accepted_labels, [])
        self.assertEqual(result.unresolved_text, "当前电影可以吗")
        self.assertTrue(result.needs_llm)

    def test_scores_contain_every_training_label(self) -> None:
        result = ModelRouter(runtime=StubRuntime()).route("低置信输入")

        self.assertEqual(set(result.scores), set(LABEL_MAPPING))

    def test_intent_confidence_is_probability(self) -> None:
        scores = {**{label: 0.1 for label in LABEL_MAPPING}, "WORLDLINE": 0.73}
        result = ModelRouter(
            runtime=StubRuntime(scores=scores, accepted_labels=("WORLDLINE",))
        ).route("如果没有这张专辑会怎样")

        self.assertEqual(result.intents[0].confidence, 0.73)
        self.assertGreaterEqual(result.intents[0].confidence, 0.0)
        self.assertLessEqual(result.intents[0].confidence, 1.0)

    def test_repeated_routes_do_not_reload_model(self) -> None:
        real_loader = load_model_bundle
        with patch(
            "app.agent.foundation.model_routing.runtime.load_model_bundle",
            wraps=real_loader,
        ) as loader:
            router = ModelRouter(self.artifact_dir, device="cpu")
            router.route("第一次调用")
            router.route("第二次调用")

        self.assertEqual(loader.call_count, 1)

    def test_classifier_output_mapping_mismatch_fails_fast(self) -> None:
        bundle = load_model_bundle(self.artifact_dir, device="cpu")
        bundle.model.classifier = nn.Linear(16, 3)
        mismatched_bundle = replace(bundle, model=bundle.model)
        with patch(
            "app.agent.foundation.model_routing.runtime.load_model_bundle",
            return_value=mismatched_bundle,
        ):
            with self.assertRaisesRegex(ValueError, "output dimension"):
                IntentClassifierRuntime(self.artifact_dir, device="cpu")

    def test_rule_router_unresolved_text_is_direct_model_input(self) -> None:
        rule_result = RuleRouter().route("暂停一下，然后讲讲这首歌")
        runtime = StubRuntime(
            scores={
                "CURRENT_SONG": 0.8,
                "TOOL_CALLING": 0.1,
                "WORLDLINE": 0.1,
                "GENERAL_CHAT": 0.1,
            },
            accepted_labels=("CURRENT_SONG",),
        )

        model_result = ModelRouter(runtime=runtime).route(rule_result.unresolved_text)

        self.assertTrue(rule_result.unresolved_text)
        self.assertEqual(runtime.calls, [rule_result.unresolved_text])
        self.assertEqual(model_result.intents[0].intent, IntentType.CURRENT_SONG)

    def test_blank_text_fails_before_runtime_prediction(self) -> None:
        runtime = StubRuntime()

        with self.assertRaisesRegex(ValueError, "blank"):
            ModelRouter(runtime=runtime).route("   ")

        self.assertEqual(runtime.calls, [])

    def test_missing_model_directory_fails_clearly(self) -> None:
        with self.assertRaisesRegex(FileNotFoundError, "does not exist"):
            IntentClassifierRuntime(self.temp_path / "missing", device="cpu")

    def test_missing_label_mapping_fails_clearly(self) -> None:
        incomplete_dir = self._artifact_missing("label_mapping.json")

        with self.assertRaisesRegex(FileNotFoundError, "label_mapping"):
            IntentClassifierRuntime(incomplete_dir, device="cpu")

    def test_missing_thresholds_fail_clearly(self) -> None:
        incomplete_dir = self._artifact_missing("thresholds.json")

        with self.assertRaisesRegex(FileNotFoundError, "thresholds"):
            IntentClassifierRuntime(incomplete_dir, device="cpu")

    def test_checkpoint_or_tokenizer_load_failure_preserves_cause(self) -> None:
        load_failure = RuntimeError("invalid checkpoint fixture")
        with patch(
            "app.agent.foundation.model_routing.runtime.load_model_bundle",
            side_effect=load_failure,
        ):
            with self.assertRaisesRegex(ModelRuntimeLoadError, "failed to load") as caught:
                IntentClassifierRuntime(self.artifact_dir, device="cpu")

        self.assertIs(caught.exception.__cause__, load_failure)

    def _artifact_missing(self, missing_name: str) -> Path:
        incomplete_dir = self.temp_path / f"missing-{missing_name}"
        incomplete_dir.mkdir()
        (incomplete_dir / "encoder").mkdir()
        for required_name in IntentClassifierRuntime.REQUIRED_FILES:
            if required_name != missing_name:
                (incomplete_dir / required_name).write_bytes(b"")
        return incomplete_dir


if __name__ == "__main__":
    unittest.main()
