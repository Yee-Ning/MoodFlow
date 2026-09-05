from typing import Any, Literal

import torch
from torch import nn
from transformers import AutoModel


class BgeMultiLabelClassifier(nn.Module):
    """BGE encoder plus a dropout/linear multi-label classification head."""

    def __init__(
        self,
        encoder: nn.Module,
        *,
        num_labels: int,
        dropout: float = 0.1,
        pooling: Literal["cls", "mean"] = "cls",
    ) -> None:
        super().__init__()
        if num_labels <= 0:
            raise ValueError("num_labels must be positive")
        if pooling not in ("cls", "mean"):
            raise ValueError("pooling must be 'cls' or 'mean'")
        hidden_size = getattr(encoder.config, "hidden_size", None)
        if not isinstance(hidden_size, int) or hidden_size <= 0:
            raise ValueError("encoder.config.hidden_size must be a positive integer")
        self.encoder = encoder
        self.pooling = pooling
        self.dropout = nn.Dropout(dropout)
        self.classifier = nn.Linear(hidden_size, num_labels)

    @classmethod
    def from_pretrained(
        cls,
        model_name_or_path: str,
        *,
        num_labels: int,
        dropout: float = 0.1,
        pooling: Literal["cls", "mean"] = "cls",
    ) -> "BgeMultiLabelClassifier":
        encoder = AutoModel.from_pretrained(model_name_or_path)
        return cls(
            encoder,
            num_labels=num_labels,
            dropout=dropout,
            pooling=pooling,
        )

    def _pool(
        self,
        last_hidden_state: torch.Tensor,
        attention_mask: torch.Tensor | None,
    ) -> torch.Tensor:
        if self.pooling == "cls":
            return last_hidden_state[:, 0, :]
        if attention_mask is None:
            raise ValueError("mean pooling requires attention_mask")
        mask = attention_mask.unsqueeze(-1).to(last_hidden_state.dtype)
        summed = (last_hidden_state * mask).sum(dim=1)
        denominator = mask.sum(dim=1).clamp_min(1.0)
        return summed / denominator

    def forward(
        self,
        input_ids: torch.Tensor,
        attention_mask: torch.Tensor | None = None,
        token_type_ids: torch.Tensor | None = None,
    ) -> torch.Tensor:
        encoder_inputs: dict[str, Any] = {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
            "return_dict": True,
        }
        if token_type_ids is not None:
            encoder_inputs["token_type_ids"] = token_type_ids
        outputs = self.encoder(**encoder_inputs)
        representation = self._pool(outputs.last_hidden_state, attention_mask)
        return self.classifier(self.dropout(representation))
