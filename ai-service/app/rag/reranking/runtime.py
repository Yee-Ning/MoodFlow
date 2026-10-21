"""Lazy local Qwen3 reranker runtime with a strict MoodFlow input ceiling."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Protocol

from .config import RerankModelConfig
from .models import RerankInput


SYSTEM_PREFIX = (
    "<|im_start|>system\n"
    "Judge whether the Document meets the requirements based on the Query and "
    'the Instruct provided. Note that the answer can only be "yes" or "no".'
    "<|im_end|>\n<|im_start|>user\n<Instruct>: "
)
QUERY_PREFIX = "\n<Query>: "
DOCUMENT_PREFIX = "\n<Document>: "
ASSISTANT_SUFFIX = (
    "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
)


class RerankScorer(Protocol):
    def score(self, inputs: Sequence[RerankInput]) -> list[float]: ...


class QwenRerankScorer:
    """Score query/Child pairs from yes/no logits without text generation."""

    def __init__(
        self,
        config: RerankModelConfig,
        *,
        tokenizer=None,
        model=None,
        torch_module=None,
        device: str | None = None,
    ) -> None:
        self.config = config
        self._tokenizer = tokenizer
        self._model = model
        self._torch = torch_module
        self._device = device

    def prepare(self) -> None:
        """Load the pinned runtime outside any per-request timeout budget."""
        self._ensure_loaded()

    def score(self, inputs: Sequence[RerankInput]) -> list[float]:
        if not inputs:
            return []
        self._ensure_loaded()
        scores: list[float] = []
        for offset in range(0, len(inputs), self.config.batch_size):
            scores.extend(self._score_batch(inputs[offset : offset + self.config.batch_size]))
        if len(scores) != len(inputs):
            raise ValueError("rerank_score_count_mismatch")
        if any(not math.isfinite(score) or not 0 <= score <= 1 for score in scores):
            raise ValueError("rerank_score_invalid")
        return scores

    def _score_batch(self, inputs: Sequence[RerankInput]) -> list[float]:
        assert self._tokenizer is not None
        assert self._model is not None
        assert self._torch is not None
        assert self._device is not None
        tokenizer = self._tokenizer
        torch = self._torch
        input_ids = [self._encode_pair(item) for item in inputs]
        batch = tokenizer.pad(
            {"input_ids": input_ids},
            padding=True,
            return_attention_mask=True,
            return_tensors="pt",
        )
        batch = {key: value.to(self._device) for key, value in batch.items()}
        true_token_id = tokenizer.convert_tokens_to_ids("yes")
        false_token_id = tokenizer.convert_tokens_to_ids("no")
        if (
            not isinstance(true_token_id, int)
            or not isinstance(false_token_id, int)
            or true_token_id == false_token_id
        ):
            raise ValueError("rerank_label_token_invalid")
        with torch.inference_mode():
            # Classification consumes only the final yes/no logits. Computing
            # vocabulary logits for every document token makes CPU calls huge.
            logits = self._model(**batch, logits_to_keep=1, use_cache=False).logits[:, -1, :]
            label_logits = torch.stack(
                [logits[:, false_token_id], logits[:, true_token_id]],
                dim=1,
            )
            probabilities = torch.nn.functional.softmax(label_logits.float(), dim=1)[
                :, 1
            ]
        return probabilities.detach().cpu().tolist()

    def _encode_pair(self, item: RerankInput) -> list[int]:
        assert self._tokenizer is not None
        tokenizer = self._tokenizer
        static_prefix = tokenizer.encode(
            SYSTEM_PREFIX + self.config.instruction + QUERY_PREFIX,
            add_special_tokens=False,
        )
        document_prefix = tokenizer.encode(DOCUMENT_PREFIX, add_special_tokens=False)
        suffix = tokenizer.encode(ASSISTANT_SUFFIX, add_special_tokens=False)
        reserved = len(static_prefix) + len(document_prefix) + len(suffix)
        if reserved + 2 > self.config.input_token_limit:
            raise ValueError("rerank_template_exceeds_input_limit")
        query_budget = min(
            self.config.query_token_budget,
            self.config.input_token_limit - reserved - 1,
        )
        query_tokens = tokenizer.encode(
            self._safe_text(item.query),
            add_special_tokens=False,
        )[:query_budget]
        document_budget = self.config.input_token_limit - reserved - len(query_tokens)
        document_tokens = tokenizer.encode(
            self._safe_text(item.document),
            add_special_tokens=False,
        )[:document_budget]
        encoded = [
            *static_prefix,
            *query_tokens,
            *document_prefix,
            *document_tokens,
            *suffix,
        ]
        if len(encoded) > self.config.input_token_limit:
            raise ValueError("rerank_input_limit_exceeded")
        return encoded

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            if self._tokenizer is None or self._torch is None or self._device is None:
                raise ValueError("rerank_injected_runtime_incomplete")
            return
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        device = self.config.device
        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        tokenizer = AutoTokenizer.from_pretrained(
            self.config.name,
            revision=self.config.revision,
            padding_side="left",
            local_files_only=self.config.local_files_only,
        )
        model_kwargs = {
            "revision": self.config.revision,
            "local_files_only": self.config.local_files_only,
            # The published BF16 default is emulated on AVX2-only CPUs.
            "dtype": torch.float32,
        }
        if device == "cuda":
            model_kwargs["dtype"] = (
                torch.bfloat16
                if torch.cuda.is_bf16_supported()
                else torch.float16
            )
        model = AutoModelForCausalLM.from_pretrained(
            self.config.name,
            **model_kwargs,
        )
        model.eval()
        model.to(device)
        self._tokenizer = tokenizer
        self._model = model
        self._torch = torch
        self._device = device

    @staticmethod
    def _safe_text(value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("rerank_text_blank")
        return normalized.replace("<|", "＜|")
