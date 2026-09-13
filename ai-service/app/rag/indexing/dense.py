"""Lazy local Qwen3 document embedding runtime."""

from __future__ import annotations

from typing import Protocol, Sequence

from .config import DenseEmbeddingConfig


class DenseEncoder(Protocol):
    def encode_documents(self, texts: Sequence[str]) -> list[list[float]]: ...


class QwenDenseEncoder:
    """Encode documents only; query instructions belong to the online pipeline."""

    def __init__(self, config: DenseEmbeddingConfig) -> None:
        self.config = config
        self._tokenizer = None
        self._model = None
        self._torch = None
        self._device: str | None = None

    def encode_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        self._ensure_loaded()
        assert self._tokenizer is not None
        assert self._model is not None
        assert self._torch is not None
        assert self._device is not None
        torch = self._torch
        batch = self._tokenizer(
            list(texts),
            padding=True,
            truncation=True,
            max_length=self.config.max_length,
            return_tensors="pt",
        )
        batch = {key: value.to(self._device) for key, value in batch.items()}
        with torch.inference_mode():
            outputs = self._model(**batch)
            embeddings = self._last_token_pool(
                outputs.last_hidden_state, batch["attention_mask"], torch
            )
            if embeddings.shape[1] != self.config.dimension:
                raise ValueError(
                    "dense_dimension_mismatch:"
                    f"expected={self.config.dimension}:actual={embeddings.shape[1]}"
                )
            if self.config.normalize:
                embeddings = self._normalize_embeddings(embeddings, torch)
        return embeddings.detach().cpu().float().tolist()

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        import torch
        from transformers import AutoModel, AutoTokenizer

        device = self.config.device
        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        tokenizer = AutoTokenizer.from_pretrained(
            self.config.model_name,
            revision=self.config.model_revision,
            padding_side="left",
            local_files_only=self.config.local_files_only,
        )
        model = AutoModel.from_pretrained(
            self.config.model_name,
            revision=self.config.model_revision,
            local_files_only=self.config.local_files_only,
        )
        model.eval()
        model.to(device)
        self._tokenizer = tokenizer
        self._model = model
        self._torch = torch
        self._device = device

    @staticmethod
    def _last_token_pool(last_hidden_states, attention_mask, torch):  # type: ignore[no-untyped-def]
        if bool((attention_mask[:, -1].sum() == attention_mask.shape[0]).item()):
            return last_hidden_states[:, -1]
        sequence_lengths = attention_mask.sum(dim=1) - 1
        batch_size = last_hidden_states.shape[0]
        return last_hidden_states[
            torch.arange(batch_size, device=last_hidden_states.device),
            sequence_lengths,
        ]

    @staticmethod
    def _normalize_embeddings(embeddings, torch):  # type: ignore[no-untyped-def]
        """Normalize in FP32 so BF16 model outputs remain unit length."""

        return torch.nn.functional.normalize(embeddings.float(), p=2, dim=1)
