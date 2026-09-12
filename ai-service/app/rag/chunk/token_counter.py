"""Replaceable token counting boundary for chunk sizing."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Protocol

from .config import TokenCounterBackend, TokenCounterConfig


class TokenCounter(Protocol):
    @property
    def name(self) -> str: ...

    def count(self, text: str) -> int: ...


class UnicodeHeuristicTokenCounter:
    """Offline-safe approximation that counts lexical/CJK units, never characters."""

    _token_pattern = re.compile(
        r"[A-Za-z0-9]+(?:[._'’-][A-Za-z0-9]+)*"
        r"|[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]"
        r"|[^\s]",
        re.UNICODE,
    )

    @property
    def name(self) -> str:
        return "unicode_heuristic:v1"

    def count(self, text: str) -> int:
        return sum(1 for _ in self._token_pattern.finditer(text))


class CallableTokenCounter:
    def __init__(self, function: Callable[[str], int], *, name: str = "callable"):
        self._function = function
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def count(self, text: str) -> int:
        count = self._function(text)
        if count < 0:
            raise ValueError("token counter returned a negative value")
        return count


class HuggingFaceTokenCounter:
    def __init__(self, tokenizer: object, *, add_special_tokens: bool = False):
        if not hasattr(tokenizer, "encode"):
            raise TypeError("tokenizer must expose encode(text, ...)")
        self.tokenizer = tokenizer
        self.add_special_tokens = add_special_tokens
        identity = getattr(tokenizer, "name_or_path", tokenizer.__class__.__name__)
        self._name = f"huggingface:{identity}"

    @property
    def name(self) -> str:
        return self._name

    def count(self, text: str) -> int:
        token_ids = self.tokenizer.encode(  # type: ignore[attr-defined]
            text,
            add_special_tokens=self.add_special_tokens,
            truncation=False,
        )
        return len(token_ids)

    @classmethod
    def from_config(cls, config: TokenCounterConfig) -> "HuggingFaceTokenCounter":
        from transformers import AutoTokenizer

        assert config.tokenizer_name_or_path is not None
        kwargs = dict(config.tokenizer_kwargs)
        kwargs["local_files_only"] = config.local_files_only
        tokenizer = AutoTokenizer.from_pretrained(
            config.tokenizer_name_or_path,
            **kwargs,
        )
        return cls(tokenizer, add_special_tokens=config.add_special_tokens)


def build_token_counter(config: TokenCounterConfig) -> TokenCounter:
    if config.backend is TokenCounterBackend.HUGGINGFACE:
        return HuggingFaceTokenCounter.from_config(config)
    return UnicodeHeuristicTokenCounter()
